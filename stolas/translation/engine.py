"""Provider-neutral contextual translation orchestration and resume logic."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .placeholders import prepare_segment
from .providers import (
    PermanentProviderError,
    ProviderError,
    RetryableProviderError,
    TranslationProvider,
)
from .qa import ResponseValidationError, validate_response
from .reconciliation import reconcile_translation


TRANSLATION_SCHEMA_VERSION = "0.1.0"
TRANSLATION_VERSION = "0.1.0"
SUPPORTED_BATCHING_SCHEMAS = frozenset({"0.1.0"})
SUPPORTED_MEMORY_SCHEMAS = frozenset({"0.1.0"})
SUPPORTED_PROTECTION_SCHEMAS = frozenset({"0.2.0"})
SUPPORTED_PROTECTION_RULESETS = frozenset({"0.2.0"})


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_id(source_sha: str, kind: str, value: object) -> str:
    return f"{source_sha[:16]}:{kind}:{_canonical_hash(value)[:24]}"


def _validate_batches(batches: object) -> dict:
    if not isinstance(batches, dict):
        raise ValueError("Translation batches must be a JSON object")
    if batches.get("schema_version") not in SUPPORTED_BATCHING_SCHEMAS:
        raise ValueError(
            f"Unsupported batching schema {batches.get('schema_version')!r}; expected 0.1.0"
        )
    document = batches.get("document")
    if not isinstance(document, dict) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", str(document.get("sha256", ""))
    ):
        raise ValueError("Translation batches have an invalid document sha256")
    values = batches.get("batches")
    if not isinstance(values, list):
        raise ValueError("Translation batches must contain a batches list")
    identifiers = [item.get("batch_id") for item in values if isinstance(item, dict)]
    if len(identifiers) != len(values) or len(identifiers) != len(set(identifiers)):
        raise ValueError("Batch IDs must be unique and non-empty")
    expected_segments = []
    for batch in values:
        payload = batch.get("payload")
        payload_ids = batch.get("payload_segment_ids")
        if not isinstance(payload, list) or not isinstance(payload_ids, list):
            raise ValueError(f"Batch {batch.get('batch_id')!r} has invalid payload fields")
        if [item.get("segment_id") for item in payload] != payload_ids:
            raise ValueError(f"Batch {batch['batch_id']!r} payload IDs do not match payload")
        if batch.get("dependencies", {}).get("batch_id") != batch["batch_id"]:
            raise ValueError(f"Batch {batch['batch_id']!r} has inconsistent dependencies")
        if not isinstance(batch.get("dependency_fingerprint"), str) or not batch[
            "dependency_fingerprint"
        ]:
            raise ValueError(f"Batch {batch['batch_id']!r} has no dependency fingerprint")
        if set(payload_ids) & set(batch.get("context_before_ids", [])) or (
            set(payload_ids) & set(batch.get("context_after_ids", []))
        ):
            raise ValueError(f"Batch {batch['batch_id']!r} mixes payload and context IDs")
        expected_segments.extend(payload_ids)
    if len(expected_segments) != len(set(expected_segments)):
        raise ValueError("A segment appears as payload in more than one batch")
    coverage = batches.get("coverage", {})
    if coverage.get("complete") is not True or coverage.get(
        "actual_payload_segment_count"
    ) != len(expected_segments):
        raise ValueError("Batching artifact does not provide complete payload coverage")
    return batches


def _validate_memory(memory: object, batches: dict) -> dict:
    if not isinstance(memory, dict) or memory.get("schema_version") not in SUPPORTED_MEMORY_SCHEMAS:
        raise ValueError("Unsupported or invalid editorial memory; expected schema 0.1.0")
    if str(memory.get("source_document", {}).get("sha256", "")).lower() != str(
        batches["document"]["sha256"]
    ).lower():
        raise ValueError("Editorial memory and batches reference different documents")
    if batches.get("source_inputs", {}).get("memory_sha256") != _canonical_hash(memory):
        raise ValueError("Translation batches do not match the editorial memory hash")
    if not isinstance(memory.get("glossary"), list) or not isinstance(memory.get("style_guide"), dict):
        raise ValueError("Editorial memory is missing glossary or style guide")
    return memory


def _validate_protection(protection: object, batches: dict) -> dict:
    if not isinstance(protection, dict) or protection.get(
        "schema_version"
    ) not in SUPPORTED_PROTECTION_SCHEMAS:
        raise ValueError("Unsupported or invalid protection artifact; expected schema 0.2.0")
    if protection.get("ruleset_version") not in SUPPORTED_PROTECTION_RULESETS:
        raise ValueError("Unsupported protection ruleset; expected 0.2.0")
    if str(protection.get("source_document", {}).get("sha256", "")).lower() != str(
        batches["document"]["sha256"]
    ).lower():
        raise ValueError("Protection artifact and batches reference different documents")
    if batches.get("source_inputs", {}).get("protection_sha256") != _canonical_hash(protection):
        raise ValueError("Translation batches do not match the protection artifact hash")
    return protection


def _validate_existing(existing: object | None, source_sha: str) -> dict | None:
    if existing is None:
        return None
    if not isinstance(existing, dict) or existing.get("schema_version") != TRANSLATION_SCHEMA_VERSION:
        raise ValueError("Existing translation results have an incompatible schema")
    if str(existing.get("document", {}).get("sha256", "")).lower() != source_sha:
        raise ValueError("Existing translation results reference a different document")
    if not isinstance(existing.get("batch_results"), list) or not isinstance(
        existing.get("segment_results"), dict
    ):
        raise ValueError("Existing translation results are missing batch or segment results")
    return existing


def _glossary_rules(batch: dict) -> list[dict]:
    rules = []
    for entry in batch.get("memory_entries", []):
        status = entry.get("status")
        preserve = entry.get("preserve") is True
        preferred = entry.get("preferred_translation")
        if preserve:
            authority = "mandatory_preservation"
        elif status == "approved" and preferred:
            authority = "mandatory_translation"
        elif status == "candidate":
            authority = "suggested"
        else:
            authority = "ambiguous"
        rules.append({
            "term_id": entry["term_id"],
            "source_term": entry.get("source_term"),
            "variants": sorted(set([
                entry.get("source_term", ""), *entry.get("variants", [])
            ]) - {""}, key=lambda value: (value.casefold(), value)),
            "preferred_translation": preferred,
            "preserve": preserve,
            "status": status,
            "decision_source": entry.get("decision_source"),
            "authority": authority,
        })
    return sorted(rules, key=lambda item: item["term_id"])


def build_translation_request(batch: dict, *, source_sha: str, source_language: str,
                              target_language: str, previous_handoff: dict | None) -> dict:
    """Build the structured provider request for one batch."""
    rules = _glossary_rules(batch)
    preserved = [item for item in batch.get("memory_entries", []) if item.get("preserve") is True]
    payload = [
        prepare_segment(
            segment, preserved, source_sha=source_sha, batch_id=batch["batch_id"]
        )
        for segment in batch["payload"]
    ]
    protected_decisions = sorted({
        identifier for segment in payload for binding in segment["protected_bindings"]
        for identifier in binding["decision_ids"]
    })
    return {
        "schema_version": "0.1.0",
        "batch_id": batch["batch_id"],
        "source_language": source_language,
        "target_language": target_language,
        "payload": payload,
        "context_before": copy.deepcopy(batch.get("context_before", [])),
        "context_after": copy.deepcopy(batch.get("context_after", [])),
        "section": copy.deepcopy(batch.get("section", {})),
        "memory_entry_ids": copy.deepcopy(batch.get("memory_entry_ids", [])),
        "glossary_rules": rules,
        "style_guide": copy.deepcopy(batch.get("style_guide", {})),
        "protected_decision_ids": protected_decisions,
        "previous_handoff": copy.deepcopy(previous_handoff),
        "output_contract": {
            "schema_version": "0.1.0",
            "mapping": "one result per payload segment_id",
            "context_is_not_output": True,
            "placeholders_must_be_preserved_exactly_once": True,
            "memory_master_is_read_only": True,
        },
    }


def _proposal_records(source_sha: str, batch_id: str, segments: list[dict]) -> list[dict]:
    records = []
    for segment in segments:
        for proposal in segment.get("proposals", []):
            identity = {
                "batch": batch_id, "segment": segment["segment_id"], "proposal": proposal
            }
            records.append({
                "proposal_id": _stable_id(source_sha, "translation-proposal", identity),
                "batch_id": batch_id,
                "segment_id": segment["segment_id"],
                "status": "pending_review",
                **copy.deepcopy(proposal),
            })
    return sorted(records, key=lambda item: item["proposal_id"])


def _compact_handoff(source_sha: str, batch_id: str, value: dict) -> dict:
    allowed_lists = (
        "new_terms", "decisions_proposed", "important_referents", "ambiguities",
        "pending_references", "continuity_notes",
    )
    result = {
        "handoff_id": _stable_id(source_sha, "handoff", {"batch": batch_id, "value": value}),
        "batch_id": batch_id,
        "status": "approved",
        "summary": value["summary"],
    }
    for field in allowed_lists:
        item = value.get(field, [])
        if not isinstance(item, list):
            raise ResponseValidationError(f"Handoff field {field!r} must be a list")
        result[field] = copy.deepcopy(item[:50])
    return result


def _blocked_result(batch: dict, attempts: int, error: Exception,
                    provider_name: str, retryable: bool) -> dict:
    return {
        "batch_id": batch["batch_id"],
        "payload_segment_ids": copy.deepcopy(batch["payload_segment_ids"]),
        "dependency_fingerprint": batch["dependency_fingerprint"],
        "status": "blocked",
        "state_history": ["pending", "translating", "blocked"],
        "attempts": attempts,
        "provider": provider_name,
        "request_sha256": None,
        "segment_result_ids": [],
        "handoff_id": None,
        "error": {
            "type": type(error).__name__, "message": str(error), "retryable": retryable,
        },
        "dependencies": copy.deepcopy(batch["dependencies"]),
    }


def _result_is_reusable(previous: dict, batch: dict) -> bool:
    return (
        previous.get("status") == "approved"
        and previous.get("batch_id") == batch["batch_id"]
        and previous.get("dependency_fingerprint") == batch["dependency_fingerprint"]
        and previous.get("payload_segment_ids") == batch["payload_segment_ids"]
    )


def translate_batches(batches: dict, memory: dict, protection: dict, *,
                      provider: TranslationProvider, existing_results: dict | None = None,
                      source_language: str = "en", target_language: str = "pt-BR",
                      max_retries: int = 2, batch_ids: list[str] | None = None) -> dict:
    """Execute structured translation with bounded retries, resume and validation."""
    batches = _validate_batches(batches)
    memory = _validate_memory(memory, batches)
    protection = _validate_protection(protection, batches)
    source_sha = batches["document"]["sha256"].lower()
    existing_results = _validate_existing(existing_results, source_sha)
    if not isinstance(provider, TranslationProvider):
        raise ValueError("provider must implement TranslationProvider")
    if not isinstance(max_retries, int) or max_retries < 0:
        raise ValueError("max_retries must be a non-negative integer")
    available_ids = {item["batch_id"] for item in batches["batches"]}
    selected_ids = set(batch_ids) if batch_ids is not None else available_ids
    unknown = sorted(selected_ids - available_ids)
    if unknown:
        raise ValueError(f"Unknown requested batch IDs: {unknown}")

    batch_hash = _canonical_hash(batches)
    memory_hash = _canonical_hash(memory)
    protection_hash = _canonical_hash(protection)
    configuration = {
        "provider": provider.name,
        "source_language": source_language,
        "target_language": target_language,
        "max_retries": max_retries,
    }
    source_inputs = {
        "batching_schema_version": batches["schema_version"],
        "batching_version": batches.get("batching_version"),
        "batches_sha256": batch_hash,
        "memory_schema_version": memory["schema_version"],
        "memory_version": memory.get("memory_version"),
        "memory_sha256": memory_hash,
        "protection_schema_version": protection["schema_version"],
        "protection_ruleset_version": protection["ruleset_version"],
        "protection_sha256": protection_hash,
        "configuration_sha256": _canonical_hash(configuration),
    }
    if existing_results and batch_ids is None and existing_results.get("source_inputs") == source_inputs and (
        all(item.get("status") == "approved" for item in existing_results["batch_results"])
    ):
        return copy.deepcopy(existing_results)

    previous_batches = {}
    previous_segments = {}
    previous_handoffs = {}
    if existing_results:
        previous_batches = {
            tuple(item.get("payload_segment_ids", [])): item
            for item in existing_results["batch_results"]
        }
        previous_segments = existing_results.get("segment_results", {})
        previous_handoffs = existing_results.get("handoffs", {})

    batch_results = []
    segment_results = {}
    handoffs = {}
    proposals = []
    previous_handoff = None
    for batch in batches["batches"]:
        key = tuple(batch["payload_segment_ids"])
        previous = previous_batches.get(key)
        if _result_is_reusable(previous or {}, batch):
            batch_results.append(copy.deepcopy(previous))
            for identifier in batch["payload_segment_ids"]:
                if identifier in previous_segments:
                    segment_results[identifier] = copy.deepcopy(previous_segments[identifier])
            if previous.get("handoff_id") in previous_handoffs:
                handoff = copy.deepcopy(previous_handoffs[previous["handoff_id"]])
                handoffs[handoff["handoff_id"]] = handoff
                previous_handoff = handoff
            continue
        if batch["batch_id"] not in selected_ids:
            stale = previous is not None
            batch_results.append({
                "batch_id": batch["batch_id"],
                "payload_segment_ids": copy.deepcopy(batch["payload_segment_ids"]),
                "dependency_fingerprint": batch["dependency_fingerprint"],
                "status": "stale" if stale else "pending",
                "state_history": ["stale"] if stale else ["pending"],
                "attempts": 0, "provider": provider.name, "request_sha256": None,
                "segment_result_ids": [], "handoff_id": None, "error": None,
                "dependencies": copy.deepcopy(batch["dependencies"]),
            })
            previous_handoff = None
            continue

        request = build_translation_request(
            batch, source_sha=source_sha, source_language=source_language,
            target_language=target_language, previous_handoff=previous_handoff,
        )
        request_hash = _canonical_hash(request)
        attempts = 0
        response = None
        failure = None
        while attempts <= max_retries:
            attempts += 1
            try:
                response = provider.translate_batch(copy.deepcopy(request))
                failure = None
                break
            except RetryableProviderError as error:
                failure = error
                if attempts > max_retries:
                    break
            except (PermanentProviderError, ProviderError) as error:
                failure = error
                break
        if failure is not None:
            batch_results.append(_blocked_result(
                batch, attempts, failure, provider.name,
                isinstance(failure, RetryableProviderError),
            ))
            previous_handoff = None
            continue
        try:
            validated, handoff_value = validate_response(request, response)
            handoff = _compact_handoff(source_sha, batch["batch_id"], handoff_value)
        except ResponseValidationError as error:
            batch_results.append(_blocked_result(
                batch, attempts, error, provider.name, False
            ))
            previous_handoff = None
            continue
        for item in validated:
            segment_results[item["segment_id"]] = item
        new_proposals = _proposal_records(source_sha, batch["batch_id"], validated)
        proposals.extend(new_proposals)
        handoffs[handoff["handoff_id"]] = handoff
        previous_handoff = handoff
        batch_results.append({
            "batch_id": batch["batch_id"],
            "payload_segment_ids": copy.deepcopy(batch["payload_segment_ids"]),
            "dependency_fingerprint": batch["dependency_fingerprint"],
            "status": "approved",
            "state_history": [
                "pending", "translating", "translated", "validating", "approved"
            ],
            "attempts": attempts,
            "provider": provider.name,
            "request_sha256": request_hash,
            "segment_result_ids": [item["segment_id"] for item in validated],
            "handoff_id": handoff["handoff_id"],
            "error": None,
            "dependencies": copy.deepcopy(batch["dependencies"]),
        })

    expected_ids = [
        identifier for batch in batches["batches"] for identifier in batch["payload_segment_ids"]
    ]
    approved_batch_ids = {
        item["batch_id"] for item in batch_results if item["status"] == "approved"
    }
    approved_expected = [
        identifier for batch in batches["batches"] if batch["batch_id"] in approved_batch_ids
        for identifier in batch["payload_segment_ids"]
    ]
    translated_ids = list(segment_results)
    missing_from_approved = [item for item in approved_expected if item not in segment_results]
    unexpected = sorted(set(translated_ids) - set(expected_ids))
    if missing_from_approved or unexpected:
        raise ValueError(
            f"Internal translation coverage failure; missing={missing_from_approved}, extras={unexpected}"
        )
    proposals = sorted(
        {item["proposal_id"]: item for item in proposals}.values(),
        key=lambda item: item["proposal_id"],
    )
    issues = reconcile_translation(segment_results, memory, proposals, source_sha)
    coverage = {
        "expected_segment_count": len(expected_ids),
        "approved_segment_count": len(segment_results),
        "pending_segment_count": len(expected_ids) - len(segment_results),
        "missing_from_approved_batch_ids": missing_from_approved,
        "unexpected_segment_ids": unexpected,
        "complete": len(segment_results) == len(expected_ids) and not unexpected,
    }
    result = {
        "schema_version": TRANSLATION_SCHEMA_VERSION,
        "translation_version": TRANSLATION_VERSION,
        "document": copy.deepcopy(batches["document"]),
        "source_language": source_language,
        "target_language": target_language,
        "provider": provider.name,
        "source_inputs": source_inputs,
        "batch_results": batch_results,
        "segment_results": segment_results,
        "handoffs": handoffs,
        "proposals": proposals,
        "issues": issues,
        "coverage": coverage,
        "dependencies": {
            item["batch_id"]: copy.deepcopy(item["dependencies"])
            for item in batch_results
        },
        "checkpoint_state": {
            "next_batch_id": next(
                (item["batch_id"] for item in batch_results if item["status"] != "approved"),
                None,
            ),
            "approved_count": sum(item["status"] == "approved" for item in batch_results),
            "pending_count": sum(item["status"] == "pending" for item in batch_results),
            "blocked_count": sum(item["status"] == "blocked" for item in batch_results),
            "stale_count": sum(item["status"] == "stale" for item in batch_results),
        },
        "summary": {
            "batch_count": len(batch_results),
            "approved_batch_count": sum(item["status"] == "approved" for item in batch_results),
            "blocked_batch_count": sum(item["status"] == "blocked" for item in batch_results),
            "segment_count": len(segment_results),
            "protected_placeholder_count": sum(
                len(item["protected_placeholders"]) for item in segment_results.values()
            ),
            "handoff_count": len(handoffs),
            "proposal_count": len(proposals),
            "issue_count": len(issues),
            "provider_call_count": sum(item["attempts"] for item in batch_results),
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


def write_translation_results(results: dict, output: str | Path) -> Path:
    """Atomically write translation results as deterministic UTF-8 JSON."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent,
            prefix=".stolas-translation-", suffix=".tmp", delete=False,
        ) as file:
            temporary = Path(file.name)
            json.dump(results, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return output
