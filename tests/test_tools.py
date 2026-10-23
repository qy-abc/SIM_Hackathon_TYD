"""Tests for the seven nyp_ tools in tools/.

Every test uses the golden dataset in data/golden/ (month 2026-09). The
tools keep no state between calls (Orchestrate runs them on a read-only
filesystem), so dispute state is passed in as open_disputes/split_overrides.  No imports from
closer.data.generator or answer_key.json — the tools are black-boxes that
only see the agent-visible files.

ADK's @tool decorator wraps return values in a ToolResponse; call .content
to get the underlying dict.
"""
from __future__ import annotations

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


@pytest.mark.parametrize("tool", ["load", "match", "compute", "payout"])
def test_a_month_with_no_data_is_refused_not_given_september(tool):
    from tools.nyp_compute_commission import nyp_compute_commission
    from tools.nyp_match_payments import nyp_match_payments
    from tools.nyp_prepare_payout import nyp_prepare_payout
    calls = {
        "load": lambda: nyp_load_month("2027-12", data_root=DATA_ROOT),
        "match": lambda: nyp_match_payments("2027-12", data_root=DATA_ROOT),
        "compute": lambda: nyp_compute_commission("2027-12", data_root=DATA_ROOT),
        "payout": lambda: nyp_prepare_payout("2027-12", data_root=DATA_ROOT),
    }
    with pytest.raises(FileNotFoundError, match="No data for month '2027-12'"):
        calls[tool]()


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
    approved = {"27": "1021"}
    result = unwrap(nyp_match_payments(MONTH, approved_matches=approved,
                                       data_root=DATA_ROOT))
    assert result["approved_so_far"] == {"27": "1021"}
    assert result["confirmed"] == [{"line_no": 27, "description": "SOFA LIM",
                                    "credit_dollars": "968.49", "order_id": "1021",
                                    "salesperson": result["confirmed"][0]["salesperson"]}]


@pytest.mark.parametrize("approved, message", [
    ({"27": "1022"}, "can only be matched to 1021, not 1022"),
    ({"99": "9999"}, "Bank line 99 is not a credit waiting to be matched"),
])
def test_a_wrong_match_is_refused(approved, message):
    for call in (nyp_match_payments, nyp_load_month):
        with pytest.raises(ValueError, match=message):
            call(MONTH, approved_matches=approved, data_root=DATA_ROOT)


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


def test_compute_commission_returns_totals():
    approved = _approve_all_singles(MONTH, DATA_ROOT)
    result = unwrap(nyp_compute_commission(MONTH, approved_matches=approved,
                                           data_root=DATA_ROOT))
    assert result["month"] == MONTH
    assert isinstance(result["totals_cents"], dict)
    assert result["line_count"] > 0
    assert all(v > 0 for v in result["totals_cents"].values())
    assert result["totals_by_name"]["Ravi (S04)"].startswith("S$")
    assert len(result["totals_by_name"]) == len(result["totals_cents"])


def test_compute_commission_holds_open_dispute():
    """Passing 1043 as an open dispute must produce a hold for that order."""
    result = unwrap(nyp_compute_commission(MONTH, open_disputes=["1043"],
                                           data_root=DATA_ROOT))
    held_ids = {h["order_id"] for h in result["holds"]}
    assert "1043" in held_ids


def test_compute_commission_raising_dispute_alone_changes_nothing():
    """Nothing is stored: a raised dispute only holds when passed back in."""
    from tools.nyp_raise_dispute import nyp_raise_dispute

    nyp_raise_dispute("1043", "S04", "I closed this jointly.")
    result = unwrap(nyp_compute_commission(MONTH, data_root=DATA_ROOT))
    held_ids = {h["order_id"] for h in result["holds"]}
    assert "1043" not in held_ids


def test_compute_commission_ruling_split_override():
    from tools.nyp_explain_statement import nyp_explain_statement

    """After a ruling with a new split, the order must not appear in dispute holds."""
    from tools.nyp_record_ruling import nyp_record_ruling

    ruling = unwrap(nyp_record_ruling("1043", "S02", "50/50 agreed.",
                                      split={"S04": 50, "S05": 50},
                                      open_disputes=["1043"]))
    result = unwrap(nyp_compute_commission(
        MONTH, data_root=DATA_ROOT,
        split_overrides={ruling["order_id"]: ruling["split"]}))
    held_ids = {h["order_id"] for h in result["holds"] if h["reason"] == "dispute open"}
    assert "1043" not in held_ids
    assert result["inputs_used"]["split_overrides"] == {"1043": {"S04": 50, "S05": 50}}
    ravi = unwrap(nyp_explain_statement(MONTH, "S04", data_root=DATA_ROOT,
                                        split_overrides={"1043": ruling["split"]}))
    assert "1043" in {l["order_id"] for l in ravi["lines"]}


# ---------------------------------------------------------------------------
# nyp_explain_statement
# ---------------------------------------------------------------------------

from tools.nyp_explain_statement import nyp_explain_statement


def test_explain_statement_returns_staff_breakdown():
    result = unwrap(nyp_explain_statement(MONTH, "S04", data_root=DATA_ROOT))
    assert result["staff_id"] == "S04"
    assert result["name"] == "Ravi"
    assert result["month"] == MONTH
    assert isinstance(result["total_cents"], int)
    assert isinstance(result["lines"], list)
    assert isinstance(result["held_orders"], list)


def test_explain_statement_lines_only_for_staff():
    result = unwrap(nyp_explain_statement(MONTH, "S06", data_root=DATA_ROOT))
    for line in result["lines"]:
        assert "order_id" in line
        assert "kind" in line
        assert "amount_cents" in line


def test_explain_statement_unknown_staff_raises():
    with pytest.raises(ValueError, match="Unknown staff_id"):
        nyp_explain_statement(MONTH, "S99", data_root=DATA_ROOT)


def test_statement_says_plainly_who_is_not_on_commission():
    crew = unwrap(nyp_explain_statement(MONTH, "S10", data_root=DATA_ROOT))
    assert crew["commission_eligible"] is False
    assert crew["note"] == "Ah Seng (delivery crew) is not on commission, so the statement is S$0.00."
    ravi = unwrap(nyp_explain_statement(MONTH, "S04", data_root=DATA_ROOT))
    assert ravi["note"] == "Ravi (salesperson) is on commission."


# ---------------------------------------------------------------------------
# nyp_raise_dispute
# ---------------------------------------------------------------------------

from tools.nyp_raise_dispute import nyp_raise_dispute


def test_raise_dispute_returns_open_record():
    result = unwrap(nyp_raise_dispute("1043", "S04", "I helped close this."))
    assert result["order_id"] == "1043"
    assert result["raised_by"] == "S04"
    assert result["status"] == "open"
    assert "open_disputes" in result["next_step"]


def test_raise_dispute_refuses_order_not_named_in_claim():
    """SJ's 22 Oct run: the claim said 1043 but the model passed 1044."""
    with pytest.raises(ValueError, match="names order 1043"):
        nyp_raise_dispute("1044", "S04", "I helped close order 1043, split 50/50 for 2026.")
    result = unwrap(nyp_raise_dispute("1043", "S04", "I helped close order 1043 in 2026."))
    assert result["order_id"] == "1043"


def test_raise_dispute_returns_totals_with_order_held():
    """SJ's 23 Oct run: the agent said totals were unchanged after a dispute."""
    approved = _approve_all_singles(MONTH, DATA_ROOT)
    # SJ's second run: the model left out month, so it must default.
    result = unwrap(nyp_raise_dispute("1043", "S04", "I helped close order 1043.",
                                      approved_matches=approved, data_root=DATA_ROOT))
    expected = unwrap(nyp_compute_commission(MONTH, approved_matches=approved,
                                             open_disputes=["1043"], data_root=DATA_ROOT))
    assert result["open_disputes"] == ["1043"]
    assert result["totals_by_name"] == expected["totals_by_name"]
    assert result["hold_count"] == expected["hold_count"]


def test_raise_dispute_opens_before_matches_confirmed():
    result = unwrap(nyp_raise_dispute("1043", "S04", "I helped close order 1043.",
                                      data_root=DATA_ROOT))
    assert result["status"] == "open"
    assert "totals_by_name" not in result


def test_raise_dispute_unknown_staff_raises():
    with pytest.raises(ValueError, match="Unknown staff_id"):
        nyp_raise_dispute("1043", "S99", "claim")


# ---------------------------------------------------------------------------
# nyp_record_ruling
# ---------------------------------------------------------------------------

from tools.nyp_record_ruling import nyp_record_ruling


def test_record_ruling_by_manager():
    result = unwrap(nyp_record_ruling("1043", "S02", "Aisyah led the sale.",
                                      split={"S05": 100}, open_disputes=["1043"]))
    assert result["order_id"] == "1043"
    assert result["ruled_by"] == "S02"
    assert result["split"] == {"S05": 100}
    assert result["status"] == "ruled"


def test_record_ruling_non_manager_raises():
    with pytest.raises(PermissionError, match="not authorised"):
        nyp_record_ruling("1043", "S04", "self-ruling attempt",
                          open_disputes=["1043"])


def test_record_ruling_no_dispute_raises():
    with pytest.raises(ValueError, match="No open dispute"):
        nyp_record_ruling("9999", "S02", "ruling on nonexistent order",
                          open_disputes=["1043"])


def test_record_ruling_bad_split_raises():
    with pytest.raises(ValueError, match="sum to 100"):
        nyp_record_ruling("1043", "S02", "bad split",
                          split={"S04": 60, "S05": 60}, open_disputes=["1043"])


@pytest.mark.parametrize("split", [
    {"S04": 0.5, "S05": 0.5},
    {"S04": 50.0, "S05": 50.0},
])
def test_record_ruling_accepts_fractions_and_floats(split):
    result = unwrap(nyp_record_ruling("1043", "S02", "50/50",
                                      split=split, open_disputes=["1043"]))
    assert result["split"] == {"S04": 50, "S05": 50}


def test_record_ruling_rejects_part_percent():
    with pytest.raises(ValueError, match="whole percentages"):
        nyp_record_ruling("1043", "S02", "bad split",
                          split={"S04": 50.5, "S05": 49.5}, open_disputes=["1043"])


def test_compute_accepts_fractional_override():
    approved = _approve_all_singles(MONTH, DATA_ROOT)
    whole = unwrap(nyp_compute_commission(MONTH, approved_matches=approved, data_root=DATA_ROOT,
                                          split_overrides={"1043": {"S04": 50, "S05": 50}}))
    frac = unwrap(nyp_compute_commission(MONTH, approved_matches=approved, data_root=DATA_ROOT,
                                         split_overrides={"1043": {"S04": 0.5, "S05": 0.5}}))
    assert frac["totals_dollars"] == whole["totals_dollars"]


def test_record_ruling_unknown_staff_in_split_raises():
    with pytest.raises(ValueError, match="Unknown staff_id"):
        nyp_record_ruling("1043", "S02", "bad split",
                          split={"S04": 50, "S99": 50}, open_disputes=["1043"])


# ---------------------------------------------------------------------------
# nyp_prepare_payout
# ---------------------------------------------------------------------------

from tools.nyp_prepare_payout import nyp_prepare_payout


def test_prepare_payout_structure():
    approved = _approve_all_singles(MONTH, DATA_ROOT)
    result = unwrap(nyp_prepare_payout(MONTH, approved_matches=approved,
                                       data_root=DATA_ROOT))
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


def test_prepare_payout_total_matches_sum():
    approved = _approve_all_singles(MONTH, DATA_ROOT)
    result = unwrap(nyp_prepare_payout(MONTH, approved_matches=approved, data_root=DATA_ROOT))
    assert result["total_cents"] == sum(r["amount_cents"] for r in result["payouts"])


def test_prepare_payout_holds_open_dispute():
    approved = _approve_all_singles(MONTH, DATA_ROOT)
    result = unwrap(nyp_prepare_payout(MONTH, approved_matches=approved, data_root=DATA_ROOT,
                                       open_disputes=["1043"]))
    held_ids = {h["order_id"] for h in result["held_orders"]}
    assert "1043" in held_ids


from tools.nyp_approve_payout import nyp_approve_payout


def _ruled_state():
    return {"approved_matches": _approve_all_singles(MONTH, DATA_ROOT),
            "split_overrides": {"1043": {"S04": 50, "S05": 50}}, "data_root": DATA_ROOT}


def _login(email):
    return {"request_context": {"wxo_email_id": email, "wxo_user_name": email.split("@")[0]}}


@pytest.fixture(autouse=True)
def _logins_file(tmp_path, monkeypatch):
    """Link test logins to staff IDs; config/logins.json is never committed."""
    import json
    import tools._identity as identity
    path = tmp_path / "logins.json"
    path.write_text(json.dumps({"Owner@Example.com": "S01", "manager@example.com": "S02",
                                "ravi@example.com": "S04"}))
    monkeypatch.setattr(identity, "LOGINS_FILE", path)


def test_payout_lists_clawbacks():
    result = unwrap(nyp_prepare_payout(MONTH, **_ruled_state()))
    assert result["total_dollars"] == "8,072.33"
    assert result["clawbacks"] == [{"order_id": "0987", "staff_id": "S06", "name": "Wei Ming",
                                    "amount_dollars": "-151.78", "amount_cents": -15178}]
    assert result["clawback_total_dollars"] == "151.78"


def test_owner_approves_payout():
    result = unwrap(nyp_approve_payout(MONTH, "S01", "approve", **_ruled_state(),
                                       context=_login("owner@example.com")))
    assert result["decision"] == "approved"
    assert result["decided_by"] == "S01"
    assert result["login"] == "owner@example.com"
    assert result["total_dollars"] == "8,072.33"
    assert "S01 approved the 2026-09 payout of S$8,072.33" in result["log_entry"]
    assert result["log_entry"].endswith("(login owner@example.com)")


def test_owner_rejects_payout():
    result = unwrap(nyp_approve_payout(MONTH, "S01", "Reject", "Check Ravi first.",
                                       **_ruled_state(), context=_login("owner@example.com")))
    assert result["decision"] == "rejected"
    assert "stays a draft" in result["status"]
    assert "Check Ravi first." in result["log_entry"]


@pytest.mark.parametrize("login", ["manager@example.com", "ravi@example.com"])
def test_only_owner_login_can_decide_payout(login):
    linked = {"manager@example.com": "S02", "ravi@example.com": "S04"}[login]
    with pytest.raises(PermissionError, match="Only the owner may: Mr Lim \\(S01\\)"):
        nyp_approve_payout(MONTH, linked, "approve", **_ruled_state(), context=_login(login))


def test_typing_owner_id_does_not_beat_the_login():
    with pytest.raises(PermissionError, match="this login is linked to 'S02'"):
        nyp_approve_payout(MONTH, "S01", "approve", **_ruled_state(),
                           context=_login("manager@example.com"))


@pytest.mark.parametrize("context", [None, {"request_context": {}},
                                     {"request_context": {"wxo_email_id": "stranger@example.com"}}])
def test_unlinked_or_missing_login_cannot_decide(context):
    with pytest.raises(PermissionError, match="not linked"):
        nyp_approve_payout(MONTH, "S01", "approve", **_ruled_state(), context=context)


def _demo_fallback_on(monkeypatch, tmp_path):
    import json
    import tools._identity as identity
    path = tmp_path / "logins_demo.json"
    path.write_text(json.dumps({"owner@example.com": "S01", "manager@example.com": "S02",
                                "demo_fallback": True}))
    monkeypatch.setattr(identity, "LOGINS_FILE", path)


@pytest.mark.parametrize("context", [None, {"request_context": {"wxo_email_id": "judge@example.com"}}])
def test_demo_fallback_lets_unlinked_login_use_typed_owner_id(context, monkeypatch, tmp_path):
    _demo_fallback_on(monkeypatch, tmp_path)
    result = unwrap(nyp_approve_payout(MONTH, "S01", "approve", **_ruled_state(), context=context))
    assert result["decision"] == "approved"
    assert result["decided_by"] == "S01"
    assert "demo mode: typed ID" in result["log_entry"]


def test_demo_fallback_still_needs_the_owner_id(monkeypatch, tmp_path):
    _demo_fallback_on(monkeypatch, tmp_path)
    with pytest.raises(PermissionError, match="Only the owner may"):
        nyp_approve_payout(MONTH, "S02", "approve", **_ruled_state(),
                           context=_login("judge@example.com"))


def test_demo_fallback_does_not_free_a_linked_login(monkeypatch, tmp_path):
    _demo_fallback_on(monkeypatch, tmp_path)
    with pytest.raises(PermissionError, match="this login is linked to 'S02'"):
        nyp_approve_payout(MONTH, "S01", "approve", **_ruled_state(),
                           context=_login("manager@example.com"))


def test_payout_decision_must_be_approve_or_reject():
    with pytest.raises(ValueError, match="approve' or 'reject"):
        nyp_approve_payout(MONTH, "S01", "maybe", **_ruled_state(),
                           context=_login("owner@example.com"))


def test_override_on_wrong_order_is_refused():
    """Draft Preview once carried Jason's 1043 ruling over as 1044 (Priya's order)."""
    state = _ruled_state()
    state["split_overrides"] = {"1044": {"S04": 50, "S05": 50}}
    with pytest.raises(ValueError, match="order '1044'.*recorded salespeople are \\['S07'\\]"):
        nyp_compute_commission(MONTH, **state)


def test_ruled_order_still_open_is_refused():
    """Draft Preview once kept 1043 in open_disputes after the ruling, so it stayed held."""
    with pytest.raises(ValueError, match=r"\['1043'\] are in both open_disputes and split_overrides"):
        nyp_compute_commission(MONTH, **_ruled_state(), open_disputes=["1043"])


def test_dropped_approval_is_flagged():
    """Draft Preview once dropped line 142 from approved_matches mid-chat."""
    approved = _approve_all_singles(MONTH, DATA_ROOT)
    full = unwrap(nyp_compute_commission(MONTH, approved_matches=approved, data_root=DATA_ROOT))
    assert full["unconfirmed_bank_lines"] == []
    assert "warning" not in full

    partial = {k: v for k, v in approved.items() if k != "142"}
    result = unwrap(nyp_compute_commission(MONTH, approved_matches=partial, data_root=DATA_ROOT))
    assert result["unconfirmed_bank_lines"] == [142]
    assert "142" in result["warning"]
    with pytest.raises(ValueError, match=r"\[142\]"):
        nyp_prepare_payout(MONTH, approved_matches=partial, data_root=DATA_ROOT)


# ---------------------------------------------------------------------------
# Demo flow and deployability
# ---------------------------------------------------------------------------


def test_demo_flow_1043_totals():
    """Held while disputed, then split 50/50 after Jason's ruling."""
    approved = _approve_all_singles(MONTH, DATA_ROOT)
    held = unwrap(nyp_compute_commission(MONTH, approved_matches=approved,
                                         open_disputes=["1043"], data_root=DATA_ROOT))
    assert held["totals_cents"]["S05"] == 168220
    assert held["totals_cents"]["S04"] == 164379

    ruling = unwrap(nyp_record_ruling("1043", "S02", "Shared close.",
                                      split={"S04": 50, "S05": 50},
                                      open_disputes=["1043"]))
    ruled = unwrap(nyp_compute_commission(
        MONTH, approved_matches=approved, data_root=DATA_ROOT,
        split_overrides={"1043": ruling["split"]}))
    assert ruled["totals_cents"]["S04"] == 172381
    assert ruled["totals_cents"]["S05"] == 176222


def test_tools_never_write_files():
    """Orchestrate runs tools on a read-only filesystem."""
    tools_dir = Path(__file__).resolve().parent.parent / "tools"
    for path in tools_dir.glob("*.py"):
        source = path.read_text("utf-8")
        for banned in ("write_text", "write_bytes", "mkdir", "open("):
            assert banned not in source, f"{path.name} uses {banned}"


def test_whoami_echoes_login_context():
    from tools.nyp_whoami import nyp_whoami
    result = unwrap(nyp_whoami(context={"request_context": {
        "wxo_email_id": "owner@example.com", "wxo_user_name": "owner", "wxo_thread_id": "t1"}}))
    assert result == {"wxo_email_id": "owner@example.com", "wxo_user_name": "owner",
                      "context_keys": ["wxo_email_id", "wxo_thread_id", "wxo_user_name"]}


# ---------------------------------------------------------------------------
# nyp_match_options and the nyp_confirm_matches form
# ---------------------------------------------------------------------------

from tools.nyp_match_options import nyp_match_options


def test_match_options_lists_each_waiting_line_with_its_candidate():
    options = unwrap(nyp_match_options(MONTH, data_root=DATA_ROOT))["options"]
    waiting = unwrap(nyp_match_payments(MONTH, data_root=DATA_ROOT))["suggestions"]
    assert {(o["line_no"], o["order_id"]) for o in options} == {
        (str(s["line_no"]), c) for s in waiting for c in s["candidates"]}
    assert options[0]["label"].startswith(f"Line {options[0]['line_no']} · ")


def test_match_options_drops_lines_already_confirmed():
    options = unwrap(nyp_match_options(MONTH, approved_matches={"27": "1021"},
                                       data_root=DATA_ROOT))["options"]
    assert "27" not in {o["line_no"] for o in options}


def test_confirm_matches_flow_compiles(monkeypatch):
    import importlib
    import sys
    # Building a Flow normally opens an Orchestrate client; compiling needs none.
    importlib.import_module("ibm_watsonx_orchestrate.flow_builder.flows")
    flow_module = sys.modules["ibm_watsonx_orchestrate.flow_builder.flows.flow"]
    monkeypatch.setattr(flow_module, "instantiate_client", lambda *a, **k: None)
    module = importlib.import_module("flows.nyp_confirm_matches")
    spec = module.build_nyp_confirm_matches().compile().flow.to_json()
    field = spec["nodes"]["ask_admin"]["nodes"]["confirm"]["spec"]["form"]["fields"][0]
    assert field["input_map"]["spec"]["maps"][0]["value_expression"] == "flow.get_options.output.options"
    assert spec["output_map"]["spec"]["maps"][0]["target_variable"] == "flow.output.selected"
