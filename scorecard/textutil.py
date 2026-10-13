from __future__ import annotations

import datetime as dt
import re
import unicodedata


def normalize(text: object) -> str:
    """Minuscules, sans accents ni espaces superflus: « Unité mobile » -> « unite mobile »."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def parse_number(value: object) -> float | None:
    """Accepte 154877, « 154 877,50 $ », « 77 % » (-> 0.77), « (1 200) » (négatif)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", " ").replace("$", "").replace(" ", "")
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    percent = text.endswith("%")
    text = text.rstrip("%")
    if "," in text and "." in text:
        text = text.replace(",", "") if text.rfind(".") > text.rfind(",") else text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    if percent:
        number /= 100
    return -number if negative else number


def parse_pct(value: object) -> float | None:
    """Pourcentage en fraction: 0.77, « 77 % » ou 77 -> 0.77."""
    number = parse_number(value)
    if number is not None and number > 1.5:
        number /= 100
    return number


_DATE_RE = re.compile(r"(\d{1,4})[/-](\d{1,2})[/-](\d{1,4})")


def parse_date(value: object) -> dt.date | None:
    """Dates Excel, ISO (2025-08-31), MM/JJ/AAAA (format du gabarit) ou JJ/MM/AAAA si non ambigu."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    match = _DATE_RE.search(str(value))
    if not match:
        return None
    a, b, c = (int(x) for x in match.groups())
    try:
        if a > 31:  # AAAA-MM-JJ
            return dt.date(a, b, c)
        if a > 12:  # JJ/MM/AAAA
            return dt.date(c, b, a)
        return dt.date(c, a, b)  # MM/JJ/AAAA
    except ValueError:
        return None
