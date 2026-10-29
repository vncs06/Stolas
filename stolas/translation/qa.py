"""Structural response validation and conservative textual QA."""
from __future__ import annotations

from collections import Counter
import re

from .placeholders import PlaceholderValidationError, restore_segment


class ResponseValidationError(ValueError):
    """A non-retryable structured response or protected-content failure."""


NUMBER_PATTERN = re.compile(r"(?<!\w)\d+(?:[.,:/-]\d+)*(?!\w)")
URL_PATTERN = re.compile(r"https?://[^\s)>\]]+")
NOTE_PATTERN = re.compile(r"\[(?:\d+|[A-Za-z])\]")


def _missing_items(pattern: re.Pattern, source: str, target: str) -> list[str]:
    source_counts = Counter(pattern.findall(source))
    target_counts = Counter(pattern.findall(target))
    return sorted(item for item, count in source_counts.items() if target_counts[item] < count)


def textual_qa(source: str, translated: str, *, context_texts: list[str]) -> list[dict]:
    issues = []
    for issue_type, pattern in (
        ("number_missing", NUMBER_PATTERN),
        ("url_missing", URL_PATTERN),
        ("note_reference_missing", NOTE_PATTERN),
    ):
        missing = _missing_items(pattern, source, translated)
        if missing:
            issues.append({"type": issue_type, "severity": "error", "values": missing})
    if source.strip() and len(translated.strip()) < max(1, int(len(source.strip()) * 0.2)):
        issues.append({"type": "possible_truncation", "severity": "error"})
    normalized_source = re.sub(r"\s+", " ", source).strip()
    normalized_target = re.sub(r"\s+", " ", translated)
    if len(normalized_source) >= 24 and normalized_target.count(normalized_source) > 1:
        issues.append({"type": "source_text_duplicated", "severity": "error"})
    for context in context_texts:
        normalized = re.sub(r"\s+", " ", context).strip()
        if len(normalized) >= 8 and normalized not in normalized_source and normalized in normalized_target:
            issues.append({"type": "context_copied_into_translation", "severity": "error"})
            break
    for opening, closing, label in (("(", ")", "parentheses"), ("[", "]", "brackets")):
        if source.count(opening) == source.count(closing) and (
            translated.count(opening) != translated.count(closing)
        ):
            issues.append({"type": f"unbalanced_{label}", "severity": "error"})
    return issues


def validate_response(request: dict, response: dict) -> tuple[list[dict], dict]:
    """Validate batch/segment coverage, placeholders and textual invariants."""
    if not isinstance(response, dict) or response.get("schema_version") != "0.1.0":
        raise ResponseValidationError("Provider response has an incompatible or missing schema")
    if response.get("batch_id") != request["batch_id"]:
        raise ResponseValidationError("Provider response batch_id does not match the request")
    values = response.get("segments")
    if not isinstance(values, list):
        raise ResponseValidationError("Provider response segments must be a list")
    identifiers = [item.get("segment_id") for item in values if isinstance(item, dict)]
    if len(identifiers) != len(values) or any(not item for item in identifiers):
        raise ResponseValidationError("Every provider segment result must have a segment_id")
    duplicates = sorted(item for item, count in Counter(identifiers).items() if count > 1)
    if duplicates:
        raise ResponseValidationError(f"Provider response duplicated segment IDs: {duplicates}")
    expected = [item["segment_id"] for item in request["payload"]]
    missing = [item for item in expected if item not in identifiers]
    extras = [item for item in identifiers if item not in set(expected)]
    if missing or extras:
        raise ResponseValidationError(
            f"Provider response coverage mismatch; missing={missing}, extras={extras}"
        )
    by_id = {item["segment_id"]: item for item in values}
    context_texts = [
        str(item.get("text", ""))
        for item in [*request.get("context_before", []), *request.get("context_after", [])]
    ]
    results = []
    for source in request["payload"]:
        value = by_id[source["segment_id"]]
        try:
            restored, placeholder_audit = restore_segment(
                value.get("translated_text"), source["protected_bindings"]
            )
        except PlaceholderValidationError as error:
            raise ResponseValidationError(str(error)) from error
        qa_issues = textual_qa(source["source_text"], restored, context_texts=context_texts)
        if qa_issues:
            raise ResponseValidationError(
                f"Textual QA failed for {source['segment_id']!r}: "
                f"{[item['type'] for item in qa_issues]}"
            )
        proposals = value.get("proposals", [])
        if not isinstance(proposals, list):
            raise ResponseValidationError("Segment proposals must be a list")
        results.append({
            "segment_id": source["segment_id"],
            "translated_text": restored,
            "status": "approved",
            "protected_placeholders": placeholder_audit,
            "warnings": sorted(set(value.get("warnings", []))),
            "confidence": value.get("confidence"),
            "applied_term_ids": sorted(set(value.get("applied_term_ids", []))),
            "proposals": proposals,
            "register": value.get("register"),
            "continuity_flags": sorted(set(value.get("continuity_flags", []))),
            "qa": {"status": "passed", "issues": []},
        })
    handoff = response.get("handoff")
    if not isinstance(handoff, dict):
        raise ResponseValidationError("Provider response must contain a structured handoff")
    summary = handoff.get("summary")
    if not isinstance(summary, str) or len(summary) > 500:
        raise ResponseValidationError("Handoff summary must be a compact string of at most 500 characters")
    return results, handoff
