# Adapter comparison and evidence

Checked against current primary documentation on **September 30, 2026**. This is
an implementation comparison, not a claim of successful provider access. As of
this document's initial implementation, the contract tests use mocked HTTP;
**no real authorized inference has been demonstrated by these tests**. Consult
[verification record](evidence.md) for the separate live evidence and unresolved registration/consent
gates. A local success does not establish hosted visitor eligibility.

## Why the official implementation is the Agents SDK

OpenAI distinguishes an application-owned SDK runner from its hosted Agents API.
The hosted API quickstart requires a platform application API key with agent and
Responses permissions and calls `/v1/agents/sessions`. The ChatGPT plan-usage
documentation authorizes an app-issued OAuth bearer at `/v1/responses`; it does
not establish that `/agents/sessions` accepts that grant. This prototype therefore
uses the official **OpenAI Agents SDK**, running in the application, with a custom
model implementation. It does not attempt the hosted Agents API with a subscriber
credential or use an operator key to make it work.

Sources: [runtime comparison](https://developers.openai.com/api/docs/guides/agents),
[hosted API prerequisites](https://developers.openai.com/api/docs/guides/agents-api/quickstart),
[SDK models and providers](https://developers.openai.com/api/docs/guides/agents/models),
[plan models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference).

## What is shared, and what is actually compared

All adapters share identical instructions, the same selected account/model slug,
EDGAR tool schemas and dispatch function, sequential tool execution, bounded
history, namespace checks, and the same HTTP/SSE implementation. Each request uses
an explicit app-issued bearer and the public OpenAI origin. There is no provider
selection by environment variable, alternate endpoint, automatic retry, or billing
fallback. A selected model must come from the selected account's model catalog;
catalog visibility is not proof that a subsequent inference request will succeed.

| Dimension | Direct Responses loop | Pydantic AI | OpenAI Agents SDK |
| --- | --- | --- | --- |
| Orchestration | Application `while` loop requests, dispatches, repeats | Actual `Agent.run` schedules registered `Tool.from_schema` tools and repeats model requests | Actual `Runner.run` resolves registered `FunctionTool` calls and repeats model requests |
| Model integration | Calls shared transport directly | Custom `pydantic_ai.models.Model.request` | Custom `agents.models.interface.Model.get_response` |
| Tool arguments | Shared JSON Schema validation before dispatch | Same validation before native `ToolCallPart` construction; native context preserves call ID | Same validation before native `ResponseFunctionToolCall`; native `ToolContext` preserves call ID |
| History | Shared raw Responses journal | Journal is authoritative; native messages drive the current run's orchestration | Same journal; SDK state is not persisted or used for HTTP response chaining |
| Browser streaming | Shared SSE text events | Same SSE sink while `Agent.run` awaits each completed model response | Same SSE sink while `Runner.run` awaits each completed model response |
| Failure handling | Errors propagate directly | Request limits translated to common errors; retries disabled | Request limits translated; SDK-wrapped tool failures unwrapped to retain original codes |
| Tracing | No tracing client | Explicit per-agent instrumentation disabled | SDK tracing disabled globally and per run; sensitive-data tracing disabled |
| Usage | Raw provider usage object or unknown | Same raw usage; no framework estimates shown | Same raw usage; SDK token accounting populated only from returned usage |
| Benefit here | Small, visible control flow and minimum machinery | Typed agent/tool ecosystem; useful if this grows into typed validation/workflows | Official agent runner, tool contexts, guards and handoffs if future scope needs them |
| Additional maintenance | Own loop and limits | Maintain the custom model bridge against pinned framework interfaces | Maintain model bridge, local tool identity conversion and exception translation |

This comparison deliberately does **not** exercise each framework's ordinary
OpenAI provider defaults or native streaming event abstraction. Transport
compatibility is supplied by this application's bridge. It also does not claim
that the frameworks' native persisted-history formats are interchangeable. The
common journal makes switching safe without translating away Responses reasoning
items or encrypted continuation data. Framework callbacks correlate outputs with
validated call IDs; they cannot select an arbitrary tool or add a new credential.

The frameworks are not aliases for the direct loop: their runners perform model
steps and tool invocation. Their model bridges convert completed Responses into
native call/text objects. The shared transport streams text to the browser in real
time; a completed native model step is released only after the Responses terminal
event. The `stream_response` native Agents SDK entry point is intentionally not
implemented; this integration uses `Runner.run` plus the transport event sink.

Pydantic sources: [current model/provider documentation source](https://github.com/pydantic/pydantic-ai/blob/main/docs/models/overview.md),
[custom tool schemas and context](https://github.com/pydantic/pydantic-ai/blob/main/docs/tools-advanced.md),
[model interface](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_ai_slim/pydantic_ai/models/__init__.py).
Installed versions are pinned in `uv.lock`; the resolution cutoff is September 23,
2026. Tested interfaces: `pydantic-ai-slim 2.47.0`, `openai-agents 0.22.3`,
`openai 3.18.0`, `httpx 0.28.1`.

## Wire contract and safety boundaries

The transport sends only `model`, `instructions`, array `input`, `store:false`,
`stream:true`, optional namespaced `tools`, and
`include:["reasoning.encrypted_content"]`. It never sends SDK defaults such as
temperature, maximum output tokens, stored conversations or HTTP
`previous_response_id`. Tools are function definitions nested under the `edgar`
namespace. A returned call must name that exact namespace and an allowed function,
contain schema-valid JSON, and have a new nonempty call ID. Hosted retrieval,
hosted MCP, `tool_search`, shell execution and custom tools are absent.

The application replays full history, including raw reasoning items and completed
tool outputs, because this route does not store conversations. Failed runs leave
the caller's supplied history unchanged. Citation cards come from retrieval
results, including prior results on follow-up questions; model-written Markdown
links should not be treated as independent proof of retrieval.

Limits are 8 model requests, 20 tool calls, 180 seconds per run, 6,000 question
characters, 350 KB serialized request content, 65 KB per tool result and 2 MB
per streamed model response. No inference retry is automatic. `response.failed`,
`response.incomplete`, explicit stream errors, malformed output and disconnects
never become a successful answer. Partial text is provisional until the enclosing
chat request completes. Allowance and admission errors preserve structured codes,
HTTP status when available, error parameter and provider request ID.

Sources: [preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations),
[function namespaces](https://developers.openai.com/api/docs/guides/function-calling#defining-namespaces),
[errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery),
[reasoning context with stateless Responses](https://developers.openai.com/api/docs/guides/reasoning).

## Repeatable acceptance scenarios

Use the same signed-in account, account-listed model and fresh conversation for
each adapter. Keep the exact question and tool evidence when comparing results.
The retrieval cache can change timing: label a run cold or warm, and rotate adapter
order before interpreting latency.

1. Ask: “Find Apple's latest 10-K and cite the filing date and accession number.”
   Expect real company/filing lookup and links to the actual SEC filing.
2. Ask: “Using that filing, summarize two risk factors and cite the passages.”
   Expect preserved conversation context, passage retrieval, and filing sources.
3. Ask about a known S-1 issuer, then a 10-Q reporting period. Confirm the requested
   filing form and period rather than accepting the most recent filing of any kind.
4. Ask a question that cannot be answered from available filings. Expect an honest
   limitation, including SEC denial/unavailability when it occurs.
5. Switch frameworks in a completed conversation and ask a follow-up. Confirm the
   same prior sources remain available and no other account's history appears.
6. Disconnect a stream or invalidate the app grant. Expect an explicit failure and
   no saved completed assistant turn; test allowance limits with fixtures rather
   than deliberately exhausting a user's allowance.

Contract tests run with:

```sh
uv run python -m pytest tests/test_adapters.py -q
uv run ruff check edgar/adapters.py edgar/transport.py tests/test_adapters.py
uv run ruff format --check edgar/adapters.py edgar/transport.py tests/test_adapters.py
```

The initial suite has **55 passing HTTP-mocked cases**. It exercises actual native
framework runners with a fake provider transport; it never substitutes a fake
Pydantic agent or a fake Agents runner. It covers wire serialization, tools and
history, account-isolated parallel calls, missing bearer/no environment fallback,
catalog slugs, schema and namespace rejection, duplicate calls, call/turn/output
limits, interrupted streams, terminal failures, and unknown usage reporting.
OAuth state/PKCE/identity/refresh tests belong to the auth module's separate suite.

Each successful real run returns adapter/model, model-request and tool-call counts,
provider request IDs, elapsed milliseconds and time to first text, plus raw usage
for each model step. No live latency, model quality ranking, allowance balance or
dollar estimate is claimed here. Token counts are not a conversion into ChatGPT
plan credits or a monetary bill. Add real comparison observations only after
authorized app consent and successful provider completion, keeping that evidence
separate from this contract suite.

## Why not Codex app-server as the third baseline

OpenAI also documents [Codex app-server with a plan-usage OAuth bearer](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server).
It provides a local orchestration process with thread history. The documented
configuration uses stdio and HTTP/SSE; the application owns token renewal and must
restart app-server with the refreshed credential before resuming the thread.
It is a viable separate local comparison, but adds process lifecycle and a broader
tool configuration to this small read-only filing app. The direct loop is the
smaller baseline for measuring the two library runners against the same wire
contract. No app-server inference or hosted eligibility is claimed here.
