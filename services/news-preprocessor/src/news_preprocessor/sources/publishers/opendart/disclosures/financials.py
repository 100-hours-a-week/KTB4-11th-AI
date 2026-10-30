import re
from decimal import Decimal, InvalidOperation
from typing import Any

# OpenDART report codes for the periodic reports we collect.
REPORT_CODES = {"q1": "11013", "half": "11012", "q3": "11014", "annual": "11011"}
SCOPES = ("CFS", "OFS")
_NIL = {"", "-", "–"}


def normalize_amount(source: str) -> tuple[Decimal | None, bool]:
    """Return the amount and whether it was understood.

    An absent amount is None and understood: DART writes an empty field or a dash for nil,
    and a missing value is never zero. Anything else that will not parse keeps its source
    string and reports that it was not understood.
    """
    value = source.strip()
    if value in _NIL:
        return None, True
    negative = value.startswith("(") and value.endswith(")")
    digits = re.sub(r"[,\s]", "", value.strip("()") if negative else value)
    try:
        amount = Decimal(digits)
    except InvalidOperation:
        return None, False
    return -amount if negative else amount, True


def financial_rows(payload: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    """One row per account in a single statement response, current term only.

    Prior-term columns belong to the reports that filed them, so folding them in here
    would merge values across periods.
    """
    rows = []
    for position, entry in enumerate(payload):
        statement_type = (entry.get("sj_div") or "").strip()
        if not statement_type:
            continue
        source_value = entry.get("thstrm_amount") or ""
        value, normalized = normalize_amount(source_value)
        ordinal = entry.get("ord") or ""
        rows.append(
            {
                "scope": scope,
                "statement_type": statement_type,
                "account_id": (entry.get("account_id") or "").strip(),
                "account_name": (entry.get("account_nm") or "").strip(),
                "currency": (entry.get("currency") or "").strip(),
                "ordinal": int(ordinal) if ordinal.strip().isdigit() else position,
                "source_value": source_value,
                "value": value,
                "normalized": normalized,
            }
        )
    return rows
