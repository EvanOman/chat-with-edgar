# Chat with EDGAR

Explore primary SEC filings and ask grounded questions using your own authorized
ChatGPT plan. Compare Pydantic AI, the OpenAI Agents SDK, and a direct Responses
loop using the same retrieval tools, instructions, model and conversation contract.

**Prototype status:** the [public filing reader](https://evanoman.com/chat-with-edgar/)
is deployed, with real SEC sources and browser verification. The local app
implements all three agent adapters and app-specific OAuth. See
[verification evidence](docs/evidence.md). Public-site ChatGPT inference is gated
on OpenAI hosted plan-usage approval. No real inference result is claimed until
app-specific consent and a completed provider stream are recorded. There is no operator-funded fallback.

## Run locally

Python 3.12+ and [uv](https://docs.astral.sh/uv/) are required.

```sh
git clone https://github.com/EvanOman/chat-with-edgar.git
cd chat-with-edgar
uv sync --locked
uv run python -m edgar.server --port 19371
```

Open http://127.0.0.1:19371/chat-with-edgar/ on the same machine. Select **Continue
with ChatGPT**, complete OpenAI's app-specific account/workspace registration and
plan-usage consent, and return to the app. Eligible model choices come from the
signed-in account. Choose a framework, select a filing, and ask a question. Each
model/tool request uses only the active registration's OAuth bearer. The app never
reads `OPENAI_API_KEY`, a gateway setting, or another application's credentials.

The server binds only to `127.0.0.1` and requires the exact loopback origin for
mutating requests. If the port is occupied, choose another unprivileged port;
never stop another service. The OAuth callback remains
`/api/edgar/auth/callback`; only its loopback port changes. Do not expose this local
server through a public tunnel or reverse proxy.

Tokens and host identity remain under ignored `.runtime/` (directory 0700, files
0600). Keep this directory private. Sign out revokes the selected renewable
session when possible and clears local tokens. Disconnect the app in ChatGPT
settings if revocation cannot be confirmed. Conversation history stays in memory
and is cleared for the browser on account switches/sign-out and server restart.

## Verify

The verification suite also requires Node.js 22+ to exercise the acceptance
runner’s browser JavaScript against offline fixtures. The app itself runs in Python.

```sh
uv run ruff check edgar tests
uv run pytest -q
uv run python scripts/smoke_retrieval.py
```

Unit/contract tests use synthetic provider responses and synthetic JWTs. They do
not prove plan eligibility or live model behavior. The retrieval smoke uses real
SEC HTTP requests, including filing text. See [acceptance scenarios](docs/acceptance.md)
for the separate bounded live-inference procedure after consent.

## Architecture

```text
Browser (same UI in site/)
  ├─ Public site: Cloudflare Pages + read-only filing snapshot
  │                hosted auth/chat return an explicit access gate
  └─ Local loopback: FastAPI → browser-owned OAuth registration
                    ├─ SEC tools → SEC metadata, archive text and XBRL
                    └─ selected orchestration adapter
                       ├─ Pydantic AI Agent + custom Model
                       ├─ OpenAI Agents Runner + custom Model
                       └─ direct tool loop
                              ↓ common strict streaming transport
                       api.openai.com/v1/responses
```

`edgar/auth.py` owns account grants, identity validation and refresh.
`edgar/transport.py` enforces the SIWC Responses subset.
`edgar/adapters.py` owns framework orchestration and normalized history.
`edgar/retrieval.py` owns bounded application-side SEC tools and citations.
`edgar/server.py` owns browser sessions and completed-turn history commits.
`site/` owns the interface. The hosted API adapter is in the personal-site
repository under `functions/api/edgar/`; its only data source is the pinned public
snapshot and it has no inference credential or model connection.

See [auth](docs/auth.md), [retrieval provenance](docs/retrieval.md),
[framework comparison](docs/comparison.md), and [hosted activation](docs/activation.md).
No dollar estimates are derived from token counts or ChatGPT quotas.

## Publish and roll back

The personal site's normal project registry imports only `site/` assets from a
full immutable commit SHA. Commit and push this repository, then update the
`chat-with-edgar` source SHA in `projects/registry.json` in the personal-site
worktree. Run its project tests, normal CI gates and browser verification; merge
through a pull request. Its successful main-branch CI triggers Cloudflare Pages.

To roll back the public app, revert that registry SHA and any accompanying Pages
Function change through a PR. To remove the prototype, revert its registration
and API adapter. The app does not provision a database, vector service, cloud
Python server, or paid inference fallback.

## Official transport scope

The current [local OSS SIWC flow](https://developers.openai.com/siwc/token-sharing-open-source)
uses an app-issued bearer with the public Responses API. Its
[preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
require a narrower request shape than ordinary SDK defaults. A protocol adapter
makes that contract explicit while each framework still runs its own tool loop.
The managed [Agents API](https://developers.openai.com/api/docs/guides/agents-api/overview)
documents API billing and managed sessions; this prototype has no evidence that
it accepts the local plan-sharing grant. The official Agents SDK is therefore the
framework compared here. Hosted website identity registration is a separate
capability from hosted inference authorization.
