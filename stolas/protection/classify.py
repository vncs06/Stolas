"""Conservative classification of extracted PDF elements without OCR."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

from .context import analyze_text

PROTECTION_SCHEMA_VERSION = "0.2.0"
RULESET_VERSION = "0.2.0"
SUPPORTED_EXTRACTION_SCHEMAS = frozenset({"0.2.0"})
SUPPORTED_OVERRIDE_SCHEMAS = frozenset({"0.1.0", "0.2.0"})
CLASSIFICATIONS = frozenset({"translatable", "protected", "review_required", "ignored"})


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_id(source_sha: str, page: int, kind: str, value: object) -> str:
    return f"{source_sha[:16]}:p{page:05d}:{kind}:{_canonical_hash(value)[:20]}"


def _bbox(value: object, context: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{context} must be a four-number bbox")
    try:
        result = [round(float(item), 3) for item in value]
    except (TypeError, ValueError) as error:
        raise ValueError(f"{context} must be a four-number bbox") from error
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{context} must contain finite coordinates")
    return result


def _validate_manifest(manifest: object) -> dict:
    if not isinstance(manifest, dict):
        raise ValueError("Extraction manifest must be a JSON object")
    schema = manifest.get("schema_version")
    if schema not in SUPPORTED_EXTRACTION_SCHEMAS:
        supported = ", ".join(sorted(SUPPORTED_EXTRACTION_SCHEMAS))
        raise ValueError(f"Unsupported extraction schema {schema!r}; expected {supported}")
    source_sha = manifest.get("source_sha256")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha):
        raise ValueError("Extraction manifest has an invalid source_sha256")
    pages = manifest.get("pages")
    if not isinstance(pages, list):
        raise ValueError("Extraction manifest field 'pages' must be a list")
    if manifest.get("page_count") != len(pages):
        raise ValueError("Extraction manifest page_count does not match pages")
    return manifest


def _slice_bbox(values: object, start: int, end: int, text_length: int) -> list[float]:
    geometry = _bbox(values, "Text fragment")
    if start == 0 and end == text_length:
        return geometry
    width = geometry[2] - geometry[0]
    left = geometry[0] + width * start / max(text_length, 1)
    right = geometry[0] + width * end / max(text_length, 1)
    return _bbox([left, geometry[1], right, geometry[3]], "Text fragment")


def _decision(source_sha: str, page: int, source_id: str, element_type: str,
              bbox: list[float], classification: str, reason: str,
              decision_source: str, *, confidence: float | None = None,
              review_required: bool = False, source_locator: dict | None = None,
              linguistic_role: str | None = None,
              evidence: list[str] | None = None) -> dict:
    identity = {
        "source_id": source_id,
        "element_type": element_type,
        "bbox": bbox,
        "source_locator": source_locator,
    }
    result = {
        "id": _stable_id(source_sha, page, "decision", identity),
        "source_id": source_id,
        "page": page,
        "element_type": element_type,
        "classification": classification,
        "reason": reason,
        "bbox": bbox,
        "decision_source": decision_source,
        "review_required": review_required,
        "override_allowed": True,
    }
    if source_locator is not None:
        result["source_locator"] = source_locator
    if linguistic_role is not None:
        result["linguistic_role"] = linguistic_role
    if evidence:
        result["evidence"] = sorted(set(evidence))
    if confidence is not None:
        result["confidence"] = confidence
    return result


def _automatic_decisions(manifest: dict) -> tuple[list[dict], list[dict], dict, list[dict]]:
    source_sha = manifest["source_sha256"]
    decisions: list[dict] = []
    regions: list[dict] = []
    document_language, analyzed_text, term_memory = analyze_text(manifest)
    text_by_page: dict[int, list[tuple[dict, list[dict]]]] = {}
    for record, segments in analyzed_text:
        text_by_page.setdefault(record["page"], []).append((record, segments))
    for page in manifest["pages"]:
        page_number = page.get("number")
        if not isinstance(page_number, int) or page_number < 1:
            raise ValueError("Each extraction page must have a positive integer number")
        content_type = page.get("content_type")
        blocks = page.get("blocks", [])
        drawings = page.get("drawings", [])
        if not isinstance(blocks, list) or not isinstance(drawings, list):
            raise ValueError(f"Page {page_number} blocks and drawings must be lists")

        for record, segments in text_by_page.get(page_number, []):
            text_length = len(record["text"])
            for segment in segments:
                locator = {
                    "line_index": record["line_index"],
                    "span_index": record["span_index"],
                    "character_start": segment["start"],
                    "character_end": segment["end"],
                }
                evidence = list(segment.get("evidence", []))
                if segment["start"] != 0 or segment["end"] != text_length:
                    evidence.append("geometry=linear_span_estimate")
                decisions.append(_decision(
                    source_sha, page_number, record["source_id"], "text_fragment",
                    _slice_bbox(record["bbox"], segment["start"], segment["end"],
                                text_length),
                    segment["classification"], segment["reason"],
                    "heuristic" if segment.get("confidence") is not None else "rule",
                    confidence=segment.get("confidence"),
                    review_required=segment["review_required"],
                    source_locator=locator,
                    linguistic_role=segment.get("linguistic_role"),
                    evidence=evidence,
                ))

        for block in blocks:
            kind = block.get("kind")
            source_id = block.get("id")
            if not isinstance(source_id, str) or not source_id:
                raise ValueError(f"Page {page_number} contains an element without an id")
            if kind == "text":
                continue
            elif kind == "image":
                geometry = _bbox(block.get("bbox"), f"Image {source_id}")
                reason = ("image_on_mixed_page_may_contain_text" if content_type == "mixed" else
                          "image_only_page_may_contain_rasterized_text" if content_type == "image" else
                          "image_preserved_by_default")
                review = bool(page.get("requires_ocr_review", False) or
                              content_type in {"mixed", "image"})
                item = _decision(source_sha, page_number, source_id, "image", geometry,
                                 "protected", reason, "rule", review_required=review,
                                 linguistic_role="graphic_preservation")
                decisions.append(item)
                regions.append(_automatic_region(source_sha, item, "image_region"))
            else:
                raise ValueError(f"Page {page_number} contains unsupported block kind {kind!r}")

        for drawing in drawings:
            source_id = drawing.get("id")
            if not isinstance(source_id, str) or not source_id:
                raise ValueError(f"Page {page_number} contains a drawing without an id")
            geometry = _bbox(drawing.get("bbox"), f"Drawing {source_id}")
            item = _decision(source_sha, page_number, source_id, "vector", geometry,
                             "protected", "vector_preserved_by_default", "rule",
                             linguistic_role="graphic_preservation")
            decisions.append(item)
            regions.append(_automatic_region(source_sha, item, "vector_region"))
    return decisions, regions, document_language, term_memory


def _automatic_region(source_sha: str, decision: dict, region_type: str) -> dict:
    identity = {"type": region_type, "source_ids": [decision["source_id"]],
                "bbox": decision["bbox"]}
    return {
        "id": _stable_id(source_sha, decision["page"], "region", identity),
        "page": decision["page"],
        "region_type": region_type,
        "classification": "protected",
        "reason": decision["reason"],
        "bbox": decision["bbox"],
        "source_ids": [decision["source_id"]],
        "source_decision_ids": [decision["id"]],
        "decision_source": "rule",
        "review_required": decision["review_required"],
        "override_allowed": True,
    }


def _sync_automatic_regions(regions: list[dict], decisions: list[dict]) -> list[dict]:
    """Make an element override prevail over its derived graphical region."""
    by_id = {item["id"]: item for item in decisions}
    retained = []
    for region in regions:
        source_decisions = region.get("source_decision_ids", [])
        if len(source_decisions) != 1:
            retained.append(region)
            continue
        decision = by_id[source_decisions[0]]
        if decision["decision_source"] != "editorial_override":
            retained.append(region)
            continue
        if decision["classification"] in {"translatable", "ignored"}:
            continue
        region["classification"] = decision["classification"]
        region["reason"] = decision["reason"]
        region["decision_source"] = "editorial_override"
        region["review_required"] = decision["review_required"]
        retained.append(region)
    return retained


def _validate_override_entry(entry: object, context: str) -> dict:
    if not isinstance(entry, dict):
        raise ValueError(f"{context} must be an object")
    classification = entry.get("classification")
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"{context} has invalid classification {classification!r}")
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(f"{context} must provide a non-empty reason")
    return entry


def _apply_element_overrides(decisions: list[dict], overrides: dict | None) -> None:
    if not overrides:
        return
    entries = overrides.get("element_overrides", [])
    if not isinstance(entries, list):
        raise ValueError("element_overrides must be a list")
    by_decision: dict[str, dict] = {}
    by_source: dict[str, dict] = {}
    for index, raw in enumerate(entries):
        entry = _validate_override_entry(raw, f"element_overrides[{index}]")
        decision_id = entry.get("decision_id")
        source_id = entry.get("source_id")
        if bool(decision_id) == bool(source_id):
            raise ValueError(f"element_overrides[{index}] must set exactly one target id")
        target = by_decision if decision_id else by_source
        key = decision_id or source_id
        if not isinstance(key, str) or key in target:
            raise ValueError(f"Duplicate or invalid override target {key!r}")
        target[key] = entry

    matched_decisions: set[str] = set()
    matched_sources: set[str] = set()
    for decision in decisions:
        entry = by_decision.get(decision["id"])
        if entry is not None:
            matched_decisions.add(decision["id"])
        else:
            entry = by_source.get(decision["source_id"])
            if entry is not None:
                matched_sources.add(decision["source_id"])
        if entry is None:
            continue
        decision["classification"] = entry["classification"]
        decision["reason"] = entry["reason"].strip()
        decision["decision_source"] = "editorial_override"
        decision["review_required"] = bool(entry.get(
            "review_required", entry["classification"] == "review_required"))
        decision.pop("confidence", None)

    missing = ((set(by_decision) - matched_decisions) |
               (set(by_source) - matched_sources))
    if missing:
        raise ValueError(f"Override targets not found: {', '.join(sorted(missing))}")


def _manual_regions(source_sha: str, overrides: dict | None,
                    page_count: int, source_pages: dict[str, int]) -> list[dict]:
    if not overrides:
        return []
    entries = overrides.get("region_overrides", [])
    if not isinstance(entries, list):
        raise ValueError("region_overrides must be a list")
    regions = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(entries):
        entry = _validate_override_entry(raw, f"region_overrides[{index}]")
        page = entry.get("page")
        if not isinstance(page, int) or not 1 <= page <= page_count:
            raise ValueError(f"region_overrides[{index}] has an invalid page")
        geometry = _bbox(entry.get("bbox"), f"region_overrides[{index}]")
        source_ids = entry.get("source_ids", [])
        if not isinstance(source_ids, list) or not all(
                isinstance(item, str) and item for item in source_ids):
            raise ValueError(f"region_overrides[{index}].source_ids must be string ids")
        source_ids = sorted(set(source_ids))
        unknown = [item for item in source_ids if item not in source_pages]
        if unknown:
            raise ValueError(
                f"region_overrides[{index}] references unknown source ids: "
                f"{', '.join(unknown)}"
            )
        wrong_page = [item for item in source_ids if source_pages[item] != page]
        if wrong_page:
            raise ValueError(
                f"region_overrides[{index}] references source ids from another page: "
                f"{', '.join(wrong_page)}"
            )
        identity = {
            "type": "manual_region",
            "bbox": geometry,
            "classification": entry["classification"],
            "reason": entry["reason"].strip(),
            "source_ids": source_ids,
        }
        region_id = _stable_id(source_sha, page, "region", identity)
        if region_id in seen_ids:
            raise ValueError(f"Duplicate manual region at region_overrides[{index}]")
        seen_ids.add(region_id)
        regions.append({
            "id": region_id,
            "page": page,
            "region_type": "manual_region",
            "classification": entry["classification"],
            "reason": entry["reason"].strip(),
            "bbox": geometry,
            "source_ids": source_ids,
            "source_decision_ids": [],
            "decision_source": "editorial_override",
            "review_required": bool(entry.get(
                "review_required", entry["classification"] == "review_required")),
            "override_allowed": True,
        })
    return regions


def _validate_overrides(overrides: object, source_sha: str) -> dict | None:
    if overrides is None:
        return None
    if not isinstance(overrides, dict):
        raise ValueError("Overrides must be a JSON object")
    schema = overrides.get("schema_version")
    if schema not in SUPPORTED_OVERRIDE_SCHEMAS:
        supported = ", ".join(sorted(SUPPORTED_OVERRIDE_SCHEMAS))
        raise ValueError(
            f"Unsupported overrides schema {schema!r}; expected one of {supported}"
        )
    expected_sha = overrides.get("source_sha256")
    if expected_sha is not None:
        if not isinstance(expected_sha, str) or not re.fullmatch(
                r"[0-9a-fA-F]{64}", expected_sha):
            raise ValueError("Overrides source_sha256 is invalid")
        if expected_sha.lower() != source_sha.lower():
            raise ValueError("Overrides source_sha256 does not match the extraction manifest")
    return overrides


def classify_manifest(manifest: dict, overrides: dict | None = None) -> dict:
    """Return a deterministic derived protection artifact without mutating input."""
    manifest = _validate_manifest(manifest)
    source_sha = manifest["source_sha256"].lower()
    overrides = _validate_overrides(overrides, source_sha)
    decisions, regions, document_language, term_memory = _automatic_decisions(manifest)
    _apply_element_overrides(decisions, overrides)
    regions = _sync_automatic_regions(regions, decisions)
    source_pages = {item["source_id"]: item["page"] for item in decisions}
    regions.extend(_manual_regions(
        source_sha, overrides, len(manifest["pages"]), source_pages
    ))
    decisions.sort(key=lambda item: (item["page"], item["bbox"], item["element_type"],
                                     item["id"]))
    regions.sort(key=lambda item: (item["page"], item["bbox"], item["region_type"],
                                   item["id"]))
    counts = Counter(item["classification"] for item in decisions)
    result = {
        "schema_version": PROTECTION_SCHEMA_VERSION,
        "ruleset_version": RULESET_VERSION,
        "source_manifest": {
            "schema_version": manifest["schema_version"],
            "sha256": _canonical_hash(manifest),
        },
        "source_document": {
            "name": manifest.get("source_name"),
            "sha256": source_sha,
            "page_count": manifest["page_count"],
            "predominant_language": document_language,
        },
        "term_memory": term_memory,
        "classifications": decisions,
        "protected_regions": regions,
        "summary": {
            "classification_counts": {name: counts.get(name, 0)
                                      for name in sorted(CLASSIFICATIONS)},
            "review_required_count": sum(item["review_required"] for item in decisions),
            "protected_region_count": len(regions),
            "editorial_override_count": sum(
                item["decision_source"] == "editorial_override" for item in decisions
            ) + sum(item["decision_source"] == "editorial_override" for item in regions),
            "linguistic_role_counts": dict(sorted(Counter(
                item.get("linguistic_role", "unspecified") for item in decisions
            ).items())),
        },
    }
    if overrides is not None:
        result["overrides"] = {
            "schema_version": overrides["schema_version"],
            "sha256": _canonical_hash(overrides),
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


def write_protection_manifest(result: dict, output: str | Path) -> Path:
    """Atomically write a UTF-8 protection artifact."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".stolas-protection-", suffix=".tmp",
                                         delete=False) as file:
            temporary = Path(file.name)
            json.dump(result, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return output
