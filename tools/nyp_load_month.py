"""nyp_load_month — load one month's agent-visible files into a Period summary.

Thin wrapper around closer.sources.loader.load_period.  No data parsing here;
every other tool in this package calls load_period / suggest_matches the same way.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from closer.sources.loader import load_period

_DATA_ROOT = Path(__file__).resolve().parent.parent / "data"


def _data_dir(month: str, data_root: Path | None = None) -> Path:
    """Resolve the directory holding the agent-visible files for *month*.

    Looks for  data/<month>/  first (e.g. data/2026-09/), then falls back to
    data/golden/ so the golden dataset is always reachable without extra setup.
    """
    root = data_root or _DATA_ROOT
    candidate = root / month
    if candidate.is_dir():
        return candidate
    fallback = root / "golden"
    if fallback.is_dir():
        return fallback
    raise FileNotFoundError(
        f"No data directory found for month '{month}'. "
        f"Tried {candidate} and {fallback}."
    )


@tool(
    name="nyp_load_month",
    description=(
        "Load one month's agent-visible files (POS orders, roadshow sheet, bank "
        "statement, card settlements, crew messages, prior payouts) and return a "
        "summary. Call this first. If unmatched_credits > 0, call "
        "nyp_match_payments next to resolve them before computing commission."
    ),
)
def nyp_load_month(
    month: str,
    approved_matches: Optional[dict[str, str]] = None,
    data_root: Optional[str] = None,
) -> dict:
    """Summarise the loaded Period for *month*.

    Parameters
    ----------
    month:
        ISO year-month, e.g. ``"2026-09"``.
    approved_matches:
        Bank line-number (as string key) → order_id, for credits a reviewer has
        already confirmed via nyp_match_payments.  Omit on the first call.
    data_root:
        Override the default ``data/`` directory (useful in tests).

    Returns
    -------
    dict
        month, order_count, pos_count, roadshow_count, unmatched_credits,
        skipped_duplicates, prior_payout_orders.
    """
    root = Path(data_root) if data_root else None
    approved: dict[int, str] = (
        {int(k): v for k, v in approved_matches.items()} if approved_matches else {}
    )
    period = load_period(month, _data_dir(month, root), approved)
    return {
        "month": period.month,
        "order_count": len(period.orders),
        "pos_count": sum(1 for o in period.orders if o.source == "pos"),
        "roadshow_count": sum(1 for o in period.orders if o.source == "roadshow"),
        "unmatched_credits": len(period.unmatched_lines),
        "skipped_duplicates": len(period.skipped_duplicates),
        "prior_payout_orders": len(period.prior_payouts),
    }
