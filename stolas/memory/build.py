"""Build and merge a versioned editorial memory without translating text."""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata


MEMORY_SCHEMA_VERSION = "0.1.0"
MEMORY_RULESET_VERSION = "0.1.0"
SUPPORTED_EDITORIAL_SCHEMAS = frozenset({"0.1.0"})
SUPPORTED_PROTECTION_SCHEMAS = frozenset({"0.2.0"})
SUPPORTED_PROTECTION_RULESETS = frozenset({"0.2.0"})
SUPPORTED_MEMORY_SCHEMAS = frozenset({"0.1.0"})
SUPPORTED_OVERRIDE_SCHEMAS = frozenset({"0.1.0"})
SUPPORTED_STYLE_SCHEMAS = frozenset({"0.1.0"})
CATEGORIES = frozenset({
    "technical_term", "proper_name", "ritual_term", "preserved_foreign",
    "transliteration", "title", "place", "organization", "ambiguous", "other",
})
STYLE_FIELDS = frozenset({
    "formality", "language_variant", "title_treatment", "quotation_style",
    "italics", "numerals", "capitalization", "terminology",
    "foreignism_preservation", "tone", "notes",
})
STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "before", "but", "by",
    "can", "do", "does", "each", "for", "from", "had", "has", "have", "he",
    "her", "here", "him", "his", "how", "i", "if", "in", "into", "is", "it",
    "its", "may", "more", "most", "not", "of", "on", "one", "only", "or", "our",
    "out", "she", "should", "so", "some", "such", "than", "that", "the", "their",
    "them", "then", "there", "these", "they", "this", "those", "through", "to",
    "up", "use", "used", "was", "we", "were", "what", "when", "where", "which",
    "who", "will", "with", "would", "you", "your",
})


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_term(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = value.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", value).strip(" \t\r\n.,;:!?()[]{}\"'").casefold()


def _stable_id(source_sha: str, kind: str, identity: object) -> str:
    return f"{source_sha[:16]}:{kind}:{_canonical_hash(identity)[:24]}"


def _validate_editorial(editorial: object) -> dict:
    if not isinstance(editorial, dict):
        raise ValueError("Editorial artifact must be a JSON object")
    if editorial.get("schema_version") not in SUPPORTED_EDITORIAL_SCHEMAS:
        raise ValueError(
            f"Unsupported editorial schema {editorial.get('schema_version')!r}; expected 0.1.0"
        )
    document = editorial.get("source_document")
    if not isinstance(document, dict) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", str(document.get("sha256", ""))
    ):
        raise ValueError("Editorial artifact has an invalid source document identity")
    if not isinstance(editorial.get("editorial_units"), list):
        raise ValueError("Editorial artifact editorial_units must be a list")
    if not isinstance(editorial.get("segments"), list):
        raise ValueError("Editorial artifact segments must be a list")
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
    editorial_document = editorial["source_document"]
    if not isinstance(document, dict) or (
        str(document.get("sha256", "")).lower()
        != str(editorial_document["sha256"]).lower()
    ):
        raise ValueError("Protection and editorial artifacts reference different documents")
    expected = editorial.get("source_protection")
    if not isinstance(expected, dict) or expected.get("sha256") != _canonical_hash(protection):
        raise ValueError("Protection artifact does not match the hash recorded by editorial structure")
    if not isinstance(protection.get("classifications"), list):
        raise ValueError("Protection artifact classifications must be a list")
    return protection


def _validate_existing(existing: object | None, source_sha: str) -> dict | None:
    if existing is None:
        return None
    if not isinstance(existing, dict):
        raise ValueError("Existing memory must be a JSON object")
    if existing.get("schema_version") not in SUPPORTED_MEMORY_SCHEMAS:
        raise ValueError(
            f"Unsupported memory schema {existing.get('schema_version')!r}; expected 0.1.0"
        )
    document = existing.get("source_document")
    if not isinstance(document, dict) or str(document.get("sha256", "")).lower() != source_sha:
        raise ValueError("Existing memory references a different source document")
    if not isinstance(existing.get("memory_version"), int) or existing["memory_version"] < 1:
        raise ValueError("Existing memory has an invalid memory_version")
    for field in ("glossary", "decisions", "dependencies", "history"):
        if not isinstance(existing.get(field), list):
            raise ValueError(f"Existing memory field {field!r} must be a list")
    return existing


def _validate_overrides(overrides: object | None, source_sha: str) -> dict | None:
    if overrides is None:
        return None
    if not isinstance(overrides, dict):
        raise ValueError("Memory overrides must be a JSON object")
    if overrides.get("schema_version") not in SUPPORTED_OVERRIDE_SCHEMAS:
        raise ValueError(
            f"Unsupported memory overrides schema {overrides.get('schema_version')!r}; expected 0.1.0"
        )
    expected_sha = overrides.get("source_document_sha256")
    if expected_sha is not None and str(expected_sha).lower() != source_sha:
        raise ValueError("Memory overrides reference a different source document")
    term_overrides = overrides.get("term_overrides", [])
    style_overrides = overrides.get("style_overrides", {})
    if not isinstance(term_overrides, list) or not isinstance(style_overrides, dict):
        raise ValueError("Memory overrides term_overrides/style_overrides have invalid types")
    seen = set()
    for index, entry in enumerate(term_overrides):
        if not isinstance(entry, dict):
            raise ValueError(f"Term override {index} must be an object")
        if not entry.get("term_id") and not isinstance(entry.get("source_term"), str):
            raise ValueError(f"Term override {index} requires term_id or source_term")
        identity = (entry.get("term_id"), _normalize_term(entry.get("source_term", "")), entry.get("context_key"))
        if identity in seen:
            raise ValueError(f"Duplicate term override target at index {index}")
        seen.add(identity)
        if entry.get("category") is not None and entry["category"] not in CATEGORIES:
            raise ValueError(f"Term override {index} has unsupported category")
        if entry.get("preserve") is True and entry.get("preferred_translation"):
            raise ValueError(f"Term override {index} cannot preserve and translate simultaneously")
    unknown_style = set(style_overrides) - STYLE_FIELDS - {"target_language"}
    if unknown_style:
        raise ValueError(f"Unsupported style override fields: {', '.join(sorted(unknown_style))}")
    return overrides


def _validate_style_guide(style_guide: object | None) -> dict | None:
    if style_guide is None:
        return None
    if not isinstance(style_guide, dict):
        raise ValueError("Style guide must be a JSON object")
    if style_guide.get("schema_version") not in SUPPORTED_STYLE_SCHEMAS:
        raise ValueError(
            f"Unsupported style guide schema {style_guide.get('schema_version')!r}; expected 0.1.0"
        )
    preferences = style_guide.get("preferences", {})
    if not isinstance(preferences, dict):
        raise ValueError("Style guide preferences must be an object")
    unknown = set(preferences) - STYLE_FIELDS
    if unknown:
        raise ValueError(f"Unsupported style guide fields: {', '.join(sorted(unknown))}")
    return style_guide


def _context_excerpt(text: str, start: int, length: int, limit: int = 120) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    relative = min(max(start, 0), len(normalized))
    left = max(0, relative - limit // 2)
    right = min(len(normalized), left + limit)
    left = max(0, right - limit)
    excerpt = normalized[left:right]
    return ("…" if left else "") + excerpt + ("…" if right < len(normalized) else "")


def _occurrence(unit: dict, segment_id: str | None, start: int, term: str,
                linguistic_role: str | None) -> dict:
    return {
        "editorial_id": unit["editorial_id"],
        "segment_id": segment_id,
        "page": unit["page"],
        "position": {"reading_order": unit["reading_order"], "character_start": start},
        "context_excerpt": _context_excerpt(unit.get("text", ""), start, len(term)),
        "linguistic_role": linguistic_role,
    }


def _occurrence_key(value: dict) -> tuple:
    position = value.get("position", {})
    return (
        value.get("editorial_id"), value.get("segment_id"), value.get("page"),
        position.get("reading_order"), position.get("character_start"),
        value.get("linguistic_role"),
    )


def _language_from_decision(decision: dict, default: str | None) -> str | None:
    for evidence in decision.get("evidence", []):
        if isinstance(evidence, str) and evidence.startswith("local_language="):
            return evidence.split("=", 1)[1] or default
    return default


def _protected_category(role: str | None, reason: str | None) -> str:
    if role in {"phonetic_formula", "pronunciation_literal"}:
        return "transliteration"
    if role == "ritual_formula":
        return "ritual_term"
    if role == "proper_name":
        return "proper_name"
    if reason == "foreign_language_preserved" or role == "foreign_language_literal":
        return "preserved_foreign"
    return "preserved_foreign"


def _candidate(source_sha: str, term: str, context_key: str, category: str,
               language: str | None, preserve: bool | None, confidence: float,
               suggested_action: str, occurrence: dict, evidence: list[str]) -> dict:
    normalized = _normalize_term(term)
    identity = {"document": source_sha, "term": normalized, "context": context_key}
    return {
        "term_id": _stable_id(source_sha, "term", identity),
        "source_term": term.strip(),
        "normalized_term": normalized,
        "variants": [term.strip()],
        "language": language,
        "preferred_translation": None,
        "preserve": preserve,
        "category": category,
        "context_key": context_key,
        "contexts": sorted(set(filter(None, [occurrence.get("linguistic_role")]))),
        "confidence": round(max(0.0, min(float(confidence), 1.0)), 3),
        "decision_source": "automatic_candidate",
        "status": "candidate" if preserve is None else "approved",
        "suggested_action": suggested_action,
        "occurrences": [occurrence],
        "first_occurrence": occurrence,
        "last_occurrence": occurrence,
        "notes": [],
        "version": 1,
        "evidence": sorted(set(evidence)),
        "decision_id": None,
    }


def _merge_candidate_records(records: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}
    for item in records:
        key = (item["normalized_term"], item["context_key"])
        if key not in merged:
            merged[key] = copy.deepcopy(item)
            continue
        target = merged[key]
        target["variants"] = sorted(
            set(target["variants"] + item["variants"]),
            key=lambda value: (value.casefold(), value),
        )
        occurrences = {_occurrence_key(value): value for value in target["occurrences"]}
        for occurrence in item["occurrences"]:
            occurrences[_occurrence_key(occurrence)] = occurrence
        target["occurrences"] = sorted(occurrences.values(), key=_occurrence_key)
        target["contexts"] = sorted(set(target["contexts"] + item["contexts"]))
        target["evidence"] = sorted(set(target["evidence"] + item["evidence"]))
        target["confidence"] = max(target["confidence"], item["confidence"])
    for item in merged.values():
        item["occurrences"].sort(key=_occurrence_key)
        item["first_occurrence"] = item["occurrences"][0]
        item["last_occurrence"] = item["occurrences"][-1]
        item["frequency"] = len(item["occurrences"])
    return sorted(merged.values(), key=lambda item: (
        item["normalized_term"], item["context_key"], item["term_id"]
    ))


def _extract_candidates(editorial: dict, protection: dict) -> list[dict]:
    source_sha = editorial["source_document"]["sha256"].lower()
    segment_by_editorial = {
        item["editorial_id"]: item["segment_id"] for item in editorial["segments"]
    }
    decisions = {item.get("id"): item for item in protection["classifications"]}
    language_info = protection.get("source_document", {}).get("predominant_language", {})
    document_language = language_info.get("language") if isinstance(language_info, dict) else None
    records = []
    token_occurrences: dict[str, list[tuple[str, dict, bool]]] = defaultdict(list)
    title_occurrences: dict[str, list[tuple[str, dict]]] = defaultdict(list)

    for unit in editorial["editorial_units"]:
        segment_id = segment_by_editorial.get(unit["editorial_id"])
        text = unit.get("text", "")
        cursor = 0
        protected_ranges = []
        for part in unit.get("parts", []):
            part_text = part.get("text", "")
            start = text.find(part_text, cursor)
            if start < 0:
                start = cursor
            cursor = start + len(part_text)
            if part.get("type") != "protected" or not _normalize_term(part_text):
                continue
            protected_ranges.append((start, cursor))
            referenced = [decisions[identifier] for identifier in part.get("decision_ids", []) if identifier in decisions]
            decision = referenced[0] if referenced else {}
            role = decision.get("linguistic_role") or "protected_literal"
            reason = decision.get("reason")
            confidence = decision.get("confidence", 1.0 if decision.get("decision_source") == "rule" else 0.85)
            occurrence = _occurrence(unit, segment_id, start, part_text, role)
            records.append(_candidate(
                source_sha, part_text, f"role:{role}", _protected_category(role, reason),
                _language_from_decision(decision, None), True, confidence,
                "preserve", occurrence,
                ["protection_classification=protected", f"linguistic_role={role}", *decision.get("evidence", [])],
            ))

        if unit.get("editorial_type") in {"heading", "subheading"}:
            token_matches = []
        else:
            token_matches = re.finditer(r"\b[^\W\d_][\w'’\-]{2,}\b", text, re.UNICODE)
        for match in token_matches:
            if any(start <= match.start() < end for start, end in protected_ranges):
                continue
            token = match.group(0)
            normalized = _normalize_term(token)
            if not normalized or normalized in STOPWORDS:
                continue
            occurrence = _occurrence(unit, segment_id, match.start(), token, None)
            capitalized = token[:1].isupper() and not token.isupper()
            token_occurrences[normalized].append((token, occurrence, capitalized))

        if unit.get("editorial_type") in {"heading", "subheading"}:
            normalized = _normalize_term(text)
            if normalized:
                title_occurrences[normalized].append((text.strip(), _occurrence(
                    unit, segment_id, 0, text, "editorial_title"
                )))

    for normalized, occurrences in token_occurrences.items():
        frequency = len(occurrences)
        capitalized_count = sum(item[2] for item in occurrences)
        non_initial_capitalized = sum(
            item[2] and item[1]["position"]["character_start"] > 0
            for item in occurrences
        )
        is_name = (
            frequency >= 2 and capitalized_count / frequency >= 0.6
            and non_initial_capitalized >= 2
        )
        hyphenated = any("-" in item[0] for item in occurrences)
        technical = hyphenated
        if not (is_name or technical or frequency >= 12):
            continue
        category = "proper_name" if is_name else "technical_term" if technical else "other"
        context_key = f"lexical:{category}"
        confidence = min(0.88, 0.5 + min(frequency, 10) * 0.035 + (0.08 if is_name else 0))
        action = "review_name" if is_name else "review_translation"
        for term, occurrence, _ in occurrences:
            records.append(_candidate(
                source_sha, term, context_key, category, document_language,
                None, confidence, action, occurrence,
                [f"frequency={frequency}", "capitalized_recurrence" if is_name else "lexical_recurrence"],
            ))

    for normalized, occurrences in title_occurrences.items():
        if len(occurrences) < 2:
            continue
        for term, occurrence in occurrences:
            records.append(_candidate(
                source_sha, term, "editorial:title", "title", document_language,
                None, min(0.9, 0.64 + len(occurrences) * 0.04),
                "review_title_treatment", occurrence,
                [f"title_frequency={len(occurrences)}"],
            ))
    return _merge_candidate_records(records)


def _entry_value(entry: dict) -> dict:
    return {
        "preferred_translation": entry.get("preferred_translation"),
        "preserve": entry.get("preserve"),
        "category": entry.get("category"),
        "language": entry.get("language"),
        "context_key": entry.get("context_key"),
    }


def _decision(source_sha: str, decision_type: str, target_id: str,
              previous_value: object, current_value: object, source: str,
              logical_version: int, context: dict, confidence: float,
              dependencies: list[str] | None = None) -> dict:
    identity = {
        "type": decision_type, "target": target_id,
        "previous": previous_value, "current": current_value,
        "source": source, "version": logical_version,
    }
    return {
        "decision_id": _stable_id(source_sha, "decision", identity),
        "decision_type": decision_type,
        "target_id": target_id,
        "previous_value": previous_value,
        "current_value": current_value,
        "source": source,
        "context": context,
        "logical_version": logical_version,
        "decision_source": "human" if source in {"human_override", "human_style_guide"} else "automatic",
        "confidence": round(float(confidence), 3),
        "dependencies": sorted(set(dependencies or [])),
    }


def _merge_occurrences(existing: dict, candidate: dict) -> bool:
    changed = False
    occurrences = {_occurrence_key(value): value for value in existing.get("occurrences", [])}
    before = len(occurrences)
    for occurrence in candidate.get("occurrences", []):
        occurrences[_occurrence_key(occurrence)] = occurrence
    if len(occurrences) != before:
        changed = True
    existing["occurrences"] = sorted(occurrences.values(), key=_occurrence_key)
    if existing["occurrences"]:
        existing["first_occurrence"] = existing["occurrences"][0]
        existing["last_occurrence"] = existing["occurrences"][-1]
    existing["frequency"] = len(existing["occurrences"])
    variants = sorted(
        set(existing.get("variants", []) + candidate.get("variants", [])),
        key=lambda value: (value.casefold(), value),
    )
    if variants != existing.get("variants", []):
        existing["variants"] = variants
        changed = True
    contexts = sorted(set(existing.get("contexts", []) + candidate.get("contexts", [])))
    if contexts != existing.get("contexts", []):
        existing["contexts"] = contexts
        changed = True
    evidence = sorted(set(existing.get("evidence", []) + candidate.get("evidence", [])))
    if evidence != existing.get("evidence", []):
        existing["evidence"] = evidence
        changed = True
    return changed


def _initial_style(target_language: str) -> dict:
    defaults = {
        "formality": None,
        "language_variant": target_language,
        "title_treatment": None,
        "quotation_style": None,
        "italics": None,
        "numerals": None,
        "capitalization": None,
        "terminology": None,
        "foreignism_preservation": None,
        "tone": None,
        "notes": None,
    }
    return {
        "target_language": target_language,
        "preferences": {
            key: {"value": value, "source": "system_default", "confidence": 1.0 if key == "language_variant" else 0.0}
            for key, value in defaults.items()
        },
    }


def _apply_style(style: dict, style_guide: dict | None, overrides: dict | None) -> tuple[dict, list[tuple]]:
    result = copy.deepcopy(style)
    changes = []
    if style_guide is not None:
        if style_guide.get("target_language") is not None:
            previous = result.get("target_language")
            current = style_guide["target_language"]
            if previous != current:
                result["target_language"] = current
                changes.append(("target_language", previous, current, "human_style_guide"))
        for key, value in sorted(style_guide.get("preferences", {}).items()):
            previous = result["preferences"].get(key, {}).get("value")
            current = value.get("value") if isinstance(value, dict) else value
            confidence = value.get("confidence", 1.0) if isinstance(value, dict) else 1.0
            if previous != current or result["preferences"].get(key, {}).get("source") != "human_style_guide":
                result["preferences"][key] = {
                    "value": current, "source": "human_style_guide",
                    "confidence": round(float(confidence), 3),
                }
                changes.append((key, previous, current, "human_style_guide"))
    if overrides is not None:
        for key, value in sorted(overrides.get("style_overrides", {}).items()):
            if key == "target_language":
                previous = result.get("target_language")
                if previous != value:
                    result["target_language"] = value
                    changes.append((key, previous, value, "human_override"))
                continue
            previous = result["preferences"].get(key, {}).get("value")
            if previous != value or result["preferences"].get(key, {}).get("source") != "human_override":
                result["preferences"][key] = {
                    "value": value, "source": "human_override", "confidence": 1.0,
                }
                changes.append((key, previous, value, "human_override"))
    return result, changes


def _apply_term_overrides(glossary: list[dict], overrides: dict | None,
                          source_sha: str, version: int) -> tuple[list[dict], list[dict], set[str]]:
    if overrides is None:
        return glossary, [], set()
    decisions = []
    changed_ids: set[str] = set()
    by_id = {item["term_id"]: item for item in glossary}
    for override in overrides.get("term_overrides", []):
        if override.get("term_id"):
            targets = [by_id[override["term_id"]]] if override["term_id"] in by_id else []
            if not targets:
                raise ValueError(f"Unknown term override target {override['term_id']!r}")
        else:
            normalized = _normalize_term(override["source_term"])
            targets = [
                item for item in glossary
                if item["normalized_term"] == normalized
                and (override.get("context_key") is None or item["context_key"] == override["context_key"])
            ]
            if not targets:
                context_key = override.get("context_key") or "override:global"
                identity = {"document": source_sha, "term": normalized, "context": context_key}
                targets = [{
                    "term_id": _stable_id(source_sha, "term", identity),
                    "source_term": override["source_term"].strip(),
                    "normalized_term": normalized,
                    "variants": [override["source_term"].strip()],
                    "language": override.get("language"),
                    "preferred_translation": None,
                    "preserve": None,
                    "category": override.get("category", "other"),
                    "context_key": context_key,
                    "contexts": [], "confidence": 1.0,
                    "decision_source": "human_override", "status": "approved",
                    "suggested_action": "human_decision", "occurrences": [],
                    "first_occurrence": None, "last_occurrence": None,
                    "frequency": 0, "notes": [], "version": 1,
                    "evidence": ["human_override"], "decision_id": None,
                }]
                glossary.extend(targets)
                by_id[targets[0]["term_id"]] = targets[0]
        for entry in targets:
            previous = _entry_value(entry)
            previous_source = entry.get("decision_source")
            for field in ("preferred_translation", "preserve", "category", "language"):
                if field in override:
                    entry[field] = override[field]
            notes = override.get("notes")
            if notes is not None:
                note_values = [notes] if isinstance(notes, str) else list(notes)
                entry["notes"] = sorted(set(entry.get("notes", []) + note_values))
            entry["confidence"] = round(float(override.get("confidence", 1.0)), 3)
            entry["decision_source"] = "human_override"
            entry["status"] = "approved"
            current = _entry_value(entry)
            if (current == previous and entry.get("decision_id")
                    and previous_source == "human_override"):
                continue
            entry["version"] = int(entry.get("version", 0)) + (1 if entry.get("decision_id") else 0)
            decision = _decision(
                source_sha, "glossary_term", entry["term_id"], previous, current,
                "human_override", version,
                {"reason": override.get("reason", "editorial_override"), "context_key": entry["context_key"]},
                entry["confidence"],
            )
            decisions.append(decision)
            entry["decision_id"] = decision["decision_id"]
            changed_ids.add(entry["term_id"])
    glossary.sort(key=lambda item: (item["normalized_term"], item["context_key"], item["term_id"]))
    return glossary, decisions, changed_ids


def _cross_references(glossary: list[dict], source_sha: str) -> list[dict]:
    by_surface: dict[str, list[dict]] = defaultdict(list)
    for entry in glossary:
        by_surface[entry["normalized_term"]].append(entry)
    result = []
    for normalized, entries in by_surface.items():
        context_keys = {item["context_key"] for item in entries}
        if len(context_keys) < 2:
            continue
        term_ids = sorted(item["term_id"] for item in entries)
        identity = {"term": normalized, "term_ids": term_ids}
        result.append({
            "reference_id": _stable_id(source_sha, "crossref", identity),
            "relation": "same_surface_different_context",
            "term_ids": term_ids,
            "normalized_term": normalized,
        })
    return sorted(result, key=lambda item: item["reference_id"])


def _conflicts(glossary: list[dict], decisions: list[dict], source_sha: str) -> list[dict]:
    result = []
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for entry in glossary:
        by_key[(entry["normalized_term"], entry["context_key"])].append(entry)
    for (term, context), entries in by_key.items():
        values = {_canonical_hash(_entry_value(item)) for item in entries if item.get("decision_id")}
        if len(values) <= 1:
            continue
        identity = {"term": term, "context": context, "ids": sorted(item["term_id"] for item in entries)}
        result.append({
            "conflict_id": _stable_id(source_sha, "conflict", identity),
            "type": "incompatible_term_decisions",
            "normalized_term": term, "context_key": context,
            "term_ids": identity["ids"], "status": "unresolved",
        })
    for decision in decisions:
        if decision.get("decision_source") != "human":
            continue
        previous = decision.get("previous_value")
        current = decision.get("current_value")
        if previous is None or previous == current:
            continue
        identity = {"decision": decision["decision_id"], "target": decision["target_id"]}
        result.append({
            "conflict_id": _stable_id(source_sha, "conflict", identity),
            "type": "automatic_or_prior_decision_overridden",
            "target_id": decision["target_id"],
            "decision_id": decision["decision_id"],
            "status": "resolved_by_human",
        })
    return sorted(result, key=lambda item: item["conflict_id"])


def _dependencies_for_decisions(glossary: list[dict], source_sha: str) -> list[dict]:
    result = []
    for entry in glossary:
        decision_id = entry.get("decision_id")
        if not decision_id:
            continue
        for occurrence in entry.get("occurrences", []):
            dependent_id = occurrence.get("segment_id") or occurrence.get("editorial_id")
            dependent_type = "segment" if occurrence.get("segment_id") else "editorial_unit"
            if not dependent_id:
                continue
            identity = {
                "target": entry["term_id"], "decision": decision_id,
                "dependent_type": dependent_type, "dependent_id": dependent_id,
            }
            result.append({
                "dependency_id": _stable_id(source_sha, "dependency", identity),
                "target_id": entry["term_id"], "decision_id": decision_id,
                "dependent_type": dependent_type, "dependent_id": dependent_id,
                "status": "current",
            })
    unique = {item["dependency_id"]: item for item in result}
    return sorted(unique.values(), key=lambda item: item["dependency_id"])


def _mark_stale(existing_dependencies: list[dict], changed_ids: set[str],
                glossary: list[dict], source_sha: str) -> tuple[list[dict], list[dict]]:
    current_decisions = {item["term_id"]: item.get("decision_id") for item in glossary}
    dependencies = []
    stale = []
    for dependency in existing_dependencies:
        item = copy.deepcopy(dependency)
        if item.get("target_id") in changed_ids and item.get("decision_id") != current_decisions.get(item["target_id"]):
            item["status"] = "stale"
            identity = {
                "dependency": item["dependency_id"],
                "previous": item.get("decision_id"),
                "current": current_decisions.get(item["target_id"]),
            }
            stale.append({
                "stale_id": _stable_id(source_sha, "stale", identity),
                "dependent_type": item.get("dependent_type"),
                "dependent_id": item.get("dependent_id"),
                "target_id": item.get("target_id"),
                "previous_decision_id": item.get("decision_id"),
                "current_decision_id": current_decisions.get(item["target_id"]),
                "reason": "editorial_decision_changed",
                "status": "stale",
            })
        dependencies.append(item)
    return dependencies, sorted(stale, key=lambda item: item["stale_id"])


def build_editorial_memory(editorial: dict, protection: dict, *,
                           existing_memory: dict | None = None,
                           overrides: dict | None = None,
                           style_guide: dict | None = None,
                           target_language: str = "pt-BR") -> dict:
    """Build or deterministically merge persistent editorial memory."""
    editorial = _validate_editorial(editorial)
    protection = _validate_protection(protection, editorial)
    source_sha = editorial["source_document"]["sha256"].lower()
    existing_memory = _validate_existing(existing_memory, source_sha)
    overrides = _validate_overrides(overrides, source_sha)
    style_guide = _validate_style_guide(style_guide)
    editorial_hash = _canonical_hash(editorial)
    protection_hash = _canonical_hash(protection)
    override_hash = _canonical_hash(overrides) if overrides is not None else None
    style_hash = _canonical_hash(style_guide) if style_guide is not None else None
    candidates = _extract_candidates(editorial, protection)

    if existing_memory is not None:
        existing_inputs = existing_memory.get("source_inputs", {})
        if (
            existing_inputs.get("editorial_sha256") == editorial_hash
            and existing_inputs.get("protection_sha256") == protection_hash
            and existing_inputs.get("overrides_sha256") == override_hash
            and existing_inputs.get("style_guide_sha256") == style_hash
            and existing_inputs.get("target_language") == target_language
        ):
            return copy.deepcopy(existing_memory)

    version = 1 if existing_memory is None else existing_memory["memory_version"] + 1
    glossary = copy.deepcopy(existing_memory.get("glossary", [])) if existing_memory else []
    decisions = copy.deepcopy(existing_memory.get("decisions", [])) if existing_memory else []
    existing_dependencies = copy.deepcopy(existing_memory.get("dependencies", [])) if existing_memory else []
    changed_ids: set[str] = set()
    new_decisions = []
    candidate_conflicts = []
    by_key = {(item["normalized_term"], item["context_key"]): item for item in glossary}

    for candidate in candidates:
        key = (candidate["normalized_term"], candidate["context_key"])
        if key in by_key:
            entry = by_key[key]
            if _merge_occurrences(entry, candidate):
                changed_ids.add(entry["term_id"])
            if (
                entry.get("decision_source") == "human_override"
                and candidate.get("preserve") is not None
                and entry.get("preserve") is not None
                and candidate["preserve"] != entry["preserve"]
            ):
                identity = {
                    "term": entry["term_id"], "candidate": candidate["preserve"],
                    "human": entry["preserve"], "version": version,
                }
                candidate_conflicts.append({
                    "conflict_id": _stable_id(source_sha, "conflict", identity),
                    "type": "automatic_candidate_conflicts_with_human_decision",
                    "target_id": entry["term_id"],
                    "automatic_value": {"preserve": candidate["preserve"]},
                    "human_value": {"preserve": entry["preserve"]},
                    "status": "resolved_by_existing_human_authority",
                })
            if entry.get("decision_source") != "human_override" and candidate.get("preserve") is True:
                entry["preserve"] = True
                entry["status"] = "approved"
                entry["confidence"] = max(entry.get("confidence", 0.0), candidate["confidence"])
            continue
        entry = copy.deepcopy(candidate)
        glossary.append(entry)
        by_key[key] = entry
        changed_ids.add(entry["term_id"])
        if entry.get("preserve") is not None:
            decision = _decision(
                source_sha, "glossary_term", entry["term_id"], None, _entry_value(entry),
                "protection_artifact", version,
                {"context_key": entry["context_key"], "evidence": entry["evidence"]},
                entry["confidence"],
            )
            entry["decision_id"] = decision["decision_id"]
            new_decisions.append(decision)

    base_style = copy.deepcopy(existing_memory.get("style_guide")) if existing_memory else _initial_style(target_language)
    merged_style, style_changes = _apply_style(base_style, style_guide, overrides)
    if style_guide is None and merged_style.get("target_language") != target_language:
        previous_language = merged_style.get("target_language")
        merged_style["target_language"] = target_language
        style_changes.append((
            "target_language", previous_language, target_language, "system_configuration"
        ))
    for key, previous, current, source in style_changes:
        new_decisions.append(_decision(
            source_sha, "style_preference", f"style:{key}", previous, current,
            source, version, {"field": key}, 1.0,
        ))

    glossary, override_decisions, override_changes = _apply_term_overrides(
        glossary, overrides, source_sha, version
    )
    new_decisions.extend(override_decisions)
    changed_ids.update(override_changes)
    decisions.extend(new_decisions)
    decisions = sorted({item["decision_id"]: item for item in decisions}.values(), key=lambda item: (
        item["logical_version"], item["decision_id"]
    ))

    stale_dependencies, new_stale = _mark_stale(
        existing_dependencies, changed_ids, glossary, source_sha
    )
    current_dependencies = _dependencies_for_decisions(glossary, source_sha)
    dependencies = {
        item["dependency_id"]: item for item in [*stale_dependencies, *current_dependencies]
    }
    stale = copy.deepcopy(existing_memory.get("stale_dependents", [])) if existing_memory else []
    stale.extend(new_stale)
    stale = sorted({item["stale_id"]: item for item in stale}.values(), key=lambda item: item["stale_id"])

    glossary.sort(key=lambda item: (item["normalized_term"], item["context_key"], item["term_id"]))
    history = copy.deepcopy(existing_memory.get("history", [])) if existing_memory else []
    history_identity = {
        "version": version,
        "parent": _canonical_hash(existing_memory) if existing_memory else None,
        "editorial": editorial_hash,
        "protection": protection_hash,
        "overrides": override_hash,
        "style": style_hash,
        "changed": sorted(changed_ids),
        "decisions": sorted(item["decision_id"] for item in new_decisions),
    }
    history.append({
        "revision_id": _stable_id(source_sha, "revision", history_identity),
        "memory_version": version,
        "parent_memory_sha256": history_identity["parent"],
        "event": "initialized" if existing_memory is None else "merged",
        "changed_term_ids": sorted(changed_ids),
        "decision_ids": history_identity["decisions"],
        "source_hashes": {
            "editorial": editorial_hash, "protection": protection_hash,
            "overrides": override_hash, "style_guide": style_hash,
        },
    })

    conflicts = _conflicts(glossary, decisions, source_sha)
    conflicts.extend(candidate_conflicts)
    conflicts = sorted(
        {item["conflict_id"]: item for item in conflicts}.values(),
        key=lambda item: item["conflict_id"],
    )
    cross_references = _cross_references(glossary, source_sha)
    category_counts = Counter(item["category"] for item in glossary)
    result = {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "memory_version": version,
        "ruleset_version": MEMORY_RULESET_VERSION,
        "source_document": copy.deepcopy(editorial["source_document"]),
        "source_inputs": {
            "editorial_schema_version": editorial["schema_version"],
            "editorial_algorithm_version": editorial.get("algorithm_version"),
            "editorial_sha256": editorial_hash,
            "protection_schema_version": protection["schema_version"],
            "protection_ruleset_version": protection["ruleset_version"],
            "protection_sha256": protection_hash,
            "memory_rules_sha256": _canonical_hash({
                "schema": MEMORY_SCHEMA_VERSION, "ruleset": MEMORY_RULESET_VERSION,
                "categories": sorted(CATEGORIES), "style_fields": sorted(STYLE_FIELDS),
            }),
            "overrides_sha256": override_hash,
            "style_guide_sha256": style_hash,
            "target_language": target_language,
        },
        "style_guide": merged_style,
        "glossary": glossary,
        "preserved_terms": sorted(
            item["term_id"] for item in glossary if item.get("preserve") is True
        ),
        "decisions": decisions,
        "translation_decisions": copy.deepcopy(
            existing_memory.get("translation_decisions", []) if existing_memory else []
        ),
        "questions": copy.deepcopy(existing_memory.get("questions", []) if existing_memory else []),
        "exceptions": copy.deepcopy(existing_memory.get("exceptions", []) if existing_memory else []),
        "cross_references": cross_references,
        "conflicts": conflicts,
        "dependencies": sorted(dependencies.values(), key=lambda item: item["dependency_id"]),
        "stale_dependents": stale,
        "history": history,
        "summary": {
            "glossary_entry_count": len(glossary),
            "candidate_count": sum(item["status"] == "candidate" for item in glossary),
            "approved_count": sum(item["status"] == "approved" for item in glossary),
            "preserved_term_count": sum(item.get("preserve") is True for item in glossary),
            "proper_name_count": category_counts.get("proper_name", 0),
            "transliteration_count": category_counts.get("transliteration", 0),
            "technical_term_count": category_counts.get("technical_term", 0),
            "repeated_term_count": sum(item.get("frequency", 0) >= 2 for item in glossary),
            "low_confidence_candidate_count": sum(
                item["status"] == "candidate" and item.get("confidence", 0) < 0.7
                for item in glossary
            ),
            "conflict_count": len(conflicts),
            "decision_count": len(decisions),
            "dependency_count": len(dependencies),
            "stale_dependency_count": len(stale),
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


def write_editorial_memory(memory: dict, output: str | Path) -> Path:
    """Atomically write an editorial memory as UTF-8 JSON."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent,
            prefix=".stolas-memory-", suffix=".tmp", delete=False,
        ) as file:
            temporary = Path(file.name)
            json.dump(memory, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return output
