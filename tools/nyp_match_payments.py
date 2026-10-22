"""nyp_match_payments — surface unmatched bank credits and record approvals.

Thin wrapper around closer.sources.loader.load_period + suggest_matches.
No data parsing; the loader does all the work.
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
    name="nyp_match_payments",
    description=(
        "List every unreferenced bank credit that the loader could not link to an "
        "order automatically, together with candidate orders it might pay. "
        "A human confirms which candidate is correct; pass those confirmations back "
        "as approved_matches and call this tool again until unmatched_count is 0. "
        "Then pass the same approved_matches to nyp_compute_commission."
    ),
)
def nyp_match_payments(
    month: str,
    approved_matches: Optional[dict[str, str]] = None,
    data_root: Optional[str] = None,
) -> dict:
    """Return unresolved bank credits and their candidate order matches.

    Parameters
    ----------
    month:
        ISO year-month, e.g. ``"2026-09"``.
    approved_matches:
        Bank line-number (string) → order_id for credits already confirmed.
        Build this up across successive calls until ``unmatched_count`` reaches 0.
    data_root:
        Override the default ``data/`` directory (useful in tests).

    Returns
    -------
    dict
        unmatched_count: int
        suggestions: list of dicts, each with
            line_no, day, description, credit_dollars, candidates, reason.
        approved_so_far: the approved_matches dict echoed back.
        confirmed: each confirmed line with its amount, order and salesperson,
            so the person can check what they confirmed.
    """
    root = Path(data_root) if data_root else None
    approved: dict[int, str] = (
        {int(k): v for k, v in approved_matches.items()} if approved_matches else {}
    )
    data_dir = _data_dir(month, root)
    check_matches(month, data_dir, approved)
    period = load_period(month, data_dir, approved)
    suggestions = suggest_matches(period)
    orders = {o.order_id: o for o in period.orders}
    return {
        "unmatched_count": len(suggestions),
        "suggestions": [
            {
                "line_no": s.line_no,
                "day": s.day.isoformat(),
                "description": s.description,
                "credit_dollars": f"{s.credit_cents / 100:,.2f}",
                "candidates": list(s.candidates),
                "reason": s.reason,
            }
            for s in suggestions
        ],
        "approved_so_far": {str(k): v for k, v in approved.items()},
        "confirmed": [
            {
                "line_no": line.line_no,
                "description": line.description or "(blank)",
                "credit_dollars": f"{line.credit_cents / 100:,.2f}",
                "order_id": approved[line.line_no],
                "salesperson": orders[approved[line.line_no]].salesperson,
            }
            for line in period.bank_lines
            if line.line_no in approved
        ],
    }
