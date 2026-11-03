"""
report_builder.py — раскладка отчёта фотографа: какие значения в какие
ячейки, что объединить, как раскрасить.

Раскладка (ReportLayout) не зависит ни от Tkinter, ни от Google:
её можно отправить в Google Таблицу (apply_to_sheet) или показать
локальным HTML-предпросмотром (render_html) — например, пока не
настроен доступ к Google.
"""

from __future__ import annotations

import html
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from data_generator import Shoot
from google_sheets import GoogleSheets, a1_to_grid, column_to_index, index_to_column

# Палитра «Фоточки»: фиолетовый акцент, лавандовые подложки.
PALETTE = {
    "primary": "#5B3FD6",
    "primary_dark": "#3E2A9E",
    "accent": "#3A8DFF",
    "lavender": "#EEEAFE",
    "zebra": "#F8F6FF",
    "border": "#D9D3F5",
    "text": "#1E1B2E",
    "muted": "#6B6880",
    "white": "#FFFFFF",
}

STATUS_COLORS = {
    "Оплачено": "#E3F4E8",
    "Предоплата": "#FFF4D6",
    "Ожидает оплаты": "#FDE7E7",
    "Отменена": "#EFEFF2",
}
STATUS_ORDER = ["Оплачено", "Предоплата", "Ожидает оплаты", "Отменена"]

COLUMNS = 12  # A..L
LAST_COL = index_to_column(COLUMNS - 1)
COLUMN_WIDTHS = [92, 185, 58, 190, 110, 64, 180, 86, 80, 86, 96, 128]

EURO = '#,##0 "€"'


@dataclass
class Formula:
    """Формула для ячейки + заранее посчитанное значение (для предпросмотра)."""
    expression: str
    preview: Any
    kind: str = "number"  # number | percent | date


@dataclass
class Style:
    range: str
    params: Dict[str, Any]


@dataclass
class ReportLayout:
    title: str
    sheet_title: str
    rows: List[List[Any]] = field(default_factory=list)
    styles: List[Style] = field(default_factory=list)
    merges: List[str] = field(default_factory=list)
    borders: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)
    row_heights: List[Tuple[int, int, int]] = field(default_factory=list)
    chart: Optional[Dict[str, Any]] = None
    summary: Dict[str, Any] = field(default_factory=dict)

    # --- построение ---
    def new_row(self) -> int:
        self.rows.append([None] * COLUMNS)
        return len(self.rows)  # номер строки в таблице (с 1)

    def put(self, row: int, col: str, value: Any) -> None:
        self.rows[row - 1][column_to_index(col)] = value

    def style(self, a1: str, **params: Any) -> None:
        self.styles.append(Style(a1, params))

    def merge(self, a1: str) -> None:
        self.merges.append(a1)


# ---------------------------------------------------------------------------
# Расчёты
# ---------------------------------------------------------------------------

def summarize(shoots: Sequence[Shoot]) -> Dict[str, Any]:
    active = [s for s in shoots if s.status != "Отменена"]
    revenue = sum(s.total for s in active)
    extras = sum(s.travel_fee + s.retouch_fee for s in active)
    by_theme: Dict[str, Dict[str, int]] = defaultdict(lambda: {"count": 0, "hours": 0, "revenue": 0})
    for s in active:
        t = by_theme[s.theme]
        t["count"] += 1
        t["hours"] += s.hours
        t["revenue"] += s.total
    by_status_count = Counter(s.status for s in shoots)
    by_status_sum: Dict[str, int] = defaultdict(int)
    for s in shoots:
        by_status_sum[s.status] += s.package_price + s.travel_fee + s.retouch_fee
    return {
        "count": len(active),
        "cancelled": by_status_count.get("Отменена", 0),
        "revenue": revenue,
        "avg_check": round(revenue / len(active)) if active else 0,
        "extras": extras,
        "by_theme": dict(sorted(by_theme.items(), key=lambda kv: -kv[1]["revenue"])),
        "by_status": {st: (by_status_count.get(st, 0), by_status_sum.get(st, 0))
                      for st in STATUS_ORDER if by_status_count.get(st)},
    }


def _date_formula(d: date) -> Formula:
    # ISO-дата распознаётся как дата при любой локали таблицы.
    return Formula(d.isoformat(), d.strftime("%d.%m.%Y"), "date")


# ---------------------------------------------------------------------------
# Раскладка
# ---------------------------------------------------------------------------

def build_report(
    shoots: Sequence[Shoot],
    start: date,
    end: date,
    photographer: str,
    generated_at: Optional[datetime] = None,
) -> ReportLayout:
    generated_at = generated_at or datetime.now()
    period = f"{start:%d.%m.%Y} – {end:%d.%m.%Y}"
    layout = ReportLayout(
        title="Отчёт по съёмкам и выручке",
        sheet_title=f"Отчёт {start:%d.%m}–{end:%d.%m.%Y}",
    )
    stats = summarize(shoots)
    layout.summary = stats
    P = PALETTE

    # 1. Шапка
    r = layout.new_row()
    layout.put(r, "A", layout.title)
    layout.merge(f"A{r}:{LAST_COL}{r}")
    layout.style(f"A{r}:{LAST_COL}{r}", bold=True, font_size=18, font_color=P["white"],
                 background=P["primary"], h_align="LEFT", v_align="MIDDLE")
    layout.row_heights.append((r, r, 48))

    r = layout.new_row()
    layout.put(r, "A", f"{photographer}  ·  период {period}  ·  "
                       f"сформирован {generated_at:%d.%m.%Y %H:%M}")
    layout.merge(f"A{r}:{LAST_COL}{r}")
    layout.style(f"A{r}:{LAST_COL}{r}", font_size=10, font_color=P["white"],
                 background=P["primary_dark"], h_align="LEFT", v_align="MIDDLE")
    layout.row_heights.append((r, r, 26))

    layout.new_row()

    # 2. Ключевые показатели — 4 карточки по 3 столбца
    kpis = [
        ("Съёмок проведено", stats["count"], None),
        ("Выручка", stats["revenue"], EURO),
        ("Средний чек", stats["avg_check"], EURO),
        ("Доплаты: выезд и ретушь", stats["extras"], EURO),
    ]
    r_label = layout.new_row()
    r_value = layout.new_row()
    for i, (label, value, fmt) in enumerate(kpis):
        c1 = index_to_column(i * 3)
        c3 = index_to_column(i * 3 + 2)
        layout.put(r_label, c1, label)
        layout.put(r_value, c1, value)
        layout.merge(f"{c1}{r_label}:{c3}{r_label}")
        layout.merge(f"{c1}{r_value}:{c3}{r_value}")
        layout.style(f"{c1}{r_label}:{c3}{r_label}", font_size=10, font_color=P["muted"],
                     background=P["lavender"], h_align="CENTER", v_align="BOTTOM")
        value_style: Dict[str, Any] = dict(bold=True, font_size=20, font_color=P["primary"],
                                           background=P["lavender"], h_align="CENTER",
                                           v_align="MIDDLE")
        if fmt:
            value_style["number_format"] = fmt
        layout.style(f"{c1}{r_value}:{c3}{r_value}", **value_style)
        layout.borders.append((f"{c1}{r_label}:{c3}{r_value}",
                               {"color": P["white"], "style": "SOLID_THICK", "inner": False}))
    layout.row_heights.append((r_label, r_label, 28))
    layout.row_heights.append((r_value, r_value, 46))
    if stats["cancelled"]:
        r = layout.new_row()
        layout.put(r, "A", f"Отменено съёмок: {stats['cancelled']} — в выручку не входят.")
        layout.merge(f"A{r}:{LAST_COL}{r}")
        layout.style(f"A{r}:{LAST_COL}{r}", italic=True, font_size=9, font_color=P["muted"])

    layout.new_row()

    # 3. Две таблицы рядом: по тематикам (A:G) и по статусам оплаты (I:L)
    r_section = layout.new_row()
    layout.put(r_section, "A", "Выручка по тематикам")
    layout.put(r_section, "I", "Статусы оплаты")
    for rng in (f"A{r_section}:G{r_section}", f"I{r_section}:L{r_section}"):
        layout.merge(rng)
        layout.style(rng, bold=True, font_size=12, font_color=P["primary"],
                     h_align="LEFT", v_align="BOTTOM")
    layout.row_heights.append((r_section, r_section, 30))

    r_head = layout.new_row()
    for col, text in [("A", "Тематика"), ("C", "Съёмок"), ("D", "Часов съёмки"),
                      ("E", "Выручка"), ("F", "Доля"), ("G", "Средний чек"),
                      ("I", "Статус"), ("K", "Съёмок"), ("L", "Сумма")]:
        layout.put(r_head, col, text)
    layout.merge(f"A{r_head}:B{r_head}")
    layout.merge(f"I{r_head}:J{r_head}")
    for rng in (f"A{r_head}:G{r_head}", f"I{r_head}:L{r_head}"):
        layout.style(rng, bold=True, font_color=P["white"], background=P["primary"],
                     h_align="CENTER", v_align="MIDDLE", wrap=True)

    theme_rows = list(stats["by_theme"].items())
    status_rows = list(stats["by_status"].items())
    first = r_head + 1
    n_theme = max(len(theme_rows), 1)
    total_theme_row = first + n_theme
    revenue_total = stats["revenue"] or 1
    body_rows = max(n_theme, len(status_rows)) + 1  # + строка «Итого»

    for i in range(body_rows):
        r = layout.new_row()
        # тематики
        if i < len(theme_rows):
            theme, t = theme_rows[i]
            layout.put(r, "A", theme)
            layout.put(r, "C", t["count"])
            layout.put(r, "D", t["hours"])
            layout.put(r, "E", t["revenue"])
            layout.put(r, "F", Formula(f"=E{r}/E${total_theme_row}",
                                       t["revenue"] / revenue_total, "percent"))
            layout.put(r, "G", Formula(f"=E{r}/C{r}", round(t["revenue"] / t["count"])))
            layout.merge(f"A{r}:B{r}")
            if i % 2:
                layout.style(f"A{r}:G{r}", background=P["zebra"])
        elif r == total_theme_row:
            last = total_theme_row - 1
            layout.put(r, "A", "Итого")
            layout.put(r, "C", Formula(f"=SUM(C{first}:C{last})", stats["count"]))
            layout.put(r, "D", Formula(f"=SUM(D{first}:D{last})",
                                       sum(t["hours"] for _, t in theme_rows)))
            layout.put(r, "E", Formula(f"=SUM(E{first}:E{last})", stats["revenue"]))
            layout.put(r, "F", Formula(f"=E{r}/E{r}", 1, "percent") if stats["revenue"] else 0)
            layout.put(r, "G", Formula(f"=E{r}/C{r}", stats["avg_check"]) if stats["count"] else 0)
            layout.merge(f"A{r}:B{r}")
            layout.style(f"A{r}:G{r}", bold=True, background=P["lavender"])
        # статусы
        if i < len(status_rows):
            status, (count, amount) = status_rows[i]
            layout.put(r, "I", status)
            layout.put(r, "K", count)
            layout.put(r, "L", amount)
            layout.merge(f"I{r}:J{r}")
            layout.style(f"I{r}:L{r}", background=STATUS_COLORS.get(status, P["white"]))

    last_body = r_head + body_rows
    layout.style(f"C{first}:D{total_theme_row}", h_align="CENTER")
    layout.style(f"E{first}:E{total_theme_row}", number_format=EURO, h_align="RIGHT")
    layout.style(f"F{first}:F{total_theme_row}", number_format="0%", number_type="PERCENT",
                 h_align="CENTER")
    layout.style(f"G{first}:G{total_theme_row}", number_format=EURO, h_align="RIGHT")
    layout.borders.append((f"A{r_head}:G{total_theme_row}", {"color": P["border"]}))
    if status_rows:
        last_status = r_head + len(status_rows)
        layout.style(f"K{first}:K{last_status}", h_align="CENTER")
        layout.style(f"L{first}:L{last_status}", number_format=EURO, h_align="RIGHT")
        layout.borders.append((f"I{r_head}:L{last_status}", {"color": P["border"]}))
        note_row = last_status + 1
        if note_row <= last_body:
            layout.put(note_row, "I", "Сумма — по прайсу, у отменённых без учёта неустойки")
            layout.merge(f"I{note_row}:L{note_row}")
            layout.style(f"I{note_row}:L{note_row}", italic=True, font_size=8,
                         font_color=P["muted"], wrap=True)

    if theme_rows:
        layout.chart = {
            "title": "Выручка по тематикам, €",
            "labels": f"A{first}:A{total_theme_row - 1}",
            "values": f"E{first}:E{total_theme_row - 1}",
        }

    layout.new_row()

    # 4. Детализация
    r = layout.new_row()
    layout.put(r, "A", f"Детализация съёмок ({len(shoots)})")
    layout.merge(f"A{r}:{LAST_COL}{r}")
    layout.style(f"A{r}:{LAST_COL}{r}", bold=True, font_size=12, font_color=P["primary"],
                 v_align="BOTTOM")
    layout.row_heights.append((r, r, 30))

    r_dh = layout.new_row()
    headers = ["Дата", "Клиент", "Язык", "Тематика", "Пакет", "Часы", "Зона выезда",
               "Пакет", "Выезд", "Ретушь сверх пакета", "Итого", "Статус"]
    for i, text in enumerate(headers):
        layout.put(r_dh, index_to_column(i), text)
    layout.style(f"A{r_dh}:{LAST_COL}{r_dh}", bold=True, font_color=P["white"],
                 background=P["primary"], h_align="CENTER", v_align="MIDDLE", wrap=True)
    layout.row_heights.append((r_dh, r_dh, 36))

    first_detail = r_dh + 1
    for i, s in enumerate(shoots):
        r = layout.new_row()
        values = [_date_formula(s.shoot_date), s.client, s.language, s.theme, s.package,
                  s.hours, s.zone, s.package_price, s.travel_fee, s.retouch_fee,
                  Formula(f'=(L{r}<>"Отменена")*(H{r}+I{r}+J{r})', s.total), s.status]
        for j, v in enumerate(values):
            layout.put(r, index_to_column(j), v)
        if s.status == "Отменена":
            layout.style(f"A{r}:{LAST_COL}{r}", font_color=P["muted"], italic=True)
        elif i % 2:
            layout.style(f"A{r}:K{r}", background=P["zebra"])
        layout.style(f"L{r}", background=STATUS_COLORS.get(s.status, P["white"]))

    last_detail = first_detail + len(shoots) - 1
    r_total = layout.new_row()
    layout.put(r_total, "A", "Итого")
    for col in ("H", "I", "J", "K"):
        preview = {
            "H": sum(s.package_price for s in shoots if s.status != "Отменена"),
            "I": sum(s.travel_fee for s in shoots if s.status != "Отменена"),
            "J": sum(s.retouch_fee for s in shoots if s.status != "Отменена"),
            "K": stats["revenue"],
        }[col]
        expr = (f'=SUMPRODUCT(($L{first_detail}:$L{last_detail}<>"Отменена")'
                f"*{col}{first_detail}:{col}{last_detail})")
        layout.put(r_total, col, Formula(expr, preview))
    layout.merge(f"A{r_total}:G{r_total}")
    layout.style(f"A{r_total}:{LAST_COL}{r_total}", bold=True, background=P["lavender"])

    if shoots:
        layout.style(f"A{first_detail}:A{last_detail}", number_format="dd.mm.yyyy",
                     number_type="DATE", h_align="CENTER")
        layout.style(f"C{first_detail}:C{last_detail}", h_align="CENTER")
        layout.style(f"F{first_detail}:F{last_detail}", h_align="CENTER")
        layout.style(f"L{first_detail}:L{last_detail}", h_align="CENTER")
    layout.style(f"H{first_detail}:K{r_total}", number_format=EURO, h_align="RIGHT")
    layout.borders.append((f"A{r_dh}:{LAST_COL}{r_total}", {"color": P["border"]}))

    # 5. Место под диаграмму и подпись
    if layout.chart:
        layout.new_row()
        layout.chart["anchor"] = f"A{len(layout.rows) + 1}"
        layout.chart["rows"] = (len(layout.rows), len(layout.rows) + 17)
        layout.chart["data"] = [(t, v["revenue"]) for t, v in stats["by_theme"].items()]
        for _ in range(17):  # диаграмма высотой ~320 px
            layout.new_row()
    r = layout.new_row()
    layout.put(r, "A", "Отчёт сформирован автоматически генератором отчётов. "
                       "Данные — симуляция для учебного проекта.")
    layout.merge(f"A{r}:{LAST_COL}{r}")
    layout.style(f"A{r}:{LAST_COL}{r}", italic=True, font_size=9, font_color=P["muted"])
    return layout


# ---------------------------------------------------------------------------
# Вывод в Google Таблицу
# ---------------------------------------------------------------------------

_FORMULA_START = ("=", "+", "-", "@")


def to_cell(value: Any) -> Any:
    """
    Значение для записи в режиме USER_ENTERED.
    Формулы остаются формулами, а текст, похожий на формулу
    (например «+49 151…» или «-15%»), экранируется апострофом,
    чтобы в ячейке не появилась #ERROR!.
    """
    if value is None:
        return ""
    if isinstance(value, Formula):
        return value.expression
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def apply_to_sheet(sheets: GoogleSheets, layout: ReportLayout) -> str:
    """Создаёт новый лист, записывает отчёт и оформляет его. Возвращает имя листа."""
    sheets.add_sheet(layout.sheet_title, rows=max(len(layout.rows) + 5, 50), cols=COLUMNS)
    name = sheets.last_added_sheet
    values = [[to_cell(v) for v in row] for row in layout.rows]
    sheets.write_range(f"A1:{LAST_COL}{len(values)}", values,
                       value_input_option="USER_ENTERED", sheet_name=name)

    with sheets.batch():
        sheets.hide_gridlines(name)
        sheets.set_tab_color(PALETTE["primary"], name)
        sheets.set_column_widths(COLUMN_WIDTHS, name)
        for first, last, height in layout.row_heights:
            sheets.set_row_height(first, last, height, name)
        sheets.format_range(f"A1:{LAST_COL}{len(values)}", sheet_name=name,
                            font_family="Roboto", font_color=PALETTE["text"], v_align="MIDDLE")
        for st in layout.styles:
            sheets.format_range(st.range, sheet_name=name, **st.params)
        for rng in layout.merges:
            sheets.merge_cells(rng, name)
        for rng, params in layout.borders:
            sheets.set_borders(rng, sheet_name=name, **params)
        if layout.chart:
            sheets.add_column_chart(layout.chart["title"], layout.chart["labels"],
                                    layout.chart["values"], layout.chart["anchor"],
                                    sheet_name=name, color=PALETTE["primary"])
    return name


# ---------------------------------------------------------------------------
# Локальный HTML-предпросмотр (без Google)
# ---------------------------------------------------------------------------

def _display(value: Any, params: Dict[str, Any]) -> str:
    if isinstance(value, Formula):
        if value.kind == "percent":
            return f"{value.preview:.0%}"
        value = value.preview
    if value is None:
        return ""
    fmt = params.get("number_format")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if fmt == "0%":
            return f"{value:.0%}"
        text = f"{value:,.0f}".replace(",", " ")
        return f"{text} €" if fmt == EURO else text
    return str(value)


def _chart_svg(chart: Dict[str, Any]) -> str:
    """Упрощённая копия диаграммы Google для предпросмотра."""
    data = chart.get("data") or []
    w, h, left, bottom, top = 720, 320, 56, 64, 40
    peak = max((v for _, v in data), default=0) or 1
    step = (w - left - 16) / max(len(data), 1)
    bar = step * 0.6
    parts = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
             f'style="background:#fff;border:1px solid {PALETTE["border"]}">',
             f'<text x="{w / 2}" y="24" text-anchor="middle" font-size="14" '
             f'fill="{PALETTE["text"]}">{html.escape(chart["title"])}</text>']
    for i in range(5):
        y = h - bottom - (h - bottom - top) * i / 4
        parts.append(f'<line x1="{left}" x2="{w - 16}" y1="{y}" y2="{y}" stroke="#e6e4ef"/>'
                     f'<text x="{left - 6}" y="{y + 4}" text-anchor="end" font-size="11" '
                     f'fill="{PALETTE["muted"]}">{peak * i / 4:,.0f}</text>'.replace(",", " "))
    for i, (label, value) in enumerate(data):
        bh = (h - bottom - top) * value / peak
        x = left + step * i + (step - bar) / 2
        parts.append(f'<rect x="{x:.1f}" y="{h - bottom - bh:.1f}" width="{bar:.1f}" '
                     f'height="{bh:.1f}" fill="{PALETTE["primary"]}"/>')
        short = label if len(label) <= 16 else label[:15] + "…"
        parts.append(f'<text x="{x + bar / 2:.1f}" y="{h - bottom + 16}" text-anchor="middle" '
                     f'font-size="11" fill="{PALETTE["muted"]}">{html.escape(short)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_html(layout: ReportLayout) -> str:
    """HTML-таблица, повторяющая раскладку: удобно проверить вид без Google."""
    n_rows = len(layout.rows)
    cell_params: List[List[Dict[str, Any]]] = [[{} for _ in range(COLUMNS)] for _ in range(n_rows)]

    def cells(a1: str):
        g = a1_to_grid(a1, 0)
        for r in range(g.get("startRowIndex", 0), g.get("endRowIndex", n_rows)):
            for c in range(g.get("startColumnIndex", 0), g.get("endColumnIndex", COLUMNS)):
                if r < n_rows and c < COLUMNS:
                    yield r, c

    for st in layout.styles:
        for r, c in cells(st.range):
            cell_params[r][c].update(st.params)
    borders = [[False] * COLUMNS for _ in range(n_rows)]
    for rng, params in layout.borders:
        if params.get("inner", True):
            for r, c in cells(rng):
                borders[r][c] = True

    span: Dict[Tuple[int, int], int] = {}
    hidden = set()
    for rng in layout.merges:
        g = a1_to_grid(rng, 0)
        r, c0, c1 = g["startRowIndex"], g["startColumnIndex"], g["endColumnIndex"]
        span[(r, c0)] = c1 - c0
        hidden.update((r, c) for c in range(c0 + 1, c1))

    heights = {}
    for first, last, h in layout.row_heights:
        for r in range(first - 1, last):
            heights[r] = h

    chart_rows = range(*layout.chart["rows"]) if layout.chart else range(0)
    out = ['<table class="sheet"><colgroup>']
    out += [f'<col style="width:{w}px">' for w in COLUMN_WIDTHS]
    out.append("</colgroup>")
    for r, row in enumerate(layout.rows):
        if r in chart_rows:
            if r == chart_rows.start:
                out.append(f'<tr><td colspan="{COLUMNS}" style="padding:12px 6px">'
                           f'{_chart_svg(layout.chart)}</td></tr>')
            continue
        out.append(f'<tr style="height:{heights.get(r, 22)}px">')
        for c, value in enumerate(row):
            if (r, c) in hidden:
                continue
            p = cell_params[r][c]
            css = []
            if p.get("background"):
                css.append(f"background:{p['background']}")
            if p.get("font_color"):
                css.append(f"color:{p['font_color']}")
            if p.get("bold"):
                css.append("font-weight:700")
            if p.get("italic"):
                css.append("font-style:italic")
            if p.get("font_size"):
                css.append(f"font-size:{p['font_size'] * 1.33:.0f}px")
            align = p.get("h_align")
            if not align and isinstance(value, (int, float, Formula)) and not (
                    isinstance(value, Formula) and value.kind == "date"):
                align = "RIGHT"
            if align:
                css.append(f"text-align:{align.lower()}")
            v_align = {"TOP": "top", "MIDDLE": "middle", "BOTTOM": "bottom"}.get(
                p.get("v_align", "MIDDLE"), "middle")
            css.append(f"vertical-align:{v_align}")
            if borders[r][c]:
                css.append(f"border:1px solid {PALETTE['border']}")
            if p.get("wrap"):
                css.append("white-space:normal")
            colspan = f' colspan="{span[(r, c)]}"' if (r, c) in span else ""
            out.append(f'<td{colspan} style="{";".join(css)}">'
                       f"{html.escape(_display(value, p))}</td>")
        out.append("</tr>")
    out.append("</table>")
    table = "\n".join(out)
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Предпросмотр отчёта</title>
<style>
body{{margin:0;padding:24px;background:#f1f0f5;font-family:Roboto,Arial,sans-serif;color:{PALETTE['text']}}}
.note{{font-size:13px;color:{PALETTE['muted']};margin:0 0 12px}}
.sheet{{border-collapse:collapse;table-layout:fixed;background:#fff;font-size:13px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
.sheet td{{padding:2px 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
</style></head><body>
<p class="note">Предпросмотр листа «{html.escape(layout.sheet_title)}» — так отчёт будет выглядеть в Google Таблице.</p>
{table}
</body></html>"""
