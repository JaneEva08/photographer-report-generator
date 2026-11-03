"""
google_sheets.py — универсальный модуль для работы с Google Таблицами
через Google Sheets API и сервисный аккаунт.

Модуль не зависит от конкретного приложения: его можно подключить
в любой проект и работать с таблицей через методы класса GoogleSheets.

Пример:
    from google_sheets import GoogleSheets

    sheets = GoogleSheets("service_account.json", "ID_таблицы")
    print(sheets.read_all_cells())
    sheets.update_cell("A1", "Привет")

Форматирование можно отправлять пачкой — это заметно быстрее,
чем отдельный запрос на каждую ячейку:

    with sheets.batch():
        sheets.format_range("Отчёт!A1:F1", bold=True, background="#5B3FD6")
        sheets.merge_cells("Отчёт!A1:F1")
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional, Sequence, Union

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

CellValue = Union[str, int, float, bool, None]

NBSP_CHARS = ("\xa0", " ", " ")  # пробелы денежного формата


class GoogleSheetsError(Exception):
    """Понятная ошибка модуля вместо «сырого» HttpError."""


# ---------------------------------------------------------------------------
# Вспомогательные функции (не требуют подключения к Google)
# ---------------------------------------------------------------------------

def clean_value(value: Any) -> Any:
    """Убирает неразрывные пробелы денежного формата и лишние пробелы по краям."""
    if isinstance(value, str):
        for ch in NBSP_CHARS:
            value = value.replace(ch, " ")
        return value.strip()
    return value


def column_to_index(letters: str) -> int:
    """'A' -> 0, 'Z' -> 25, 'AA' -> 26."""
    index = 0
    for ch in letters.upper():
        if not ("A" <= ch <= "Z"):
            raise ValueError(f"Некорректная буква столбца: {letters!r}")
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def index_to_column(index: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA'."""
    if index < 0:
        raise ValueError("Индекс столбца не может быть отрицательным")
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


_CELL_RE = re.compile(r"^([A-Za-z]+)?(\d+)?$")


def split_range(range_name: str) -> tuple[Optional[str], str]:
    """'Отчёт!A1:B2' -> ('Отчёт', 'A1:B2'); 'A1' -> (None, 'A1')."""
    if "!" in range_name:
        sheet, a1 = range_name.rsplit("!", 1)
        return sheet.strip("'"), a1
    return None, range_name


def a1_to_grid(a1: str, sheet_id: int) -> dict:
    """
    Переводит диапазон A1 ('B2:D5', 'A1', 'A:C', '3:5') в GridRange,
    который требуется в запросах форматирования. Индексы — с нуля,
    конечные границы не включаются (так устроен API).
    """
    parts = a1.split(":")
    if len(parts) > 2 or not parts[0]:
        raise ValueError(f"Некорректный диапазон: {a1!r}")

    def parse(cell: str) -> tuple[Optional[int], Optional[int]]:
        m = _CELL_RE.match(cell)
        if not m or not (m.group(1) or m.group(2)):
            raise ValueError(f"Некорректная ячейка: {cell!r}")
        col = column_to_index(m.group(1)) if m.group(1) else None
        row = int(m.group(2)) - 1 if m.group(2) else None
        return row, col

    start_row, start_col = parse(parts[0])
    end_row, end_col = parse(parts[-1])

    grid: dict = {"sheetId": sheet_id}
    if start_row is not None:
        grid["startRowIndex"] = start_row
    if end_row is not None:
        grid["endRowIndex"] = end_row + 1
    if start_col is not None:
        grid["startColumnIndex"] = start_col
    if end_col is not None:
        grid["endColumnIndex"] = end_col + 1
    return grid


def hex_to_color(hex_color: str) -> dict:
    """'#5B3FD6' -> {'red': 0.357, 'green': 0.247, 'blue': 0.839}."""
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise ValueError(f"Цвет должен быть в формате #RRGGBB: {hex_color!r}")
    r, g, b = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return {"red": round(r, 4), "green": round(g, 4), "blue": round(b, 4)}


# ---------------------------------------------------------------------------
# Класс для работы с таблицей
# ---------------------------------------------------------------------------

class GoogleSheets:
    """CRUD-операции и форматирование для одной Google Таблицы."""

    def __init__(self, credentials_path: str, spreadsheet_id: str, service: Any = None):
        """
        Args:
            credentials_path: путь к JSON-ключу сервисного аккаунта.
            spreadsheet_id: ID таблицы — часть URL между /d/ и /edit.
            service: готовый клиент API (нужен для тестов); обычно не передаётся.
        """
        self.spreadsheet_id = spreadsheet_id
        if service is not None:
            self.service = service
        else:
            try:
                creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
            except FileNotFoundError as e:
                raise GoogleSheetsError(f"Не найден JSON-ключ: {credentials_path}") from e
            self.service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self._queue: Optional[List[dict]] = None
        self._sheet_ids: dict[str, int] = {}

    # ----- служебное ---------------------------------------------------------

    @property
    def url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}/edit"

    def _execute(self, request: Any, action: str) -> dict:
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(e.resp, "status", "?")
            hint = ""
            if status == 403:
                hint = " Проверьте, что таблица открыта сервисному аккаунту с правами «Редактор»."
            elif status == 404:
                hint = " Проверьте ID таблицы и название листа."
            raise GoogleSheetsError(f"{action}: ошибка {status}.{hint} Детали: {e}") from e

    def get_metadata(self) -> dict:
        """Свойства таблицы и её листов."""
        return self._execute(
            self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id),
            "Чтение свойств таблицы",
        )

    def get_title(self) -> str:
        return self.get_metadata()["properties"]["title"]

    def get_sheet_names(self) -> List[str]:
        sheets = self.get_metadata().get("sheets", [])
        self._sheet_ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in sheets}
        return list(self._sheet_ids)

    def get_sheet_id(self, sheet_name: str) -> int:
        """Числовой sheetId листа (нужен для форматирования и удаления строк)."""
        if sheet_name not in self._sheet_ids:
            self.get_sheet_names()
        if sheet_name not in self._sheet_ids:
            raise GoogleSheetsError(f"Лист «{sheet_name}» не найден")
        return self._sheet_ids[sheet_name]

    def _first_sheet(self) -> str:
        names = self.get_sheet_names()
        if not names:
            raise GoogleSheetsError("В таблице нет листов")
        return names[0]

    def _full_range(self, range_name: str, sheet_name: Optional[str] = None) -> str:
        sheet, a1 = split_range(range_name)
        sheet = sheet or sheet_name
        return f"'{sheet}'!{a1}" if sheet else a1

    def _grid(self, range_name: str, sheet_name: Optional[str] = None) -> dict:
        sheet, a1 = split_range(range_name)
        sheet = sheet or sheet_name or self._first_sheet()
        return a1_to_grid(a1, self.get_sheet_id(sheet))

    # ----- пакетный режим ----------------------------------------------------

    def batch_update(self, requests: Sequence[dict]) -> dict:
        """Отправляет запросы batchUpdate (или копит их внутри `with batch()`)."""
        if not requests:
            return {}
        if self._queue is not None:
            self._queue.extend(requests)
            return {"queued": len(requests)}
        return self._execute(
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id, body={"requests": list(requests)}
            ),
            "Пакетное обновление",
        )

    @contextmanager
    def batch(self) -> Iterator["GoogleSheets"]:
        """Копит запросы форматирования и отправляет их одним вызовом API."""
        if self._queue is not None:  # уже внутри batch — просто продолжаем копить
            yield self
            return
        self._queue = []
        try:
            yield self
            queued, self._queue = self._queue, None
            if queued:
                self.batch_update(queued)
        finally:
            self._queue = None

    # ----- чтение (Read) -----------------------------------------------------

    def read_range(self, range_name: str, sheet_name: Optional[str] = None) -> List[List[Any]]:
        """Значения диапазона, очищенные от неразрывных пробелов."""
        result = self._execute(
            self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id, range=self._full_range(range_name, sheet_name)
            ),
            "Чтение диапазона",
        )
        return [[clean_value(v) for v in row] for row in result.get("values", [])]

    def read_all_cells(self, sheet_name: Optional[str] = None) -> List[List[Any]]:
        """Все заполненные ячейки листа (по умолчанию — первого)."""
        sheet = sheet_name or self._first_sheet()
        result = self._execute(
            self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id, range=f"'{sheet}'"
            ),
            "Чтение листа",
        )
        return [[clean_value(v) for v in row] for row in result.get("values", [])]

    def read_cell(self, cell_address: str, sheet_name: Optional[str] = None) -> Any:
        values = self.read_range(cell_address, sheet_name)
        return values[0][0] if values and values[0] else None

    # ----- запись (Create / Update) -----------------------------------------

    def write_range(
        self,
        range_name: str,
        values: List[List[CellValue]],
        value_input_option: str = "RAW",
        sheet_name: Optional[str] = None,
    ) -> dict:
        """
        Записывает данные в диапазон.

        values — список строк, каждая строка — список значений:
            write_range("B26:F26", [[1, 2, 3, 4, 5]])     # одна строка
            write_range("A1:B2", [["Имя", "Сумма"], ["Анна", 450]])

        value_input_option:
            'RAW' — как есть: «+49 151...» останется текстом, а не формулой;
            'USER_ENTERED' — как при вводе руками: формулы и даты распознаются.
        """
        if not isinstance(values, list) or (values and not isinstance(values[0], list)):
            raise GoogleSheetsError(
                "values должен быть списком строк, например [[1, 2, 3]] для одной строки"
            )
        return self._execute(
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=self._full_range(range_name, sheet_name),
                valueInputOption=value_input_option,
                body={"values": values},
            ),
            "Запись диапазона",
        )

    def update_cell(
        self,
        cell_address: str,
        value: CellValue,
        sheet_name: Optional[str] = None,
        value_input_option: str = "RAW",
    ) -> dict:
        """Обновляет одну ячейку: update_cell("E18", "Привет")."""
        return self.write_range(cell_address, [[value]], value_input_option, sheet_name)

    def append_row(
        self,
        values: List[CellValue],
        sheet_name: Optional[str] = None,
        value_input_option: str = "RAW",
    ) -> dict:
        """Добавляет строку после последней заполненной."""
        return self.append_rows([values], sheet_name, value_input_option)

    def append_rows(
        self,
        rows: List[List[CellValue]],
        sheet_name: Optional[str] = None,
        value_input_option: str = "RAW",
    ) -> dict:
        sheet = sheet_name or self._first_sheet()
        return self._execute(
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet}'",
                valueInputOption=value_input_option,
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            ),
            "Добавление строк",
        )

    # ----- удаление (Delete) -------------------------------------------------

    def clear_range(self, range_name: str, sheet_name: Optional[str] = None) -> dict:
        """Очищает значения диапазона (форматирование остаётся)."""
        return self._execute(
            self.service.spreadsheets().values().clear(
                spreadsheetId=self.spreadsheet_id,
                range=self._full_range(range_name, sheet_name),
            ),
            "Очистка диапазона",
        )

    def delete_row(self, row_number: int, sheet_name: Optional[str] = None) -> dict:
        """Удаляет строку по номеру (с 1) со сдвигом нижних строк вверх."""
        return self.delete_rows(row_number, row_number, sheet_name)

    def delete_rows(self, first_row: int, last_row: int, sheet_name: Optional[str] = None) -> dict:
        if first_row < 1 or last_row < first_row:
            raise GoogleSheetsError("Номера строк начинаются с 1, last_row >= first_row")
        sheet = sheet_name or self._first_sheet()
        return self.batch_update([{
            "deleteDimension": {
                "range": {
                    "sheetId": self.get_sheet_id(sheet),
                    "dimension": "ROWS",
                    "startIndex": first_row - 1,
                    "endIndex": last_row,
                }
            }
        }])

    # ----- листы -------------------------------------------------------------

    def add_sheet(self, title: str, rows: int = 200, cols: int = 12) -> int:
        """Создаёт лист и возвращает его sheetId. Если имя занято — добавляет (2), (3)…"""
        existing = set(self.get_sheet_names())
        name, n = title, 2
        while name in existing:
            name, n = f"{title} ({n})", n + 1
        result = self._execute(
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {
                    "title": name,
                    "gridProperties": {"rowCount": rows, "columnCount": cols},
                }}}]},
            ),
            "Создание листа",
        )
        props = result["replies"][0]["addSheet"]["properties"]
        self._sheet_ids[props["title"]] = props["sheetId"]
        self.last_added_sheet = props["title"]
        return props["sheetId"]

    def delete_sheet(self, sheet_name: str) -> dict:
        sheet_id = self.get_sheet_id(sheet_name)
        result = self.batch_update([{"deleteSheet": {"sheetId": sheet_id}}])
        self._sheet_ids.pop(sheet_name, None)
        return result

    # ----- форматирование ----------------------------------------------------

    def format_range(
        self,
        range_name: str,
        *,
        sheet_name: Optional[str] = None,
        bold: Optional[bool] = None,
        italic: Optional[bool] = None,
        font_size: Optional[int] = None,
        font_family: Optional[str] = None,
        font_color: Optional[str] = None,
        background: Optional[str] = None,
        h_align: Optional[str] = None,
        v_align: Optional[str] = None,
        wrap: Optional[bool] = None,
        number_format: Optional[str] = None,
        number_type: str = "NUMBER",
    ) -> dict:
        """
        Оформление диапазона. Цвета — '#RRGGBB', выравнивание — LEFT/CENTER/RIGHT
        и TOP/MIDDLE/BOTTOM, number_format — шаблон вида '#,##0 "€"',
        number_type — NUMBER, PERCENT, DATE, CURRENCY или TEXT.
        Меняются только переданные параметры.
        """
        fmt: dict = {}
        fields: List[str] = []
        text: dict = {}
        if bold is not None:
            text["bold"] = bold
        if italic is not None:
            text["italic"] = italic
        if font_size is not None:
            text["fontSize"] = font_size
        if font_family is not None:
            text["fontFamily"] = font_family
        if font_color is not None:
            text["foregroundColor"] = hex_to_color(font_color)
        if text:
            fmt["textFormat"] = text
            fields += [f"userEnteredFormat.textFormat.{k}" for k in text]
        if background is not None:
            fmt["backgroundColor"] = hex_to_color(background)
            fields.append("userEnteredFormat.backgroundColor")
        if h_align is not None:
            fmt["horizontalAlignment"] = h_align.upper()
            fields.append("userEnteredFormat.horizontalAlignment")
        if v_align is not None:
            fmt["verticalAlignment"] = v_align.upper()
            fields.append("userEnteredFormat.verticalAlignment")
        if wrap is not None:
            fmt["wrapStrategy"] = "WRAP" if wrap else "OVERFLOW_CELL"
            fields.append("userEnteredFormat.wrapStrategy")
        if number_format is not None:
            fmt["numberFormat"] = {"type": number_type.upper(), "pattern": number_format}
            fields.append("userEnteredFormat.numberFormat")
        if not fields:
            return {}
        return self.batch_update([{
            "repeatCell": {
                "range": self._grid(range_name, sheet_name),
                "cell": {"userEnteredFormat": fmt},
                "fields": ",".join(fields),
            }
        }])

    def merge_cells(self, range_name: str, sheet_name: Optional[str] = None) -> dict:
        """Объединяет диапазон в одну ячейку."""
        return self.batch_update([{
            "mergeCells": {"range": self._grid(range_name, sheet_name), "mergeType": "MERGE_ALL"}
        }])

    def unmerge_cells(self, range_name: str, sheet_name: Optional[str] = None) -> dict:
        return self.batch_update([{"unmergeCells": {"range": self._grid(range_name, sheet_name)}}])

    def set_borders(
        self,
        range_name: str,
        color: str = "#D0D0D8",
        style: str = "SOLID",
        inner: bool = True,
        sheet_name: Optional[str] = None,
    ) -> dict:
        """Рамки вокруг диапазона и (по умолчанию) между ячейками."""
        border = {"style": style, "color": hex_to_color(color)}
        body: dict = {"range": self._grid(range_name, sheet_name),
                      "top": border, "bottom": border, "left": border, "right": border}
        if inner:
            body["innerHorizontal"] = border
            body["innerVertical"] = border
        return self.batch_update([{"updateBorders": body}])

    def set_column_widths(self, widths: Sequence[int], sheet_name: Optional[str] = None,
                          start_column: int = 0) -> dict:
        """Ширина столбцов в пикселях, начиная со столбца start_column (A = 0)."""
        sheet = sheet_name or self._first_sheet()
        sheet_id = self.get_sheet_id(sheet)
        return self.batch_update([
            {"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": start_column + i, "endIndex": start_column + i + 1},
                "properties": {"pixelSize": int(w)},
                "fields": "pixelSize",
            }}
            for i, w in enumerate(widths)
        ])

    def set_row_height(self, first_row: int, last_row: int, height: int,
                       sheet_name: Optional[str] = None) -> dict:
        sheet = sheet_name or self._first_sheet()
        return self.batch_update([{"updateDimensionProperties": {
            "range": {"sheetId": self.get_sheet_id(sheet), "dimension": "ROWS",
                      "startIndex": first_row - 1, "endIndex": last_row},
            "properties": {"pixelSize": int(height)},
            "fields": "pixelSize",
        }}])

    def freeze(self, rows: int = 0, cols: int = 0, sheet_name: Optional[str] = None) -> dict:
        sheet = sheet_name or self._first_sheet()
        return self.batch_update([{"updateSheetProperties": {
            "properties": {"sheetId": self.get_sheet_id(sheet),
                           "gridProperties": {"frozenRowCount": rows, "frozenColumnCount": cols}},
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
        }}])

    def hide_gridlines(self, sheet_name: Optional[str] = None, hidden: bool = True) -> dict:
        sheet = sheet_name or self._first_sheet()
        return self.batch_update([{"updateSheetProperties": {
            "properties": {"sheetId": self.get_sheet_id(sheet),
                           "gridProperties": {"hideGridlines": hidden}},
            "fields": "gridProperties.hideGridlines",
        }}])

    def add_column_chart(
        self,
        title: str,
        labels_range: str,
        values_range: str,
        anchor_cell: str,
        sheet_name: Optional[str] = None,
        color: str = "#5B3FD6",
        width: int = 720,
        height: int = 320,
    ) -> dict:
        """
        Столбчатая диаграмма: подписи из labels_range, значения из values_range
        (оба — один столбец), левый верхний угол диаграммы — anchor_cell.
        """
        sheet = sheet_name or self._first_sheet()
        sheet_id = self.get_sheet_id(sheet)
        anchor = a1_to_grid(split_range(anchor_cell)[1], sheet_id)
        return self.batch_update([{"addChart": {"chart": {
            "spec": {
                "title": title,
                "basicChart": {
                    "chartType": "COLUMN",
                    "legendPosition": "NO_LEGEND",
                    "axis": [{"position": "BOTTOM_AXIS"}, {"position": "LEFT_AXIS"}],
                    "domains": [{"domain": {"sourceRange": {"sources": [
                        self._grid(labels_range, sheet)]}}}],
                    "series": [{
                        "series": {"sourceRange": {"sources": [self._grid(values_range, sheet)]}},
                        "targetAxis": "LEFT_AXIS",
                        "color": hex_to_color(color),
                    }],
                    "headerCount": 0,
                },
            },
            "position": {"overlayPosition": {
                "anchorCell": {"sheetId": sheet_id,
                               "rowIndex": anchor.get("startRowIndex", 0),
                               "columnIndex": anchor.get("startColumnIndex", 0)},
                "widthPixels": width,
                "heightPixels": height,
            }},
        }}}])

    def set_tab_color(self, color: str, sheet_name: Optional[str] = None) -> dict:
        sheet = sheet_name or self._first_sheet()
        return self.batch_update([{"updateSheetProperties": {
            "properties": {"sheetId": self.get_sheet_id(sheet), "tabColor": hex_to_color(color)},
            "fields": "tabColor",
        }}])


# ---------------------------------------------------------------------------
# Точка входа: проверка подключения — список листов и все ячейки первого листа
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "service_account.json")
    SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")

    if not SPREADSHEET_ID:
        sys.exit("Укажите SPREADSHEET_ID в файле .env (часть ссылки между /d/ и /edit)")

    try:
        sheets = GoogleSheets(CREDENTIALS_PATH, SPREADSHEET_ID)
        print(f"Таблица: {sheets.get_title()}")
        print("Листы:")
        for name in sheets.get_sheet_names():
            print(f"   - {name}")
        print()
        cells = sheets.read_all_cells()
        print("Ячейки первого листа:")
        print("-" * 50)
        if not cells:
            print("Лист пуст")
        for i, row in enumerate(cells, start=1):
            print(f"Строка {i}: {row}")
        print("-" * 50)
        print(f"Всего строк: {len(cells)}")
    except GoogleSheetsError as e:
        sys.exit(f"Ошибка: {e}")
