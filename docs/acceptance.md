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
