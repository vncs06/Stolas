"""Structured, non-OCR PDF extraction with stable IDs and geometry."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile

import fitz

SCHEMA_VERSION = "0.1.0"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bbox(values):
    return [round(float(v), 3) for v in values]


def _block_id(source_sha: str, page_number: int, block_index: int) -> str:
    return f"{source_sha[:16]}:p{page_number:05d}:b{block_index:04d}"


def extract_pdf(path: str | Path) -> dict:
    """Extract every page's text blocks, spans, image blocks and vector bounds.

    Does not perform OCR, infer protected sigils, or guarantee reading order
    for complex multi-column documents. All coordinates use PDF points.
    """
    path = Path(path)
    source_sha = sha256_file(path)
    pages = []
    with fitz.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("Password-protected PDF: supply an unlocked, authorized copy.")
        for page_index, page in enumerate(doc):
            page_number = page_index + 1
            raw = page.get_text("dict", sort=True)
            blocks = []
            for block_index, block in enumerate(raw.get("blocks", [])):
                item = {
                    "id": _block_id(source_sha, page_number, block_index),
                    "kind": "text" if block["type"] == 0 else "image",
                    "bbox": _bbox(block["bbox"]),
                    "reading_order": block_index,
                }
                if block["type"] == 0:
                    lines = []
                    for line in block.get("lines", []):
                        spans = []
                        for span in line.get("spans", []):
                            spans.append({
                                "text": span.get("text", ""),
                                "bbox": _bbox(span["bbox"]),
                                "font": span.get("font"),
                                "size": round(float(span.get("size", 0)), 3),
                                "flags": span.get("flags"),
                                "color": span.get("color"),
                            })
                        lines.append({"bbox": _bbox(line["bbox"]), "spans": spans})
                    item["lines"] = lines
                    item["text"] = "\n".join("".join(s["text"] for s in line["spans"]) for line in lines)
                else:
                    item["width"] = block.get("width")
                    item["height"] = block.get("height")
                    item["ext"] = block.get("ext")
                blocks.append(item)
            drawings = []
            for drawing_index, drawing in enumerate(page.get_drawings()):
                drawings.append({
                    "id": f"{source_sha[:16]}:p{page_number:05d}:d{drawing_index:04d}",
                    "bbox": _bbox(drawing["rect"]),
                    "type": drawing.get("type"),
                })
            pages.append({
                "number": page_number,
                "width": round(page.rect.width, 3),
                "height": round(page.rect.height, 3),
                "rotation": page.rotation,
                "blocks": blocks,
                "drawings": drawings,
                "text_character_count": sum(len(b.get("text", "")) for b in blocks),
                "requires_ocr_review": not any(b["kind"] == "text" and b.get("text", "").strip() for b in blocks),
            })
    return {
        "schema_version": SCHEMA_VERSION,
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
