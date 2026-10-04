# Verification record

Updated October 3, 2026 (America/Chicago). The public filing reader is deployed
and verified. End-to-end ChatGPT inference acceptance is **incomplete**.
Initial live SEC retrieval completed 2026-10-01T01:08:52Z.

| Evidence | Result | What it establishes |
| --- | --- | --- |
| `just check` | 194 tests pass; Ruff passes | Synthetic OAuth/JWT, refresh/revocation, real framework orchestration over mocked HTTP, serialization, tool bounds/history, streaming failures, browser isolation and cancellation |
| Hosted Pages handler tests | 9 pass; Functions typecheck passes | Hosted chat/auth stay disabled even with operator credentials; bounded snapshot retrieval, origin/input validation and no arbitrary upstream |
| Live SEC smoke | 25 tool calls passed across 10 unique SEC resources | Actual metadata, narrative HTML and XBRL access; six Apple/Figma filings (10-K, 10-Q, 8-K, S-1), 59 selected excerpts, four XBRL records |
| Local browser | Apple company lookup, filing filter, passage search and cited SEC links exercised; desktop and 390px mobile inspected | Actual UI and real retrieval, separate from model behavior |
| Synthetic browser streams | Five scenarios passed: interrupted EOF, expiry, late failure, completed EOF and partial usage | No false completed-run entry; citation/tool counts and token reporting verified without inference |
| App-specific OpenAI authorization | Browser reached the official authorization flow and human-verification screen | Auth navigation only; user authentication and consent are still pending |
| Unauthenticated browser preflight | Rejects with `consent_required`, zero turns attempted | The new live runner does not initiate inference without an app-specific grant |
| Live provider inference | **Not run** | No model eligibility, end-to-end tool use or model latency claim yet |
| Production browser | Desktop and 390px reader, Apple/Figma forms, revenue search, SEC links, Ask gate, adapter choices, comparison and homepage navigation pass | Visible deployed behavior; no model call |
| Public deployment | [Live filing reader](https://evanoman.com/chat-with-edgar/); seven production HTTP checks pass | Real SEC snapshot matches the released source; company lookup and cited passages work; hosted auth and chat explicitly return `hosted_access_unapproved` |
| Scheduler integration | Deferred by acceptance gate | Existing scheduler left unchanged |

The 194 tests include 41 auth, 55 adapter/transport, 62 retrieval, eight HTTP
boundary tests, and 28 acceptance-runner tests. The runner tests execute its actual
browser JavaScript against offline HTTP fixtures, including late failures, missing
consent, unsupported quotations, history loss, startup ambiguity and run-bound
cancellation. They are separate from real inference evidence. The adapter tests invoke the actual Pydantic AI and OpenAI Agents
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

## Production release

[Personal-site PR #40](https://github.com/EvanOman/conway-personal-website/pull/40)
merged the reader, bounded retrieval API and access gate. Its
[main-branch CI](https://github.com/EvanOman/conway-personal-website/actions/runs/36801578844)
and [Pages deployment](https://github.com/EvanOman/conway-personal-website/actions/runs/36802265707)
both succeeded. Subsequent site releases preserve the EDGAR integration.

Production HTTP verification on October 3 confirmed the page, byte-identical SEC
snapshot, explicit unauthenticated status, Figma search, canonical S-1 passage
citations, and refusal of hosted auth/chat. This establishes deployed retrieval;
it does not establish an authorized model request.

OpenAI's hosted eligibility boundary was rechecked against its current
[SIWC usage policy](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt#usage-policy-and-terms).
A public hosted app needs approved access even when its source is open. Website
identity registration and local app consent are separate from hosted plan usage.
