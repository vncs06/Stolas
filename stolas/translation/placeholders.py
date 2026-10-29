"""Deterministic protected-content placeholders and restoration."""
from __future__ import annotations

import hashlib
import json
import re


PLACEHOLDER_PATTERN = re.compile(r"\[\[STOLAS_PROTECTED_[0-9A-F]{24}\]\]")


class PlaceholderValidationError(ValueError):
    """A provider response lost, duplicated or introduced a placeholder."""


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _placeholder(identity: dict) -> str:
    return f"[[STOLAS_PROTECTED_{_digest(identity)[:24].upper()}]]"


def _binding(source_sha: str, batch_id: str, segment_id: str, part_index: int,
             occurrence: int, original: str, kind: str,
             decision_ids: list[str], term_id: str | None = None) -> dict:
    identity = {
        "document": source_sha, "batch": batch_id, "segment": segment_id,
        "part": part_index, "occurrence": occurrence,
        "original_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
        "kind": kind, "term_id": term_id,
    }
    return {
        "placeholder": _placeholder(identity),
        "segment_id": segment_id,
        "part_index": part_index,
        "occurrence_index": occurrence,
        "kind": kind,
        "term_id": term_id,
        "decision_ids": sorted(set(decision_ids)),
        "original_text": original,
        "original_sha256": identity["original_sha256"],
    }


def _preserve_matches(text: str, entries: list[dict]) -> list[tuple[int, int, dict]]:
    candidates = []
    for entry in entries:
        variants = sorted(
            set([entry.get("source_term", ""), *entry.get("variants", [])]),
            key=lambda value: (-len(value), value.casefold(), value),
        )
        for variant in variants:
            if not variant:
                continue
            pattern = re.compile(rf"(?<!\w){re.escape(variant)}(?!\w)", re.IGNORECASE)
            for match in pattern.finditer(text):
                candidates.append((match.start(), match.end(), entry))
    chosen = []
    occupied_until = -1
    for start, end, entry in sorted(
        candidates, key=lambda item: (item[0], -(item[1] - item[0]), item[2]["term_id"])
    ):
        if start < occupied_until:
            continue
        chosen.append((start, end, entry))
        occupied_until = end
    return chosen


def prepare_segment(segment: dict, preserved_entries: list[dict], *,
                    source_sha: str, batch_id: str) -> dict:
    """Replace protected parts and approved preserved terms with unique placeholders."""
    original_source = "".join(str(part.get("text", "")) for part in segment["parts"])
    if PLACEHOLDER_PATTERN.search(original_source):
        raise PlaceholderValidationError(
            f"Segment {segment['segment_id']!r} already contains a reserved placeholder"
        )
    rendered = []
    bindings = []
    occurrence = 0
    for part_index, part in enumerate(segment["parts"]):
        text = str(part.get("text", ""))
        if part.get("type") == "protected":
            binding = _binding(
                source_sha, batch_id, segment["segment_id"], part_index,
                occurrence, text, "protected_part", part.get("decision_ids", []),
            )
            rendered.append(binding["placeholder"])
            bindings.append(binding)
            occurrence += 1
            continue
        matches = _preserve_matches(text, preserved_entries)
        cursor = 0
        for start, end, entry in matches:
            rendered.append(text[cursor:start])
            original = text[start:end]
            binding = _binding(
                source_sha, batch_id, segment["segment_id"], part_index,
                occurrence, original, "preserved_memory_term",
                [entry["decision_id"]] if entry.get("decision_id") else [],
                entry["term_id"],
            )
            rendered.append(binding["placeholder"])
            bindings.append(binding)
            occurrence += 1
            cursor = end
        rendered.append(text[cursor:])
    placeholders = [item["placeholder"] for item in bindings]
    if len(placeholders) != len(set(placeholders)):
        raise PlaceholderValidationError("Generated protected placeholders are not unique")
    return {
        "segment_id": segment["segment_id"],
        "editorial_id": segment.get("editorial_id"),
        "page": segment.get("page"),
        "editorial_type": segment.get("editorial_type"),
        "source_text": original_source,
        "source_text_with_placeholders": "".join(rendered),
        "protected_bindings": bindings,
        "review_required": bool(segment.get("review_required")),
    }


def restore_segment(text: str, bindings: list[dict]) -> tuple[str, list[dict]]:
    """Validate exact placeholder coverage and restore original protected content."""
    if not isinstance(text, str):
        raise PlaceholderValidationError("Translated text must be a string")
    expected = {item["placeholder"] for item in bindings}
    present = PLACEHOLDER_PATTERN.findall(text)
    unknown = sorted(set(present) - expected)
    if unknown:
        raise PlaceholderValidationError(f"Unknown protected placeholders: {unknown}")
    audit = []
    restored = text
    for binding in bindings:
        count = restored.count(binding["placeholder"])
        if count != 1:
            raise PlaceholderValidationError(
                f"Placeholder {binding['placeholder']} occurs {count} times; expected exactly once"
            )
        restored = restored.replace(binding["placeholder"], binding["original_text"], 1)
        audit.append({
            "placeholder": binding["placeholder"],
            "kind": binding["kind"],
            "term_id": binding.get("term_id"),
            "decision_ids": binding["decision_ids"],
            "restored_sha256": binding["original_sha256"],
            "restored": True,
        })
    if PLACEHOLDER_PATTERN.search(restored):
        raise PlaceholderValidationError("A protected placeholder remained after restoration")
    return restored, audit
