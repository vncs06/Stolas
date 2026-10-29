"""Non-destructive cross-batch textual reconciliation."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re


def _issue_id(source_sha: str, value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{source_sha[:16]}:translation-issue:{hashlib.sha256(encoded).hexdigest()[:24]}"


def _contains(text: str, value: str) -> bool:
    return bool(value and re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text, re.IGNORECASE))


def reconcile_translation(segment_results: dict, memory: dict, proposals: list[dict],
                          source_sha: str) -> list[dict]:
    """Return issues and recommendations without rewriting translated text."""
    issues = []
    proposal_groups = defaultdict(set)
    for proposal in proposals:
        term = str(proposal.get("source_term", "")).casefold().strip()
        translation = str(proposal.get("preferred_translation", "")).casefold().strip()
        if term and translation:
            proposal_groups[term].add(translation)
    for term, translations in sorted(proposal_groups.items()):
        if len(translations) > 1:
            value = {"type": "terminology_proposal_conflict", "term": term,
                     "translations": sorted(translations)}
            issues.append({
                "issue_id": _issue_id(source_sha, value), **value,
                "severity": "review", "recommendation": "reconcile_before_memory_promotion",
            })

    for entry in memory.get("glossary", []):
        occurrence_ids = {
            item.get("segment_id") for item in entry.get("occurrences", [])
            if item.get("segment_id") in segment_results
        }
        if not occurrence_ids:
            continue
        translations = [segment_results[item]["translated_text"] for item in sorted(occurrence_ids)]
        variants = [entry.get("source_term", ""), *entry.get("variants", [])]
        if entry.get("preserve") is True:
            missing = [
                identifier for identifier in sorted(occurrence_ids)
                if not any(_contains(segment_results[identifier]["translated_text"], variant)
                           for variant in variants if variant)
            ]
            if missing:
                value = {"type": "preserved_name_or_term_inconsistent",
                         "term_id": entry["term_id"], "segment_ids": missing}
                issues.append({
                    "issue_id": _issue_id(source_sha, value), **value,
                    "severity": "error", "recommendation": "restore_approved_spelling",
                })
        preferred = entry.get("preferred_translation")
        if preferred and entry.get("status") == "approved":
            used_preferred = any(_contains(text, preferred) for text in translations)
            left_source = any(
                _contains(text, variant) for text in translations
                for variant in variants if variant
            )
            if used_preferred and left_source:
                value = {"type": "approved_term_inconsistent", "term_id": entry["term_id"],
                         "preferred_translation": preferred}
                issues.append({
                    "issue_id": _issue_id(source_sha, value), **value,
                    "severity": "review", "recommendation": "apply_approved_glossary_consistently",
                })

    registers = defaultdict(list)
    for identifier, result in sorted(segment_results.items()):
        if result.get("register"):
            registers[result["register"]].append(identifier)
        for flag in result.get("continuity_flags", []):
            value = {"type": "continuity_flag", "segment_id": identifier, "flag": flag}
            issues.append({
                "issue_id": _issue_id(source_sha, value), **value,
                "severity": "review", "recommendation": "review_cross_batch_continuity",
            })
    if len(registers) > 1:
        value = {"type": "register_inconsistent", "registers": sorted(registers)}
        issues.append({
            "issue_id": _issue_id(source_sha, value), **value,
            "severity": "review", "recommendation": "review_style_guide_application",
        })
    return sorted(issues, key=lambda item: item["issue_id"])
