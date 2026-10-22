"""nyp_match_options — every candidate (bank line, order) pair as a pickable row.

Feeds the nyp_confirm_matches form, so a person ticks matches instead of typing
them. Same rules as nyp_match_payments; nothing is applied here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from tools._state import check_matches
from closer.sources.loader import load_period, suggest_matches

_DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
_GOLDEN_MONTH = "2026-09"  # data/golden/ holds this month only


def _data_dir(month: str, data_root: Path | None = None) -> Path:
    root = data_root or _DATA_ROOT
    candidate = root / month
    if candidate.is_dir():
        return candidate
    fallback = root / "golden"
    if month == _GOLDEN_MONTH and fallback.is_dir():
        return fallback
    raise FileNotFoundError(
        f"No data for month '{month}'. Closer only has data for {_GOLDEN_MONTH}."
    )


@tool(
    name="nyp_match_options",
    description=(
        "List every bank credit still waiting to be matched, one row per candidate "
        "order, for the nyp_confirm_matches form. Used by that form; the agent does "
        "not need to call it."
    ),
)
def nyp_match_options(
    month: str,
    approved_matches: Optional[dict[str, str]] = None,
    data_root: Optional[str] = None,
) -> dict:
    """Return {"options": [...]}, one row per (bank line, candidate order).

    Each row: line_no, description, amount, order_id, salesperson, label.
    A line with no candidate is left out; it still has to be typed.
    """
    root = Path(data_root) if data_root else None
    approved: dict[int, str] = (
        {int(k): v for k, v in approved_matches.items()} if approved_matches else {}
    )
    data_dir = _data_dir(month, root)
    check_matches(month, data_dir, approved)
    period = load_period(month, data_dir, approved)
    orders = {o.order_id: o for o in period.orders}
    options = []
    for s in suggest_matches(period):
        for order_id in s.candidates:
            amount = f"S${s.credit_cents / 100:,.2f}"
            options.append({
                "line_no": str(s.line_no),
                "description": s.description,
                "amount": amount,
                "order_id": order_id,
                "salesperson": orders[order_id].salesperson or "",
                "label": f"Line {s.line_no} · {s.description} · {amount} → order {order_id}",
            })
    return {"options": options}
