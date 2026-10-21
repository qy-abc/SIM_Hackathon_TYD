"""nyp_compute_commission — run the commission engine for one month.

Thin wrapper around closer.sources.loader.load_period and
closer.rules.commission.compute_month.

Dispute state comes in through the arguments (see tools/_state.py):
  - every order_id in open_disputes is held, and
  - every split in split_overrides replaces that order's split before the
    engine runs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from closer.data.staff import load_staff
from closer.rules.commission import compute_month
from closer.rules.plan import load_plans
from closer.sources.loader import load_period
from tools._state import apply_split_overrides, inputs_used as _inputs_used, unconfirmed_warning

_DATA_ROOT = Path(__file__).resolve().parent.parent / "data"


def _data_dir(month: str, data_root: Path | None = None) -> Path:
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
    name="nyp_compute_commission",
    description=(
        "Run the commission engine for one month and return per-staff totals "
        "and all held orders with their "
        "reasons. Orders in open_disputes are held; splits in split_overrides "
        "(from nyp_record_ruling) are applied before calculation. "
        "Resolve unmatched bank credits with nyp_match_payments first."
    ),
)
def nyp_compute_commission(
    month: str,
    approved_matches: Optional[dict[str, str]] = None,
    open_disputes: Optional[list[str]] = None,
    split_overrides: Optional[dict[str, dict[str, float]]] = None,
    data_root: Optional[str] = None,
) -> dict:
    """Compute commission totals and line detail for *month*.

    Parameters
    ----------
    month:
        ISO year-month, e.g. ``"2026-09"``.
    approved_matches:
        Bank line-number (string) → order_id, from nyp_match_payments.
    open_disputes:
        Order IDs with an open dispute (from nyp_raise_dispute); held.
    split_overrides:
        order_id -> split from nyp_record_ruling, e.g.
        ``{"1043": {"S04": 50, "S05": 50}}``.
    data_root:
        Override the default ``data/`` directory (useful in tests).

    Returns
    -------
    dict
        month, totals_dollars (staff_id → S$ string),
        totals_cents (staff_id → int),
        holds (list of {order_id, reason}),
        line_count, hold_count, inputs_used, unconfirmed_bank_lines and,
        when any bank line is unconfirmed, a warning.
    """
    root = Path(data_root) if data_root else None
    approved: dict[int, str] = (
        {int(k): v for k, v in approved_matches.items()} if approved_matches else {}
    )

    period = load_period(month, _data_dir(month, root), approved)

    held = set(open_disputes or [])

    patched_orders = apply_split_overrides(period.orders, split_overrides, open_disputes)

    result = compute_month(
        patched_orders,
        load_plans(),
        load_staff(),
        month,
        period.prior_payouts,
        open_disputes=held if held else None,
    )
    totals = {k: v for k, v in result.totals().items() if v}
    return {
        "month": result.month,
        "totals_dollars": {sid: f"{cents / 100:,.2f}" for sid, cents in totals.items()},
        "totals_cents": totals,
        "holds": [
            {"order_id": h.order_id, "reason": h.reason}
            for h in result.holds
        ],
        "line_count": len(result.lines),
        "hold_count": len(result.holds),
        "inputs_used": _inputs_used(approved_matches, open_disputes, split_overrides),
        **unconfirmed_warning(period),
    }
