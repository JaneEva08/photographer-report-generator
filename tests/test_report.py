from datetime import date, datetime

import pytest

from data_generator import THEMES, generate_shoots
from fake_service import FakeService
from google_sheets import GoogleSheets
from report_builder import COLUMNS, Formula, apply_to_sheet, build_report, render_html, to_cell

START, END = date(2026, 9, 1), date(2026, 9, 30)


def test_generator_is_reproducible_and_in_period():
    a = generate_shoots(START, END, seed=7)
    b = generate_shoots(START, END, seed=7)
    assert a == b and len(a) >= 5
    assert all(START <= s.shoot_date <= END for s in a)
    assert [s.shoot_date for s in a] == sorted(s.shoot_date for s in a)


def test_generator_filters_themes():
    shoots = generate_shoots(START, END, themes=["Свадьба"], seed=1)
    assert {s.theme for s in shoots} == {"Свадьба"}
    with pytest.raises(ValueError):
        generate_shoots(START, END, themes=[])
    with pytest.raises(ValueError):
        generate_shoots(END, START)


def test_cancelled_shoot_gives_no_revenue():
    shoots = generate_shoots(date(2026, 1, 1), date(2026, 12, 31), seed=3)
    cancelled = [s for s in shoots if s.status == "Отменена"]
    assert cancelled and all(s.total == 0 for s in cancelled)


def test_layout_totals_match():
    shoots = generate_shoots(START, END, seed=11)
    layout = build_report(shoots, START, END, "Ксения Толстых", datetime(2026, 10, 22, 5, 0))
    s = layout.summary
    active = [x for x in shoots if x.status != "Отменена"]
    assert s["count"] == len(active)
    assert s["revenue"] == sum(x.total for x in active)
    assert sum(t["revenue"] for t in s["by_theme"].values()) == s["revenue"]
    assert all(len(row) == COLUMNS for row in layout.rows)
    assert layout.chart and layout.chart["anchor"].startswith("A")


def test_formulas_have_no_locale_separators():
    """Формулы без «,» и «;» работают при любой локали таблицы."""
    shoots = generate_shoots(START, END, seed=5)
    layout = build_report(shoots, START, END, "Test")
    formulas = [v.expression for row in layout.rows for v in row
                if isinstance(v, Formula) and v.expression.startswith("=")]
    assert formulas
    assert not any("," in f or ";" in f for f in formulas)


def test_to_cell_escapes_formula_like_text():
    assert to_cell("+49 151 1234567") == "'+49 151 1234567"
    assert to_cell("=SUM(A1)") == "'=SUM(A1)"
    assert to_cell(Formula("=A1+B1", 3)) == "=A1+B1"
    assert to_cell(None) == ""
    assert to_cell(42) == 42


def test_apply_to_sheet_with_fake_service():
    svc = FakeService()
    sheets = GoogleSheets("unused.json", "SHEET", service=svc)
    shoots = generate_shoots(START, END, seed=2)
    layout = build_report(shoots, START, END, "Ксения Толстых")
    name = apply_to_sheet(sheets, layout)
    assert name == layout.sheet_title
    update = [c for c in svc.calls if c[0] == "values.update"][-1]
    assert update[2] == "USER_ENTERED"
    assert len(update[3]) == len(layout.rows)
    # 1 batch на создание листа + 1 batch на всё оформление
    assert len(svc.batches) == 2
    kinds = {list(r)[0] for r in svc.batches[1]}
    assert {"repeatCell", "mergeCells", "updateBorders", "addChart",
            "updateDimensionProperties", "updateSheetProperties"} <= kinds


def test_empty_period_still_builds():
    shoots = [s for s in generate_shoots(START, END, seed=4) if s.status == "Отменена"]
    layout = build_report(shoots, START, END, "Test")
    assert layout.summary["revenue"] == 0
    assert "<table" in render_html(layout)


def test_all_themes_known():
    assert len(THEMES) == 6


@pytest.mark.parametrize("seed", range(20))
def test_merges_do_not_overlap(seed):
    """Google отклоняет запрос, если объединения пересекаются."""
    from google_sheets import a1_to_grid
    shoots = generate_shoots(START, END, seed=seed)
    layout = build_report(shoots, START, END, "Test")
    taken = set()
    for rng in layout.merges:
        g = a1_to_grid(rng, 0)
        cells = {(r, c) for r in range(g["startRowIndex"], g["endRowIndex"])
                 for c in range(g["startColumnIndex"], g["endColumnIndex"])}
        assert not (cells & taken), rng
        taken |= cells
