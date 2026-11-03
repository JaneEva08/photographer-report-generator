"""
report_generator.py — «Генератор отчётов» фотографа на Tkinter.

Пользователь выбирает период, фотографа и тематики, нажимает
«Сгенерировать отчёт» — приложение создаёт случайные данные о съёмках
и записывает их на новый лист Google Таблицы с оформлением:
цвета, шрифты, объединённые ячейки, итоги формулами и диаграмма.

Кнопка «Предпросмотр» показывает тот же отчёт в браузере без Google —
удобно проверить вид, пока не настроен доступ.

Запуск:  python report_generator.py
"""

from __future__ import annotations

import os
import queue
import tempfile
import threading
import tkinter as tk
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk
from typing import List, Optional, Tuple

from dotenv import load_dotenv

from data_generator import THEMES, generate_shoots
from google_sheets import GoogleSheets, GoogleSheetsError
from report_builder import PALETTE, apply_to_sheet, build_report, render_html

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")

DATE_FMT = "%d.%m.%Y"


# ---------------------------------------------------------------------------
# Даты
# ---------------------------------------------------------------------------

def parse_date(text: str) -> date:
    try:
        return datetime.strptime(text.strip(), DATE_FMT).date()
    except ValueError as e:
        raise ValueError(f"Дата «{text}» не в формате ДД.ММ.ГГГГ") from e


def previous_month(today: date) -> Tuple[date, date]:
    end = today.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


def current_quarter(today: date) -> Tuple[date, date]:
    first_month = 3 * ((today.month - 1) // 3) + 1
    return date(today.year, first_month, 1), today


def current_year(today: date) -> Tuple[date, date]:
    return date(today.year, 1, 1), today


def validate_period(start: date, end: date) -> None:
    if end < start:
        raise ValueError("Дата «по» раньше даты «с»")
    if (end - start).days > 366:
        raise ValueError("Период не длиннее года")


# ---------------------------------------------------------------------------
# Окно
# ---------------------------------------------------------------------------

class ReportApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Генератор отчётов фотографа")
        self.minsize(560, 560)
        self.configure(bg=PALETTE["white"])
        self._events: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self._busy = False
        self._last_url: Optional[str] = None

        self._setup_styles()
        self._build()
        self._set_period(previous_month(date.today()))
        self.after(100, self._poll_events)

    # ----- оформление --------------------------------------------------------

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        bg = PALETTE["white"]
        style.configure(".", background=bg, foreground=PALETTE["text"], font=("Segoe UI", 10))
        style.configure("Header.TFrame", background=PALETTE["primary"])
        style.configure("Header.TLabel", background=PALETTE["primary"],
                        foreground=PALETTE["white"], font=("Segoe UI", 16, "bold"))
        style.configure("Sub.TLabel", background=PALETTE["primary"],
                        foreground="#DCD5FF", font=("Segoe UI", 10))
        style.configure("Section.TLabel", foreground=PALETTE["primary"],
                        font=("Segoe UI", 11, "bold"))
        style.configure("Muted.TLabel", foreground=PALETTE["muted"], font=("Segoe UI", 9))
        style.configure("TCheckbutton", background=bg)
        style.configure("Accent.TButton", background=PALETTE["primary"],
                        foreground=PALETTE["white"], font=("Segoe UI", 11, "bold"),
                        padding=(16, 10), borderwidth=0)
        style.map("Accent.TButton",
                  background=[("disabled", "#B9AEEF"), ("active", PALETTE["primary_dark"])])
        style.configure("Chip.TButton", padding=(8, 3), font=("Segoe UI", 9))
        style.configure("Card.TFrame", background=PALETTE["lavender"])
        style.configure("CardLabel.TLabel", background=PALETTE["lavender"],
                        foreground=PALETTE["muted"], font=("Segoe UI", 9))
        style.configure("CardValue.TLabel", background=PALETTE["lavender"],
                        foreground=PALETTE["primary"], font=("Segoe UI", 15, "bold"))
        style.configure("Purple.Horizontal.TProgressbar", background=PALETTE["primary"],
                        troughcolor=PALETTE["lavender"], borderwidth=0)

    def _build(self) -> None:
        header = ttk.Frame(self, style="Header.TFrame", padding=(20, 16))
        header.pack(fill="x")
        ttk.Label(header, text="Генератор отчётов", style="Header.TLabel").pack(anchor="w")
        ttk.Label(header, text="Съёмки и выручка фотографа → Google Таблица",
                  style="Sub.TLabel").pack(anchor="w")

        body = ttk.Frame(self, padding=(20, 14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        # Фотограф
        ttk.Label(body, text="Фотограф").grid(row=0, column=0, sticky="w", pady=4, padx=(0, 12))
        self.var_photographer = tk.StringVar(value=os.getenv("PHOTOGRAPHER_NAME", "Ксения Толстых"))
        ttk.Entry(body, textvariable=self.var_photographer).grid(
            row=0, column=1, columnspan=3, sticky="ew", pady=4)

        # Период
        ttk.Label(body, text="Период").grid(row=1, column=0, sticky="w", pady=4, padx=(0, 12))
        period = ttk.Frame(body)
        period.grid(row=1, column=1, columnspan=3, sticky="w", pady=4)
        self.var_start = tk.StringVar()
        self.var_end = tk.StringVar()
        ttk.Label(period, text="с").pack(side="left")
        ttk.Entry(period, textvariable=self.var_start, width=11).pack(side="left", padx=(4, 10))
        ttk.Label(period, text="по").pack(side="left")
        ttk.Entry(period, textvariable=self.var_end, width=11).pack(side="left", padx=4)

        chips = ttk.Frame(body)
        chips.grid(row=2, column=1, columnspan=3, sticky="w", pady=(0, 6))
        for text, fn in [("Прошлый месяц", previous_month),
                         ("Текущий квартал", current_quarter),
                         ("С начала года", current_year)]:
            ttk.Button(chips, text=text, style="Chip.TButton",
                       command=lambda f=fn: self._set_period(f(date.today()))).pack(
                side="left", padx=(0, 6))

        # Тематики
        ttk.Label(body, text="Тематики", style="Section.TLabel").grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(10, 2))
        self.theme_vars: dict[str, tk.BooleanVar] = {}
        themes_box = ttk.Frame(body)
        themes_box.grid(row=4, column=0, columnspan=4, sticky="w")
        for i, theme in enumerate(THEMES):
            var = tk.BooleanVar(value=True)
            self.theme_vars[theme] = var
            ttk.Checkbutton(themes_box, text=theme, variable=var).grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, 24), pady=2)

        # Кнопки
        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(16, 6))
        self.btn_generate = ttk.Button(buttons, text="Сгенерировать отчёт",
                                       style="Accent.TButton", command=self.on_generate)
        self.btn_generate.pack(side="left")
        self.btn_preview = ttk.Button(buttons, text="Предпросмотр", command=self.on_preview)
        self.btn_preview.pack(side="left", padx=8)
        self.btn_open = ttk.Button(buttons, text="Открыть таблицу", command=self.on_open,
                                   state="disabled")
        self.btn_open.pack(side="left")

        self.progress = ttk.Progressbar(body, mode="indeterminate",
                                        style="Purple.Horizontal.TProgressbar")
        self.progress.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(6, 2))
        self.progress.grid_remove()  # показываем только во время записи
        self.var_status = tk.StringVar(value="Выберите период и нажмите «Сгенерировать отчёт».")
        ttk.Label(body, textvariable=self.var_status, style="Muted.TLabel", wraplength=500).grid(
            row=7, column=0, columnspan=4, sticky="w")

        # Карточки с итогами последнего отчёта
        cards = ttk.Frame(body)
        cards.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        self.card_values: dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate([("count", "Съёмок"), ("revenue", "Выручка"),
                                          ("avg_check", "Средний чек")]):
            cards.columnconfigure(i, weight=1)
            card = ttk.Frame(cards, style="Card.TFrame", padding=(12, 8))
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))
            ttk.Label(card, text=label, style="CardLabel.TLabel").pack(anchor="w")
            var = tk.StringVar(value="—")
            self.card_values[key] = var
            ttk.Label(card, textvariable=var, style="CardValue.TLabel").pack(anchor="w")

    # ----- ввод --------------------------------------------------------------

    def _set_period(self, period: Tuple[date, date]) -> None:
        self.var_start.set(period[0].strftime(DATE_FMT))
        self.var_end.set(period[1].strftime(DATE_FMT))

    def _collect(self) -> Optional[Tuple[date, date, str, List[str]]]:
        try:
            start = parse_date(self.var_start.get())
            end = parse_date(self.var_end.get())
            validate_period(start, end)
        except ValueError as e:
            messagebox.showwarning("Период", str(e), parent=self)
            return None
        themes = [t for t, v in self.theme_vars.items() if v.get()]
        if not themes:
            messagebox.showwarning("Тематики", "Отметьте хотя бы одну тематику.", parent=self)
            return None
        name = self.var_photographer.get().strip() or "Фотограф"
        return start, end, name, themes

    def _make_layout(self, start: date, end: date, name: str, themes: List[str]):
        shoots = generate_shoots(start, end, themes)
        return build_report(shoots, start, end, name)

    # ----- действия ----------------------------------------------------------

    def on_preview(self) -> None:
        params = self._collect()
        if not params:
            return
        layout = self._make_layout(*params)
        path = Path(tempfile.gettempdir()) / "photo_report_preview.html"
        path.write_text(render_html(layout), encoding="utf-8")
        self._show_summary(layout.summary)
        self.var_status.set(f"Предпросмотр открыт в браузере: {path}")
        webbrowser.open(path.as_uri())

    def on_generate(self) -> None:
        if self._busy:
            return
        params = self._collect()
        if not params:
            return
        creds = os.getenv("GOOGLE_CREDENTIALS_PATH", "service_account.json")
        creds_path = Path(creds) if Path(creds).is_absolute() else APP_DIR / creds
        sheet_id = os.getenv("SPREADSHEET_ID", "").strip()
        if not sheet_id or not creds_path.exists():
            messagebox.showerror(
                "Нет подключения к Google",
                "Заполните файл .env:\n"
                "  GOOGLE_CREDENTIALS_PATH — путь к JSON-ключу сервисного аккаунта\n"
                "  SPREADSHEET_ID — ID таблицы из ссылки (между /d/ и /edit)\n\n"
                f"Сейчас: ключ {'найден' if creds_path.exists() else 'не найден'}, "
                f"ID {'указан' if sheet_id else 'не указан'}.\n\n"
                "Чтобы посмотреть отчёт без Google, нажмите «Предпросмотр».",
                parent=self)
            return

        layout = self._make_layout(*params)
        self._set_busy(True, "Записываю отчёт в Google Таблицу…")

        def work() -> None:
            try:
                sheets = GoogleSheets(str(creds_path), sheet_id)
                name = apply_to_sheet(sheets, layout)
                gid = sheets.get_sheet_id(name)
                self._events.put(("done", (layout.summary, name, f"{sheets.url}#gid={gid}")))
            except GoogleSheetsError as e:
                self._events.put(("error", str(e)))
            except Exception as e:  # сеть, неверный ключ и т. п.
                self._events.put(("error", f"{type(e).__name__}: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def on_open(self) -> None:
        if self._last_url:
            webbrowser.open(self._last_url)

    # ----- фоновая работа ----------------------------------------------------

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.btn_generate.configure(state=state)
        self.btn_preview.configure(state=state)
        self.var_status.set(status)
        if busy:
            self.progress.grid()
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.grid_remove()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "done":
                    summary, name, url = payload  # type: ignore[misc]
                    self._last_url = url
                    self.btn_open.configure(state="normal")
                    self._show_summary(summary)
                    self._set_busy(False, f"Готово: лист «{name}» добавлен в таблицу.")
                    if messagebox.askyesno("Отчёт готов",
                                           f"Лист «{name}» создан.\nОткрыть таблицу?", parent=self):
                        self.on_open()
                elif kind == "error":
                    self._set_busy(False, "Отчёт не записан — см. сообщение об ошибке.")
                    messagebox.showerror("Ошибка Google Sheets", str(payload), parent=self)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _show_summary(self, summary: dict) -> None:
        def euro(v: int) -> str:
            return f"{v:,.0f} €".replace(",", " ")
        self.card_values["count"].set(str(summary["count"]))
        self.card_values["revenue"].set(euro(summary["revenue"]))
        self.card_values["avg_check"].set(euro(summary["avg_check"]))


if __name__ == "__main__":
    ReportApp().mainloop()
