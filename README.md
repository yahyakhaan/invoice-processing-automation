# Acme Invoice Automation

Acme was leaking **$2M/year** to a 30% error rate and a 5 day AP cycle: messy PDFs, an inconsistent inventory database, VP email chains, and a brittle payment step. This project compresses that path to seconds with an auditable control plane: four specialized agents, deterministic financial gates, and a checkpointed VP review.

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py --invoice_path=data/invoices
python main.py --demo=vp-review
# copy one of the printed --resume commands, for example:
python main.py --resume=<thread_id> --vp-decision=approve --vp-actor="VP Demo" --vp-reason="Within policy after review"
```

Database bootstrap is **idempotent**. `python db/init_db.py` is optional.

Zero key runs are labeled **DETERMINISTIC FALLBACK — NO LLM**. They still execute the four stage workflow, parsers, SQLite checks, and payment gate.

### Live LLM (agentic demonstration)

```bash
cp .env.example .env   # set XAI_API_KEY
python main.py --invoice_path=data/invoices/invoice_1012.txt --provider=xai --require-llm
```

`--require-llm` fails fast if the process would silently fall back to mock.

Streamlit inspector (optional):

```bash
streamlit run ui/app.py
```

## What I did

Four agents, coordinated by a LangGraph supervisor:

| Stage | Who | What |
|---|---|---|
| Ingestion | `DocumentIngestionAgent` | Parsers + OCR normalizers; structured extract for messy txt/pdf |
| Validation | `ValidationAgent` | Mandatory SQLite/integrity/total/duplicate/fraud tools |
| Approval | `ApprovalAgent` + independent `ApprovalCriticAgent` | Policy + reflection; critic cannot pay |
| Payment | `payment_executor` (service) | Idempotent mock payment; never auto-pays ≥ $10k USD |

Normalization, reporting, and VP review are **services**, not agents.

## Why LangGraph

I used LangGraph because this workflow is a control plane, not a conversation. The graph owns routing, retry budgets, SQLite checkpoints, and VP interrupts; agents only fill the judgment shaped holes. Stock checks, totals, and payment stay in deterministic services so the LLM never owns cash or inventory.

## Dataset as spec

| Invoice | Expected |
|---|---|
| INV-1001 | Pay |
| INV-1002 | Reject `OVER_STOCK` (20× GadgetX > 5) |
| INV-1003 | Reject fake/zero stock + fraud language + unparseable due date |
| INV-1004 | Pay |
| INV-1004 R1 | Pay with `REVISION` (GadgetX 5 ≤ 5) |
| INV-1005 | Reject over stock (high value does not override) |
| INV-1006 | Pay (pivoted CSV) |
| INV-1007 | Reject over stock |
| INV-1008 | Reject unknown items |
| INV-1009 | Reject integrity / missing fields |
| INV-1010 | Pay after aggregating WidgetA 8+4=12 |
| INV-1011 txt | Pay; PDF in the same batch is `DEDUP` |
| INV-1012 | Pay with OCR / vendor identity warnings |
| INV-1013 | Reject over stock + total mismatch |
| INV-1014 | Pay; EUR converted locally (`FX_REVIEW`) |
| INV-1015 | Pay |
| INV-1016 | Reject unknown `WidgetC` |

Inventory is **read only** during a run so batch order cannot drain stock.

## Controls a PE owner should care about

- No auto pay on integrity, unknown items, over stock, or total mismatch
- ≥ $10k USD equivalent pauses for VP review (`--demo=vp-review` because the provided corpus never has a clean high value invoice)
- EUR is not treated as USD
- Quantities are aggregated by SKU before the stock check
- Payment idempotency keys; resume cannot double pay
- Humans cannot override blocking validation facts

## Run modes

| Mode | Command | What it proves |
|---|---|---|
| Functional / offline | `python main.py --invoice_path=data/invoices` | Parsers + gates on the full corpus |
| HITL | `python main.py --demo=vp-review` then `--resume` | Escalation that the corpus cannot reach |
| Agentic | `--provider=xai --require-llm` | Live tool using agents |

## Observability

Each run writes `outputs/<run_id>/<stem>/result.json` and `events.jsonl` with `agent_id`, handoffs, tools, and outcomes. The Rich CLI prints a one screen tree. Streamlit shows the same timeline plus a local VP queue.

## Tests

```bash
pytest -q
```

## What I cut

No live bank, email inbox, cloud deploy, stock reservation, CrewAI, or production SSO. VP review is a local checkpoint (CLI or Streamlit).