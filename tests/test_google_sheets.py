import pytest

from fake_service import FakeService
from google_sheets import (GoogleSheets, GoogleSheetsError, a1_to_grid, clean_value,
                           column_to_index, hex_to_color, index_to_column)


def make(values=None):
    svc = FakeService(values)
    return GoogleSheets("unused.json", "SHEET", service=svc), svc


def test_columns_roundtrip():
    for i in (0, 1, 25, 26, 51, 701, 702):
        assert column_to_index(index_to_column(i)) == i
    assert index_to_column(26) == "AA"


def test_a1_to_grid():
    assert a1_to_grid("B2:D5", 7) == {"sheetId": 7, "startRowIndex": 1, "endRowIndex": 5,
                                      "startColumnIndex": 1, "endColumnIndex": 4}
    assert a1_to_grid("A1", 0) == {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1,
                                   "startColumnIndex": 0, "endColumnIndex": 1}
    assert a1_to_grid("A:C", 0) == {"sheetId": 0, "startColumnIndex": 0, "endColumnIndex": 3}
    with pytest.raises(ValueError):
        a1_to_grid("1A", 0)


def test_hex_to_color():
    assert hex_to_color("#ffffff") == {"red": 1.0, "green": 1.0, "blue": 1.0}
    assert hex_to_color("#000") == {"red": 0.0, "green": 0.0, "blue": 0.0}


def test_read_cleans_nbsp():
    assert clean_value("1\xa0250,00\xa0€") == "1 250,00 €"
    sheets, _ = make([["Январь", "1\xa0250"]])
    assert sheets.read_all_cells() == [["Январь", "1 250"]]


def test_write_range_requires_list_of_rows():
    sheets, svc = make()
    with pytest.raises(GoogleSheetsError):
        sheets.write_range("B26:F26", [1, 2, 3])
    sheets.write_range("B26:F26", [[1, 2, 3, 4, 5]])
    assert svc.calls[-1] == ("values.update", "B26:F26", "RAW", [[1, 2, 3, 4, 5]])


def test_update_cell_on_sheet():
    sheets, svc = make()
    sheets.update_cell("E18", "Привет", sheet_name="Лист1")
    assert svc.calls[-1] == ("values.update", "'Лист1'!E18", "RAW", [["Привет"]])


def test_delete_row_uses_sheet_id():
    sheets, svc = make()
    sheets.delete_row(3)
    rng = svc.batches[-1][0]["deleteDimension"]["range"]
    assert rng == {"sheetId": 0, "dimension": "ROWS", "startIndex": 2, "endIndex": 3}


def test_batch_sends_one_request():
    sheets, svc = make()
    with sheets.batch():
        sheets.format_range("A1:C1", bold=True, background="#5B3FD6")
        sheets.merge_cells("A1:C1")
        sheets.set_borders("A1:C3")
    assert len(svc.batches) == 1
    kinds = [list(r)[0] for r in svc.batches[0]]
    assert kinds == ["repeatCell", "mergeCells", "updateBorders"]
    fields = svc.batches[0][0]["repeatCell"]["fields"]
    assert "userEnteredFormat.textFormat.bold" in fields
    assert "userEnteredFormat.backgroundColor" in fields


def test_add_sheet_avoids_duplicates():
    sheets, svc = make()
    sheets.add_sheet("Отчёт")
    sheets.add_sheet("Отчёт")
    assert "Отчёт" in svc.sheets and "Отчёт (2)" in svc.sheets
    assert sheets.last_added_sheet == "Отчёт (2)"


def test_unknown_sheet():
    sheets, _ = make()
    with pytest.raises(GoogleSheetsError):
        sheets.get_sheet_id("Нет такого")
