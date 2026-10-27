"""Structured, non-OCR PDF extraction with deterministic element IDs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata

import pymupdf

SCHEMA_VERSION = "0.2.0"
ENGINE_NAME = "PyMuPDF"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bbox(values) -> list[float]:
    return [round(float(v), 3) for v in values]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def _json_value(value):
    """Convert PyMuPDF geometry and drawing values to JSON-safe data."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    try:
        return [round(float(item), 6) for item in value]
    except (TypeError, ValueError):
        return str(value)


def _stable_id(source_sha: str, page_number: int, kind: str, payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return f"{source_sha[:16]}:p{page_number:05d}:{kind}:{digest}"


def _assign_unique_id(item: dict, source_sha: str, page_number: int, kind: str,
                      identity: dict, seen: dict[str, int]) -> None:
    base = _stable_id(source_sha, page_number, kind, identity)
    duplicate = seen.get(base, 0)
    seen[base] = duplicate + 1
    item["id"] = base if duplicate == 0 else f"{base}:n{duplicate + 1}"


def _boxes_overlap(first: list[float], second: list[float]) -> bool:
    return (min(first[2], second[2]) > max(first[0], second[0]) and
            min(first[3], second[3]) > max(first[1], second[1]))


def _image_object_metadata(document: pymupdf.Document, xref: int,
                           cache: dict[int, dict]) -> dict:
    """Hash one image object once and retain metadata, never its binary data."""
    if xref in cache:
        return cache[xref]
    extension = None
    content_sha256 = None
    decoded_samples_md5 = None
    try:
        extracted = document.extract_image(xref)
        extension = extracted.get("ext")
        content = extracted.get("image")
        if content:
            content_sha256 = hashlib.sha256(memoryview(content)).hexdigest()
        del content, extracted
    except (RuntimeError, ValueError):
        pass
    try:
        pixmap = pymupdf.Pixmap(document, xref)
        decoded_samples_md5 = hashlib.md5(pixmap.samples_mv).hexdigest()
        del pixmap
    except (RuntimeError, ValueError):
        pass
    finally:
        # MuPDF otherwise retains every decoded image in its global store.
        pymupdf.TOOLS.store_shrink(100)
    metadata = {
        "ext": extension,
        "content_sha256": content_sha256,
        "decoded_samples_md5": decoded_samples_md5,
        "hash_basis": "extracted_stream" if content_sha256 else None,
    }
    cache[xref] = metadata
    return metadata


def _raw_image_evidence(page: pymupdf.Page) -> list[dict]:
    """Load image bytes only for inline or ambiguous resource matching."""
    evidence = []
    data = page.get_text("dict", sort=True)
    for block in data.get("blocks", []):
        if block.get("type") != 1:
            continue
        content = block.get("image")
        content_sha256 = hashlib.sha256(memoryview(content)).hexdigest() if content else None
        decoded_samples_md5 = None
        if content:
            try:
                pixmap = pymupdf.Pixmap(content)
                decoded_samples_md5 = hashlib.md5(pixmap.samples_mv).hexdigest()
                del pixmap
            except (RuntimeError, ValueError):
                pass
            finally:
                pymupdf.TOOLS.store_shrink(100)
        evidence.append({
            "bbox": _bbox(block["bbox"]),
            "width": block.get("width"),
            "height": block.get("height"),
            "ext": block.get("ext"),
            "content_sha256": content_sha256,
            "decoded_samples_md5": decoded_samples_md5,
        })
    del data
    return evidence


def _discover_images(document: pymupdf.Document, page: pymupdf.Page,
                     source_sha: str, page_number: int, seen_ids: dict[str, int],
                     object_cache: dict[int, dict]) -> list[dict]:
    """Discover displayed image occurrences through independent PDF APIs."""
    infos = page.get_image_info(hashes=False, xrefs=False)
    resources_by_size: dict[tuple[int, int], list[dict]] = {}
    for resource in page.get_images(full=True):
        xref = int(resource[0])
        size = (int(resource[2]), int(resource[3]))
        records = resources_by_size.setdefault(size, [])
        if not any(record["xref"] == xref for record in records):
            records.append({"xref": xref})

    candidate_sets = [resources_by_size.get((info.get("width"), info.get("height")), [])
                      for info in infos]
    raw_evidence = _raw_image_evidence(page) if any(len(items) != 1 for items in candidate_sets) else []
    used_raw: set[int] = set()
    hashed_infos = None
    images: list[dict] = []
    for info, candidates in zip(infos, candidate_sets):
        bbox = _bbox(info["bbox"])
        width, height = info.get("width"), info.get("height")
        raw = None
        matching_raw = [
            (index, item) for index, item in enumerate(raw_evidence)
            if index not in used_raw and item["width"] == width and item["height"] == height
            and _boxes_overlap(item["bbox"], bbox)
        ]
        if len(matching_raw) == 1:
            raw_index, raw = matching_raw[0]
            used_raw.add(raw_index)

        resource = candidates[0] if len(candidates) == 1 else None
        if resource is None and raw is not None:
            matching_resources = []
            for candidate in candidates:
                metadata = _image_object_metadata(document, candidate["xref"], object_cache)
                if metadata["content_sha256"] == raw["content_sha256"]:
                    matching_resources.append(candidate)
            if len(matching_resources) == 1:
                resource = matching_resources[0]

        # Preserve recoverability for rare ambiguous resources. The expensive
        # engine matcher is a last resort for this page, never the normal path.
        if resource is None and candidates:
            if hashed_infos is None:
                hashed_infos = page.get_image_info(hashes=True, xrefs=True)
                pymupdf.TOOLS.store_shrink(100)
            matching_infos = [item for item in hashed_infos
                              if item.get("width") == width and item.get("height") == height
                              and _bbox(item["bbox"]) == bbox and item.get("xref")]
            if len(matching_infos) == 1:
                resource = {"xref": int(matching_infos[0]["xref"])}

        xref = resource["xref"] if resource else None
        metadata = (_image_object_metadata(document, xref, object_cache) if xref else
                    {"ext": raw.get("ext") if raw else None,
                     "content_sha256": raw.get("content_sha256") if raw else None,
                     "decoded_samples_md5": raw.get("decoded_samples_md5") if raw else None,
                     "hash_basis": "text_dict_stream" if raw and raw.get("content_sha256") else None})
        methods = ["image_info"]
        if resource:
            methods.append("page_resources")
        if raw:
            methods.append("text_dict")
        image = {
            "kind": "image", "page_number": page_number, "bbox": bbox,
            "xref": xref, "width": width, "height": height,
            "ext": metadata["ext"],
            "content_sha256": metadata["content_sha256"],
            "decoded_samples_md5": metadata["decoded_samples_md5"],
            "hash_basis": metadata["hash_basis"],
            "detection_methods": methods,
        }
        if info.get("transform") is not None:
            image["transform"] = _json_value(info["transform"])
        images.append(image)

    images.sort(key=lambda item: (item["bbox"], item.get("xref") or -1,
                                  item.get("content_sha256") or ""))
    for image in images:
        image["detection_methods"].sort()
        identity = {key: image.get(key) for key in
                    ("bbox", "xref", "width", "height", "content_sha256",
                     "decoded_samples_md5")}
        _assign_unique_id(image, source_sha, page_number, "image", identity, seen_ids)
    return images


def _extract_text_blocks(raw_blocks: list[dict], source_sha: str,
                         page_number: int, seen_ids: dict[str, int]) -> list[dict]:
    blocks = []
    reading_order = 0
    for block in raw_blocks:
        if block.get("type") != 0:
            continue
        lines = []
        for line in block.get("lines", []):
            spans = []
            for span in line.get("spans", []):
                spans.append({
                    "text": span.get("text", ""), "bbox": _bbox(span["bbox"]),
                    "font": span.get("font"),
                    "size": round(float(span.get("size", 0)), 3),
                    "flags": span.get("flags"), "color": span.get("color"),
                })
            lines.append({"bbox": _bbox(line["bbox"]), "spans": spans})
        text = "\n".join("".join(span["text"] for span in line["spans"]) for line in lines)
        item = {
            "kind": "text", "bbox": _bbox(block["bbox"]),
            "reading_order": reading_order, "lines": lines, "text": text,
        }
        _assign_unique_id(item, source_sha, page_number, "text",
                          {"bbox": item["bbox"], "text": _normalize_text(text)}, seen_ids)
        blocks.append(item)
        reading_order += 1
    return blocks


def _extract_drawings(page: pymupdf.Page, source_sha: str, page_number: int,
                      seen_ids: dict[str, int]) -> list[dict]:
    drawings = []
    properties = (
        "type", "items", "color", "fill", "width", "dashes", "lineCap",
        "lineJoin", "closePath", "fill_opacity", "stroke_opacity", "even_odd",
        "layer", "seqno",
    )
    for raw in page.get_drawings():
        drawing = {"bbox": _bbox(raw["rect"])}
        for key in properties:
            if key in raw:
                drawing[key] = _json_value(raw[key])
        identity = {key: value for key, value in drawing.items() if key != "seqno"}
        _assign_unique_id(drawing, source_sha, page_number, "drawing", identity, seen_ids)
        drawings.append(drawing)
    return drawings


def _page_classification(has_text: bool, has_images: bool,
                         has_drawings: bool) -> str:
    if not (has_text or has_images or has_drawings):
        return "empty"
    if has_text and has_images:
        return "mixed"
    if has_text:
        return "text"
    if has_images:
        return "image"
    return "vector"


def extract_pdf(path: str | Path) -> dict:
    """Extract text, image references and vector properties without OCR."""
    path = Path(path)
    source_sha = sha256_file(path)
    pages = []
    image_object_cache: dict[int, dict] = {}
    text_flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    with pymupdf.open(path) as document:
        if document.needs_pass:
            raise ValueError("Password-protected PDF: supply an unlocked, authorized copy.")
        for page_index, page in enumerate(document):
            page_number = page_index + 1
            raw_blocks = page.get_text("dict", sort=True, flags=text_flags).get("blocks", [])
            seen_ids: dict[str, int] = {}
            text_blocks = _extract_text_blocks(raw_blocks, source_sha, page_number, seen_ids)
            images = _discover_images(document, page, source_sha, page_number,
                                      seen_ids, image_object_cache)
            drawings = _extract_drawings(page, source_sha, page_number, seen_ids)
            blocks = text_blocks + images
            blocks.sort(key=lambda item: (item["bbox"][1], item["bbox"][0],
                                          item["kind"], item["id"]))
            for reading_order, block in enumerate(blocks):
                block["reading_order"] = reading_order
            has_text = any(block["text"].strip() for block in text_blocks)
            has_images = bool(images)
            has_drawings = bool(drawings)
            ocr_reasons = []
            if has_images:
                ocr_reasons.append("images_may_contain_rasterized_text")
            if has_images and not has_text:
                ocr_reasons.append("no_native_text_with_images")
            pages.append({
                "number": page_number,
                "width": round(page.rect.width, 3),
                "height": round(page.rect.height, 3),
                "rotation": page.rotation,
                "content_type": _page_classification(has_text, has_images, has_drawings),
                "has_native_text": has_text,
                "has_images": has_images,
                "has_vector_drawings": has_drawings,
                "blocks": blocks,
                "drawings": drawings,
                "text_character_count": sum(len(block["text"]) for block in text_blocks),
                "requires_ocr_review": has_images,
                "ocr_review": {"required": has_images, "reasons": ocr_reasons},
            })
    return {
        "schema_version": SCHEMA_VERSION,
        "extraction_engine": {"name": ENGINE_NAME, "version": pymupdf.__version__},
        "source_name": path.name,
        "source_sha256": source_sha,
        "page_count": len(pages),
        "coordinate_unit": "PDF points",
        "reading_order_note": "Heuristic coordinate sorting; inspect complex multi-column layouts.",
        "pages": pages,
    }


def write_manifest(manifest: dict, output: str | Path) -> Path:
    """Atomically write a UTF-8 JSON manifest."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".stolas-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(manifest, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return output
