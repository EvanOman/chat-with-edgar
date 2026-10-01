# Local ChatGPT authorization

This implements the **local open-source** Sign in with ChatGPT protocol documented
on September 30, 2026. It does not authorize a public multitenant hosted service.
Hosted integration requires its own approved client and plan-usage permission.
No API key, operator-paid account, coding-agent login file, or gateway is consulted.

## Server contract

`AuthManager(runtime_dir, redirect_uri, client=None)` takes a private runtime directory
and an exact HTTP loopback callback on `127.0.0.1`. The server must listen before it
opens authorization. The persisted callback path cannot change on a host; the port
may change. One process exclusively owns each runtime using a file lock; start a
single server worker and call `await manager.aclose()` during shutdown.

The HTTP layer must supply a cryptographically random browser identifier (32 or more
base64url characters), store it only in an HttpOnly, SameSite=Lax cookie, and enforce
exact Host and Origin checks on requests that change state. Bind the listener to
`127.0.0.1`, disable callback query/access logging, never honor forwarding headers,
and redirect the callback promptly to a clean URL. This module does not turn a
browser-supplied identity or registration ID into an authenticated session.

| Method | Result |
| --- | --- |
| `await begin(browser_id, registration_id=None, reconsent=False)` | Authorization URL; omitted registration starts a new app-specific registration. |
| `await complete(browser_id, params)` | Safe status after one-time state, code exchange, signature and permission checks. |
| `status(browser_id)` | Safe current identity and browser-owned saved registrations. |
| `await select(browser_id, registration_id)` | Status after selecting a validated registration owned by this browser. |
| `await get_access_token(browser_id, force_refresh=False)` | Server-only bearer, or `AuthError`; refreshes near expiry. Never send this return value to a browser. |
| `await logout(browser_id)` | Safe status and explicit remote-revocation confirmation or failure. |
| `await aclose()` | Close owned HTTP client and release the runtime lock. |

Status contains `authenticated`, `inference_enabled`, `active_registration`,
`account`, `registrations`, and `usage_url`. Account metadata contains
`registration_id`, `subject`, `email`, `client_id`, `label`, `authenticated`, and
`inference_enabled`. Each registration has a distinct label even if emails match.
`inference_enabled` means the last verified grant authorizes the route; it is not a
quota, remaining-balance, or current allowance guarantee. Provider requests must
still honor actual eligibility, usage-limit, and revocation failures.

`AuthError.public()` includes only authored `code`, `message`, HTTP `status`,
`retryable`, and a constrained provider `request_id`. Neither raw provider bodies nor
credentials are rendered through this interface. Unexpected exceptions must receive
a generic HTTP error response rather than a traceback.

## Protocol and security decisions

New registration uses `dynamic_agent_client`, the actual application name, and a
persisted `urn:uuid:` host ID. Authorization generates fresh state, nonce, and S256
PKCE values. Transactions expire after ten minutes; a new attempt supersedes a prior
attempt in that browser. Correct-browser callbacks consume the transaction before
network activity. A callback in another browser cannot consume or redeem it.

Issued client IDs remain paired with verified subjects and separate browser-owned
registration IDs. The code exchange uses the original exact redirect and PKCE
verifier. A failed initial `invalid_grant` retains the issued client as an unfinished
registration so a new authorization can reuse it. Returning callbacks cannot change
the selected client or verified subject. Email is display metadata, never identity.

ID-token verification uses PyJWT and the fixed OpenAI discovery/JWKS endpoints,
RS256 signatures, issuer, audience, expiry, issued time, nonce, and nonempty subject.
Multiple-audience ID tokens also require the expected authorized party. JWKS caches
expire after an hour and refresh once for an unfamiliar key ID. Token-provided key
URLs are ignored. Discovery cannot redirect credentials or key requests to another
host. OAuth and JWKS requests do not follow redirects or use environment proxies.

The documented access JWT is separately signature-verified against its resource
audience and bound to the issued client and ID-token subject. Both the token response
and signed access token must grant `chatgpt.tokens.use.direct` and `resource.invoke`
before inference is allowed. A valid identity without plan permission remains signed
in with inference disabled. An explicit enable-plan action may use `prompt=consent`;
ordinary sign-in does not force consent. No alternate billing path exists.

The optional retained ID-token authorization hint is deliberately omitted: returning
sign-in uses the account selector and optional email hint, avoiding an ID token in
browser history or diagnostic URLs. Retained tokens remain server-side. The protected
JSON file is written atomically with mode `0600` in a `0700` directory; raw browser
cookie identifiers are hashed before persistence. The stable host ID and registration
metadata survive sign-out. Losing the browser cookie prevents that browser from
accessing saved registrations; it must authorize again.

Refreshes serialize per browser within the exclusively locked runtime and replace the
full credential set atomically. Terminal refresh errors clear tokens while preserving
the issued registration. Temporary failures and invalid-client errors preserve the
credential record for recovery. Refresh does not broaden scopes or fall back to any
other identity. Sign-out blocks further token issuance, discards pending callbacks,
tries remote revocation with one bounded retry, and clears local tokens regardless of
remote outcome. An unconfirmed revocation directs the user to ChatGPT Settings.

Inference callers must not retry a revoked or usage-limited request through a different
account. In-flight requests need cancellation at the server conversation layer when
signing out or switching accounts; possession of an earlier returned bearer cannot be
recalled by this module alone.

## Verification and live activation

Run `uv run python -m pytest tests/test_auth.py -q` and
`uv run ruff check edgar/auth.py tests/test_auth.py`.

The tests use generated RSA keys and HTTP mocks. They cover authorization/PKCE,
callback replay and browser isolation, nonce/issuer/audience/expiry/signature failures,
access-token client/subject binding, identity-only grants, same-email registrations,
returning-account validation, refresh races and rotation, terminal/temporary errors,
revocation, private storage, restart persistence, one-process ownership, callback
restrictions, and discovery-host boundaries. These are contract tests, not proof that
OpenAI accepted this app or funded real inference.

For the first real flow, run the loopback server, open its local UI, and have the
account owner choose **Continue with ChatGPT**, sign in, and explicitly authorize this
application's plan usage. Never copy credentials from another application or coding
harness. After consent, list eligible models and complete bounded, cited filing
questions through each adapter. Record only redacted request IDs, timings, event
counts, model identifiers, and safe usage fields; never record auth callback URLs,
codes, or tokens. Live consent and inference remain a separate acceptance gate.

## Primary sources

- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Accounts, refresh and revocation](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Token reference](https://developers.openai.com/siwc/token-sharing-open-source/token-reference)
- [Auth errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)
- [ID-token validation example](https://developers.openai.com/siwc/website)
- [Local OSS scope and hosted interest gate](https://developers.openai.com/siwc/token-sharing-open-source)
