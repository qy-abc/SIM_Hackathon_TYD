"""Tests for the seven nyp_ tools in tools/.

Every test uses the golden dataset in data/golden/ (month 2026-09) and a
temporary state file so no test pollutes another.  No imports from
closer.data.generator or answer_key.json — the tools are black-boxes that
only see the agent-visible files.

ADK's @tool decorator wraps return values in a ToolResponse; call .content
to get the underlying dict.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------

MONTH = "2026-09"
# data/golden/ ships with the repo and is the fallback data directory.
DATA_ROOT = str(Path(__file__).resolve().parent.parent / "data")


def unwrap(response) -> dict:
    """Extract the plain dict from an ADK ToolResponse (or pass through if
    the tool was called from a plain function not wrapped by the ADK runtime)."""
    return response.content if hasattr(response, "content") else response


@pytest.fixture()
def state_file(tmp_path) -> str:
    """An empty, isolated disputes.json for each test."""
    return str(tmp_path / "disputes.json")


# ---------------------------------------------------------------------------
# nyp_load_month
# ---------------------------------------------------------------------------

from tools.nyp_load_month import nyp_load_month


def test_load_month_returns_summary():
    result = unwrap(nyp_load_month(MONTH, data_root=DATA_ROOT))
    assert result["month"] == MONTH
    assert result["order_count"] > 0
    assert result["pos_count"] > 0
    assert result["roadshow_count"] >= 0
    assert isinstance(result["unmatched_credits"], int)
    assert isinstance(result["skipped_duplicates"], int)


def test_load_month_pos_plus_roadshow_equals_total():
    result = unwrap(nyp_load_month(MONTH, data_root=DATA_ROOT))
    assert result["pos_count"] + result["roadshow_count"] == result["order_count"]


def test_load_month_approved_matches_reduce_unmatched():
    """Passing an accepted single-candidate match must reduce unmatched_credits."""
    from tools.nyp_match_payments import nyp_match_payments

    suggestions = unwrap(nyp_match_payments(MONTH, data_root=DATA_ROOT))["suggestions"]
    singles = [s for s in suggestions if len(s["candidates"]) == 1]
    if not singles:
        pytest.skip("no single-candidate suggestions in golden dataset")

    approved = {str(s["line_no"]): s["candidates"][0] for s in singles}
    result_after = unwrap(nyp_load_month(MONTH, approved_matches=approved,
                                         data_root=DATA_ROOT))
    result_before = unwrap(nyp_load_month(MONTH, data_root=DATA_ROOT))
    assert result_after["unmatched_credits"] < result_before["unmatched_credits"]


# ---------------------------------------------------------------------------
# nyp_match_payments
# ---------------------------------------------------------------------------

from tools.nyp_match_payments import nyp_match_payments


def test_match_payments_returns_suggestions():
    result = unwrap(nyp_match_payments(MONTH, data_root=DATA_ROOT))
    assert "unmatched_count" in result
    assert "suggestions" in result
    assert len(result["suggestions"]) == result["unmatched_count"]


def test_match_payments_suggestion_schema():
    result = unwrap(nyp_match_payments(MONTH, data_root=DATA_ROOT))
    for s in result["suggestions"]:
        assert "line_no" in s
        assert "day" in s
        assert "description" in s
        assert "credit_dollars" in s
        assert "candidates" in s
        assert "reason" in s


def test_match_payments_echoes_approved():
    approved = {"99": "9999"}
    result = unwrap(nyp_match_payments(MONTH, approved_matches=approved,
                                       data_root=DATA_ROOT))
    assert result["approved_so_far"] == {"99": "9999"}


# ---------------------------------------------------------------------------
# Helper: resolve all single-candidate suggestions
# ---------------------------------------------------------------------------

def _approve_all_singles(month: str, data_root: str) -> dict[str, str]:
    """Build a fully-resolved approved_matches dict by iterating suggest_matches."""
    approved: dict[str, str] = {}
    while True:
        suggestions = unwrap(nyp_match_payments(month, approved_matches=approved,
                                                data_root=data_root))["suggestions"]
        new = {str(s["line_no"]): s["candidates"][0]
               for s in suggestions if len(s["candidates"]) == 1
               and str(s["line_no"]) not in approved}
        if not new:
            break
        approved.update(new)
    return approved


# ---------------------------------------------------------------------------
# nyp_compute_commission
# ---------------------------------------------------------------------------

from tools.nyp_compute_commission import nyp_compute_commission


def test_compute_commission_returns_totals(state_file):
    approved = _approve_all_singles(MONTH, DATA_ROOT)
    result = unwrap(nyp_compute_commission(MONTH, approved_matches=approved,
                                           data_root=DATA_ROOT, state_file=state_file))
    assert result["month"] == MONTH
    assert isinstance(result["totals_cents"], dict)
    assert result["line_count"] > 0
    assert all(v > 0 for v in result["totals_cents"].values())


def test_compute_commission_holds_open_dispute(state_file):
    """Passing 1043 as an open dispute must produce a hold for that order."""
    result = unwrap(nyp_compute_commission(MONTH, open_disputes=["1043"],
                                           data_root=DATA_ROOT, state_file=state_file))
    held_ids = {h["order_id"] for h in result["holds"]}
    assert "1043" in held_ids


def test_compute_commission_state_file_dispute_is_held(state_file):
    """A dispute saved via nyp_raise_dispute is auto-held without open_disputes arg."""
    from tools.nyp_raise_dispute import nyp_raise_dispute

    nyp_raise_dispute("1043", "S04", "I closed this jointly.", state_file=state_file)
    result = unwrap(nyp_compute_commission(MONTH, data_root=DATA_ROOT,
                                           state_file=state_file))
    held_ids = {h["order_id"] for h in result["holds"]}
    assert "1043" in held_ids


def test_compute_commission_ruling_split_override(state_file):
    """After a ruling with a new split, the order must not appear in dispute holds."""
    from tools.nyp_raise_dispute import nyp_raise_dispute
    from tools.nyp_record_ruling import nyp_record_ruling

    nyp_raise_dispute("1043", "S04", "Claim joint credit.", state_file=state_file)
    nyp_record_ruling("1043", "S02", "50/50 agreed.", split={"S04": 50, "S05": 50},
                      state_file=state_file)

    result = unwrap(nyp_compute_commission(MONTH, data_root=DATA_ROOT,
                                           state_file=state_file))
    # Ruling closes the dispute: 1043 must not appear as "dispute open".
    held_ids = {h["order_id"] for h in result["holds"] if h["reason"] == "dispute open"}
    assert "1043" not in held_ids


# ---------------------------------------------------------------------------
# nyp_explain_statement
# ---------------------------------------------------------------------------

from tools.nyp_explain_statement import nyp_explain_statement


def test_explain_statement_returns_staff_breakdown(state_file):
    result = unwrap(nyp_explain_statement(MONTH, "S04", data_root=DATA_ROOT,
                                          state_file=state_file))
    assert result["staff_id"] == "S04"
    assert result["name"] == "Ravi"
    assert result["month"] == MONTH
    assert isinstance(result["total_cents"], int)
    assert isinstance(result["lines"], list)
    assert isinstance(result["held_orders"], list)


def test_explain_statement_lines_only_for_staff(state_file):
    result = unwrap(nyp_explain_statement(MONTH, "S06", data_root=DATA_ROOT,
                                          state_file=state_file))
    for line in result["lines"]:
        assert "order_id" in line
        assert "kind" in line
        assert "amount_cents" in line


def test_explain_statement_unknown_staff_raises(state_file):
    with pytest.raises(ValueError, match="Unknown staff_id"):
        nyp_explain_statement(MONTH, "S99", data_root=DATA_ROOT,
                              state_file=state_file)


# ---------------------------------------------------------------------------
# nyp_raise_dispute
# ---------------------------------------------------------------------------

from tools.nyp_raise_dispute import nyp_raise_dispute


def test_raise_dispute_persists(state_file):
    result = unwrap(nyp_raise_dispute("1043", "S04", "I helped close this.",
                                      state_file=state_file))
    assert result["order_id"] == "1043"
    assert result["raised_by"] == "S04"
    assert result["status"] == "open"
    state = json.loads(Path(state_file).read_text())
    assert "1043" in state["disputes"]
    assert state["disputes"]["1043"]["status"] == "open"


def test_raise_dispute_idempotent_timestamp(state_file):
    """Re-raising preserves the original raised_at timestamp."""
    first = unwrap(nyp_raise_dispute("1043", "S04", "first claim",
                                     state_file=state_file))
    second = unwrap(nyp_raise_dispute("1043", "S04", "updated claim",
                                      state_file=state_file))
    assert first["raised_at"] == second["raised_at"]
    assert second["claim"] == "updated claim"


# ---------------------------------------------------------------------------
# nyp_record_ruling
# ---------------------------------------------------------------------------

from tools.nyp_record_ruling import nyp_record_ruling


def test_record_ruling_by_manager(state_file):
    nyp_raise_dispute("1043", "S04", "claim", state_file=state_file)
    result = unwrap(nyp_record_ruling("1043", "S02", "Aisyah led the sale.",
                                      split={"S05": 100}, state_file=state_file))
    assert result["order_id"] == "1043"
    assert result["ruled_by"] == "S02"
    assert result["split"] == {"S05": 100}
    state = json.loads(Path(state_file).read_text())
    assert state["disputes"]["1043"]["status"] == "ruled"


def test_record_ruling_non_manager_raises(state_file):
    nyp_raise_dispute("1043", "S04", "claim", state_file=state_file)
    with pytest.raises(PermissionError, match="not authorised"):
        nyp_record_ruling("1043", "S04", "self-ruling attempt",
                          state_file=state_file)


def test_record_ruling_no_dispute_raises(state_file):
    with pytest.raises(ValueError, match="No dispute on record"):
        nyp_record_ruling("9999", "S02", "ruling on nonexistent order",
                          state_file=state_file)


def test_record_ruling_bad_split_raises(state_file):
    nyp_raise_dispute("1043", "S04", "claim", state_file=state_file)
    with pytest.raises(ValueError, match="sum to 100"):
        nyp_record_ruling("1043", "S02", "bad split",
                          split={"S04": 60, "S05": 60}, state_file=state_file)


# ---------------------------------------------------------------------------
# nyp_prepare_payout
# ---------------------------------------------------------------------------

from tools.nyp_prepare_payout import nyp_prepare_payout


def test_prepare_payout_structure(state_file):
    approved = _approve_all_singles(MONTH, DATA_ROOT)
    result = unwrap(nyp_prepare_payout(MONTH, approved_matches=approved,
                                       data_root=DATA_ROOT, state_file=state_file))
    assert result["month"] == MONTH
    assert isinstance(result["payouts"], list)
    assert isinstance(result["total_cents"], int)
    assert result["total_cents"] > 0
    for row in result["payouts"]:
        assert "staff_id" in row
        assert "name" in row
        assert "amount_cents" in row
        assert "amount_dollars" in row
        assert row["amount_cents"] > 0


def test_prepare_payout_total_matches_sum(state_file):
    result = unwrap(nyp_prepare_payout(MONTH, data_root=DATA_ROOT,
                                       state_file=state_file))
    assert result["total_cents"] == sum(r["amount_cents"] for r in result["payouts"])


def test_prepare_payout_holds_open_dispute(state_file):
    nyp_raise_dispute("1043", "S04", "claim", state_file=state_file)
    result = unwrap(nyp_prepare_payout(MONTH, data_root=DATA_ROOT,
                                       state_file=state_file))
    held_ids = {h["order_id"] for h in result["held_orders"]}
    assert "1043" in held_ids
