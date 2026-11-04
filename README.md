# earnings-desk

AI earnings notes on US-listed companies, written from SEC filings, where **every number in the note is checked by code against the SEC's structured (XBRL) data**. Tools that summarize filings already exist. This project is about showing when a note can be trusted, and measuring how often it can.

> Status: **Milestone 1** (data layer + numbers view, no AI). The verified note, accuracy scorecard and landing page come in later milestones.

## Data sources (all free, SEC only)

| What | Where |
|---|---|
| Ticker → CIK | `https://www.sec.gov/files/company_tickers.json` |
| Filing list | `https://data.sec.gov/submissions/CIK##########.json` |
| Structured financials | `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` |
| Earnings press release | Exhibit 99.1 of the Item 2.02 8-K |

Earnings-call transcripts are **not** used because they are not freely available. Every request sends the `SEC_USER_AGENT` header, and requests are throttled to about 8 per second.

## Data rules

- **Periods come from the facts' own start/end dates**, not the `fy`/`fp` fields. Those describe the filing that reported a fact, not the fact itself.
- **Fiscal vs calendar labels.** Each quarter is labeled by the company's fiscal calendar (`FY2026 Q3`) *and* by calendar quarter (`CY2025 Q3`, using the quarter's midpoint), and the exact period-end date is shown. 52/53-week years (e.g. ADBE) are handled with a 7-day tolerance.
- **Q4 and cash-flow quarters are derived.** 10-Ks have no Q4 figure, so Q4 = full year − 9-month YTD. Cash-flow statements are year-to-date only, so Q2 = 6M − 3M, and so on. Every derived value is flagged ᵈ and shows its formula. Diluted EPS is never derived (share counts differ), so Q4 EPS is blank.
- **Concept fallbacks.** Each metric maps to an ordered list of XBRL concepts (see `earnings_desk/concepts.py`), and the concept used is recorded per value.
- **Restatements.** The most recently filed value is used and the original is kept (flagged ʳ).
- **Limited coverage.** Banks, lenders, insurers, 20-F/40-F foreign filers and IFRS filers get a "limited coverage for this filer type" message instead of numbers. Filers with fewer than 5 quarters (e.g. recent IPOs) get a warning.
- **Deferred revenue** uses the total contract liability when tagged. Otherwise it falls back to the current portion, flagged ᵖ.

## Layout

```
earnings_desk/   config, SEC client, concept map, normalizer, analytics
pages/           Streamlit pages
scripts/         fetch_showcase.py (used by GitHub Actions), make_test_samples.py
data/raw/<T>/    submissions.json, companyfacts.json (mapped concepts only, values verbatim), press/*.htm
data/processed/  normalized quarterly table per showcase ticker
tests/           unit tests (synthetic fixtures + real SEC samples once fetched)
```

## Run locally

```bash
pip install -r requirements.txt
```

```bash
export SEC_USER_AGENT="Your Name your@email.com"
```

```bash
python -m scripts.fetch_showcase CRM MSFT
```

```bash
streamlit run app.py
```

```bash
pytest -q
```

Showcase tickers (refreshed by `.github/workflows/fetch.yml` on weekdays): CRM, NOW, ADBE, SNOW, DDOG, NVDA, MSFT, PLTR, AI (C3 AI), NDAQ. Any other ticker is fetched live from the SEC.

Not investment advice. No price targets, ratings or valuations.
