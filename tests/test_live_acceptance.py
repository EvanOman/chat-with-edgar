"""Execute the real browser acceptance JS against synthetic HTTP streams, offline.

These fixtures test acceptance decisions; they are not live inference evidence.
Node supplies browser-compatible fetch Response/ReadableStream implementations.
"""

import json
import subprocess

import pytest

from scripts import verify_live
from scripts.verify_live import ADAPTERS, SOURCE, browser_eval, build_script, run_browser

FIXTURE = r"""
const calls = [];
globalThis.window = {};
globalThis.location = {origin: config.origin, pathname: '/chat-with-edgar/', search: '', hash: ''};
const quote = 'Supply chains can be disrupted by natural disasters and other events ' +
  'outside our control';
const frame = (event, data) => 'event: ' + event + '\r\ndata: ' + JSON.stringify(data) + '\r\n\r\n';
globalThis.fetch = async (path, options) => {
  const body = options.body ? JSON.parse(options.body) : null;
  calls.push({path, body, credentials: options.credentials});
  if (path === '/api/edgar/status') return Response.json({
    authenticated: scenario !== 'no_consent', inference_enabled: true,
    email: 'PRIVATE_EMAIL', active_registration: 'PRIVATE_REGISTRATION',
    adapters: config.adapters.map(id => ({id, available: true}))
  });
  if (path === '/api/edgar/models') return Response.json({
    models: [{id: scenario === 'ineligible' ? 'other' : config.model}]
  });
  if (path === '/api/edgar/tools') return Response.json({
    ok: true, data: [{text: quote + '.'}],
    filing: {url: config.source, form: '10-K', ...body.arguments}
  });
  if (path !== '/api/edgar/chat') throw new Error('UNEXPECTED_REQUEST');
  const followup = Boolean(body.conversation_id);
  const cid = scenario === 'reused_conversation' ? 'one' : 'conversation-' + body.adapter;
  const tools = followup || scenario === 'missing_tool' ? [] : [
    frame('tool', {name: 'filing_passages', status: 'tool_start'}),
    frame('tool', {name: 'filing_passages', status: 'tool_end', ok: scenario !== 'tool_failed'})
  ];
  const completion = frame('completed', {
    conversation_id: cid,
    text: 'A risk: "' +
      (scenario === 'ungrounded' ? 'Invented evidence that the source never said' : quote)
      + '" [SEC](' +
      (scenario === 'wrong_citation' ? 'https://other.example/' : config.source) + ') '
      + (followup && scenario !== 'lost_history' ? config.marker : ''),
    citations: [{url: config.source}],
    usage: {reporting: 'provider_tokens_only', responses: [scenario === 'no_usage' ? null : {
      input_tokens: scenario === 'bad_usage' ? -2 : 123, output_tokens: 45,
      PRIVATE_EXTRA_FIELD: 'PRIVATE_VALUE'
    }]},
    telemetry: {adapter: body.adapter, model: body.model, model_requests: 1,
                tool_calls: tools.length / 2, elapsed_ms: 1, first_text_ms: 0,
                request_ids: ['PRIVATE_REQUEST_NOT_EXPORTED']}
  });
  let stream = tools.join('') + frame('token', {delta: 'A risk ⚡'});
  if (scenario !== 'missing_completion') stream += completion;
  if (scenario === 'late_error') stream += frame('error', {message: 'PRIVATE_FAILURE_BODY'});
  if (scenario === 'duplicate') stream += completion;
  if (scenario === 'truncated') stream += 'data: {"incomplete":';
  const bytes = new TextEncoder().encode(stream);
  return new Response(new ReadableStream({
    start(controller) {
      // Split every UTF-8 character and CRLF: the production parser handles both.
      for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
      controller.close();
    }
  }), {headers: {'Content-Type': 'text/event-stream'}});
};
eval(script);
for (let i = 0; i < 500 && window.__edgarAcceptanceRun.status === 'running'; i++) {
  await new Promise(resolve => setTimeout(resolve, 1));
}
console.log(JSON.stringify({report: window.__edgarAcceptanceRun, calls}));
"""


def run_fixture(scenario="ok", *, check_only=False):
    script = build_script("http://127.0.0.1:19371", "eligible-model", check_only=check_only)
    config = json.loads(script.split("const config = ", 1)[1].split(";\n", 1)[0])
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=(
            f"const script = {json.dumps(script)};\n"
            f"const config = {json.dumps(config)};\n"
            f"const scenario = {json.dumps(scenario)};\n" + FIXTURE
        ),
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    return json.loads(result.stdout)


def test_six_bounded_turns_same_model_fresh_history_and_redacted_evidence():
    fixture = run_fixture()
    report = fixture["report"]
    assert report["status"] == "passed"
    assert report["real_provider_inference"] is True
    assert report["turns_attempted"] == len(report["turns"]) == 6
    assert [turn["adapter"] for turn in report["turns"]] == [a for a in ADAPTERS for _ in range(2)]
    assert all(turn["source_urls"] == [SOURCE] for turn in report["turns"])
    assert all(turn["model"] == "eligible-model" for turn in report["turns"])
    assert all(turn["clean_eof"] and turn["verified_quote_count"] for turn in report["turns"])
    assert "PRIVATE" not in json.dumps(report)
    assert "conversation-" not in json.dumps(report)
    calls = [call["body"] for call in fixture["calls"] if call["path"].endswith("/chat")]
    for initial, followup in zip(calls[::2], calls[1::2], strict=True):
        assert "conversation_id" not in initial
        assert followup["conversation_id"] == "conversation-" + initial["adapter"]
        assert "EDGAR-LIVE-" not in followup["message"]  # Must come from server history.
    assert all(call["credentials"] == "same-origin" for call in fixture["calls"])


@pytest.mark.parametrize(
    ("scenario", "error", "attempts"),
    [
        ("no_consent", "consent_required", 0),
        ("ineligible", "model_not_eligible", 0),
        ("missing_completion", "stream_interrupted", 1),
        ("late_error", "stream_error", 1),
        ("duplicate", "duplicate_completion", 1),
        ("truncated", "stream_interrupted", 1),
        ("missing_tool", "missing_filing_tool", 1),
        ("tool_failed", "tool_failed", 1),
        ("wrong_citation", "missing_citation", 1),
        ("ungrounded", "unsupported_quote", 1),
        ("lost_history", "history_not_preserved", 2),
        ("bad_usage", "invalid_usage", 1),
        ("reused_conversation", "invalid_conversation", 3),
    ],
)
def test_stop_on_first_failure_without_retry_or_fallback(scenario, error, attempts):
    fixture = run_fixture(scenario)
    report = fixture["report"]
    assert report["status"] == "failed"
    assert report["error_code"] == error
    assert report["turns_attempted"] == attempts
    assert len([call for call in fixture["calls"] if call["path"].endswith("/chat")]) == attempts
    assert "PRIVATE" not in json.dumps(report)
    if attempts == 0:
        assert report["real_provider_inference"] is False
    elif not report["turns"]:
        assert report["real_provider_inference"] is None  # Attempted, not verified.


def test_usage_unknown_is_not_estimated_or_zero():
    report = run_fixture("no_usage")["report"]
    assert report["status"] == "passed"
    assert report["turns"][0]["provider_usage"] == [{"reporting": "unavailable"}]


def test_preflight_cannot_infer_or_retrieve():
    fixture = run_fixture(check_only=True)
    assert fixture["report"]["status"] == "passed"
    assert fixture["report"]["real_provider_inference"] is False
    assert fixture["report"]["turns_attempted"] == 0
    assert [call["path"] for call in fixture["calls"]] == [
        "/api/edgar/status", "/api/edgar/models"
    ]


@pytest.mark.parametrize("origin", [
    "https://unrelated.example", "http://localhost:19371", "http://127.0.0.1:19371/?secret=1",
    "https://evanoman.com@other.example", "http://127.0.0.1:80",
])
def test_origin_must_be_an_exact_allowed_app_origin(origin):
    with pytest.raises(ValueError):
        build_script(origin, "eligible-model")


def test_cli_error_does_not_echo_browser_private_data(monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "agent-browser", stderr="PRIVATE_BROWSER_URL")

    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(RuntimeError, match="no automatic retry") as failure:
        browser_eval("task-session", "test")
    assert "PRIVATE" not in str(failure.value)


def test_existing_evidence_is_preserved_without_starting_browser(tmp_path, monkeypatch):
    destination = tmp_path / "evidence.json"
    destination.write_text("original evidence")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("Browser started"))
    with pytest.raises(ValueError, match="already exists"):
        run_browser("task-session", "test", destination, run_id="this-run")
    assert destination.read_text() == "original evidence"


@pytest.mark.parametrize("startup", [RuntimeError("reply lost after dispatch"), {"started": False}])
def test_ambiguous_or_rejected_start_cancels_only_its_own_run(tmp_path, monkeypatch, startup):
    calls = []

    def evaluate(session, script):
        calls.append(script)
        if len(calls) == 1:
            if isinstance(startup, Exception):
                raise startup
            return startup
        return {"cancelled": True}

    monkeypatch.setattr(verify_live, "browser_eval", evaluate)
    with pytest.raises(RuntimeError):
        run_browser("task-session", "start", tmp_path / "evidence.json", run_id="own-run")
    assert len(calls) == 2
    assert '__edgarAcceptanceCancel?.("own-run")' in calls[1]
    assert not (tmp_path / "evidence.json").exists()


def test_browser_cancel_cannot_cancel_another_runs_request():
    script = build_script("http://127.0.0.1:19371", "eligible-model", run_id="own-run")
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=(
            "globalThis.window = {}; globalThis.location = {origin: 'http://127.0.0.1:19371',"
            "pathname: '/chat-with-edgar/', search: '', hash: ''};\n"
            "let active; globalThis.fetch = (path, options) => { active = options.signal; "
            "return new Promise((resolve, reject) => options.signal.addEventListener('abort', "
            "() => reject(new Error('aborted')))); };\n"
            f"eval({json.dumps(script)});\n"
            "window.__edgarAcceptanceCancel('someone-else');\n"
            "const preserved = !active.aborted;\n"
            "window.__edgarAcceptanceCancel('own-run');\n"
            "await new Promise(resolve => setTimeout(resolve, 0));\n"
            "console.log(JSON.stringify({preserved, aborted: active.aborted, "
            "report: window.__edgarAcceptanceRun}));"
        ),
        text=True, capture_output=True, timeout=10, check=True,
    )
    outcome = json.loads(result.stdout)
    assert outcome["preserved"] is True and outcome["aborted"] is True
    assert outcome["report"]["error_code"] == "cancelled"


@pytest.mark.parametrize("replace_at", ["start", "poll"])
def test_replaced_run_cannot_be_saved_as_this_runs_evidence(tmp_path, monkeypatch, replace_at):
    calls = []

    def evaluate(session, script):
        calls.append(script)
        if len(calls) == 1:
            return {"started": True, "run_id": "other-run" if replace_at == "start" else "own"}
        if "Cancel" in script:
            return {"cancelled": True}
        return {"status": "passed", "run_id": "other-run"}

    monkeypatch.setattr(verify_live, "browser_eval", evaluate)
    output = tmp_path / "evidence.json"
    with pytest.raises(RuntimeError, match="this acceptance run"):
        run_browser("task-session", "start", output, run_id="own")
    assert not output.exists()
    assert '__edgarAcceptanceCancel?.("own")' in calls[-1]
