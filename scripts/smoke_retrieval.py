"""Bounded live SEC retrieval evidence; no model, OAuth token, or inference cost.

Run from the repository root: uv run python scripts/smoke_retrieval.py
Add --snapshot to refresh the public curated site/data/snapshot.json artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edgar.retrieval import SECClient, dispatch  # noqa: E402


async def run(snapshot: bool) -> None:
    evidence = {
        "started_at": datetime.now(UTC).isoformat(),
        "real_sec_requests": True,
        "real_provider_inference": False,
        "tools": [],
    }
    artifact = {
        "generated_at": evidence["started_at"],
        "schema_version": 1,
        "description": "Curated SEC primary filing passages retrieved by Chat with EDGAR. "
        "A dated snapshot, not a live or exhaustive filing search.",
        "companies": [],
        "provenance": [],
    }

    async with SECClient() as client:

        async def call(name, arguments):
            start = time.monotonic()
            result = await dispatch(name, arguments, client=client)
            evidence["tools"].append(
                {
                    "name": name,
                    "arguments": arguments,
                    "ok": result["ok"],
                    "elapsed_ms": round((time.monotonic() - start) * 1000),
                    "result_count": len(result.get("data", [])),
                    "provenance": result.get("provenance", []),
                    "error": result.get("error"),
                }
            )
            if not result["ok"]:
                raise RuntimeError(
                    f"{name}: {result['error']['code']}: {result['error']['message']}"
                )
            artifact["provenance"].extend(result.get("provenance", []))
            return result

        plan = [("AAPL", ["10-K", "10-Q", "8-K"]), ("FIG", ["S-1", "10-K", "10-Q"])]
        if not snapshot:
            plan = [("AAPL", ["10-K"])]
        for ticker, forms in plan:
            search = await call("search_companies", {"query": ticker, "limit": 1})
            if not search["data"] or search["data"][0]["ticker"] != ticker:
                raise RuntimeError(f"SEC ticker lookup did not match {ticker}")
            company = {**search["data"][0], "filings": []}
            for form in forms:
                listing = await call(
                    "list_filings", {"cik": company["cik"], "forms": [form], "limit": 1}
                )
                if not listing["data"]:
                    raise RuntimeError(f"No {form} found for {ticker} in bounded filing history")
                filing = listing["data"][0]
                passages = {}
                queries = (
                    [
                        "iPhone Mac iPad Services",
                        "supply chain tariffs competition",
                        "net sales Products Services",
                    ]
                    if ticker == "AAPL"
                    else [
                        "platform design collaboration Dev Mode",
                        "revenue subscription customers",
                        "risk competition artificial intelligence",
                    ]
                )
                if form == "8-K":
                    queries = ["results operations financial earnings"]
                for query in queries if snapshot else queries[:1]:
                    result = await call(
                        "filing_passages",
                        {
                            "cik": company["cik"],
                            "accession": filing["accession"],
                            "document": filing["document"],
                            "query": query,
                            "limit": 4 if snapshot else 2,
                        },
                    )
                    for passage in result["data"]:
                        passages[passage["id"]] = passage
                if not passages:
                    raise RuntimeError("No real filing passages returned")
                company["filings"].append({**filing, "passages": list(passages.values())})
            artifact["companies"].append(company)
        facts = await call("company_facts", {"cik": "0000320193", "limit": 4})
        artifact["companies"][0]["facts"] = facts
        if not facts["data"]:
            raise RuntimeError("No real XBRL facts returned")

    evidence["completed_at"] = datetime.now(UTC).isoformat()
    evidence["status"] = "passed"
    unique = {}
    for source in artifact["provenance"]:
        key = (source["url"], source["sha256"])
        if key not in unique or not source["cache_hit"]:
            unique[key] = source
    artifact["provenance"] = list(unique.values())
    mission = ROOT / ".mission"
    mission.mkdir(exist_ok=True)
    (mission / "retrieval-smoke.json").write_text(json.dumps(evidence, indent=2) + "\n")
    if snapshot:
        (ROOT / "site" / "data").mkdir(exist_ok=True)
        (ROOT / "site" / "data" / "snapshot.json").write_text(json.dumps(artifact, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": "passed",
                "tools": len(evidence["tools"]),
                "unique_sec_resources": len(unique),
                "evidence": ".mission/retrieval-smoke.json",
                "snapshot": "site/data/snapshot.json" if snapshot else None,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", action="store_true", help="Refresh the curated public snapshot"
    )
    asyncio.run(run(parser.parse_args().snapshot))
