"""Document-level verification of deduped ``Missing`` findings (issue #77).

After :func:`agents.validate_agent.dedupe_missing_findings` collapses N
per-chunk duplicates of a `Type=="Missing"` finding into one, this module
asks the model — once per surviving distinct principle — whether the
document addresses the principle **anywhere**. False positives (chunk
myopia) are dropped from the response; genuine absences are rewritten with
the trustworthy "Not defined anywhere in the document." sentence and the
per-chunk count suffix is removed.

See ``prompt-contracts/MISSING-VERIFY.md`` for the behavior spec.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from .config import Config

logger = logging.getLogger(__name__)


_DOC_TEXT_CHAR_BUDGET = 120_000
_DOC_TRUNCATION_MARKER = "\n\n...[truncated]"

# Trailing "(also missing in N other chunk[s])" that dedupe_missing_findings
# appends. Tolerant to surrounding whitespace.
_CHUNK_COUNT_SUFFIX_RE = re.compile(
    r"\s*\(also missing in \d+ other chunks?\)\s*$"
)

_NOT_DEFINED_SENTENCE = "Not defined anywhere in the document."


_VERIFY_SYSTEM_PROMPT = (
    "You are an Azure architecture-review verifier. The user will give you a "
    "POLICY PRINCIPLE name and the full text of an Architecture Design "
    "Document (ASD). Your job is to decide whether the document, anywhere in "
    "its body, addresses that principle — even briefly, even imperfectly. "
    "Boilerplate mentions in a table of contents, glossary, or sign-off "
    "block do NOT count as addressing the principle.\n\n"
    "Return ONLY a single JSON object on one line, no prose, no code fence:\n"
    "{\"present\": true, \"quote\": \"<one short verbatim phrase from the document>\"}\n"
    "or\n"
    "{\"present\": false, \"quote\": \"\"}\n\n"
    "If the document mentions the principle by name but does not state any "
    "substantive content about it, set present=false."
)


def _truncate_doc_text(doc_text: str) -> str:
    if len(doc_text) <= _DOC_TEXT_CHAR_BUDGET:
        return doc_text
    logger.info(
        "verify_missing: doc_text=%d chars exceeds budget=%d; truncating from end",
        len(doc_text), _DOC_TEXT_CHAR_BUDGET,
    )
    keep = _DOC_TEXT_CHAR_BUDGET - len(_DOC_TRUNCATION_MARKER)
    return doc_text[:keep] + _DOC_TRUNCATION_MARKER


def _strip_chunk_count_suffix(description: str) -> str:
    """Remove the trailing ``(also missing in N other chunk[s])`` if present.

    Idempotent: no-op when the suffix isn't there.
    """
    if not description:
        return description or ""
    return _CHUNK_COUNT_SUFFIX_RE.sub("", description).rstrip()


def _parse_verify_response(raw: str) -> tuple[bool | None, str | None]:
    """Lenient parse of the verify model's response.

    Returns ``(present, quote)`` where ``present`` is ``True``/``False``
    when the model's intent could be read, or ``None`` when the response
    was unparseable. ``quote`` is the model's evidence quote when present,
    else ``None``.
    """
    if not raw:
        return None, None
    s = raw.strip()
    # Strip ```json or ``` code fences if present.
    if s.startswith("```"):
        s = s.strip("`")
        # Drop leading "json" language tag.
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    # Keep only the first line — model should answer on one line.
    if "\n" in s:
        s = s.split("\n", 1)[0].strip()
    # Some models wrap the JSON in surrounding text; find the first {...}.
    if not s.startswith("{"):
        m = re.search(r"\{.*\}", s)
        if m:
            s = m.group(0)
        else:
            return None, None
    try:
        obj = json.loads(s)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(obj, dict) or "present" not in obj:
        return None, None
    present = obj.get("present")
    if isinstance(present, str):
        norm = present.strip().lower()
        if norm in {"true", "yes", "1"}:
            present = True
        elif norm in {"false", "no", "0"}:
            present = False
        else:
            return None, None
    if not isinstance(present, bool):
        return None, None
    quote = obj.get("quote")
    if quote is not None and not isinstance(quote, str):
        quote = None
    return present, quote


def _get_verify_deployment() -> str | None:
    return (
        os.getenv("FOUNDRY_CATEGORIZE_DEPLOYMENT", "").strip()
        or os.getenv("FOUNDRY_MODEL_DEPLOYMENT", "").strip()
        or None
    )


def _build_user_message(principle: str, doc_text: str) -> str:
    return (
        f"PRINCIPLE:\n{principle}\n\n"
        f"DOCUMENT:\n{_truncate_doc_text(doc_text)}"
    )


def _verify_one_principle_sync(
    client: Any, deployment: str, principle: str, doc_text: str,
) -> tuple[bool | None, str | None]:
    """Sync verify call. Never raises — returns ``(None, None)`` on error."""
    user = _build_user_message(principle, doc_text)
    try:
        try:
            resp = client.chat.completions.create(
                model=deployment,
                messages=[
                    {"role": "system", "content": _VERIFY_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                max_completion_tokens=128,
            )
        except TypeError:
            resp = client.chat.completions.create(
                model=deployment,
                messages=[
                    {"role": "system", "content": _VERIFY_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                max_tokens=128,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("verify_missing: model call failed for principle=%r: %s",
                       principle, e)
        return None, None
    raw = (resp.choices[0].message.content or "").strip()
    present, quote = _parse_verify_response(raw)
    if present is None:
        logger.warning("verify_missing: unparseable response for principle=%r raw=%r",
                       principle, raw[:200])
    return present, quote


async def verify_missing_findings(
    findings: list[dict],
    doc_text: str,
    cfg: Config,
    client: Any | None = None,
) -> list[dict]:
    """Document-level verification pass for deduped ``Missing`` findings.

    See ``prompt-contracts/MISSING-VERIFY.md``. Does not mutate the input.
    """
    if not cfg.missing_verify_enabled:
        return findings
    if not findings:
        return list(findings)

    # Find distinct (Missing + non-empty principle) entries in order.
    distinct: list[str] = []
    seen: set[str] = set()
    for f in findings:
        if not isinstance(f, dict) or f.get("Type") != "Missing":
            continue
        principles = (f.get("Principles") or "").strip()
        if not principles:
            continue
        key = principles.lower()
        if key in seen:
            continue
        seen.add(key)
        distinct.append(principles)

    if not distinct:
        return list(findings)

    cap = max(0, int(cfg.missing_verify_max or 0))
    truncated_principles = distinct[:cap] if cap else []
    if cap and len(distinct) > cap:
        logger.warning(
            "verify_missing: %d distinct Missing principles exceeds cap=%d; "
            "verifying first %d only",
            len(distinct), cap, cap,
        )

    if not truncated_principles:
        return list(findings)

    deployment = _get_verify_deployment()
    endpoint = os.getenv("FOUNDRY_ENDPOINT", "").strip()
    if not deployment or not endpoint:
        logger.warning(
            "verify_missing: skipping — FOUNDRY_ENDPOINT or deployment env var "
            "not set (endpoint=%r deployment=%r)",
            endpoint, deployment,
        )
        return list(findings)

    if client is None:
        # Lazy import keeps verify_missing testable without azure-openai SDK
        # on the import path.
        from .categorize_chunk import _get_aoai_client
        client = _get_aoai_client(endpoint)

    loop = asyncio.get_running_loop()
    results = await asyncio.gather(
        *[
            loop.run_in_executor(
                None,
                _verify_one_principle_sync,
                client, deployment, principle, doc_text,
            )
            for principle in truncated_principles
        ],
        return_exceptions=True,
    )

    verdict_by_key: dict[str, tuple[bool | None, str | None]] = {}
    for principle, res in zip(truncated_principles, results):
        if isinstance(res, BaseException):
            logger.warning("verify_missing: task error for principle=%r: %s",
                           principle, res)
            verdict_by_key[principle.lower()] = (None, None)
        else:
            verdict_by_key[principle.lower()] = res

    output: list[dict] = []
    for f in findings:
        if not isinstance(f, dict) or f.get("Type") != "Missing":
            output.append(f)
            continue
        principles = (f.get("Principles") or "").strip()
        if not principles:
            output.append(f)
            continue
        key = principles.lower()
        if key not in verdict_by_key:
            # Beyond the cap → pass through unchanged.
            output.append(f)
            continue
        present, quote = verdict_by_key[key]
        if present is True:
            logger.info(
                "verify_missing: dropping false-positive Missing for principle=%r (quote=%r)",
                principles, (quote or "")[:160],
            )
            continue
        if present is False:
            new_f = dict(f)
            desc = _strip_chunk_count_suffix(str(new_f.get("Description") or ""))
            if not desc.endswith(_NOT_DEFINED_SENTENCE):
                if desc and not desc.endswith((".", "!", "?")):
                    desc = desc + "."
                desc = (desc + " " + _NOT_DEFINED_SENTENCE).strip()
            new_f["Description"] = desc
            output.append(new_f)
            continue
        # present is None → call failed / unparseable; leave unchanged.
        output.append(f)

    return output
