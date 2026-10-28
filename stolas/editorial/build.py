"""Build an auditable editorial model from extraction and protection artifacts."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import tempfile
import unicodedata


EDITORIAL_SCHEMA_VERSION = "0.1.0"
ALGORITHM_VERSION = "0.1.0"
SUPPORTED_EXTRACTION_SCHEMAS = frozenset({"0.2.0"})
SUPPORTED_PROTECTION_SCHEMAS = frozenset({"0.2.0"})
SUPPORTED_PROTECTION_RULESETS = frozenset({"0.2.0"})
EDITORIAL_TYPES = frozenset({
    "heading", "subheading", "paragraph", "list_item", "caption", "note",
    "footnote", "header", "footer", "page_number", "quote", "body", "unknown",
})


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def _bbox(value: object, context: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{context} must be a four-number bbox")
    try:
        result = [round(float(item), 3) for item in value]
    except (TypeError, ValueError) as error:
        raise ValueError(f"{context} must be a four-number bbox") from error
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{context} must contain finite coordinates")
    if result[2] < result[0] or result[3] < result[1]:
        raise ValueError(f"{context} has inverted coordinates")
    return result


def _union(boxes: list[list[float]]) -> list[float]:
    return [
        round(min(box[0] for box in boxes), 3),
        round(min(box[1] for box in boxes), 3),
        round(max(box[2] for box in boxes), 3),
        round(max(box[3] for box in boxes), 3),
    ]


def _overlap(first: list[float], second: list[float]) -> tuple[float, float]:
    return (
        max(0.0, min(first[2], second[2]) - max(first[0], second[0])),
        max(0.0, min(first[3], second[3]) - max(first[1], second[1])),
    )


def _horizontal_overlap_ratio(first: list[float], second: list[float]) -> float:
    overlap, _ = _overlap(first, second)
    denominator = max(1.0, min(first[2] - first[0], second[2] - second[0]))
    return overlap / denominator


def _validate_manifest(manifest: object) -> dict:
    if not isinstance(manifest, dict):
        raise ValueError("Extraction manifest must be a JSON object")
    if manifest.get("schema_version") not in SUPPORTED_EXTRACTION_SCHEMAS:
        raise ValueError(
            f"Unsupported extraction schema {manifest.get('schema_version')!r}; "
            "expected 0.2.0"
        )
    source_sha = manifest.get("source_sha256")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha):
        raise ValueError("Extraction manifest has an invalid source_sha256")
    pages = manifest.get("pages")
    if not isinstance(pages, list) or manifest.get("page_count") != len(pages):
        raise ValueError("Extraction manifest page_count does not match pages")
    return manifest


def _validate_protection(protection: object, manifest: dict) -> dict:
    if not isinstance(protection, dict):
        raise ValueError("Protection artifact must be a JSON object")
    if protection.get("schema_version") not in SUPPORTED_PROTECTION_SCHEMAS:
        raise ValueError(
            f"Unsupported protection schema {protection.get('schema_version')!r}; "
            "expected 0.2.0"
        )
    if protection.get("ruleset_version") not in SUPPORTED_PROTECTION_RULESETS:
        raise ValueError(
            f"Unsupported protection ruleset {protection.get('ruleset_version')!r}; "
            "expected 0.2.0"
        )
    source_manifest = protection.get("source_manifest")
    if not isinstance(source_manifest, dict):
        raise ValueError("Protection artifact is missing source_manifest")
    if source_manifest.get("schema_version") != manifest["schema_version"]:
        raise ValueError("Protection artifact references a different extraction schema")
    if source_manifest.get("sha256") != _canonical_hash(manifest):
        raise ValueError("Protection artifact does not match the extraction manifest hash")
    source_document = protection.get("source_document")
    if not isinstance(source_document, dict) or (
        str(source_document.get("sha256", "")).lower()
        != manifest["source_sha256"].lower()
    ):
        raise ValueError("Protection artifact references a different source document")
    if not isinstance(protection.get("classifications"), list):
        raise ValueError("Protection artifact classifications must be a list")
    if not isinstance(protection.get("protected_regions"), list):
        raise ValueError("Protection artifact protected_regions must be a list")
    return protection


def _source_ids(manifest: dict) -> dict[str, int]:
    result: dict[str, int] = {}
    for page in manifest["pages"]:
        number = page.get("number")
        if not isinstance(number, int) or number < 1:
            raise ValueError("Each extraction page must have a positive integer number")
        for element in [*page.get("blocks", []), *page.get("drawings", [])]:
            identifier = element.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise ValueError(f"Page {number} contains an element without an id")
            if identifier in result:
                raise ValueError(f"Duplicate extraction source id {identifier!r}")
            result[identifier] = number
    return result


def _decision_index(protection: dict, sources: dict[str, int]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = defaultdict(list)
    for decision in protection["classifications"]:
        source_id = decision.get("source_id")
        if source_id not in sources:
            raise ValueError(f"Protection decision references unknown source_id {source_id!r}")
        if decision.get("page") != sources[source_id]:
            raise ValueError(f"Protection decision {decision.get('id')!r} has the wrong page")
        result[source_id].append(decision)
    for decisions in result.values():
        decisions.sort(key=lambda item: (
            item.get("source_locator", {}).get("line_index", -1),
            item.get("source_locator", {}).get("span_index", -1),
            item.get("source_locator", {}).get("character_start", -1),
            item.get("id", ""),
        ))
    return result


def _line_records(manifest: dict) -> list[dict]:
    records: list[dict] = []
    for page in manifest["pages"]:
        page_number = page["number"]
        for block_index, block in enumerate(page.get("blocks", [])):
            if block.get("kind") != "text":
                continue
            source_id = block["id"]
            lines = block.get("lines")
            if not isinstance(lines, list):
                raise ValueError(f"Text source {source_id!r} lines must be a list")
            for line_index, line in enumerate(lines):
                spans = line.get("spans", [])
                if not isinstance(spans, list):
                    raise ValueError(f"Text source {source_id!r} spans must be a list")
                normalized_spans = []
                for span_index, span in enumerate(spans):
                    text = span.get("text", "")
                    if not isinstance(text, str):
                        raise ValueError(f"Text source {source_id!r} has a non-string span")
                    normalized_spans.append({
                        "text": text,
                        "bbox": _bbox(span.get("bbox"), f"Span in {source_id}"),
                        "font": span.get("font"),
                        "size": round(float(span.get("size", 0.0)), 3),
                        "flags": int(span.get("flags", 0) or 0),
                        "span_index": span_index,
                    })
                text = "".join(span["text"] for span in normalized_spans)
                if not text.strip():
                    continue
                geometry = _bbox(line.get("bbox", block.get("bbox")), f"Line in {source_id}")
                sizes = [span["size"] for span in normalized_spans if span["size"] > 0]
                styled_characters = sum(len(span["text"]) for span in normalized_spans)
                italic_characters = sum(
                    len(span["text"]) for span in normalized_spans if span["flags"] & 2
                )
                records.append({
                    "page": page_number,
                    "source_id": source_id,
                    "block_index": block_index,
                    "line_index": line_index,
                    "bbox": geometry,
                    "text": text,
                    "spans": normalized_spans,
                    "font_size": round(statistics.median(sizes), 3) if sizes else 0.0,
                    "bold": any(span["flags"] & 16 for span in normalized_spans),
                    "italic": bool(styled_characters) and italic_characters / styled_characters >= 0.65,
                })
    return records


def _repeat_roles(lines: list[dict], pages: dict[int, dict]) -> dict[tuple[int, str, int], str]:
    candidates: dict[tuple[str, str], list[dict]] = defaultdict(list)
    roles: dict[tuple[int, str, int], str] = {}
    for line in lines:
        page = pages[line["page"]]
        height = max(1.0, float(page["height"]))
        center_y = (line["bbox"][1] + line["bbox"][3]) / 2
        text = _normalize_text(line["text"])
        key = (line["page"], line["source_id"], line["line_index"])
        if center_y >= height * 0.9 and re.fullmatch(r"(?:\d+|[ivxlcdm]+)", text, re.I):
            roles[key] = "page_number"
            continue
        zone = "top" if center_y <= height * 0.08 else "bottom" if center_y >= height * 0.92 else ""
        if zone:
            candidates[(zone, text.casefold())].append(line)
    page_count = len(pages)
    threshold = (
        page_count + 1 if page_count == 1 else
        page_count if page_count <= 5 else
        max(3, math.ceil(page_count * 0.08))
    )
    for (zone, _), occurrences in candidates.items():
        distinct_pages = {item["page"] for item in occurrences}
        if len(distinct_pages) < threshold:
            continue
        role = "header" if zone == "top" else "footer"
        for line in occurrences:
            roles[(line["page"], line["source_id"], line["line_index"])] = role
    return roles


def _column_clusters(lines: list[dict], page_width: float) -> tuple[list[list[dict]], float, list[str]]:
    if not lines:
        return [], 1.0, ["no_body_text"]
    narrow = [line for line in lines if line["bbox"][2] - line["bbox"][0] < page_width * 0.68]
    if len(narrow) < 2:
        return [sorted(lines, key=lambda item: (item["bbox"][1], item["bbox"][0]))], 0.98, ["single_column_geometry"]
    tolerance = max(18.0, page_width * 0.16)
    clusters: list[list[dict]] = []
    centers: list[float] = []
    for line in sorted(narrow, key=lambda item: ((item["bbox"][0] + item["bbox"][2]) / 2, item["bbox"][1])):
        center = (line["bbox"][0] + line["bbox"][2]) / 2
        choices = [(abs(center - value), index) for index, value in enumerate(centers)]
        if choices and min(choices)[0] <= tolerance:
            index = min(choices)[1]
            clusters[index].append(line)
            centers[index] = statistics.mean(
                (item["bbox"][0] + item["bbox"][2]) / 2 for item in clusters[index]
            )
        else:
            clusters.append([line])
            centers.append(center)
    ordering = sorted(range(len(clusters)), key=lambda index: centers[index])
    clusters = [clusters[index] for index in ordering]
    centers = [centers[index] for index in ordering]
    if not 2 <= len(clusters) <= 4:
        return [sorted(lines, key=lambda item: (item["bbox"][1], item["bbox"][0]))], 0.9, ["single_column_geometry"]
    gaps = [centers[index + 1] - centers[index] for index in range(len(centers) - 1)]
    minimum_gap = min(gaps)
    envelopes = [_union([item["bbox"] for item in cluster]) for cluster in clusters]
    edge_gaps = [
        envelopes[index + 1][0] - envelopes[index][2]
        for index in range(len(envelopes) - 1)
    ]
    minimum_edge_gap = min(edge_gaps)
    if minimum_edge_gap <= 0:
        return [sorted(lines, key=lambda item: (item["bbox"][1], item["bbox"][0]))], 0.94, [
            "single_column_overlapping_horizontal_ranges"
        ]
    confidence = 0.96 if minimum_edge_gap >= page_width * 0.05 else 0.74
    evidence = [
        f"column_count={len(clusters)}",
        f"minimum_center_gap={round(minimum_gap, 3)}",
        f"minimum_edge_gap={round(minimum_edge_gap, 3)}",
    ]
    assigned = {id(line) for cluster in clusters for line in cluster}
    full_width = [line for line in lines if id(line) not in assigned]
    if full_width:
        evidence.append(f"full_width_anchors={len(full_width)}")
    for cluster in clusters:
        cluster.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    return clusters, confidence, evidence


def _order_body(lines: list[dict], page_width: float) -> tuple[list[dict], dict]:
    if not lines:
        return [], {
            "column_count": 0, "confidence": 1.0, "review_required": False,
            "evidence": ["no_body_text"], "candidates": [],
        }
    full_width = [
        line for line in lines
        if line["bbox"][2] - line["bbox"][0] >= page_width * 0.68
    ]
    narrow = [line for line in lines if line not in full_width]
    clusters, confidence, evidence = _column_clusters(narrow, page_width)
    column_count = len(clusters) if narrow else 1
    ordered: list[dict] = []
    remaining = [item for cluster in clusters for item in cluster]
    for anchor in sorted(full_width, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        above = [item for item in remaining if (item["bbox"][1] + item["bbox"][3]) / 2 < anchor["bbox"][1]]
        for cluster in clusters:
            ordered.extend(item for item in cluster if item in above)
        remaining = [item for item in remaining if item not in above]
        ordered.append(anchor)
    for cluster in clusters:
        ordered.extend(item for item in cluster if item in remaining)
    if not narrow:
        ordered = sorted(full_width, key=lambda item: (item["bbox"][1], item["bbox"][0]))
        evidence = ["single_column_full_width"]
        confidence = 0.98
    coordinate_candidate = sorted(lines, key=lambda item: (item["bbox"][1], item["bbox"][0]))
    candidates = []
    if [id(item) for item in coordinate_candidate] != [id(item) for item in ordered]:
        candidates.append({
            "strategy": "coordinate_order",
            "source_ids": [item["source_id"] for item in coordinate_candidate],
        })
    review = confidence < 0.8
    if review and not candidates and len(clusters) > 1:
        candidates.append({
            "strategy": "reverse_column_order",
            "source_ids": [
                item["source_id"] for cluster in reversed(clusters) for item in cluster
            ],
        })
    return ordered, {
        "column_count": column_count,
        "confidence": round(confidence, 3),
        "review_required": review,
        "evidence": sorted(evidence),
        "candidates": candidates,
    }


def _graphics(page: dict) -> list[dict]:
    result = []
    for block in page.get("blocks", []):
        if block.get("kind") == "image":
            result.append({"source_id": block["id"], "kind": "image", "bbox": _bbox(block["bbox"], "Image")})
    for drawing in page.get("drawings", []):
        result.append({"source_id": drawing["id"], "kind": "vector", "bbox": _bbox(drawing["bbox"], "Vector")})
    return result


def _nearest_graphic(line: dict, graphics: list[dict], max_gap: float = 30.0) -> dict | None:
    choices = []
    for graphic in graphics:
        vertical_gap = line["bbox"][1] - graphic["bbox"][3]
        if 0 <= vertical_gap <= max_gap and _horizontal_overlap_ratio(line["bbox"], graphic["bbox"]) >= 0.25:
            choices.append((vertical_gap, graphic["source_id"], graphic))
    return min(choices)[2] if choices else None


def _classify_line(line: dict, page: dict, role: str | None, body_size: float,
                   graphics: list[dict]) -> tuple[str, float, list[str]]:
    text = _normalize_text(line["text"])
    if role:
        return role, 0.99, [f"repeated_role={role}"]
    if re.match(r"^(?:[-•*‣▪]|\(?\d+[.)]|[A-Za-z][.)])\s+", text):
        return "list_item", 0.96, ["list_marker"]
    if re.match(r"^(?:note|nota|warning|caution)\s*[:—-]", text, re.I):
        return "note", 0.94, ["note_marker"]
    caption_label = bool(re.match(r"^(?:figure|fig\.?|plate|illustration|diagram)\s*\d*", text, re.I))
    if (_nearest_graphic(line, graphics) is not None and len(text) <= 240
            and (caption_label or (line["font_size"] and line["font_size"] <= body_size * 0.92))):
        return "caption", 0.88, ["below_graphic", "short_text"]
    height = max(1.0, float(page["height"]))
    footnote_marker = bool(re.match(r"^(?:\d+|[*†‡])(?:[.)]|\s)", text))
    if (line["bbox"][1] >= height * 0.78 and line["font_size"]
            and line["font_size"] <= body_size * 0.86 and footnote_marker):
        return "footnote", 0.82, ["bottom_zone", "small_font"]
    short = len(text) <= 140
    if short and body_size and line["font_size"] >= body_size * 1.28 and line["bold"]:
        return "heading", 0.93, ["large_font", "short_text"]
    if short and body_size and line["font_size"] >= body_size * 1.1 and line["bold"]:
        return "subheading", 0.87, ["bold", "elevated_font", "short_text"]
    if short and line["bold"] and len(text.split()) <= 12 and not re.search(r"[.!?]$", text):
        evidence = ["bold", "short_text"]
        if text.isupper():
            evidence.append("uppercase")
        return "subheading", 0.84, evidence
    quoted = (
        (text.startswith("“") and text.endswith("”"))
        or (text.startswith("\"") and text.endswith("\""))
        or (text.startswith("‘") and text.endswith("’"))
        or (text.startswith("'") and text.endswith("'"))
    )
    if quoted or (line["italic"] and line["bbox"][0] >= float(page["width"]) * 0.12):
        return "quote", 0.8, ["quotation_style"]
    return "paragraph", 0.86, ["body_text_geometry"]


def _same_column(first: dict, second: dict, page_width: float) -> bool:
    return (
        _horizontal_overlap_ratio(first["bbox"], second["bbox"]) >= 0.35
        or abs(first["bbox"][0] - second["bbox"][0]) <= page_width * 0.08
    )


def _can_merge(first: dict, second: dict, page_width: float) -> bool:
    if not _same_column(first, second, page_width):
        return False
    gap = second["bbox"][1] - first["bbox"][3]
    line_height = max(1.0, first["bbox"][3] - first["bbox"][1], second["bbox"][3] - second["bbox"][1])
    if gap < -line_height * 0.35 or gap > line_height * 1.25:
        return False
    first_protected = _parts_fully_protected(first.get("parts", []))
    second_protected = _parts_fully_protected(second.get("parts", []))
    protected_label = bool(re.match(
        r"^(?:invocation\s+psalm|pronunciation|the\s+shem\s+angel|repeat\s+the\s+following|the\s+words\s+are)\b",
        _normalize_text(first["text"]), re.I,
    ))
    if second_protected and (first_protected or protected_label):
        return True
    if first["editorial_type"] != second["editorial_type"]:
        return False
    if first["editorial_type"] in {"heading", "subheading", "list_item", "caption", "note", "footnote", "header", "footer", "page_number"}:
        return False
    if second["bbox"][0] - first["bbox"][0] > max(12.0, page_width * 0.025):
        return False
    if first["source_id"] == second["source_id"]:
        return True
    return gap <= line_height * 0.55 or not re.search(r"[.!?][\"')\]]?$", first["text"].rstrip())


def _parts_fully_protected(parts: list[dict]) -> bool:
    return bool(parts) and all(
        part["type"] in {"protected", "ignored"} or not part["text"].strip()
        for part in parts
    )


def _parts_for_line(line: dict, decisions: dict[str, list[dict]]) -> list[dict]:
    parts: list[dict] = []
    source_decisions = decisions.get(line["source_id"], [])
    for span in line["spans"]:
        relevant = []
        for decision in source_decisions:
            locator = decision.get("source_locator")
            if not isinstance(locator, dict):
                continue
            if locator.get("line_index") == line["line_index"] and locator.get("span_index") == span["span_index"]:
                relevant.append(decision)
        relevant.sort(key=lambda item: item["source_locator"]["character_start"])
        cursor = 0
        for decision in relevant:
            start = max(cursor, int(decision["source_locator"]["character_start"]))
            end = min(len(span["text"]), int(decision["source_locator"]["character_end"]))
            if start > cursor:
                parts.append({
                    "type": "translatable", "text": span["text"][cursor:start],
                    "source_id": line["source_id"], "decision_ids": [],
                    "review_required": False,
                })
            if end > start:
                classification = decision["classification"]
                part_type = (
                    "protected" if classification == "protected" else
                    "ignored" if classification == "ignored" else
                    "review_required" if classification == "review_required" else
                    "translatable"
                )
                parts.append({
                    "type": part_type,
                    "text": span["text"][start:end],
                    "source_id": line["source_id"],
                    "decision_ids": [decision["id"]],
                    "review_required": bool(decision.get("review_required")) or classification == "review_required",
                })
                cursor = end
        if cursor < len(span["text"]):
            parts.append({
                "type": "translatable", "text": span["text"][cursor:],
                "source_id": line["source_id"], "decision_ids": [],
                "review_required": False,
            })
    return _coalesce_parts(parts)


def _coalesce_parts(parts: list[dict]) -> list[dict]:
    result: list[dict] = []
    for part in parts:
        if not part["text"]:
            continue
        if result and (
            result[-1]["type"] == part["type"]
            and result[-1]["review_required"] == part["review_required"]
            and result[-1]["source_id"] == part["source_id"]
        ):
            result[-1]["text"] += part["text"]
            result[-1]["decision_ids"] = sorted(set(
                result[-1]["decision_ids"] + part["decision_ids"]
            ))
        else:
            result.append(dict(part))
    return result


def _join_parts(groups: list[list[dict]]) -> list[dict]:
    result: list[dict] = []
    for index, parts in enumerate(groups):
        if index and result:
            separator = "" if result[-1]["text"].endswith("-") else " "
            if separator:
                next_type = parts[0]["type"] if parts else "translatable"
                result.append({
                    "type": result[-1]["type"] if result[-1]["type"] == next_type else "translatable",
                    "text": separator,
                    "source_id": result[-1]["source_id"], "decision_ids": [],
                    "review_required": False,
                })
        result.extend(parts)
    return _coalesce_parts(result)


def _make_units(ordered: list[dict], page: dict, roles: dict, decisions: dict[str, list[dict]],
                graphics: list[dict]) -> list[dict]:
    sizes = [line["font_size"] for line in ordered if line["font_size"] > 0]
    body_size = statistics.median(sizes) if sizes else 10.0
    contents_page = any(
        _normalize_text(line["text"]).casefold() in {"contents", "table of contents"}
        for line in ordered
    )
    classified = []
    for line in ordered:
        key = (line["page"], line["source_id"], line["line_index"])
        editorial_type, confidence, evidence = _classify_line(
            line, page, roles.get(key), body_size, graphics
        )
        normalized_text = _normalize_text(line["text"])
        if (contents_page and editorial_type == "paragraph" and len(normalized_text) <= 120
                and not re.search(r"[.!?]$", normalized_text)):
            editorial_type, confidence = "list_item", 0.84
            evidence = [*evidence, "contents_context"]
        classified.append({
            **line,
            "editorial_type": editorial_type,
            "type_confidence": confidence,
            "type_evidence": evidence,
            "parts": _parts_for_line(line, decisions),
        })
    groups: list[list[dict]] = []
    for line in classified:
        if groups and _can_merge(groups[-1][-1], line, float(page["width"])):
            groups[-1].append(line)
        else:
            groups.append([line])
    units = []
    for order, group in enumerate(groups):
        parts = _join_parts([line["parts"] for line in group])
        source_ids = list(dict.fromkeys(line["source_id"] for line in group))
        unit = {
            "page": page["number"],
            "bbox": _union([line["bbox"] for line in group]),
            "source_ids": source_ids,
            "source_lines": [
                {"source_id": line["source_id"], "line_index": line["line_index"]}
                for line in group
            ],
            "reading_order": order,
            "editorial_type": group[0]["editorial_type"],
            "confidence": round(min(line["type_confidence"] for line in group), 3),
            "evidence": sorted(set(
                evidence for line in group for evidence in line["type_evidence"]
            )),
            "text": "".join(part["text"] for part in parts),
            "parts": parts,
            "protection": {
                "contains_protected": any(part["type"] == "protected" for part in parts),
                "fully_protected": bool(parts) and all(
                    part["type"] in {"protected", "ignored"} or not part["text"].strip()
                    for part in parts
                ),
                "review_required": any(part["review_required"] for part in parts),
                "decision_ids": sorted(set(
                    decision_id for part in parts for decision_id in part["decision_ids"]
                )),
            },
        }
        units.append(unit)
    return units


def _assign_editorial_ids(units: list[dict], source_sha: str, page: dict) -> None:
    normalized = [_normalize_text(unit["text"]).casefold() for unit in units]
    counts = Counter((unit["editorial_type"], normalized[index]) for index, unit in enumerate(units))
    duplicate_index: Counter = Counter()
    for index, unit in enumerate(units):
        key = (unit["editorial_type"], normalized[index])
        identity = {
            "document": source_sha,
            "page": unit["page"],
            "type": unit["editorial_type"],
            "text": normalized[index],
            "previous": normalized[index - 1][:80] if index else "",
            "next": normalized[index + 1][:80] if index + 1 < len(units) else "",
        }
        if counts[key] > 1:
            identity["position_band"] = [
                round(((unit["bbox"][0] + unit["bbox"][2]) / 2) / max(float(page["width"]), 1) * 12),
                round(((unit["bbox"][1] + unit["bbox"][3]) / 2) / max(float(page["height"]), 1) * 24),
            ]
            duplicate_index[key] += 1
            identity["duplicate"] = duplicate_index[key]
        digest = _canonical_hash(identity)[:24]
        unit["editorial_id"] = f"{source_sha[:16]}:p{unit['page']:05d}:editorial:{digest}"


def _style_signature(drawing: dict) -> tuple:
    return (
        json.dumps(drawing.get("color"), sort_keys=True),
        json.dumps(drawing.get("fill"), sort_keys=True),
        round(float(drawing.get("width", 0.0) or 0.0), 2),
        round(float(drawing.get("stroke_opacity", 1.0) or 0.0), 2),
        round(float(drawing.get("fill_opacity", 1.0) or 0.0), 2),
    )


def _vector_related(first: dict, second: dict, page: dict) -> tuple[bool, list[str]]:
    if _style_signature(first) != _style_signature(second):
        return False, []
    first_box = first["_bbox"]
    second_box = second["_bbox"]
    x_overlap, y_overlap = _overlap(first_box, second_box)
    x_gap = max(0.0, max(first_box[0], second_box[0]) - min(first_box[2], second_box[2]))
    y_gap = max(0.0, max(first_box[1], second_box[1]) - min(first_box[3], second_box[3]))
    size = max(
        2.0,
        min(
            max(first_box[2] - first_box[0], first_box[3] - first_box[1]),
            max(second_box[2] - second_box[0], second_box[3] - second_box[1]),
        ),
    )
    proximity_limit = max(4.0, min(18.0, size * 0.4))
    near = math.hypot(x_gap, y_gap) <= proximity_limit
    touching = (
        (x_overlap > 0 and y_gap <= 4.0)
        or (y_overlap > 0 and x_gap <= 4.0)
    )
    if not (near or touching):
        return False, []
    combined = _union([first_box, second_box])
    if (combined[2] - combined[0] > float(page["width"]) * 0.82 and
            combined[3] - combined[1] > float(page["height"]) * 0.3):
        return False, []
    evidence = ["matching_vector_style"]
    evidence.append("intersecting_or_continuous" if touching else "relative_proximity")
    return True, evidence


def _graphic_groups(page: dict, source_sha: str) -> list[dict]:
    drawings = []
    for drawing in page.get("drawings", []):
        item = dict(drawing)
        item["_bbox"] = _bbox(drawing.get("bbox"), f"Drawing {drawing.get('id')}")
        drawings.append(item)
    if len(drawings) < 2:
        return []
    parent = list(range(len(drawings)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            parent[second_root] = first_root

    pair_evidence: dict[tuple[int, int], list[str]] = {}
    for first in range(len(drawings)):
        for second in range(first + 1, len(drawings)):
            related, evidence = _vector_related(drawings[first], drawings[second], page)
            if related:
                union(first, second)
                pair_evidence[(first, second)] = evidence
    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(drawings)):
        components[find(index)].append(index)
    groups = []
    for members in components.values():
        if len(members) < 2:
            continue
        boxes = [drawings[index]["_bbox"] for index in members]
        combined = _union(boxes)
        if (combined[2] - combined[0] > float(page["width"]) * 0.9
                or combined[3] - combined[1] > float(page["height"]) * 0.6
                or (len(members) > 8
                    and combined[3] - combined[1] > float(page["height"]) * 0.2)):
            continue
        source_ids = sorted(drawings[index]["id"] for index in members)
        evidence = sorted(set(
            item for pair, values in pair_evidence.items()
            if pair[0] in members and pair[1] in members for item in values
        ))
        identity = {
            "document": source_sha, "page": page["number"],
            "member_count": len(source_ids), "styles": sorted(
                set(_style_signature(drawings[index]) for index in members)
            ),
            "position_band": [
                round(combined[0] / max(float(page["width"]), 1) * 20),
                round(combined[1] / max(float(page["height"]), 1) * 20),
                round(combined[2] / max(float(page["width"]), 1) * 20),
                round(combined[3] / max(float(page["height"]), 1) * 20),
            ],
        }
        groups.append({
            "graphic_group_id": f"{source_sha[:16]}:p{page['number']:05d}:graphic:{_canonical_hash(identity)[:24]}",
            "page": page["number"], "bbox": combined,
            "source_ids": source_ids, "member_count": len(source_ids),
            "evidence": evidence, "confidence": 0.9,
        })
    groups.sort(key=lambda item: (item["bbox"], item["graphic_group_id"]))
    return groups


def _segments(units: list[dict], source_sha: str) -> list[dict]:
    excluded = {"header", "footer", "page_number"}
    segments = []
    for unit in units:
        if unit["editorial_type"] in excluded:
            continue
        if not any(part["type"] in {"translatable", "review_required"} and part["text"].strip()
                   for part in unit["parts"]):
            continue
        parts = [dict(part) for part in unit["parts"] if part["type"] != "ignored"]
        identity = {
            "document": source_sha,
            "editorial_id": unit["editorial_id"],
            "parts": [(part["type"], _normalize_text(part["text"])) for part in parts],
        }
        segments.append({
            "segment_id": f"{source_sha[:16]}:p{unit['page']:05d}:segment:{_canonical_hash(identity)[:24]}",
            "editorial_id": unit["editorial_id"], "page": unit["page"],
            "editorial_type": unit["editorial_type"],
            "source_ids": unit["source_ids"], "parts": parts,
            "review_required": unit["protection"]["review_required"],
            "contains_protected_parts": any(part["type"] == "protected" for part in parts),
        })
    return segments


def _interleave_source_ids(units: list[dict], graphics: list[dict]) -> list[str]:
    entries = []
    for unit in units:
        entries.append((unit["reading_order"], unit["bbox"][1], 0, unit["source_ids"]))
    for graphic in graphics:
        nearest = min(
            (abs(graphic["bbox"][1] - unit["bbox"][1]), unit["reading_order"])
            for unit in units
        )[1] if units else 0
        entries.append((nearest + 0.5, graphic["bbox"][1], 1, [graphic["source_id"]]))
    source_ids = []
    for _, _, _, identifiers in sorted(entries):
        for identifier in identifiers:
            if identifier not in source_ids:
                source_ids.append(identifier)
    return source_ids


def build_editorial_structure(manifest: dict, protection: dict) -> dict:
    """Return a deterministic editorial artifact without mutating either input."""
    manifest = _validate_manifest(manifest)
    protection = _validate_protection(protection, manifest)
    sources = _source_ids(manifest)
    decisions = _decision_index(protection, sources)
    lines = _line_records(manifest)
    pages_by_number = {page["number"]: page for page in manifest["pages"]}
    roles = _repeat_roles(lines, pages_by_number)
    lines_by_page: dict[int, list[dict]] = defaultdict(list)
    for line in lines:
        lines_by_page[line["page"]].append(line)
    all_units = []
    all_groups = []
    page_results = []
    warnings = []
    source_sha = manifest["source_sha256"].lower()
    for page in manifest["pages"]:
        page_number = page["number"]
        page_lines = lines_by_page.get(page_number, [])
        headers = []
        footers = []
        body = []
        for line in page_lines:
            role = roles.get((page_number, line["source_id"], line["line_index"]))
            if role == "header":
                headers.append(line)
            elif role in {"footer", "page_number"}:
                footers.append(line)
            else:
                body.append(line)
        ordered_body, order_meta = _order_body(body, float(page["width"]))
        ordered = (
            sorted(headers, key=lambda item: (item["bbox"][1], item["bbox"][0]))
            + ordered_body
            + sorted(footers, key=lambda item: (item["bbox"][1], item["bbox"][0]))
        )
        page_graphics = _graphics(page)
        units = _make_units(ordered, page, roles, decisions, page_graphics)
        _assign_editorial_ids(units, source_sha, page)
        groups = _graphic_groups(page, source_sha)
        if order_meta["review_required"]:
            warnings.append({
                "code": "ambiguous_reading_order", "page": page_number,
                "message": "Column geometry permits more than one plausible reading order.",
            })
        page_results.append({
            "number": page_number,
            "width": page["width"], "height": page["height"],
            "reading_order": {
                **order_meta,
                "editorial_ids": [unit["editorial_id"] for unit in units],
                "source_ids": _interleave_source_ids(units, page_graphics),
            },
            "editorial_ids": [unit["editorial_id"] for unit in units],
            "graphic_group_ids": [group["graphic_group_id"] for group in groups],
        })
        all_units.extend(units)
        all_groups.extend(groups)
    segments = _segments(all_units, source_sha)
    type_counts = Counter(unit["editorial_type"] for unit in all_units)
    grouped_vectors = {source_id for group in all_groups for source_id in group["source_ids"]}
    ambiguous_units = sum(unit["confidence"] < 0.8 for unit in all_units)
    stats = {
        "page_count": len(page_results),
        "source_element_count": len(sources),
        "editorial_unit_count": len(all_units),
        "segment_count": len(segments),
        "average_segments_per_page": round(len(segments) / max(len(page_results), 1), 3),
        "editorial_type_counts": {name: type_counts.get(name, 0) for name in sorted(EDITORIAL_TYPES)},
        "ambiguity_count": sum(page["reading_order"]["review_required"] for page in page_results) + ambiguous_units,
        "graphic_group_count": len(all_groups),
        "grouped_vector_count": len(grouped_vectors),
        "segments_with_protected_parts": sum(segment["contains_protected_parts"] for segment in segments),
    }
    return {
        "schema_version": EDITORIAL_SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "source_manifest": {
            "schema_version": manifest["schema_version"],
            "sha256": _canonical_hash(manifest),
        },
        "source_protection": {
            "schema_version": protection["schema_version"],
            "ruleset_version": protection["ruleset_version"],
            "sha256": _canonical_hash(protection),
        },
        "source_document": {
            "name": manifest.get("source_name"), "sha256": source_sha,
            "page_count": manifest["page_count"],
        },
        "pages": page_results,
        "editorial_units": all_units,
        "segments": segments,
        "graphic_groups": all_groups,
        "warnings": warnings,
        "statistics": stats,
    }


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


def write_editorial_structure(result: dict, output: str | Path) -> Path:
    """Atomically write an editorial artifact as UTF-8 JSON."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent,
            prefix=".stolas-editorial-", suffix=".tmp", delete=False,
        ) as file:
            temporary = Path(file.name)
            json.dump(result, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return output
