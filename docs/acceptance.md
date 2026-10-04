# Acceptance and evidence

Mocked contracts, real retrieval, real OAuth and real inference are separate evidence.
Do not describe a working public page or a synthetic stream as end-to-end success.

## Reproducible scenarios

Use the same eligible model, question, tool catalog and fresh conversation for all
three adapters. Run at most one initial question plus one follow-up per adapter
for the first authorized smoke (six user turns total). Stop on permission denial,
expired/revoked credentials, allowance limits or an incomplete stream. Never retry
with an operator key. Any continuation is a new explicitly visible user request.

1. Apple 10-K: ask which supply-chain risks the selected filing describes and
   request quotations/citations. Require a real `filing_passages` call, correct
   CIK/accession/SEC link, and answer supported by the returned text.
2. Follow-up: ask which passage supports the largest claim; require continuity
   without `previous_response_id` or server-stored OpenAI history.
3. Separately inspect a 10-Q and S-1 in the filing explorer; their titles, dates,
   excerpts and SEC links must correspond to the same accession.
4. Switch account/registration and confirm the model catalog and conversation
   clear. A second browser cannot retrieve or continue the first browser's chat.
5. Revoke/disallow usage or sign out. Sending chat must stop before inference.
6. Interrupt a stream or inject `response.failed` / usage-limit fixtures. Partial
   text must be labeled unsuccessful and excluded from committed history.

Record framework/version, selected model slug, UTC time, request IDs if supplied,
completed/failed status, tool names and source URLs, time to first text and total
latency, provider-reported tokens or explicit unavailability. Record no tokens,
auth codes, state, callback URLs, account subjects/email or registration IDs.
Token counts are not monetary cost or remaining plan quota.

## Bounded live runner

After completing app-specific sign-in and enabling ChatGPT plan usage, leave that
browser on `http://127.0.0.1:19371/chat-with-edgar/`. Select an eligible model in
the app. Run the server and runner from the same installed checkout so the
recorded SDK versions describe the service under test. The CLI requires `agent-browser` to be installed. For an existing
`agent-browser` session:

```sh
uv run python scripts/verify_live.py --session YOUR_SESSION --model ELIGIBLE_SLUG \
  --check-only --output .mission/evidence/preflight.json
just verify-live YOUR_SESSION ELIGIBLE_SLUG
```

The second command consumes the signed-in user's allowance: one initial question
and one follow-up for each of the three adapters, with no retry after any failure.
Each user turn can involve multiple model requests for tools, bounded by the
server's eight-request limit. Failed or interrupted requests may still consume
allowance. `--check-only` reads consent status and the model catalog without
retrieval or inference. Use a fresh `--output` path for a deliberately authorized
rerun; the runner never overwrites existing evidence.

If the user signed in through another supported browser automation surface,
generate exactly the same browser script without running inference:

```sh
mkdir -p .mission
uv run python scripts/verify_live.py --model ELIGIBLE_SLUG --emit-script \
  > .mission/run-live.js
```

Evaluate that file on the signed-in app tab using that surface's documented
JavaScript execution API. It returns immediately with `started: true` and a
nonsecret `run_id`. Inspect
`window.__edgarAcceptanceRun` until its status is `passed` or `failed`, then save
that object as the evidence JSON, checking its `run_id` matches the started run.
Do not export cookies, browser storage or
network captures. Calling
`window.__edgarAcceptanceCancel(window.__edgarAcceptanceRun.run_id)` cancels that run;
cancellation can still incur allowance usage for requests already sent.

The runner uses only same-origin HTTP with the existing HttpOnly session cookie.
It requires current consent and model eligibility, a real Apple 10-K passage
retrieval, a successful `filing_passages` tool invocation per initial answer,
the matching SEC citation in each answer, and at least one verbatim quotation
matched against retrieved text. A fresh conversation is created for each adapter;
each follow-up must recover a random label supplied only in its preceding turn.
This proves a minimal continuity and quotation check, not that every claim in an
answer is correct. It does not replace the UI and account-isolation checks below.

Completion evidence means the browser saw exactly one app `completed` event and
clean EOF. The server contract emits that event only after verifying each upstream
`response.completed` and clean provider EOF; the browser does not directly observe
the provider stream. Late error events, duplicate completions, incomplete streams,
bad citations, failed tools and unavailable consent all fail the run. No completed
fixture can be substituted for the missing live result.

Evidence contains model/adapter, runner environment SDK versions, timestamps,
tool names, the public SEC URL, latency, and allowlisted provider token counts.
It excludes answers, conversation IDs, account details, request bodies and OAuth
material. Missing token counts are explicitly unavailable. The
`real_provider_inference` field is `false` if no request was attempted, `null` if
attempted without a verified successful turn, and `true` after a verified turn;
only `status: passed` with six recorded turns passes the full live run. A preflight
pass always has `mode: preflight` and zero turns. Record the deployed server revision
alongside evidence when testing a deployment; runner environment versions alone
do not establish deployed SDK versions. Hosted testing uses `--origin
https://evanoman.com` only after approved hosted inference is actually available.

## UI checks

Exercise company search, filing type filter, passage query, source links,
framework selector, account controls, errors and chat streaming at 1440px and
390px widths. Browser screenshots should show source content and status, not
private account details or authorization query parameters. The public page must
label snapshot coverage and the hosted auth gate clearly.

## Release gate for the scheduler

Only after an approved hosted registration completes consent, inference and real
retrieval on the public EDGAR deployment can the scheduler's optional provider
path be implemented. Verify the existing scheduler mode independently before
and after that change. A local-only EDGAR test does not open this gate.
