import datetime as dt

from scorecard.textutil import normalize, parse_date, parse_number, parse_pct


def test_parse_number_formats():
    assert parse_number("154 877,50 $") == 154877.5
    assert parse_number("1,234.5") == 1234.5
    assert parse_number("(1 200)") == -1200
    assert parse_number("") is None


def test_parse_pct():
    assert parse_pct("77") == 0.77
    assert parse_pct("77 %") == 0.77
    assert parse_pct(0.21) == 0.21


def test_parse_date():
    assert parse_date("08/31/2025") == dt.date(2025, 8, 31)
    assert parse_date("31/08/2025") == dt.date(2025, 8, 31)
    assert parse_date("2025-08-31") == dt.date(2025, 8, 31)
    assert parse_date("Fait 08/01/2025") == dt.date(2025, 8, 1)


def test_normalize():
    assert normalize("  Unité   mobile ") == "unite mobile"
