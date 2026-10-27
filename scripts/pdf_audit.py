#!/usr/bin/env python3
"""Pré-auditoria estrutural e visual de PDFs; não traduz nem aprova páginas."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import fitz
from PIL import Image, ImageChops, ImageStat


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inventory(path):
    doc = fitz.open(path)
    pages = []
    for index, page in enumerate(doc):
        blocks = page.get_text("dict")["blocks"]
        spans = [s for b in blocks if "lines" in b for line in b["lines"] for s in line["spans"]]
        pages.append({
            "page": index + 1,
            "width": page.rect.width,
            "height": page.rect.height,
            "rotation": page.rotation,
            "text_blocks": sum(b["type"] == 0 for b in blocks),
            "image_blocks": sum(b["type"] == 1 for b in blocks),
            "embedded_images": len(page.get_images(full=True)),
            "vector_drawings": len(page.get_drawings()),
            "characters": len(page.get_text()),
            "fonts": sorted(set(s["font"] for s in spans)),
            "font_sizes": sorted(set(round(s["size"], 2) for s in spans)),
        })
    result = {"source": str(path), "sha256": digest(path), "page_count": len(doc), "pages": pages}
    doc.close()
    return result


def render(page, dpi):
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def audit(original, translated, out, dpi=150, start=1, end=None):
    src, dst = fitz.open(original), fitz.open(translated)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    total = max(len(src), len(dst))
    last = min(end or total, total)
    rows = []
    for p in range(max(1, start), last + 1):
        row = {"page": p, "source_present": p <= len(src), "target_present": p <= len(dst),
               "size_match": False, "image_difference_mean": None,
               "status": "needs_manual_review", "final_visual_review": "pending"}
        if p > len(src) or p > len(dst):
            row["status"] = "missing_page"
            rows.append(row)
            continue
        a, b = src[p - 1], dst[p - 1]
        row["size_match"] = abs(a.rect.width - b.rect.width) < 0.1 and abs(a.rect.height - b.rect.height) < 0.1
        im_a, im_b = render(a, dpi), render(b, dpi)
        im_a.save(out / f"page-{p:04d}-original.png")
        im_b.save(out / f"page-{p:04d}-translated.png")
        if im_a.size == im_b.size:
            diff = ImageChops.difference(im_a, im_b)
            diff.save(out / f"page-{p:04d}-diff.png")
            row["image_difference_mean"] = round(sum(ImageStat.Stat(diff).mean) / 3, 3)
        rows.append(row)
    report = {
        "source_sha256": digest(original), "target_sha256": digest(translated),
        "source_pages": len(src), "target_pages": len(dst),
        "limitations": "Pixel differences include intentional translation changes; no semantic, sigil or layout approval is inferred.",
        "pages": rows,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out / "report.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["page", "status"])
        writer.writeheader()
        writer.writerows(rows)
    src.close()
    dst.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inv = commands.add_parser("inventory")
    inv.add_argument("pdf")
    inv.add_argument("--out", required=True)
    qa = commands.add_parser("audit")
    qa.add_argument("original")
    qa.add_argument("translated")
    qa.add_argument("--out", required=True)
    qa.add_argument("--dpi", type=int, default=150)
    qa.add_argument("--start", type=int, default=1)
    qa.add_argument("--end", type=int)
    args = parser.parse_args()
    if args.command == "inventory":
        result = inventory(args.pdf)
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Inventariadas {result['page_count']} páginas: {path}")
    else:
        result = audit(args.original, args.translated, args.out, args.dpi, args.start, args.end)
        print(f"Relatório gerado: {len(result['pages'])} páginas; revisão manual pendente.")


if __name__ == "__main__":
    main()
