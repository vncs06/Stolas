"""Build deterministic contextual batches without invoking a translation model."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata


BATCHING_SCHEMA_VERSION = "0.1.0"
BATCHING_VERSION = "0.1.0"
SUPPORTED_EDITORIAL_SCHEMAS = frozenset({"0.1.0"})
SUPPORTED_MEMORY_SCHEMAS = frozenset({"0.1.0"})
SUPPORTED_PROTECTION_SCHEMAS = frozenset({"0.2.0"})
SUPPORTED_PROTECTION_RULESETS = frozenset({"0.2.0"})
PAYLOAD_PART_TYPES = frozenset({"translatable", "review_required"})
STRUCTURAL_TYPES = frozenset({"heading", "subheading"})
DEFAULTS = {
    "profile": "generic-local-v1",
    "max_input_tokens": 8192,
    "reserved_output_tokens": 2048,
    "reserved_system_tokens": 768,
    "context_budget_tokens": 512,
    "memory_budget_tokens": 768,
}


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_id(source_sha: str, kind: str, value: object) -> str:
    return f"{source_sha[:16]}:{kind}:{_canonical_hash(value)[:24]}"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def estimate_tokens(value: str) -> int:
    """Return a conservative, local and deterministic token estimate."""
    normalized = _normalize_text(value)
    if not normalized:
        return 0
    return max(1, math.ceil(len(normalized.encode("utf-8")) / 4))


def _validate_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError(f"{label} must be a SHA-256 hex digest")
    return value.lower()


def _validate_editorial(editorial: object) -> dict:
    if not isinstance(editorial, dict):
        raise ValueError("Editorial artifact must be a JSON object")
    if editorial.get("schema_version") not in SUPPORTED_EDITORIAL_SCHEMAS:
        raise ValueError(
            f"Unsupported editorial schema {editorial.get('schema_version')!r}; expected 0.1.0"
        )
    document = editorial.get("source_document")
    if not isinstance(document, dict):
        raise ValueError("Editorial artifact is missing source_document")
    _validate_sha(document.get("sha256"), "Editorial source document sha256")
    segments = editorial.get("segments")
    units = editorial.get("editorial_units")
    if not isinstance(segments, list) or not isinstance(units, list):
        raise ValueError("Editorial artifact must contain segment and editorial unit lists")
    unit_ids = {item.get("editorial_id") for item in units if isinstance(item, dict)}
    if len(unit_ids) != len(units) or None in unit_ids:
        raise ValueError("Editorial units must have unique editorial_id values")
    seen = set()
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("Each editorial segment must be an object")
        identifier = segment.get("segment_id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("Editorial segments must have unique non-empty segment_id values")
        seen.add(identifier)
        if segment.get("editorial_id") not in unit_ids:
            raise ValueError(f"Segment {identifier!r} references an unknown editorial unit")
        if not isinstance(segment.get("parts"), list):
            raise ValueError(f"Segment {identifier!r} parts must be a list")
        if not any(
            part.get("type") in PAYLOAD_PART_TYPES and str(part.get("text", "")).strip()
            for part in segment["parts"] if isinstance(part, dict)
        ):
            raise ValueError(f"Protected-only segment {identifier!r} cannot be a payload")
    return editorial


def _validate_protection(protection: object, editorial: dict) -> dict:
    if not isinstance(protection, dict):
        raise ValueError("Protection artifact must be a JSON object")
    if protection.get("schema_version") not in SUPPORTED_PROTECTION_SCHEMAS:
        raise ValueError(
            f"Unsupported protection schema {protection.get('schema_version')!r}; expected 0.2.0"
        )
    if protection.get("ruleset_version") not in SUPPORTED_PROTECTION_RULESETS:
        raise ValueError(
            f"Unsupported protection ruleset {protection.get('ruleset_version')!r}; expected 0.2.0"
        )
    document = protection.get("source_document")
    if not isinstance(document, dict) or (
        str(document.get("sha256", "")).lower()
        != str(editorial["source_document"]["sha256"]).lower()
    ):
        raise ValueError("Protection and editorial artifacts reference different documents")
    classifications = protection.get("classifications")
    if not isinstance(classifications, list):
        raise ValueError("Protection classifications must be a list")
    identifiers = [item.get("id") for item in classifications if isinstance(item, dict)]
    if len(identifiers) != len(set(identifiers)) or any(not item for item in identifiers):
        raise ValueError("Protection decision IDs must be unique and non-empty")
    reference = editorial.get("source_protection")
    if not isinstance(reference, dict) or reference.get("sha256") != _canonical_hash(protection):
        raise ValueError("Editorial artifact does not match the protection artifact hash")
    return protection


def _validate_memory(memory: object, editorial: dict, protection: dict) -> dict:
    if not isinstance(memory, dict):
        raise ValueError("Editorial memory must be a JSON object")
    if memory.get("schema_version") not in SUPPORTED_MEMORY_SCHEMAS:
        raise ValueError(
            f"Unsupported memory schema {memory.get('schema_version')!r}; expected 0.1.0"
        )
    document = memory.get("source_document")
    if not isinstance(document, dict) or (
        str(document.get("sha256", "")).lower()
        != str(editorial["source_document"]["sha256"]).lower()
    ):
        raise ValueError("Memory and editorial artifacts reference different documents")
    inputs = memory.get("source_inputs")
    if not isinstance(inputs, dict):
        raise ValueError("Editorial memory is missing source_inputs")
    if inputs.get("editorial_sha256") != _canonical_hash(editorial):
        raise ValueError("Editorial memory does not match the editorial artifact hash")
    if inputs.get("protection_sha256") != _canonical_hash(protection):
        raise ValueError("Editorial memory does not match the protection artifact hash")
    for field in ("glossary", "dependencies", "decisions", "conflicts"):
        if not isinstance(memory.get(field), list):
            raise ValueError(f"Editorial memory field {field!r} must be a list")
    term_ids = [item.get("term_id") for item in memory["glossary"] if isinstance(item, dict)]
    if len(term_ids) != len(set(term_ids)) or any(not item for item in term_ids):
        raise ValueError("Memory glossary term IDs must be unique and non-empty")
    if not isinstance(memory.get("style_guide"), dict):
        raise ValueError("Editorial memory is missing style_guide")
    return memory


def _validate_existing(existing: object | None, source_sha: str) -> dict | None:
    if existing is None:
        return None
    if not isinstance(existing, dict):
        raise ValueError("Existing batching artifact must be a JSON object")
    if existing.get("schema_version") != BATCHING_SCHEMA_VERSION:
        raise ValueError("Existing batching artifact has an incompatible schema")
    if str(existing.get("document", {}).get("sha256", "")).lower() != source_sha:
        raise ValueError("Existing batching artifact references a different document")
    if not isinstance(existing.get("batches"), list) or not isinstance(existing.get("checkpoints"), list):
        raise ValueError("Existing batching artifact is missing batches or checkpoints")
    return existing


def _configuration(profile: str, max_input_tokens: int,
                   reserved_output_tokens: int, reserved_system_tokens: int,
                   context_budget_tokens: int, memory_budget_tokens: int) -> dict:
    values = {
        "max_input_tokens": max_input_tokens,
        "reserved_output_tokens": reserved_output_tokens,
        "reserved_system_tokens": reserved_system_tokens,
        "context_budget_tokens": context_budget_tokens,
        "memory_budget_tokens": memory_budget_tokens,
    }
    if not isinstance(profile, str) or not profile.strip():
        raise ValueError("Batching profile must be a non-empty string")
    for key, value in values.items():
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")
    if max_input_tokens <= 0:
        raise ValueError("max_input_tokens must be positive")
    payload_budget = (
        max_input_tokens - reserved_output_tokens - reserved_system_tokens
        - context_budget_tokens - memory_budget_tokens
    )
    if payload_budget <= 0:
        raise ValueError("Token reservations leave no budget for payload")
    return {
        "profile": profile.strip(),
        **values,
        "payload_budget_tokens": payload_budget,
        "usable_input_tokens": max_input_tokens - reserved_output_tokens,
        "token_estimator": "utf8_bytes_divided_by_4_ceiling_v1",
    }


def _segment_text(segment: dict, *, translatable_only: bool = False) -> str:
    parts = []
    for part in segment["parts"]:
        if translatable_only and part.get("type") not in PAYLOAD_PART_TYPES:
            continue
        text = str(part.get("text", ""))
        if text:
            parts.append(text)
    return _normalize_text(" ".join(parts))


def _ordered_segments(editorial: dict) -> list[dict]:
    units = {item["editorial_id"]: item for item in editorial["editorial_units"]}
    original = {item["segment_id"]: index for index, item in enumerate(editorial["segments"])}
    result = []
    for segment in editorial["segments"]:
        unit = units[segment["editorial_id"]]
        item = copy.deepcopy(segment)
        item["reading_order"] = unit.get("reading_order", original[segment["segment_id"]])
        item["text"] = _segment_text(segment)
        item["payload_text"] = _segment_text(segment, translatable_only=True)
        item["payload_tokens"] = estimate_tokens(item["payload_text"]) + 8
        result.append(item)
    result.sort(key=lambda item: (
        item.get("page", 0), item.get("reading_order", 0),
        original[item["segment_id"]], item["segment_id"],
    ))
    for index, item in enumerate(result):
        item["global_order"] = index
    return result


def _section_metadata(segments: list[dict]) -> dict[str, dict]:
    current_heading = None
    current_subheading = None
    result = {}
    for segment in segments:
        if segment.get("editorial_type") == "heading":
            current_heading = segment
            current_subheading = None
        elif segment.get("editorial_type") == "subheading":
            current_subheading = segment
        result[segment["segment_id"]] = {
            "heading_id": current_heading["segment_id"] if current_heading else None,
            "heading": current_heading["text"] if current_heading else None,
            "subheading_id": current_subheading["segment_id"] if current_subheading else None,
            "subheading": current_subheading["text"] if current_subheading else None,
        }
    return result


def _payload_groups(segments: list[dict], payload_budget: int) -> list[list[dict]]:
    semantic_units = []
    index = 0
    while index < len(segments):
        unit = [segments[index]]
        if (
            segments[index].get("editorial_type") in STRUCTURAL_TYPES
            and index + 1 < len(segments)
            and segments[index + 1].get("editorial_type") not in STRUCTURAL_TYPES
        ):
            unit.append(segments[index + 1])
            index += 1
        semantic_units.append(unit)
        index += 1
    groups: list[list[dict]] = []
    current: list[dict] = []
    current_tokens = 0
    for unit in semantic_units:
        starts_section = unit[0].get("editorial_type") in STRUCTURAL_TYPES
        if current and starts_section:
            groups.append(current)
            current = []
            current_tokens = 0
        cost = sum(segment["payload_tokens"] for segment in unit)
        if current and current_tokens + cost > payload_budget:
            groups.append(current)
            current = []
            current_tokens = 0
        current.extend(unit)
        current_tokens += cost
    if current:
        groups.append(current)
    return groups


def _context_snapshot(segment: dict, relation: str) -> dict:
    return {
        "segment_id": segment["segment_id"],
        "editorial_id": segment["editorial_id"],
        "page": segment["page"],
        "editorial_type": segment["editorial_type"],
        "global_order": segment["global_order"],
        "relation": relation,
        "text": segment["text"],
        "estimated_tokens": estimate_tokens(segment["text"]) + 6,
    }


def _select_context(group: list[dict], segments: list[dict], sections: dict[str, dict],
                    budget: int) -> tuple[list[dict], list[dict]]:
    if budget <= 0:
        return [], []
    first = group[0]["global_order"]
    last = group[-1]["global_order"]
    before_budget = budget // 2
    after_budget = budget - before_budget
    before_candidates = []
    section = sections[group[0]["segment_id"]]
    for identifier, relation in (
        (section.get("heading_id"), "current_heading"),
        (section.get("subheading_id"), "current_subheading"),
    ):
        if identifier:
            candidate = next(item for item in segments if item["segment_id"] == identifier)
            if candidate["global_order"] < first:
                before_candidates.append((candidate, relation))
    if first > 0:
        before_candidates.append((segments[first - 1], "previous_segment"))
    unique_before = {}
    for candidate, relation in before_candidates:
        unique_before[candidate["segment_id"]] = (candidate, relation)
    before = []
    used = 0
    for candidate, relation in sorted(
        unique_before.values(), key=lambda value: value[0]["global_order"]
    ):
        snapshot = _context_snapshot(candidate, relation)
        if used + snapshot["estimated_tokens"] <= before_budget:
            before.append(snapshot)
            used += snapshot["estimated_tokens"]
    after = []
    if last + 1 < len(segments):
        snapshot = _context_snapshot(segments[last + 1], "next_segment")
        if snapshot["estimated_tokens"] <= after_budget:
            after.append(snapshot)
    return before, after


def _payload_snapshot(segment: dict) -> dict:
    return {
        "segment_id": segment["segment_id"],
        "editorial_id": segment["editorial_id"],
        "page": segment["page"],
        "editorial_type": segment["editorial_type"],
        "global_order": segment["global_order"],
        "source_ids": copy.deepcopy(segment.get("source_ids", [])),
        "parts": copy.deepcopy(segment["parts"]),
        "text": segment["text"],
        "payload_text": segment["payload_text"],
        "estimated_tokens": segment["payload_tokens"],
        "review_required": bool(segment.get("review_required")),
        "contains_protected_parts": bool(segment.get("contains_protected_parts")),
    }


def _memory_runtime_index(memory: dict) -> dict[str, dict]:
    result = {}
    for entry in memory["glossary"]:
        variants = {
            _normalize_text(str(value)).casefold()
            for value in [entry.get("source_term", ""), *entry.get("variants", [])]
            if value is not None and _normalize_text(str(value))
        }
        alternatives = "|".join(
            re.escape(value) for value in sorted(variants, key=lambda value: (-len(value), value))
        )
        result[entry["term_id"]] = {
            "pattern": re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)") if alternatives else None,
            "occurrence_segments": {
                item.get("segment_id") for item in entry.get("occurrences", [])
                if item.get("segment_id")
            },
        }
    return result


def _matches_pattern(text: str, pattern: re.Pattern | None) -> bool:
    return bool(pattern and pattern.search(_normalize_text(text).casefold()))


def _memory_snapshot(entry: dict, reasons: list[str]) -> dict:
    return {
        "term_id": entry["term_id"],
        "source_term": entry.get("source_term"),
        "variants": copy.deepcopy(entry.get("variants", [])),
        "preferred_translation": entry.get("preferred_translation"),
        "preserve": entry.get("preserve"),
        "category": entry.get("category"),
        "context_key": entry.get("context_key"),
        "decision_id": entry.get("decision_id"),
        "decision_source": entry.get("decision_source"),
        "status": entry.get("status"),
        "inclusion_reasons": sorted(set(reasons)),
    }


def _memory_cost(snapshot: dict) -> int:
    text = " ".join(str(value) for value in (
        snapshot.get("source_term"), snapshot.get("preferred_translation"),
        snapshot.get("category"), snapshot.get("context_key"),
        " ".join(snapshot.get("variants", [])),
    ) if value)
    return estimate_tokens(text) + 12


def _select_memory(memory: dict, payload: list[dict], before: list[dict], after: list[dict],
                   protected_ids: set[str], budget: int,
                   runtime_index: dict[str, dict]) -> tuple[list[dict], list[str]]:
    payload_ids = {item["segment_id"] for item in payload}
    context_ids = {item["segment_id"] for item in [*before, *after]}
    payload_text = " ".join(item["text"] for item in payload)
    context_text = " ".join(item["text"] for item in [*before, *after])
    ranked = []
    for entry in memory["glossary"]:
        runtime = runtime_index[entry["term_id"]]
        occurrence_segments = runtime["occurrence_segments"]
        pattern = runtime["pattern"]
        reasons = []
        priority = 90
        mandatory = False
        if entry.get("decision_id") in protected_ids:
            reasons.append("protected_decision_in_payload")
            priority = 0
            mandatory = True
        if occurrence_segments & payload_ids:
            reasons.append("occurrence_in_payload")
            priority = min(priority, 1 if entry.get("preserve") is True else 20)
            mandatory = mandatory or entry.get("preserve") is True
        if entry.get("decision_source") == "human_override" and _matches_pattern(
            payload_text, pattern
        ):
            reasons.append("human_override_matches_payload")
            priority = min(priority, 2)
            mandatory = True
        if _matches_pattern(payload_text, pattern):
            reasons.append("term_or_variant_in_payload")
            category_rank = 10 if entry.get("category") in {
                "proper_name", "technical_term", "transliteration", "ritual_term", "title"
            } else 40
            priority = min(priority, category_rank)
        if occurrence_segments & context_ids:
            reasons.append("occurrence_in_adjacent_context")
            priority = min(priority, 50)
        elif _matches_pattern(context_text, pattern):
            reasons.append("term_or_variant_in_adjacent_context")
            priority = min(priority, 60)
        if not reasons:
            continue
        snapshot = _memory_snapshot(entry, reasons)
        ranked.append((not mandatory, priority, entry["term_id"], snapshot))
    selected = []
    warnings = []
    used = 0
    for optional, _, _, snapshot in sorted(ranked):
        cost = _memory_cost(snapshot)
        if optional and used + cost > budget:
            continue
        if not optional and used + cost > budget:
            warnings.append("mandatory_memory_exceeds_budget")
        selected.append(snapshot)
        used += cost
    return selected, sorted(set(warnings))


def _protected_decisions(payload: list[dict], protection_index: dict[str, dict]) -> list[str]:
    identifiers = set()
    for segment in payload:
        for part in segment["parts"]:
            if part.get("type") != "protected":
                continue
            for identifier in part.get("decision_ids", []):
                if identifier not in protection_index:
                    raise ValueError(
                        f"Protected payload part references unknown decision {identifier!r}"
                    )
                identifiers.add(identifier)
    return sorted(identifiers)


def _dependency_record(batch_id: str, payload_ids: list[str], memory_entries: list[dict],
                       protected_ids: list[str], memory: dict, editorial_hash: str,
                       config_hash: str) -> dict:
    term_ids = {item["term_id"] for item in memory_entries}
    decision_ids = sorted({
        item["decision_id"] for item in memory_entries if item.get("decision_id")
    })
    dependency_ids = sorted(
        item["dependency_id"] for item in memory["dependencies"]
        if item.get("target_id") in term_ids
        and item.get("dependent_id") in set(payload_ids)
        and item.get("status") == "current"
    )
    return {
        "batch_id": batch_id,
        "editorial_segment_ids": payload_ids,
        "memory_term_ids": sorted(term_ids),
        "memory_decision_ids": decision_ids,
        "memory_dependency_ids": dependency_ids,
        "protected_decision_ids": protected_ids,
        "editorial_sha256": editorial_hash,
        "config_sha256": config_hash,
    }


def _handoff(batch_id: str, supersedes: str | None = None) -> dict:
    return {
        "schema_version": "0.1.0",
        "batch_id": batch_id,
        "status": "pending",
        "summary": None,
        "new_decisions": [],
        "characters_and_nomenclature": [],
        "new_terms": [],
        "pending_references": [],
        "continuity_notes": [],
        "supersedes_batch_id": supersedes,
    }


def _checkpoint(batch: dict, previous: dict | None, previous_checkpoint: dict | None) -> dict:
    same = previous is not None and (
        previous.get("dependency_fingerprint") == batch["dependency_fingerprint"]
        and previous.get("batch_id") == batch["batch_id"]
    )
    if same and previous_checkpoint is not None:
        result = copy.deepcopy(previous_checkpoint)
        result["batch_id"] = batch["batch_id"]
        result["dependency_fingerprint"] = batch["dependency_fingerprint"]
        return result
    stale_reasons = []
    if previous is not None:
        if previous.get("dependencies", {}).get("editorial_sha256") != batch["dependencies"]["editorial_sha256"]:
            stale_reasons.append("editorial_artifact_changed")
        if previous.get("dependencies", {}).get("config_sha256") != batch["dependencies"]["config_sha256"]:
            stale_reasons.append("batching_configuration_changed")
        if previous.get("dependency_fingerprint") != batch["dependency_fingerprint"]:
            stale_reasons.append("relevant_memory_or_protection_changed")
    return {
        "checkpoint_id": _stable_id(
            batch["source_document_sha256"], "checkpoint",
            {"payload": batch["payload_segment_ids"], "dependency": batch["dependency_fingerprint"]},
        ),
        "batch_id": batch["batch_id"],
        "previous_batch_id": previous.get("batch_id") if previous else None,
        "status": "stale" if stale_reasons else "pending",
        "completed": False,
        "dependency_fingerprint": batch["dependency_fingerprint"],
        "stale_reasons": sorted(set(stale_reasons)),
    }


def validate_coverage(editorial: dict, batches: list[dict]) -> dict:
    expected = [item["segment_id"] for item in _ordered_segments(editorial)]
    actual = [identifier for batch in batches for identifier in batch["payload_segment_ids"]]
    counts = {identifier: actual.count(identifier) for identifier in set(actual)}
    duplicates = sorted(identifier for identifier, count in counts.items() if count > 1)
    missing = [identifier for identifier in expected if identifier not in counts]
    unexpected = sorted(identifier for identifier in counts if identifier not in set(expected))
    order_preserved = actual == expected
    result = {
        "expected_payload_segment_count": len(expected),
        "actual_payload_segment_count": len(actual),
        "unique_payload_segment_count": len(set(actual)),
        "missing_segment_ids": missing,
        "duplicate_segment_ids": duplicates,
        "unexpected_segment_ids": unexpected,
        "order_preserved": order_preserved,
        "complete": not missing and not duplicates and not unexpected and order_preserved,
    }
    if not result["complete"]:
        raise ValueError(
            "Batch coverage failed: every translatable segment must appear exactly once in order"
        )
    return result


def build_translation_batches(editorial: dict, memory: dict, protection: dict, *,
                              existing_batches: dict | None = None,
                              max_input_tokens: int = DEFAULTS["max_input_tokens"],
                              reserved_output_tokens: int = DEFAULTS["reserved_output_tokens"],
                              reserved_system_tokens: int = DEFAULTS["reserved_system_tokens"],
                              context_budget_tokens: int = DEFAULTS["context_budget_tokens"],
                              memory_budget_tokens: int = DEFAULTS["memory_budget_tokens"],
                              profile: str = DEFAULTS["profile"]) -> dict:
    """Build deterministic batches with payload, context, memory and checkpoints."""
    editorial = _validate_editorial(editorial)
    protection = _validate_protection(protection, editorial)
    memory = _validate_memory(memory, editorial, protection)
    source_sha = editorial["source_document"]["sha256"].lower()
    existing_batches = _validate_existing(existing_batches, source_sha)
    config = _configuration(
        profile, max_input_tokens, reserved_output_tokens, reserved_system_tokens,
        context_budget_tokens, memory_budget_tokens,
    )
    config_hash = _canonical_hash(config)
    editorial_hash = _canonical_hash(editorial)
    memory_hash = _canonical_hash(memory)
    protection_hash = _canonical_hash(protection)
    segments = _ordered_segments(editorial)
    sections = _section_metadata(segments)
    groups = _payload_groups(segments, config["payload_budget_tokens"])
    protection_index = {item["id"]: item for item in protection["classifications"]}
    memory_runtime = _memory_runtime_index(memory)
    previous_by_payload = {}
    previous_checkpoints = {}
    previous_batch_list = []
    if existing_batches:
        previous_batch_list = existing_batches["batches"]
        previous_by_payload = {
            tuple(item["payload_segment_ids"]): item for item in previous_batch_list
        }
        previous_checkpoints = {
            item["batch_id"]: item for item in existing_batches["checkpoints"]
        }

    batches = []
    for order, group in enumerate(groups):
        payload = [_payload_snapshot(item) for item in group]
        before, after = _select_context(
            group, segments, sections, config["context_budget_tokens"]
        )
        protected_ids = _protected_decisions(payload, protection_index)
        style = copy.deepcopy(memory["style_guide"])
        style_tokens = estimate_tokens(json.dumps(style, ensure_ascii=False, sort_keys=True))
        memory_entries, warnings = _select_memory(
            memory, payload, before, after, set(protected_ids),
            max(0, config["memory_budget_tokens"] - style_tokens),
            memory_runtime,
        )
        payload_ids = [item["segment_id"] for item in payload]
        relevant_snapshot = {
            "memory_entries": memory_entries,
            "protected_decision_ids": protected_ids,
            "style_guide": style,
        }
        dependency_fingerprint = _canonical_hash(relevant_snapshot)
        identity = {
            "document": source_sha,
            "editorial_version": editorial.get("algorithm_version"),
            "editorial_sha256": editorial_hash,
            "payload_range": [payload_ids[0], payload_ids[-1]],
            "payload_ids": payload_ids,
            "config_sha256": config_hash,
            "dependency_fingerprint": dependency_fingerprint,
        }
        batch_id = _stable_id(source_sha, "batch", identity)
        dependency = _dependency_record(
            batch_id, payload_ids, memory_entries, protected_ids, memory,
            editorial_hash, config_hash,
        )
        payload_tokens = sum(item["estimated_tokens"] for item in payload)
        before_tokens = sum(item["estimated_tokens"] for item in before)
        after_tokens = sum(item["estimated_tokens"] for item in after)
        memory_tokens = sum(_memory_cost(item) for item in memory_entries)
        content_input = payload_tokens + before_tokens + after_tokens + memory_tokens + style_tokens
        total_input = content_input + config["reserved_system_tokens"]
        if payload_tokens > config["payload_budget_tokens"]:
            if any(item["estimated_tokens"] > config["payload_budget_tokens"] for item in payload):
                warnings.append("indivisible_segment_exceeds_payload_budget")
            else:
                warnings.append("indivisible_semantic_group_exceeds_payload_budget")
        if total_input > config["usable_input_tokens"]:
            warnings.append("estimated_input_exceeds_usable_budget")
        section = copy.deepcopy(sections[group[0]["segment_id"]])
        batches.append({
            "batch_id": batch_id,
            "source_document_sha256": source_sha,
            "global_order": order,
            "payload_segment_ids": payload_ids,
            "payload": payload,
            "context_before_ids": [item["segment_id"] for item in before],
            "context_before": before,
            "context_after_ids": [item["segment_id"] for item in after],
            "context_after": after,
            "memory_entry_ids": [item["term_id"] for item in memory_entries],
            "memory_entries": memory_entries,
            "preserved_term_ids": sorted(
                item["term_id"] for item in memory_entries if item.get("preserve") is True
            ),
            "protected_decision_ids": protected_ids,
            "relevant_conflict_ids": sorted(
                item["conflict_id"] for item in memory["conflicts"]
                if item.get("target_id") in {entry["term_id"] for entry in memory_entries}
            ),
            "style_guide": style,
            "dependencies": dependency,
            "dependency_fingerprint": dependency_fingerprint,
            "estimated_tokens": {
                "payload": payload_tokens,
                "context_before": before_tokens,
                "context_after": after_tokens,
                "memory": memory_tokens,
                "style_guide": style_tokens,
                "future_system_instructions_reserved": config["reserved_system_tokens"],
                "content_input": content_input,
                "total_input_with_system_reservation": total_input,
                "reserved_output": config["reserved_output_tokens"],
                "model_window": config["max_input_tokens"],
            },
            "segment_range": {
                "first_segment_id": payload_ids[0],
                "last_segment_id": payload_ids[-1],
                "first_global_order": payload[0]["global_order"],
                "last_global_order": payload[-1]["global_order"],
                "first_page": payload[0]["page"],
                "last_page": payload[-1]["page"],
            },
            "section": section,
            "previous_batch_id": None,
            "next_batch_id": None,
            "continuity": {},
            "handoff": _handoff(batch_id),
            "warnings": sorted(set(warnings)),
        })

    for index, batch in enumerate(batches):
        previous_id = batches[index - 1]["batch_id"] if index else None
        next_id = batches[index + 1]["batch_id"] if index + 1 < len(batches) else None
        batch["previous_batch_id"] = previous_id
        batch["next_batch_id"] = next_id
        batch["continuity"] = {
            "previous_batch_id": previous_id,
            "next_batch_id": next_id,
            "same_section_as_previous": bool(index and batch["section"] == batches[index - 1]["section"]),
            "same_section_as_next": bool(index + 1 < len(batches) and batch["section"] == batches[index + 1]["section"]),
        }

    checkpoints = []
    for batch in batches:
        key = tuple(batch["payload_segment_ids"])
        previous = previous_by_payload.get(key)
        if previous is None and previous_batch_list:
            current_ids = set(batch["payload_segment_ids"])
            overlapping = [
                (len(current_ids & set(item["payload_segment_ids"])), item["batch_id"], item)
                for item in previous_batch_list
            ]
            overlap_count, _, candidate = max(overlapping, key=lambda value: (value[0], value[1]))
            if overlap_count:
                previous = candidate
        previous_checkpoint = previous_checkpoints.get(previous["batch_id"]) if previous else None
        checkpoint = _checkpoint(batch, previous, previous_checkpoint)
        if previous and previous.get("batch_id") != batch["batch_id"]:
            batch["handoff"] = _handoff(batch["batch_id"], previous["batch_id"])
        elif previous and previous.get("handoff"):
            batch["handoff"] = copy.deepcopy(previous["handoff"])
        checkpoints.append(checkpoint)

    coverage = validate_coverage(editorial, batches)
    memory_counts = [len(item["memory_entry_ids"]) for item in batches]
    payload_counts = [len(item["payload_segment_ids"]) for item in batches]
    payload_token_counts = [item["estimated_tokens"]["payload"] for item in batches]
    total_input_counts = [
        item["estimated_tokens"]["total_input_with_system_reservation"] for item in batches
    ]
    context_token_counts = [
        item["estimated_tokens"]["context_before"] + item["estimated_tokens"]["context_after"]
        for item in batches
    ]
    glossary_size = len(memory["glossary"])
    result = {
        "schema_version": BATCHING_SCHEMA_VERSION,
        "batching_version": BATCHING_VERSION,
        "document": copy.deepcopy(editorial["source_document"]),
        "source_inputs": {
            "editorial_schema_version": editorial["schema_version"],
            "editorial_algorithm_version": editorial.get("algorithm_version"),
            "editorial_sha256": editorial_hash,
            "memory_schema_version": memory["schema_version"],
            "memory_version": memory.get("memory_version"),
            "memory_sha256": memory_hash,
            "protection_schema_version": protection["schema_version"],
            "protection_ruleset_version": protection["ruleset_version"],
            "protection_sha256": protection_hash,
        },
        "config": config,
        "batches": batches,
        "coverage": coverage,
        "dependencies": [copy.deepcopy(item["dependencies"]) for item in batches],
        "checkpoints": checkpoints,
        "checkpoint_state": {
            "next_pending_batch_id": next(
                (item["batch_id"] for item in checkpoints if item["status"] in {"pending", "stale"}),
                None,
            ),
            "pending_count": sum(item["status"] == "pending" for item in checkpoints),
            "stale_count": sum(item["status"] == "stale" for item in checkpoints),
            "completed_count": sum(item.get("completed") is True for item in checkpoints),
        },
        "warnings": sorted({warning for batch in batches for warning in batch["warnings"]}),
        "summary": {
            "batch_count": len(batches),
            "payload_segment_count": len(segments),
            "minimum_segments_per_batch": min(payload_counts, default=0),
            "maximum_segments_per_batch": max(payload_counts, default=0),
            "average_segments_per_batch": round(
                sum(payload_counts) / len(payload_counts), 3
            ) if payload_counts else 0.0,
            "minimum_payload_tokens": min(payload_token_counts, default=0),
            "maximum_payload_tokens": max(payload_token_counts, default=0),
            "average_payload_tokens": round(
                sum(payload_token_counts) / len(payload_token_counts), 3
            ) if payload_token_counts else 0.0,
            "minimum_total_input_tokens": min(total_input_counts, default=0),
            "maximum_total_input_tokens": max(total_input_counts, default=0),
            "average_total_input_tokens": round(
                sum(total_input_counts) / len(total_input_counts), 3
            ) if total_input_counts else 0.0,
            "minimum_context_tokens": min(context_token_counts, default=0),
            "maximum_context_tokens": max(context_token_counts, default=0),
            "average_context_tokens": round(
                sum(context_token_counts) / len(context_token_counts), 3
            ) if context_token_counts else 0.0,
            "minimum_memory_entries_per_batch": min(memory_counts, default=0),
            "maximum_memory_entries_per_batch": max(memory_counts, default=0),
            "average_memory_entries_per_batch": round(
                sum(memory_counts) / len(memory_counts), 3
            ) if memory_counts else 0.0,
            "average_glossary_percentage_per_batch": round(
                (sum(memory_counts) / len(memory_counts) / glossary_size * 100), 4
            ) if memory_counts and glossary_size else 0.0,
            "batches_with_protected_parts": sum(
                bool(item["protected_decision_ids"]) for item in batches
            ),
        },
    }
    return result


def load_json(path: str | Path) -> dict:
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as source:
            value = json.load(source)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path}: {error.msg}") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSON root in {path} must be an object")
    return value


def write_translation_batches(batches: dict, output: str | Path) -> Path:
    """Atomically write a batching artifact as deterministic UTF-8 JSON."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent,
            prefix=".stolas-batches-", suffix=".tmp", delete=False,
        ) as file:
            temporary = Path(file.name)
            json.dump(batches, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return output
