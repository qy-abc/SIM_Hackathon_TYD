# Closer

A month-end close agent team for a 15-person furniture retailer, built for AI-GNITION 2026.
Messy inputs (POS, roadshow sheet, bank CSV, crew Telegram messages) become typed events;
agents reconcile, compute commission, and route every money-moving action through approval.

This repo is at **foundation stage**: the event contract, the golden month with its answer key,
and the commission rules engine. Agents, bot, ledger and dashboard come next.

## Quick start

```bash
pip install -e ".[dev]"
python -m closer.cli generate   # writes data/golden/ (seeded, reproducible)
python -m closer.cli eval       # scores the rules engine against the answer key
python -m pytest -q
```

`data/golden/` is git-ignored: anyone regenerates the same bytes from the seed.
**`answer_key.json` is for `eval/` only. No agent code may read it.**

## Layout

| Folder | Owner | What lives there | Status |
| --- | --- | --- | --- |
| `closer/data/` | R1 | Golden month generator, answer key, staff, commission plan YAML | v0.1 |
| `closer/sources/` | R1 | Mock POS API, sheet loader, bank CSV drops, crew message replay | empty |
| `closer/events/` | R1 | Event schema, append-only log, ingestion | v0.1 |
| `closer/resolve/` | R1 | Entity resolution | empty |
| `closer/recon/` | R2 | Matcher, fee gross-up | empty |
| `closer/rules/` | R2 | Commission engine, statements, payroll file | engine v0.1 |
| `closer/agent/` | R3 | Supervisor, specialists, memory, audit log | empty |
| `closer/policy/` | R4 | Approval tiers, permissions, idempotency | empty |
| `closer/bot/` | R4 | Telegram bot, approval cards | empty |
| `closer/ledger/` | R4 | Ledger interface, mock ledger, Xero adapter | empty |
| `closer/dashboard/` | R5 | Streamlit screens, replay clock | empty |
| `closer/eval/` | R2 | Golden-month scoring | commission check |
| `pitch/` | R5 | Deck, video script, Bob log | empty |

## Contracts to freeze by Fri 9 Oct

**Events** (`closer/events/schema.py`, v0.1). Every event has `type`, `occurred_at`, `source`, `payload`;
`hash` (SHA-256 of the content) is its identity, so replaying an input never duplicates it.
`REQUIRED_PAYLOAD_KEYS` lists the keys each of the 14 types must carry. The SQLite log rejects
UPDATE and DELETE. `occurred_at` must be timezone-aware and must be **the source record's own time**,
never ingest time. Otherwise a replay hashes differently and gets stored twice.

**Money** is integer cents everywhere. Rates are basis points (300 = 3%).

**Commission** (`closer/rules/commission.py`). The six rules are pure functions with no LLM.
Interpretation choices the team must confirm:

1. An order is earned on the later of `paid_on` (the balance reached zero) and `delivered_on`.
2. The 5% tier is cumulative within the month, taken in earned order, per person.
3. Rounding is half up to the cent, once per order, person and kind of line.
4. An order refunded in the month it was earned earns nothing; no clawback is needed. A refund
   on an earlier month's order with no payout on record is held, never silently skipped.
5. The promo bonus follows the plan in force on the **order date**, not the delivery date.
6. Promo bonuses are split like the order and are not cut by the discount penalty.

## Golden month (seed 20260930)

The generator writes 95 POS orders, a 30-row roadshow sheet and the hidden answer key.
These anomalies are seeded:

- 5 duplicates across POS and the sheet (same phone, retyped name)
- 3 blank salespeople on the sheet
- order 1027: 14% discount with no approver
- orders 1018 and 1061: 60/40 splits
- order 1052: failed delivery
- order 1012: return within 14 days
- order 0987: refund on an order paid out in August
- order 0991: delivered in August, paid off in September, so it earns in September
- order 1043: late 50/50 dispute
- the promo bonus rises from S$50 to S$80 on 15 Sep

**Not generated yet:** the bank statement (~140 lines, 3 drops) and crew messages (~95).
They carry the reconciliation anomalies: partial payments, ambiguous references, batched
card settlements and instalment payouts.

The answer key's commission totals come from `closer/data/reference.py`, a second, separately
written reading of the rules. `tests/test_commission.py` holds hand-worked cases for each rule.
