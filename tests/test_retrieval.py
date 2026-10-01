import asyncio
import hashlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from edgar import retrieval
from edgar.retrieval import (
    SECClient,
    dispatch,
    extract_text,
    filing_url,
    normalize_cik,
    select_passages,
)

CIK = "0000320193"
ACCESSION = "0000320193-25-000079"
DOCUMENT = "aapl-20250927.htm"
SUBMISSIONS = f"https://data.sec.gov/submissions/CIK{CIK}.json"
ARCHIVE = filing_url(CIK, ACCESSION, DOCUMENT)


def submissions():
    return {
        "cik": 320193,
        "name": "Apple Inc.",
        "tickers": ["AAPL"],
        "filings": {
            "recent": {
                "accessionNumber": [ACCESSION],
                "form": ["10-K"],
                "filingDate": ["2025-10-31"],
                "primaryDocument": [DOCUMENT],
                "reportDate": ["2025-09-27"],
            },
            "files": [],
        },
    }


@pytest.fixture
def no_delay(monkeypatch):
    monkeypatch.setattr(retrieval.asyncio, "sleep", AsyncMock())


@pytest.mark.parametrize(
    "cik", [True, 0, -1, "", "12345678901", "1/../2", "https://evil.test", None, 1.2]
)
def test_invalid_cik(cik):
    with pytest.raises(retrieval.RetrievalError):
        normalize_cik(cik)


@pytest.mark.parametrize(
    "document",
    [
        "../x.htm",
        "x/1.htm",
        "%2e%2e.htm",
        "x.htm?url=http://evil",
        "//evil/x.htm",
        "a..htm",
        "a.pdf",
    ],
)
def test_invalid_document_path(document):
    with pytest.raises(retrieval.RetrievalError):
        filing_url(CIK, ACCESSION, document)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.sec.gov/files/company_tickers.json",
        "https://data.sec.gov.evil.test/submissions/CIK0000320193.json",
        "https://data.sec.gov@127.0.0.1/submissions/CIK0000320193.json",
        "https://www.sec.gov:443/files/company_tickers.json",
        "https://www.sec.gov/files/company_tickers.json?target=evil",
        "https://www.sec.gov/Archives/edgar/data/1/000000000000000001/../x.htm",
        "https://127.0.0.1/",
        "https://data.sec.gov/submissions/CIK0000320193.json#x",
    ],
)
async def test_ssrf_urls_never_requested(url):
    transport = httpx.MockTransport(lambda _: pytest.fail("Network must not be reached"))
    async with SECClient(transport=transport) as client:
        with pytest.raises(retrieval.RetrievalError, match="SEC"):
            await client._fetch(url)


async def test_metadata_passages_and_cached_provenance(no_delay):
    html = (
        b"<html><head><title>ignore</title></head><body><p>Revenue increased from "
        b"services and products.</p><script>steal secrets</script><ix:hidden>hidden fact"
        b"</ix:hidden><p>Supply chain risk affected operations.</p></body></html>"
    )
    calls = []

    def handle(request):
        calls.append(request)
        if str(request.url) == SUBMISSIONS:
            return httpx.Response(200, json=submissions())
        assert str(request.url) == ARCHIVE
        return httpx.Response(200, content=html)

    async with SECClient(transport=httpx.MockTransport(handle)) as client:
        listing = await dispatch("list_filings", {"cik": "320193"}, client=client)
        assert listing["ok"]
        assert listing["data"][0]["url"] == ARCHIVE
        assert listing["data"][0]["report_date"] == "2025-09-27"
        args = {"cik": CIK, "accession": ACCESSION, "document": DOCUMENT, "query": "supply risk"}
        result = await dispatch("filing_passages", args, client=client)
        cached = await dispatch("filing_passages", args, client=client)
    assert len(calls) == 2
    assert all("https://evanoman.com" in request.headers["User-Agent"] for request in calls)
    assert all("authorization" not in request.headers for request in calls)
    assert result["untrusted_content"] is True
    assert "Supply chain risk" in result["data"][0]["text"]
    assert "steal secrets" not in result["data"][0]["text"]
    assert result["citations"][0]["url"] == ARCHIVE
    assert result["provenance"][-1]["sha256"] == hashlib.sha256(html).hexdigest()
    assert cached["provenance"][-1]["cache_hit"] is True
    assert cached["provenance"][-1]["retrieved_at"] == result["provenance"][-1]["retrieved_at"]


async def test_redirect_is_never_followed():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})

    async with SECClient(transport=httpx.MockTransport(handle)) as client:
        result = await dispatch("search_companies", {"query": "AAPL"}, client=client)
    assert len(calls) == 1
    assert result["error"]["code"] == "redirect_blocked"


@pytest.mark.parametrize(
    "name,args",
    [
        ("shell", {"command": "anything"}),
        ("search_companies", {"query": "AAPL", "url": "http://evil.test"}),
        ("search_companies", {"query": "AAPL", "limit": 11}),
        ("search_companies", {"query": "AAPL", "limit": True}),
        ("search_companies", {"query": ""}),
        ("search_companies", {"query": "x" * 301}),
        ("list_filings", {"cik": CIK, "forms": ["not a form"]}),
        ("list_filings", {"cik": CIK, "forms": []}),
        ("list_filings", {"cik": CIK, "limit": 21}),
        ("list_filings", {"cik": CIK, "forms": "10-K"}),
        ("filing_passages", {"cik": CIK}),
        ("company_facts", {"cik": CIK, "concept": "../x"}),
        ("company_facts", {"cik": CIK, "limit": 0}),
    ],
)
async def test_invalid_tool_arguments_make_no_request(name, args):
    async with SECClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("No request"))
    ) as client:
        result = await dispatch(name, args, client=client)
    assert result["ok"] is False
    assert result["error"]["code"] in {"unknown_tool", "invalid_arguments"}


async def test_primary_document_must_match_submissions():
    async with SECClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=submissions()))
    ) as client:
        result = await dispatch(
            "filing_passages",
            {"cik": CIK, "accession": ACCESSION, "document": "invented.htm", "query": "risk"},
            client=client,
        )
    assert result["error"]["code"] == "filing_not_found"


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (403, "sec_access_limited", True),
        (429, "sec_access_limited", True),
        (404, "not_found", False),
        (503, "sec_unavailable", True),
    ],
)
async def test_http_error_behavior(status, code, retryable):
    async with SECClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, text="untrusted upstream error")
        )
    ) as client:
        result = await dispatch("search_companies", {"query": "AAPL"}, client=client)
    assert result["error"]["code"] == code
    assert result["error"]["retryable"] is retryable
    assert "upstream" not in json.dumps(result)


async def test_response_size_cap(monkeypatch):
    monkeypatch.setattr(retrieval, "MAX_RESPONSE_BYTES", 20)
    async with SECClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 21))
    ) as client:
        result = await dispatch("search_companies", {"query": "AAPL"}, client=client)
    assert result["error"]["code"] == "resource_too_large"


@pytest.mark.parametrize("body", [b"broken", b"[]", b"null", b'{"0":{"ticker":"AAPL","title":7}}'])
async def test_malformed_sec_json(body):
    async with SECClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    ) as client:
        result = await dispatch("search_companies", {"query": "AAPL"}, client=client)
    assert result["error"]["code"] == "invalid_sec_data"


async def test_mismatched_submission_identity():
    data = submissions()
    data["cik"] = 1
    async with SECClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    ) as client:
        result = await dispatch("list_filings", {"cik": CIK}, client=client)
    assert result["error"]["code"] == "invalid_sec_data"


async def test_malformed_column_lengths():
    data = submissions()
    data["filings"]["recent"]["form"] = []
    async with SECClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    ) as client:
        result = await dispatch("list_filings", {"cik": CIK}, client=client)
    assert result["error"]["code"] == "invalid_sec_data"


async def test_malicious_history_filename_not_followed(no_delay):
    data = submissions()
    data["filings"]["files"] = [{"name": "https://evil.test/secret.json"}]
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=data)

    async with SECClient(transport=httpx.MockTransport(handle)) as client:
        result = await dispatch("list_filings", {"cik": CIK, "forms": ["S-1"]}, client=client)
    assert len(calls) == 1
    assert result["error"]["code"] == "invalid_sec_data"


async def test_xbrl_has_period_unit_and_explicit_evidence_type():
    data = {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "taxonomy": "us-gaap",
        "tag": "Assets",
        "units": {
            "USD": [
                {
                    "val": 123,
                    "accn": ACCESSION,
                    "end": "2025-09-27",
                    "filed": "2025-10-31",
                    "form": "10-K",
                }
            ]
        },
    }
    async with SECClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    ) as client:
        result = await dispatch("company_facts", {"cik": CIK, "concept": "Assets"}, client=client)
    assert result["ok"]
    assert result["data"][0]["value"] == 123
    assert result["data"][0]["unit"] == "USD"
    assert result["data"][0]["start"] is None
    assert "not narrative" in result["evidence_type"]
    assert result["citations"][0]["data_url"].endswith("/Assets.json")
    assert result["citations"][0]["url"].endswith(f"/{ACCESSION}-index.html")


async def test_timeout_is_safe():
    def handle(request):
        raise httpx.ReadTimeout("upstream secrets", request=request)

    async with SECClient(transport=httpx.MockTransport(handle)) as client:
        result = await dispatch("search_companies", {"query": "AAPL"}, client=client)
    assert result["error"]["code"] == "sec_unavailable"
    assert "secrets" not in json.dumps(result)


async def test_cancellation_is_not_reported_as_tool_success():
    def handle(request):
        raise asyncio.CancelledError

    async with SECClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(asyncio.CancelledError):
            await dispatch("search_companies", {"query": "AAPL"}, client=client)


def test_hidden_content_removed_without_losing_inline_words():
    html = (
        '<p>Sup<b>ply</b> risk &amp; revenue.</p><div style="display: none">hidden</div>'
        "<ix:header><ix:hidden>metadata</ix:hidden></ix:header><p>Next sentence.</p>"
    )
    assert extract_text(html) == "Supply risk & revenue.\nNext sentence."


def test_no_matching_evidence_does_not_return_irrelevant_text():
    assert select_passages("Revenue increased this year.", "litigation", 4, {"id": "a"}) == []


def test_passage_offsets_match_extracted_text():
    text = "Supply chain risks increase operating expenses. " * 100
    passages = select_passages(text, "supply chain risk", 3, {"id": "a", "url": ARCHIVE})
    assert len(passages) <= 3
    for passage in passages:
        assert passage["text"] == text[passage["start_char"] : passage["end_char"]]
        assert len(passage["text"]) <= 1500


def test_all_tool_schemas_are_closed():
    assert len(retrieval.TOOL_DEFINITIONS) == 4
    assert all(
        tool["parameters"]["additionalProperties"] is False for tool in retrieval.TOOL_DEFINITIONS
    )


async def test_request_start_spacing(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(retrieval.asyncio, "sleep", sleep)
    monkeypatch.setattr(retrieval.time, "monotonic", lambda: 100.1)
    async with SECClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
    ) as client:
        client._last_request = 100.0
        await client._fetch(SUBMISSIONS)
    assert sleep.await_args.args[0] == pytest.approx(0.4)


async def test_cache_eviction_is_bounded(monkeypatch, no_delay):
    monkeypatch.setattr(retrieval, "MAX_CACHE_BYTES", 40)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, content=b"x" * 30)

    async with SECClient(transport=httpx.MockTransport(handle)) as client:
        await client._fetch(SUBMISSIONS)
        await client._fetch("https://www.sec.gov/files/company_tickers.json")
        assert len(client._cache) == 1
        assert client._cache_bytes == 30
        await client._fetch(SUBMISSIONS)
    assert len(calls) == 3


async def test_malformed_xbrl_period_returns_safe_error():
    data = {
        "cik": 320193,
        "taxonomy": "us-gaap",
        "tag": "Assets",
        "units": {
            "USD": [
                {
                    "val": 123,
                    "accn": ACCESSION,
                    "start": [],
                    "end": "2025-09-27",
                    "filed": "2025-10-31",
                    "form": "10-K",
                }
            ]
        },
    }
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    async with SECClient(transport=transport) as client:
        result = await dispatch("company_facts", {"cik": CIK, "concept": "Assets"}, client=client)
    assert result["error"]["code"] == "invalid_sec_data"
