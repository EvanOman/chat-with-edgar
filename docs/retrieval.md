# SEC retrieval

`edgar/retrieval.py` is the shared application-side tool layer. The three model
adapters consume the same `TOOL_DEFINITIONS` and `dispatch(name, arguments,
client=client)` contract. It requires only `httpx` beyond the standard library.
Create one `SECClient` for the server lifetime and call `aclose()` on shutdown, or
use `async with SECClient()`. SEC credentials and model credentials are never
needed or sent by this layer.

## Tools and evidence

| Tool | Inputs | What it establishes |
| --- | --- | --- |
| `search_companies` | `query`, optional `limit` (1–10) | Ticker/name search over the SEC company ticker map, or direct submissions lookup by CIK. |
| `list_filings` | `cik`, optional `forms`, `limit` (1–20) | Primary document metadata, accession, filing/report dates and exact SEC document URL. Supports S-1, 10-K, 10-Q, 8-K and listed amendments/related forms. |
| `filing_passages` | `cik`, `accession`, `document`, `query`, optional `limit` (1–6) | Up to 1,500 characters per query-ranked passage from a verified primary filing. |
| `company_facts` | `cik`, optional US-GAAP `concept`, `limit` (1–20) | SEC-extracted financial facts from the companyconcept API, explicitly labeled as XBRL rather than narrative text. |

All successful results contain `ok`, `kind`, `data`, `provenance` and `citations`.
A citation has `id`, `label`, `url`, `company`, `form` and `filed`. Each provenance
record includes its SEC URL, UTC retrieval timestamp, SHA-256 of retrieved bytes
and cache-hit state. XBRL citations add `data_url`; each fact retains unit, period,
accession and filing context. Errors contain `ok: false` and
`error: {code, message, retryable}`. Cancellation propagates instead of returning
success. Model or UI code must distinguish an empty result from supporting evidence.

`filing_passages` verifies the accession and primary filename against submissions
metadata before requesting the document. HTML extraction removes script, style,
head, hidden inline-XBRL and explicitly hidden content. Text is normalized and
ranked by lexical query matches; returned character offsets refer to that normalized
text. No matching terms produces an empty passage list. Extracted tables can lose
visual column alignment: consult the original filing or XBRL fact context before
making numerical comparisons. A passage is evidence, not a complete document review.

The default XBRL concept is
`RevenueFromContractWithCustomerExcludingAssessedTax`. Callers can request another
exact US-GAAP tag such as `Assets` or `NetIncomeLoss`. There is no automatic conversion
across taxonomies, fiscal periods, units or amended/repeated observations.

## Boundaries and fair access

- Only HTTPS on `www.sec.gov` and `data.sec.gov` with narrowly matched SEC paths is
  allowed. Tools accept identifiers, not arbitrary URLs. Redirects, credentials in
  URLs, ports, query strings, fragments, traversal and unsupported extensions are
  rejected. Only primary HTML/text documents are read.
- One shared client serializes network fetches and spaces request starts by at
  least 0.5 seconds (two per second). This applies across visitors using that client.
  Separate server processes do not share a limiter; keep one process for this
  prototype and coordinate any future distributed deployments.
- Client identity is `Chat-with-EDGAR/0.1 Evan Oman https://evanoman.com`. This uses
  Evan's public site identity without inventing an email address. `SECClient` can
  accept an explicitly supplied, accurate contact user-agent. It never impersonates
  a browser or retries through another host when SEC blocks access.
- Network operations have a 20-second HTTP timeout and a 30-second overall fetch
  deadline. Decoded response bodies are capped at 12 MiB. An in-memory LRU cache is
  bounded to 64 resources / 32 MiB; metadata expires after 15 minutes, ticker maps
  and filing documents after 24 hours. No provider secrets enter that cache.
- Filings history is bounded to the recent submissions list and, when needed, at
  most three older SEC history files. `history_truncated` and `coverage` expose
  that limitation. This is not exhaustive full-text search of all EDGAR filings.
- All filing content is untrusted data. Tool definitions and outputs mark it as
  evidence; adapters must preserve their instruction boundary. The dispatcher can
  only execute these four read-only tools, never file writes, shell commands or
  model-selected network endpoints.
- SEC 403/429, timeouts, malformed data and oversize responses produce explicit
  failure results. No data vendor, fabricated excerpt or inference fallback exists.

The SEC documents its public, unauthenticated
[submissions and XBRL APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces),
[ticker mappings, declared user-agent guidance and 10-request-per-second ceiling](https://www.sec.gov/about/webmaster-frequently-asked-questions),
and the ceiling's application
[across machines](https://www.sec.gov/filergroup/announcements-old/new-rate-control-limits).
Those official pages were checked September 30, 2026. The lower local limit leaves
headroom; it does not authorize uncoordinated parallel deployments. SEC also notes
that ticker mappings are not guaranteed exhaustive or accurate.

## Real retrieval evidence and repeatable verification

Run from the repository root:

```sh
uv sync --locked
uv run python -m pytest -q tests/test_retrieval.py
uv run python scripts/smoke_retrieval.py
uv run python scripts/smoke_retrieval.py --snapshot
```

The first smoke mode performs four tool calls and reads one Apple 10-K plus revenue
facts. The snapshot mode makes 25 tool calls using ten unique SEC resources and
refreshes `site/data/snapshot.json`, the canonical public artifact. It does not
invoke an LLM and is separate from any real-provider inference proof. The local
run report is `.mission/retrieval-smoke.json`; it contains only public retrieval
identifiers, outcomes, durations and source provenance.

A successful snapshot run completed at **2026-10-01 01:08:52 UTC** (September 30 in
America/Chicago). All six primary documents were downloaded and passages extracted:

| Company | Form | Filed | Primary SEC document |
| --- | --- | --- | --- |
| Apple | 10-K | 2025-10-31 | [aapl-20250927.htm](https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm) |
| Apple | 10-Q | 2026-07-31 | [aapl-20260627.htm](https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/aapl-20260627.htm) |
| Apple | 8-K | 2026-07-30 | [aapl-20260730.htm](https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/aapl-20260730.htm) |
| Figma | S-1 | 2025-07-01 | [figma-sx1.htm](https://www.sec.gov/Archives/edgar/data/1579878/000162828025033742/figma-sx1.htm) |
| Figma | 10-K | 2026-02-18 | [fig-20251231.htm](https://www.sec.gov/Archives/edgar/data/1579878/000162828026009228/fig-20251231.htm) |
| Figma | 10-Q | 2026-08-05 | [fig-20260630.htm](https://www.sec.gov/Archives/edgar/data/1579878/000162828026053348/fig-20260630.htm) |

The 142,271-byte snapshot contains two companies, six filings, 59 excerpts and
four Apple revenue XBRL records. It is a dated, curated public filing explorer,
not a substitute for live search or authorized inference. Network access to both
SEC archives and data APIs succeeded on this machine; no access-control bypass or
paid data provider was needed. The captured source URLs, hashes and timestamps are
in the public snapshot; measurements include caching and extraction work and must
not be presented as model latency.

The contract suite covers CIK/document validation, bounded tool arguments and hosts,
blocked redirects, malformed data and identities, size limits, request spacing,
cache eviction/provenance, passage offsets, hidden-text removal, XBRL periods,
auth-free SEC headers, timeouts and cancellation. These use an HTTP mock transport;
they are separate from the live SEC smoke described above.
