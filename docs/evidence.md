# Verification record

Recorded September 30, 2026 (America/Chicago; live retrieval completed
2026-10-01T01:08:52Z). End-to-end acceptance is **incomplete**.

| Evidence | Result | What it establishes |
| --- | --- | --- |
| `just check` | 166 tests pass; Ruff passes | Synthetic OAuth/JWT, refresh/revocation, real framework orchestration over mocked HTTP, serialization, tool bounds/history, streaming failures, browser isolation and cancellation |
| Hosted Pages handler tests | 9 pass; Functions typecheck passes | Hosted chat/auth stay disabled even with operator credentials; bounded snapshot retrieval, origin/input validation and no arbitrary upstream |
| Live SEC smoke | 25 tool calls passed across 10 unique SEC resources | Actual metadata, narrative HTML and XBRL access; six Apple/Figma filings (10-K, 10-Q, 8-K, S-1), 59 selected excerpts, four XBRL records |
| Local browser | Apple company lookup, filing filter, passage search and cited SEC links exercised; desktop and 390px mobile inspected | Actual UI and real retrieval, separate from model behavior |
| Synthetic browser streams | Five scenarios passed: interrupted EOF, expiry, late failure, completed EOF and partial usage | No false completed-run entry; citation/tool counts and token reporting verified without inference |
| App-specific OpenAI authorization | Browser reached the official authorization flow and human-verification screen | Auth navigation only; user authentication and consent are still pending |
| Live provider inference | **Not run** | No model eligibility, end-to-end tool use or model latency claim yet |
| Public deployment | Pending | No hosted auth/inference claim |
| Scheduler integration | Deferred by acceptance gate | Existing scheduler left unchanged |

The 166 tests include 41 auth, 55 adapter/transport, 62 retrieval, and eight HTTP
boundary tests. The adapter tests invoke the actual Pydantic AI and OpenAI Agents
SDK runners, with synthetic Responses streams. No model token is consumed by them.
An independent security review found two logout/cancellation defects; fixes and
regression tests stop continued inference and release the session lock even if the
producer is cancelled before startup. Review found no further confirmed exploitable
path in the tested source, but real provider and production routing remain separate
verification work.

The SEC smoke took approximately 8.6 seconds. Individual cached/uncached tool calls
ranged from 1 to 1,858 ms; this is **retrieval timing**, not inference latency or a
framework performance comparison. Raw provenance timestamps, content hashes and
source URLs accompany the public snapshot. Recreate the live evidence with:

```sh
uv run python scripts/smoke_retrieval.py --snapshot
```

App-specific consent and approved hosted plan usage are required before any final
end-to-end success claim. See [activation](activation.md) for exact missing access
and [acceptance](acceptance.md) for bounded live scenarios.
