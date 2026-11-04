"""Glue: pick a quarter, load its press release, and get or create the verified note."""
from __future__ import annotations

from . import config
from .note import LLM, anthropic_llm, generate_note
from .press import paragraphs
from .verify import verify_note
from .sec import SECClient, filing_url, find_exhibit_991
from .store import NoteStore


def note_quarters(company: dict, n: int = 5) -> list[int]:
    """Indexes of the latest `n` quarters that have both XBRL data and an earnings 8-K, newest first."""
    idx = [i for i, q in enumerate(company["quarters"]) if q["sources"].get("earnings_8k_accn")]
    return list(reversed(idx[-n:]))


def load_paragraphs(company: dict, quarter_idx: int) -> tuple[list[dict], dict]:
    html, meta = press_release(company, quarter_idx)
    return paragraphs(html), meta


def press_release(company: dict, quarter_idx: int, client: SECClient | None = None) -> tuple[str, dict]:
    """Return (html, meta) for the quarter's Exhibit 99.1. Repo copy first, SEC otherwise."""
    q = company["quarters"][quarter_idx]
    accn = q["sources"]["earnings_8k_accn"]
    meta = {"earnings_8k_accn": accn, "earnings_8k_date": q["sources"].get("earnings_8k_date"),
            "filing_index": filing_url(company["cik"], accn), "periodic_filing": q["sources"].get("filing_url")}
    local = config.RAW_DIR / company["ticker"] / "press" / f"{accn}.htm"
    if local.exists():
        import json

        idx_path = local.parent / "index.json"
        if idx_path.exists():
            meta["exhibit_url"] = json.loads(idx_path.read_text()).get(accn, {}).get("exhibit_url")
        return local.read_text(encoding="utf-8"), meta
    client = client or SECClient()
    url = find_exhibit_991(client.get_text(filing_url(company["cik"], accn)))
    if not url:
        raise LookupError(f"No Exhibit 99.1 found in 8-K {accn}")
    meta["exhibit_url"] = url
    return client.get_text(url), meta


def get_note(company: dict, quarter_idx: int, store: NoteStore | None = None) -> dict | None:
    store = store or NoteStore()
    accn = company["quarters"][quarter_idx]["sources"].get("earnings_8k_accn")
    note = store.get(company["ticker"], accn) if accn else None
    if note and note.get("paragraphs"):
        # Re-run the deterministic verifier on load, so verifier fixes apply to stored notes too.
        note = dict(verify_note(note, company, note["paragraphs"]), raw_model_output=note.get("raw_model_output"))
    return note


def create_note(company: dict, quarter_idx: int, store: NoteStore | None = None, llm: LLM = anthropic_llm) -> dict:
    """Generate, verify and store a note in data/notes/ (showcase tickers, GitHub Actions only)."""
    store = store or NoteStore()
    html, meta = press_release(company, quarter_idx)
    paras = paragraphs(html)
    note = generate_note(company, quarter_idx, paras, meta, llm=llm, note_model=config.NOTE_MODEL)
    note["paragraphs"] = paras  # stored so the note can show sources without refetching
    store.put(company["ticker"], meta["earnings_8k_accn"], note)
    return note
