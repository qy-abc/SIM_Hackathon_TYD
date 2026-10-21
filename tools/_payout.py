"""Payout draft shared by nyp_prepare_payout and nyp_approve_payout. No file I/O."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

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


def build_payout(
    month: str,
    approved_matches: Optional[dict[str, str]] = None,
    open_disputes: Optional[list[str]] = None,
    split_overrides: Optional[dict[str, dict[str, float]]] = None,
    data_root: Optional[str] = None,
) -> dict:
    """The payout draft as a plain dict, shared with nyp_approve_payout."""
    root = Path(data_root) if data_root else None
    approved: dict[int, str] = (
        {int(k): v for k, v in approved_matches.items()} if approved_matches else {}
    )

    period = load_period(month, _data_dir(month, root), approved)
    unconfirmed = unconfirmed_warning(period)
    if unconfirmed.get("warning"):
        raise ValueError(unconfirmed["warning"])
    held = set(open_disputes or [])

    patched_orders = apply_split_overrides(period.orders, split_overrides, open_disputes)

    all_staff = load_staff()
    result = compute_month(
        patched_orders,
        load_plans(),
        all_staff,
        month,
        period.prior_payouts,
        open_disputes=held if held else None,
    )

    name_by_id = {s.staff_id: s.name for s in all_staff}
    totals = {k: v for k, v in result.totals().items() if v}

    payouts = [
        {
            "staff_id": sid,
            "name": name_by_id.get(sid, sid),
            "amount_dollars": f"{cents / 100:,.2f}",
            "amount_cents": cents,
        }
        for sid, cents in sorted(totals.items())
    ]
    grand_total = sum(totals.values())
    clawbacks = [
        {"order_id": line.order_id, "staff_id": line.staff_id,
         "amount_dollars": f"{line.amount_cents / 100:,.2f}", "amount_cents": line.amount_cents}
        for line in result.lines if line.kind == "clawback"
    ]

    return {
        "month": month,
        "status": "draft: nothing has been paid",
        "payouts": payouts,
        "total_dollars": f"{grand_total / 100:,.2f}",
        "total_cents": grand_total,
        "clawbacks": clawbacks,
        "clawback_total_dollars": f"{-sum(c['amount_cents'] for c in clawbacks) / 100:,.2f}",
        "hold_count": len(result.holds),
        "held_orders": [
            {"order_id": h.order_id, "reason": h.reason}
            for h in result.holds
        ],
        "inputs_used": _inputs_used(approved_matches, open_disputes, split_overrides),
    }
