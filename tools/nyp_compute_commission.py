"""nyp_compute_commission — run the commission engine for one month.

Thin wrapper around closer.sources.loader.load_period and
closer.rules.commission.compute_month.

Dispute/ruling state is read automatically from data/state/disputes.json:
  - every open dispute is held (same effect as passing its order_id in
    open_disputes), and
  - every ruled dispute whose ruling includes a split override has that split
    applied to the order before the engine runs.
The open_disputes argument acts as an *additional* set of holds on top of
whatever the state file already contains.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from closer.data.staff import load_staff
from closer.rules.commission import compute_month
from closer.rules.plan import load_plans
from closer.sources.loader import load_period
from tools._state import open_dispute_ids, split_override

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
        "Run the commission engine for one month and return per-staff totals, "
        "every commission/bonus/clawback line, and all held orders with their "
        "reasons. Open disputes from data/state/disputes.json are held "
        "automatically; ruling split overrides are applied before calculation. "
        "Resolve unmatched bank credits with nyp_match_payments first."
    ),
)
def nyp_compute_commission(
    month: str,
    approved_matches: Optional[dict[str, str]] = None,
    open_disputes: Optional[list[str]] = None,
    data_root: Optional[str] = None,
    state_file: Optional[str] = None,
) -> dict:
    """Compute commission totals and line detail for *month*.

    Parameters
    ----------
    month:
        ISO year-month, e.g. ``"2026-09"``.
    approved_matches:
        Bank line-number (string) → order_id, from nyp_match_payments.
    open_disputes:
        Extra order IDs to hold in addition to those already open in the
        state file (useful for one-off overrides without writing a dispute).
    data_root:
        Override the default ``data/`` directory (useful in tests).
    state_file:
        Override the default ``data/state/disputes.json`` (useful in tests).

    Returns
    -------
    dict
        month, totals_dollars (staff_id → S$ string),
        totals_cents (staff_id → int),
        lines (list of commission/bonus/clawback detail),
        holds (list of {order_id, reason}),
        line_count, hold_count.
    """
    root = Path(data_root) if data_root else None
    sf = Path(state_file) if state_file else None
    approved: dict[int, str] = (
        {int(k): v for k, v in approved_matches.items()} if approved_matches else {}
    )

    period = load_period(month, _data_dir(month, root), approved)

    # Merge state-file open disputes with any extra ones passed in.
    held = open_dispute_ids(sf) | set(open_disputes or [])

    # Apply ruling split overrides: replace an order's split in-place before
    # passing it to the engine, so the ruled allocation is what gets computed.
    patched_orders = []
    for order in period.orders:
        override = split_override(order.order_id, sf)
        if override is not None:
            order = order.model_copy(update={"split": override})
        patched_orders.append(order)

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
        "lines": [
            {
                "order_id": line.order_id,
                "staff_id": line.staff_id,
                "kind": line.kind,
                "amount_cents": line.amount_cents,
                "base_cents": line.base_cents,
                "note": line.note,
            }
            for line in result.lines
        ],
        "holds": [
            {"order_id": h.order_id, "reason": h.reason}
            for h in result.holds
        ],
        "line_count": len(result.lines),
        "hold_count": len(result.holds),
    }
