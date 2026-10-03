import csv
import json

import pytest

from closer.data.generator import build_month, name_variant, write_month
from closer.eval.golden import score_commission


@pytest.fixture(scope="module")
def month_files(tmp_path_factory):
    return write_month(tmp_path_factory.mktemp("golden"))


@pytest.fixture(scope="module")
def key(month_files):
    return json.loads(month_files["answer_key.json"].read_text("utf-8"))


def test_same_seed_same_bytes(tmp_path):
    a = write_month(tmp_path / "a", seed=7)
    b = write_month(tmp_path / "b", seed=7)
    for name in a:
        assert a[name].read_bytes() == b[name].read_bytes()


def test_volumes_match_the_plan():
    month = build_month()
    assert len(month["pos"]) == 95
    assert len(month["roadshow_only"]) + len(month["duplicates"]) == 30


def test_seeded_anomaly_counts(key):
    assert len(key["duplicates"]) == 5
    assert len(key["missing_salesperson"]) == 3
    unapproved = [o for o in key["golden_orders"] if o["discount_pct"] > 10 and not o["discount_approved_by"]]
    assert [o["order_id"] for o in unapproved] == ["1027"]
    assert sum(len(o["split"]) > 1 for o in key["golden_orders"]) == 2


def test_duplicates_share_a_phone_but_not_the_typed_name(month_files, key):
    with month_files["roadshow.csv"].open(encoding="utf-8", newline="") as f:
        rows = {int(r["row"]): r for r in csv.DictReader(f)}
    pos = {o["order_id"]: o for o in json.loads(month_files["pos_orders.json"].read_text("utf-8"))}
    for dup in key["duplicates"]:
        row, order = rows[dup["roadshow_row"]], pos[dup["pos_order_id"]]
        assert row["phone"] == order["phone"]
        assert row["customer_name"] != order["customer_name"]


def test_missing_salespeople_are_blank_on_the_sheet(month_files, key):
    with month_files["roadshow.csv"].open(encoding="utf-8", newline="") as f:
        blank = {int(r["row"]) for r in csv.DictReader(f) if not r["salesperson"]}
    assert blank == {m["roadshow_row"] for m in key["missing_salesperson"]}


def test_answer_key_is_not_in_agent_visible_files(month_files):
    visible = month_files["pos_orders.json"].read_text("utf-8") + month_files["roadshow.csv"].read_text("utf-8")
    for leak in ("delivered_on", "refunded_on", "paid_cents", "true_salesperson"):
        assert leak not in visible


def test_name_variant():
    assert name_variant("Tan Ah Kow") == "Mr Tan A.K."


def test_engine_matches_answer_key(key):
    report = score_commission(key)
    assert report["passed"], report["mismatches"]
