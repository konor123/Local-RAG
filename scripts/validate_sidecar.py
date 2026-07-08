# -*- coding: utf-8 -*-
"""Compare vector search and SQLite FTS sidecar results for validation.

Usage:
    python scripts/validate_sidecar.py --query "fire alarm" --query "pump"
    python scripts/validate_sidecar.py --queries-file queries.txt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_queries(args: argparse.Namespace) -> list[str]:
    queries = list(args.query or [])
    if args.queries_file:
        with open(args.queries_file, "r", encoding="utf-8") as f:
            queries.extend(line.strip() for line in f if line.strip() and not line.startswith("#"))
    return queries or [
        "유도등 설치 기준",
        "불꽃감지기 카탈로그",
        "소방 펌프 점검",
    ]


def _sources(result: dict) -> set[str]:
    return {item.get("source", "") for item in result.get("results", []) if item.get("source")}


def compare_query(query: str, k: int) -> dict:
    from tools import search_content, search_metadata_content

    vector = search_content(query, k=k)
    fts = search_metadata_content(query, k=k)
    vector_sources = _sources(vector)
    fts_sources = _sources(fts)
    overlap = vector_sources & fts_sources
    union = vector_sources | fts_sources
    return {
        "query": query,
        "vector_count": vector.get("count", 0),
        "fts_count": fts.get("count", 0),
        "fts_disabled": bool(fts.get("disabled", False)),
        "source_overlap": len(overlap),
        "source_union": len(union),
        "overlap_ratio": round(len(overlap) / len(union), 3) if union else 0.0,
        "vector_sources": sorted(vector_sources)[:k],
        "fts_sources": sorted(fts_sources)[:k],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate SQLite FTS sidecar against vector search")
    parser.add_argument("--query", action="append", help="Query to compare; can be repeated")
    parser.add_argument("--queries-file", help="UTF-8 text file with one query per line")
    parser.add_argument("-k", type=int, default=5, help="Top-k results per engine")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a text table")
    args = parser.parse_args()

    queries = _load_queries(args)
    rows = [compare_query(query, args.k) for query in queries]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print("query\tvector\tfts\toverlap\tunion\tratio\tfts_disabled")
    for row in rows:
        print(
            f"{row['query']}\t{row['vector_count']}\t{row['fts_count']}\t"
            f"{row['source_overlap']}\t{row['source_union']}\t{row['overlap_ratio']}\t{row['fts_disabled']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
