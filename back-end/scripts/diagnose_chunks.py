"""Per-chunk diagnostic for the validate_arb_chunks pipeline.

Walks every chunk produced by Document Intelligence for a given ASD doc and
records, for each chunk:

* the AOAI-assigned category
* the number of hits returned by the (category-filtered) hybrid+semantic search
* whether the unfiltered fallback was triggered
* the top-K policy headers + categories returned
* the parsed findings from the validate agent (count + Type breakdown)
* the raw findings JSON for offline inspection

Writes a JSON report next to the input doc and prints a compact summary table.

Console output is forced to UTF-8 so non-ASCII characters in chunk content
(em-dashes, smart quotes, etc.) do not crash on Windows code page 1252.

Usage:
    python -m scripts.diagnose_chunks <path-to-asd> [--max-chunks N] [--top-k K]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# UTF-8 stdout/stderr (Windows fix). Must run before any print/log calls.
# ---------------------------------------------------------------------------
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is None:
        continue
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        try:
            buf = getattr(_stream, "buffer", None)
            if buf is not None:
                setattr(sys, _stream_name,
                        io.TextIOWrapper(buf, encoding="utf-8", errors="replace",
                                         line_buffering=True))
        except Exception:  # noqa: BLE001
            pass

# Allow `python scripts/diagnose_chunks.py …` from the back-end/ dir.
_HERE = Path(__file__).resolve()
_BACK_END = _HERE.parent.parent
if str(_BACK_END) not in sys.path:
    sys.path.insert(0, str(_BACK_END))

from agents.asd_chunker import chunk_asd_document  # noqa: E402
from agents.categorize_chunk import categorize_chunk  # noqa: E402
from agents.config import Config  # noqa: E402
from agents.embeddings import embed_text  # noqa: E402
from agents.validate_agent import (  # noqa: E402
    SYSTEM_PROMPT,
    _call_agent,
    _format_retrieved_policies,
    _parse_findings,
    build_project_client,
)
from search.query import search_policies  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("diagnose_chunks")


async def _diagnose_one_chunk(
    cli: Any,
    cfg: Config,
    idx: int,
    chunk_text: str,
    top_k: int,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    record: dict[str, Any] = {
        "idx": idx,
        "chunk_chars": len(chunk_text),
        "chunk_preview": chunk_text[:160].replace("\n", " "),
    }

    # categorize + embed in parallel (mirrors validate_arb_chunks)
    t0 = time.monotonic()
    try:
        cat_enum, vector = await asyncio.gather(
            loop.run_in_executor(None, categorize_chunk, chunk_text),
            loop.run_in_executor(None, embed_text, chunk_text),
        )
        record["category"] = cat_enum.value
        record["vector_dim"] = len(vector)
    except Exception as e:  # noqa: BLE001
        record["error"] = f"categorize/embed: {e}"
        record["elapsed_s"] = round(time.monotonic() - t0, 2)
        return record

    category = record["category"]
    effective_category: str | None = category
    if category.strip().lower() == "general":
        effective_category = None
        record["filter_dropped_reason"] = "general"

    # filtered hybrid + semantic search
    try:
        hits = await loop.run_in_executor(
            None,
            lambda: search_policies(
                query=chunk_text[:16384],
                category=effective_category,
                top=top_k,
                vector=vector,
            ),
        )
        record["filtered_hits"] = len(hits)
        used_fallback = False
        if not hits and effective_category is not None:
            used_fallback = True
            hits = await loop.run_in_executor(
                None,
                lambda: search_policies(
                    query=chunk_text[:16384],
                    category=None,
                    top=top_k,
                    vector=vector,
                ),
            )
        record["used_unfiltered_fallback"] = used_fallback
        record["final_hits"] = len(hits)
        record["top_hits"] = [
            {
                "header": h.get("header"),
                "category": h.get("category"),
                "rerank": round(h.get("@rerank") or 0.0, 4),
                "score": round(h.get("@score") or 0.0, 4),
                "id": h.get("id"),
            }
            for h in hits
        ]
    except Exception as e:  # noqa: BLE001
        record["error"] = f"search: {e}"
        record["elapsed_s"] = round(time.monotonic() - t0, 2)
        return record

    # call the validate agent
    policies_block = _format_retrieved_policies(hits)
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"[Chunk Index]\n{idx}\n\n"
        f"[Policy Category Filter]\n{category}\n\n"
        f"[Retrieved Policies]\n{policies_block}\n\n"
        f"[Section Content]\n{chunk_text}\n"
    )
    try:
        raw = await _call_agent(cli, cfg.validate_agent_name, prompt, cfg)
        findings = _parse_findings(raw)
        record["findings_count"] = len(findings)
        record["findings_by_type"] = dict(
            Counter((f.get("Type") or "?") for f in findings)
        )
        record["findings"] = findings
    except Exception as e:  # noqa: BLE001
        record["error"] = f"agent: {e}"

    record["elapsed_s"] = round(time.monotonic() - t0, 2)
    return record


async def _run(path: Path, max_chunks: int | None, top_k: int) -> dict[str, Any]:
    cfg = Config()
    cli = build_project_client(cfg)

    file_bytes = path.read_bytes()
    log.info("Cracking %s (%d bytes) via DocIntel…", path.name, len(file_bytes))
    chunks = chunk_asd_document(file_bytes, filename=path.name)
    log.info("Produced %d chunks", len(chunks))

    if max_chunks is not None:
        chunks = chunks[:max_chunks]
        log.info("Limiting to first %d chunks (per --max-chunks)", len(chunks))

    # Bound concurrency so we don't slam Foundry / Search with 40+ parallel calls.
    sem = asyncio.Semaphore(4)

    async def _bounded(i: int, c: str) -> dict[str, Any]:
        async with sem:
            log.info("[chunk %02d] start (%d chars)", i, len(c))
            r = await _diagnose_one_chunk(cli, cfg, i, c, top_k)
            log.info("[chunk %02d] done category=%s hits=%s findings=%s%s",
                     i,
                     r.get("category", "?"),
                     r.get("final_hits", "?"),
                     r.get("findings_count", "?"),
                     " [FALLBACK]" if r.get("used_unfiltered_fallback") else "")
            return r

    records = await asyncio.gather(*[_bounded(i, c) for i, c in enumerate(chunks)])

    return {
        "doc": str(path),
        "chunk_count_total": len(chunks),
        "top_k": top_k,
        "chunks": records,
    }


def _print_summary(report: dict[str, Any]) -> None:
    chunks = report["chunks"]
    print("\n=== Per-chunk diagnostic summary ===")
    print(f"Doc: {report['doc']}")
    print(f"Chunks: {report['chunk_count_total']}  top_k={report['top_k']}\n")

    header = ("idx", "chars", "category", "filtHits", "fallback", "findings", "types", "elapsed")
    print("{:>3} {:>5} {:<28} {:>8} {:>8} {:>8} {:<28} {:>7}".format(*header))
    print("-" * 110)
    cat_counter: Counter = Counter()
    fallback_count = 0
    zero_findings = 0
    total_findings = 0
    type_counter: Counter = Counter()
    for r in chunks:
        cat = r.get("category", "?")
        cat_counter[cat] += 1
        if r.get("used_unfiltered_fallback"):
            fallback_count += 1
        fc = r.get("findings_count", 0)
        total_findings += fc
        if fc == 0 and "error" not in r:
            zero_findings += 1
        for t, n in (r.get("findings_by_type") or {}).items():
            type_counter[t] += n
        types_str = ", ".join(f"{t}:{n}" for t, n in (r.get("findings_by_type") or {}).items())
        print("{:>3} {:>5} {:<28} {:>8} {:>8} {:>8} {:<28} {:>7}".format(
            r["idx"],
            r.get("chunk_chars", 0),
            (cat or "")[:28],
            str(r.get("filtered_hits", "-")),
            "Y" if r.get("used_unfiltered_fallback") else "-",
            str(fc),
            types_str[:28],
            f"{r.get('elapsed_s', 0):.1f}s",
        ))
        if "error" in r:
            print(f"      ERROR: {r['error']}")

    print("\n=== Aggregate ===")
    print(f"Category histogram: {dict(cat_counter)}")
    print(f"Unfiltered-fallback fired on {fallback_count}/{len(chunks)} chunks")
    print(f"Chunks with zero findings: {zero_findings}/{len(chunks)}")
    print(f"Total findings: {total_findings}  (by type: {dict(type_counter)})")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("doc", type=Path, help="Path to ASD .pdf or .docx")
    p.add_argument("--max-chunks", type=int, default=None,
                   help="Limit to first N chunks (default: all)")
    p.add_argument("--top-k", type=int, default=8,
                   help="Top-K policy hits per chunk (default 8)")
    p.add_argument("--out", type=Path, default=None,
                   help="Write the full JSON report to this path "
                        "(default: <doc>.diagnostic.json next to input)")
    args = p.parse_args()

    if not args.doc.is_file():
        print(f"ERROR: file not found: {args.doc}", file=sys.stderr)
        return 2

    report = asyncio.run(_run(args.doc, args.max_chunks, args.top_k))

    out_path = args.out or args.doc.with_suffix(args.doc.suffix + ".diagnostic.json")
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"\nWrote full report → {out_path}")

    _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
