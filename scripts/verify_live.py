"""Run six bounded live turns through an already-consenting browser session.

Cookies never leave the browser. OAuth tokens remain on the application server.
This command does not sign in, retry, change accounts, or use SDK credentials.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ADAPTERS = ["direct", "pydantic-ai", "openai-agents"]
SOURCE = (
    "https://www.sec.gov/Archives/edgar/data/320193/"
    "000032019325000079/aapl-20250927.htm"
)

# This same script can be evaluated by any authorized browser automation on the
# app tab. It starts asynchronously so browser command timeouts cannot retry turns.
# Only the normalized, allowlisted result is exposed on window; answers, cookies,
# conversations, account details and model-catalog bodies are never exported.
BROWSER_SCRIPT = r"""
(() => {
  const config = __CONFIG__;
  if (window.__edgarAcceptanceRun?.status === 'running') return {started: false};
  const report = {
    schema_version: 1, status: 'running', started_at: new Date().toISOString(),
    run_id: config.runId,
    model: config.model, versions: config.versions, origin: config.origin,
    mode: config.checkOnly ? 'preflight' : 'live_app_http',
    turns_attempted: 0, turns: [], real_provider_inference: false,
    completion_boundary: 'app_completed_after_server_verified_response.completed_and_eof'
  };
  window.__edgarAcceptanceRun = report;
  const controllers = new Set();
  window.__edgarAcceptanceCancel = runId => {
    if (runId !== report.run_id) return;
    report.cancelled = true;
    for (const controller of controllers) controller.abort();
  };
  const fail = code => { throw new Error(code); };
  const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  const normal = text => text.replace(/\s+/g, ' ').trim();
  const codes = new Set([
    'wrong_app_tab', 'consent_required', 'adapter_unavailable', 'model_not_eligible',
    'http_error', 'invalid_json', 'retrieval_failed', 'invalid_source', 'invalid_stream',
    'stream_interrupted', 'stream_error', 'duplicate_completion', 'event_after_completion',
    'tool_failed', 'invalid_telemetry', 'missing_filing_tool', 'missing_citation',
    'unsupported_quote', 'history_not_preserved', 'invalid_conversation', 'invalid_usage',
    'request_timeout', 'cancelled'
  ]);
  async function request(path, body, consume) {
    if (report.cancelled) fail('cancelled');
    const controller = new AbortController();
    controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), body?.adapter ? 190000 : 50000);
    try {
      const response = await fetch(path, {
        method: body ? 'POST' : 'GET', credentials: 'same-origin',
        redirect: 'error', cache: 'no-store', signal: controller.signal,
        ...(body ? {headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body)} : {})
      });
      if (!response.ok) fail('http_error');
      return await consume(response);
    } catch (error) {
      if (controller.signal.aborted) fail(report.cancelled ? 'cancelled' : 'request_timeout');
      throw error;
    } finally {
      clearTimeout(timer);
      controllers.delete(controller);
    }
  }
  const json = (path, body) => request(path, body, async response => {
    const text = await response.text();
    if (text.length > 100000) fail('invalid_json');
    try { return JSON.parse(text); } catch { fail('invalid_json'); }
  });
  function usage(raw, count) {
    if (raw?.reporting !== 'provider_tokens_only' || !Array.isArray(raw.responses) ||
        raw.responses.length !== count) fail('invalid_usage');
    return raw.responses.map(item => {
      if (item === null) return {reporting: 'unavailable'};
      if (typeof item !== 'object' || Array.isArray(item)) fail('invalid_usage');
      const result = {};
      for (const key of ['input_tokens', 'output_tokens', 'total_tokens']) {
        if (item[key] !== undefined) {
          if (!integer(item[key])) fail('invalid_usage');
          result[key] = item[key];
        }
      }
      return {reporting: Object.keys(result).length ? 'provider_tokens' : 'unavailable',
              ...result};
    });
  }
  async function turn(adapter, model, message, conversation, followup, passages) {
    const started = performance.now();
    let firstText = null, completed = null, buffer = '', bytes = 0;
    const tools = [], pending = new Map();
    report.turns_attempted++;
    if (report.real_provider_inference === false) report.real_provider_inference = null;
    await request('/api/edgar/chat', {
      adapter, model, message, ...(conversation ? {conversation_id: conversation} : {})
    }, async response => {
      if (!response.headers.get('content-type')?.includes('text/event-stream') ||
          !response.body) fail('invalid_stream');
      const reader = response.body.getReader(), decoder = new TextDecoder('utf-8', {fatal: true});
      function frame(raw) {
        let event = 'message';
        const data = [];
        for (const line of raw.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
        }
        if (!data.length) return; // SSE comments/heartbeats carry no evidence.
        let payload;
        try { payload = JSON.parse(data.join('\n')); } catch { fail('invalid_stream'); }
        if (!payload || typeof payload !== 'object') fail('invalid_stream');
        if (event === 'error') fail('stream_error'); // Also rejects errors after completion.
        if (completed)
          fail(event === 'completed' ? 'duplicate_completion' : 'event_after_completion');
        if (event === 'token') {
          if (typeof payload.delta !== 'string') fail('invalid_stream');
          if (payload.delta && firstText === null) firstText = performance.now() - started;
        } else if (event === 'tool') {
          if (!['search_companies', 'list_filings', 'filing_passages', 'company_facts']
                .includes(payload.name)) fail('invalid_stream');
          const count = pending.get(payload.name) || 0;
          if (payload.status === 'tool_start') pending.set(payload.name, count + 1);
          else if (payload.status === 'tool_end' && count > 0) {
            if (payload.ok !== true) fail('tool_failed');
            pending.set(payload.name, count - 1);
            tools.push(payload.name);
          } else fail('invalid_stream');
        } else if (event === 'completed') completed = payload;
        else fail('invalid_stream');
      }
      try {
        while (true) {
          const {value, done} = await reader.read();
          if (done) break;
          bytes += value.byteLength;
          if (bytes > 2000000) fail('invalid_stream');
          buffer += decoder.decode(value, {stream: true});
          // Keep a trailing CR until the next chunk, including split CRLF delimiters.
          buffer = buffer.replace(/\r\n/g, '\n');
          let split;
          while ((split = buffer.indexOf('\n\n')) !== -1) {
            frame(buffer.slice(0, split));
            buffer = buffer.slice(split + 2);
          }
        }
        buffer += decoder.decode();
        if (buffer.trim() || !completed) fail('stream_interrupted');
      } finally {
        await reader.cancel().catch(() => {});
      }
    });
    if ([...pending.values()].some(count => count !== 0)) fail('invalid_stream');
    const meta = completed.telemetry;
    if (meta?.adapter !== adapter || meta.model !== model ||
        !integer(meta.model_requests) || meta.model_requests < 1 || meta.model_requests > 8 ||
        meta.tool_calls !== tools.length || !number(meta.elapsed_ms) ||
        !(meta.first_text_ms === null || number(meta.first_text_ms))) fail('invalid_telemetry');
    if (!followup && !tools.includes('filing_passages')) fail('missing_filing_tool');
    const text = completed.text;
    if (typeof text !== 'string' || !text.trim() ||
        !Array.isArray(completed.citations) ||
        !completed.citations.some(citation => citation.url === config.source) ||
        !text.includes('](' + config.source + ')')) fail('missing_citation');
    const quotes = [...text.matchAll(/["“]([^"“”\n]{30,300})["”]/g)].map(m => normal(m[1]));
    const verifiedQuotes = quotes.filter(quote => passages.some(p => normal(p).includes(quote)));
    if (!verifiedQuotes.length) fail('unsupported_quote');
    if (followup && !text.includes(config.marker)) fail('history_not_preserved');
    const cid = completed.conversation_id;
    if (typeof cid !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(cid) ||
        (conversation && cid !== conversation)) fail('invalid_conversation');
    report.turns.push({
      adapter, turn: followup ? 'followup' : 'initial', model,
      app_completed: true, clean_eof: true, source_urls: [config.source],
      successful_tools: tools, verified_quote_count: verifiedQuotes.length,
      history_verified: followup, model_requests: meta.model_requests,
      elapsed_ms: Math.round(performance.now() - started),
      first_text_ms: firstText === null ? null : Math.round(firstText),
      server_elapsed_ms: meta.elapsed_ms,
      provider_usage: usage(completed.usage, meta.model_requests)
    });
    report.real_provider_inference = true;
    return cid;
  }
  (async () => {
    if (location.origin !== config.origin || location.pathname !== '/chat-with-edgar/' ||
        location.search || location.hash) fail('wrong_app_tab');
    const status = await json('/api/edgar/status');
    if (status.authenticated !== true || status.inference_enabled !== true)
      fail('consent_required');
    if (!config.adapters.every(id => status.adapters?.some(a => a.id === id && a.available)))
      fail('adapter_unavailable');
    const catalog = await json('/api/edgar/models');
    if (!catalog.models?.some(model => model.id === config.model)) fail('model_not_eligible');
    if (config.checkOnly) { report.status = 'passed'; return; }
    const args = {cik: '0000320193', accession: '0000320193-25-000079',
                  document: 'aapl-20250927.htm',
                  query: 'supply chain tariffs competition', limit: 4};
    const baseline = await json('/api/edgar/tools', {name: 'filing_passages', arguments: args});
    if (baseline.ok !== true || !Array.isArray(baseline.data) || !baseline.data.length)
      fail('retrieval_failed');
    if (baseline.filing?.url !== config.source || baseline.filing.form !== '10-K' ||
        baseline.filing.cik !== args.cik || baseline.filing.accession !== args.accession ||
        baseline.filing.document !== args.document) fail('invalid_source');
    const passages = baseline.data.map(p => p.text);
    if (!passages.every(p => typeof p === 'string')) fail('invalid_source');
    const initial = 'Use filing_passages with these exact arguments: ' + JSON.stringify(args) +
      '. Describe one supply-chain risk in this Apple 10-K in at most 100 words. Include a ' +
      'verbatim 12–20 word quote inside double quotes and a Markdown link to the source. ' +
      'Remember the test label ' + config.marker + ' for my next question; do not repeat it now.';
    const followup = 'For the same filing and risk, give one verbatim 12–20 word supporting ' +
      'quote inside double quotes and its Markdown source link. Repeat the test label ' +
      'from my previous message. Keep this answer under 100 words.';
    const conversations = new Set();
    for (const adapter of config.adapters) {
      report.current_adapter = adapter;
      report.current_turn = 'initial';
      const conversation = await turn(adapter, config.model, initial, null, false, passages);
      if (conversations.has(conversation)) fail('invalid_conversation');
      conversations.add(conversation);
      report.current_turn = 'followup';
      await turn(adapter, config.model, followup, conversation, true, passages);
    }
    report.status = 'passed';
  })().catch(error => {
    report.status = 'failed';
    report.error_code = codes.has(error.message) ? error.message : 'unexpected_browser_failure';
  }).finally(() => {
    report.finished_at = new Date().toISOString();
    delete window.__edgarAcceptanceCancel;
  });
  return {started: true, run_id: report.run_id};
})()
"""


def build_script(
    origin: str, model: str, *, check_only: bool = False, run_id: str | None = None
) -> str:
    parsed = urlsplit(origin)
    if not (
        origin == "https://evanoman.com"
        or (
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port is not None
            and 1024 <= parsed.port <= 65535
            and origin == f"http://127.0.0.1:{parsed.port}"
        )
    ):
        raise ValueError("Use the exact app origin: loopback HTTP or https://evanoman.com")
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,160}", model):
        raise ValueError("Supply the eligible model slug shown by this app")
    versions = {"source": "acceptance_runner_environment"}
    for package in ("pydantic-ai-slim", "openai-agents", "openai"):
        versions[package] = importlib.metadata.version(package)
    return BROWSER_SCRIPT.replace(
        "__CONFIG__",
        json.dumps(
            {
                "origin": origin,
                "model": model,
                "source": SOURCE,
                "marker": "EDGAR-LIVE-" + secrets.token_hex(6),
                "adapters": ADAPTERS,
                "versions": versions,
                "checkOnly": check_only,
                "runId": run_id or secrets.token_hex(12),
            }
        ),
    )


def browser_eval(session: str, script: str) -> dict:
    """Never surface raw CLI errors: those can contain a private browser URL."""
    try:
        result = subprocess.run(
            ["agent-browser", "--session", session, "--json", "eval", "--stdin"],
            input=script,
            capture_output=True,
            text=True,
            timeout=35,
            check=True,
        )
        payload = json.loads(result.stdout)
        if payload.get("success") is not True:
            raise ValueError
        value = payload["data"]["result"]
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        raise RuntimeError("Browser command failed; no automatic retry was attempted") from None


def run_browser(session: str, script: str, output: Path, *, run_id: str) -> dict:
    if output.exists():
        raise ValueError("Evidence file already exists; choose a new path for this run")
    try:
        # Dispatch may succeed even when its CLI reply is lost. Cover startup with
        # cancellation too, scoped to this run so a rejected start preserves others.
        started = browser_eval(session, script)
        if started.get("started") is not True:
            raise RuntimeError("An acceptance run is already active in this tab")
        if started.get("run_id") != run_id:
            raise RuntimeError("The browser did not confirm this acceptance run")
        deadline = time.monotonic() + 1250
        while time.monotonic() < deadline:
            report = browser_eval(session, "window.__edgarAcceptanceRun")
            if report.get("run_id") != run_id:
                raise RuntimeError("The browser tab no longer contains this acceptance run")
            if report.get("status") != "running":
                output.parent.mkdir(parents=True, exist_ok=True)
                # The JS exports only allowlisted telemetry, not raw app responses.
                with output.open("x") as file:
                    json.dump(report, file, indent=2)
                    file.write("\n")
                return report
            time.sleep(1)
        raise RuntimeError("Acceptance run timed out; no further turns were scheduled")
    except (Exception, KeyboardInterrupt):
        # Cancel only this acceptance script's fetches, never close a borrowed browser.
        try:
            browser_eval(
                session,
                "(() => { window.__edgarAcceptanceCancel?.("
                + json.dumps(run_id)
                + "); return {cancelled: true}; })()",
            )
        except RuntimeError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", help="Existing, authenticated agent-browser session")
    parser.add_argument("--origin", default="http://127.0.0.1:19371")
    parser.add_argument("--model", required=True, help="An eligible model slug from the app")
    parser.add_argument("--check-only", action="store_true", help="No retrieval or inference")
    parser.add_argument(
        "--emit-script", action="store_true", help="Print JS for another authenticated browser"
    )
    parser.add_argument(
        "--output", type=Path, default=Path(".mission/evidence/live-inference.json")
    )
    args = parser.parse_args()
    if not args.emit_script and not args.session:
        parser.error("--session is required unless --emit-script is used")
    try:
        run_id = secrets.token_hex(12)
        script = build_script(args.origin, args.model, check_only=args.check_only, run_id=run_id)
        if args.emit_script:
            print(script)
            return 0
        report = run_browser(args.session, script, args.output, run_id=run_id)
        print(json.dumps({
            "status": report["status"], "turns_attempted": report["turns_attempted"],
            "error_code": report.get("error_code"), "evidence": str(args.output),
        }))
        return 0 if report["status"] == "passed" else 1
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
