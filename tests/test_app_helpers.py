from datetime import date

import pytest

from report_generator import current_quarter, parse_date, previous_month, validate_period


def test_parse_date():
    assert parse_date(" 05.09.2026 ") == date(2026, 9, 5)
    with pytest.raises(ValueError):
        parse_date("2026-09-05")


def test_previous_month_handles_january():
    assert previous_month(date(2026, 1, 15)) == (date(2025, 12, 1), date(2025, 12, 31))
    assert previous_month(date(2026, 10, 22)) == (date(2026, 9, 1), date(2026, 9, 30))


def test_current_quarter():
    assert current_quarter(date(2026, 10, 22)) == (date(2026, 10, 1), date(2026, 10, 22))


def test_validate_period():
    with pytest.raises(ValueError):
        validate_period(date(2026, 9, 30), date(2026, 9, 1))
    with pytest.raises(ValueError):
        validate_period(date(2025, 1, 1), date(2026, 9, 1))
