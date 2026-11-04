"""Generate the scorecard notes (10 showcase tickers x last 5 quarters) via the Message Batches API (50% off).

Two steps, run in GitHub Actions:
    python -m scripts.scorecard_batch plan      # list what's missing, no API calls
    python -m scripts.scorecard_batch submit    # paragraph selection (cheap model, sync) + submit one batch
    python -m scripts.scorecard_batch collect   # if the batch has ended, save notes to data/notes/

Existing notes are never regenerated. Submitting twice is refused while a batch is pending.
"""
from __future__ import annotations

import json
import sys

from earnings_desk import config
from earnings_desk.note import anthropic_llm, finish_note, note_request_params, prepare_note
from earnings_desk.normalize import normalize
from earnings_desk.notes_service import get_note, load_paragraphs, note_quarters
from earnings_desk.sec import load_raw
from earnings_desk.store import NoteStore

QUARTERS = 5
STATE = config.DATA_DIR / "scorecard" / "batch_state.json"
# Batch list prices, $ per million tokens (input, output) = 50% of standard.
BATCH_PRICE = {"claude-sonnet-5-5": (1.0, 5.0)}


def missing() -> list[tuple[dict, int]]:
    store = NoteStore()
    out = []
    for t in config.SHOWCASE_TICKERS:
        raw = load_raw(t)
        if not raw:
            continue
        company = normalize(*raw, t)
        for i in note_quarters(company, QUARTERS):
            if not get_note(company, i, store):
                out.append((company, i))
    return out


def plan() -> None:
    todo = missing()
    for company, i in todo:
        print(company["ticker"], company["quarters"][i]["fiscal_label"])
    print(f"{len(todo)} notes to generate (~${len(todo) * 0.025:.2f} via batch)")


def submit() -> None:
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    if STATE.exists() and json.loads(STATE.read_text()).get("status") == "submitted":
        sys.exit("A batch is already pending; run `collect` first.")
    todo = missing()
    if not todo:
        print("Nothing to do.")
        return
    requests, pending = [], {}
    for company, i in todo:
        paras, meta = load_paragraphs(company, i)
        user, selected, usage = prepare_note(company, i, paras, anthropic_llm)
        cid = f"{company['ticker']}-{meta['earnings_8k_accn']}"
        requests.append(Request(custom_id=cid, params=MessageCreateParamsNonStreaming(
            **note_request_params(user, config.NOTE_MODEL))))
        pending[cid] = {"ticker": company["ticker"], "period_end": company["quarters"][i]["period_end"],
                        "selected": [p["id"] for p in selected], "usage": usage}
        print(f"prepared {cid} ({company['quarters'][i]['fiscal_label']})")
    batch = anthropic.Anthropic().messages.batches.create(requests=requests)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"status": "submitted", "batch_id": batch.id, "pending": pending}, indent=1))
    print(f"Submitted batch {batch.id} with {len(requests)} requests")


def collect() -> None:
    import anthropic

    if not STATE.exists():
        sys.exit("No batch state.")
    state = json.loads(STATE.read_text())
    if state.get("status") != "submitted":
        print("Nothing pending.")
        return
    client = anthropic.Anthropic()
    batch = client.messages.batches.retrieve(state["batch_id"])
    if batch.processing_status != "ended":
        print(f"Batch {batch.id} still {batch.processing_status}: {batch.request_counts}")
        return
    store = NoteStore()
    companies: dict[str, dict] = {}
    saved, failed, cost = 0, [], 0.0
    for r in client.messages.batches.results(batch.id):
        info = state["pending"].get(r.custom_id)
        if not info:
            continue
        if r.result.type != "succeeded" or r.result.message.stop_reason != "end_turn":
            failed.append(f"{r.custom_id}: {r.result.type}"
                          + (f"/{r.result.message.stop_reason}" if r.result.type == "succeeded" else ""))
            continue
        msg = r.result.message
        t = info["ticker"]
        company = companies.setdefault(t, normalize(*load_raw(t), t))
        i = next(k for k, q in enumerate(company["quarters"]) if q["period_end"] == info["period_end"])
        paras, meta = load_paragraphs(company, i)
        raw = json.loads(next(b.text for b in msg.content if b.type == "text"))
        usage = info["usage"] + [{"model": msg.model, "input_tokens": msg.usage.input_tokens,
                                  "output_tokens": msg.usage.output_tokens, "batch": True}]
        pin, pout = BATCH_PRICE.get(config.NOTE_MODEL, (1.0, 5.0))
        cost += msg.usage.input_tokens / 1e6 * pin + msg.usage.output_tokens / 1e6 * pout
        selected = [p for p in paras if p["id"] in set(info["selected"])]
        note = finish_note(company, i, paras, meta, raw, selected, usage, config.NOTE_MODEL)
        note["paragraphs"] = paras
        store.put(t, meta["earnings_8k_accn"], note)
        saved += 1
    state.update(status="collected", saved=saved, failed=failed, note_cost_usd=round(cost, 3))
    STATE.write_text(json.dumps(state, indent=1))
    print(f"Saved {saved} notes, {len(failed)} failed {failed}, note-call cost ~${cost:.2f}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    {"plan": plan, "submit": submit, "collect": collect}[cmd]()
