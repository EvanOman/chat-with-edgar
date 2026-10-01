# Activation handoff

## Current access boundary

OpenAI's September 30, 2026 documentation describes dynamic registration for
local/open-source clients. Remotely hosted applications are directed to the
interest form; website identity sign-in is a limited partner trial. The installed
personal-site environment and GitHub secret names did not contain an approved
SIWC registration at inspection. That is evidence of missing configuration, not
a claim about every entitlement its owner might have.

Sources:

- https://developers.openai.com/siwc/token-sharing-open-source
- https://developers.openai.com/siwc/website
- https://developers.openai.com/siwc/request-client-id
- https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms

No registration form was submitted. No claim about commercial partnership or
hosted inference approval was made on the owner's behalf.

## Local real-provider proof

Start the loopback app, open its local address on that same machine and complete
**Continue with ChatGPT** using an authorized eligible account. App-specific
consent must grant plan usage. The app verifies identity and scope, then fetches
the account's eligible models. Run the bounded three-adapter scenarios in
`acceptance.md`. Do not paste tokens into chat or environment files, and do not
copy credentials from Codex or another client. If the browser is on another
computer, run the app there; a remote server's loopback callback will not reach it.

## Hosted activation requires concrete provider approval

Obtain the approved app client ID, exact callback(s), issuer/endpoints, client
authentication method, and explicit scope/eligibility instructions for
**remotely hosted ChatGPT plan-funded inference**. Identity-only approval is
insufficient. Proposed production callback for registration planning:
`https://evanoman.com/api/edgar/auth/callback`. This is a proposal, not a registered
callback or a substitution for the local loopback flow.

Then implement the approved server flow using atomic expiring OAuth transactions,
secure session cookies, encrypted per-registration token storage, isolated
conversation state, serialized refresh and revocation. Choose the Python backend
needed to host all three adapters only after access terms and resource bounds are
known; prefer the personal site's existing deployment conventions. Current Pages
Functions intentionally provide no client-ID switch that could accidentally turn
local OSS grants into multitenant hosted authorization.

A Python backend and durable encrypted session store are **not provisioned**.
Their deployment location, request/concurrency/time limits and billable hosting
budget must be recorded before activation. The existing public filing explorer
adds static assets and a bounded Pages Function; it creates no separate resource.

Deploy through the personal-site PR → CI → Cloudflare Pages route and verify real
consent, model/tool calls, citations, expiry/limits, cross-user isolation and
mobile/desktop behavior on the production origin. Only that proof unlocks the
optional scheduler integration.
