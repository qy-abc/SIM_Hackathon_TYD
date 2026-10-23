# Closer

**Team N(Y)P · AI-GNITION 2026 · Nexius Labs Problem Statement 2, "One Less Thing"**

Closer is an AI agent on IBM watsonx Orchestrate that runs a small shop's month-end sales
commission close, from raw sales and bank data to an owner-approved payout draft. Python tools
do the maths; people make the calls:

- the admin confirms bank credits Closer can't match, by ticking them in a form;
- only the sales manager can rule on a commission dispute;
- only the owner can approve the payout, and every approval is logged with who, what and when;
- Closer never moves money. It drafts the payout; a person pays it.

The demo shop is a fictional Singapore furniture showroom, Lim & Sons Living, with one
synthetic month of data (September 2026). No real customer or personal data is used.

**Try it:** the public demo page is in `docs/index.html`, served by GitHub Pages at
https://qy-abc.github.io/SIM_Hackathon_TYD/. It embeds the live agent and lists the lines to
paste, in order, with the expected result for each.

## How it works

One agent, `nyp_closer` (`agents/nyp_closer.agent.yaml`), runs on watsonx Orchestrate with the
model gpt-oss-120b. Its tools are Python files in `tools/`:

| Step | Tool | What it does |
| --- | --- | --- |
| Load | `nyp_load_month` | Reads POS orders, the roadshow sheet, the bank statement, card settlements and team chat; skips duplicates |
| Match | `nyp_match_payments`, `nyp_match_options` | Matches bank credits to orders and lists candidates for the ones it can't match |
| Confirm | `nyp_confirm_matches` (`flows/`) | A tick-box form where the admin confirms those matches |
| Calculate | `nyp_compute_commission`, `nyp_explain_statement` | Commission per person on the price before GST, holds with reasons, line-by-line statements |
| Disputes | `nyp_raise_dispute`, `nyp_record_ruling` | Opens a case and holds the order; only a sales manager can rule |
| Payout | `nyp_prepare_payout`, `nyp_approve_payout` | Drafts the payout with clawbacks; only the owner can approve |
| Identity | `nyp_whoami` | Shows the signed-in user's email and name, as Orchestrate supplies them |

The commission rules (`closer/rules/`) are plain Python with no language model, so the same month
always gives the same numbers. Orchestrate runs tools on a read-only filesystem, so the close state
(approved matches, open disputes, rulings) travels in the chat, and every tool re-checks it.

**Who may do what** is checked inside the tools, not the prompt. Payout approval checks the
caller's Orchestrate login against `config/logins.json`. Disputes and rulings use the staff ID the
person types. On the public page nobody is signed in, so it runs in demo mode: approval accepts
the typed staff ID, and the log marks it as demo mode.

## Run it locally

Needs Python 3.11 or later.

```bash
pip install -e ".[dev]" ibm-watsonx-orchestrate
python -m closer.cli generate   # writes data/golden/ from a fixed seed (same files every time)
python -m closer.cli eval       # checks the rules engine against the answer key
python -m pytest -q             # 124 tests
```

`data/golden/` is git-ignored because anyone can regenerate the same files from the seed.
`answer_key.json` is only for `eval`; no agent or tool code reads it, and the deploy script
leaves it out of the package.

## Deploy to watsonx Orchestrate

1. Activate your Orchestrate environment: `orchestrate env activate <your-env>`.
2. Optional: copy `config/logins.example.json` to `config/logins.json` and map your Orchestrate
   login to a staff ID (S01 is the owner). Set `"demo_fallback": true` to let unlinked logins
   approve with a typed ID, as on the public page. `logins.json` is git-ignored and must never be
   committed, because it holds real emails.
3. Build and import the tools, the form and the agent: `python scripts/deploy.py --run`
   (without `--run` it only builds and checks the package).
4. Test in Draft Preview, then deploy to Live: `orchestrate agents deploy --name nyp_closer`.

## Cast

| ID | Name | Role |
| --- | --- | --- |
| S01 | Mr Lim | Owner: approves or rejects the payout |
| S02 | Jason | Sales manager: rules on disputes |
| S03 | Mei | Admin: confirms unclear bank matches |
| S04–S09 | Ravi, Aisyah, Wei Ming, Priya, Darren, Siti | Salespeople |
| S10, S11 | Ah Seng, Kumar | Delivery crew, not on commission |

## Repository layout

| Folder | What lives there |
| --- | --- |
| `agents/` | `nyp_closer` (the agent we use) and four earlier multi-agent files, kept for reference |
| `tools/` | The `nyp_` Orchestrate tools and their shared helpers |
| `flows/` | The tick-box form for confirming bank matches |
| `closer/` | Data generator, loaders, commission rules engine, evaluation |
| `config/` | `logins.example.json`; your own `logins.json` stays local |
| `docs/` | The public demo page |
| `scripts/` | `deploy.py` |
| `tests/` | Unit tests for the rules and the tools |

## Limitations

- Synthetic data for one month.
- Only payout approval checks the login; disputes and rulings use typed staff IDs.
- No database: an open dispute lives in the chat.
- No payroll, CPF or accounting integration.
