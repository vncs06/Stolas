"""Synthetic tests for reading order, editorial structure and reconciliation."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stolas.editorial import (
    build_editorial_structure,
    reconcile_editorial_structures,
    write_editorial_structure,
)
from stolas.protection import classify_manifest


SOURCE_SHA = "d" * 64


def _span(text, bbox, *, size=12.0, flags=0):
    return {
        "text": text, "bbox": bbox, "font": "Synthetic",
        "size": size, "flags": flags, "color": 0,
    }


def _block(identifier, text, bbox, *, size=12.0, flags=0, spans=None, lines=None):
    if lines is None:
        line_spans = spans or [_span(text, bbox, size=size, flags=flags)]
        lines = [{"bbox": bbox, "spans": line_spans}]
    return {
        "id": identifier, "kind": "text", "bbox": bbox,
        "reading_order": 0,
        "text": "\n".join("".join(span["text"] for span in line["spans"]) for line in lines),
        "lines": lines,
    }


def _image(identifier, bbox):
    return {
        "id": identifier, "kind": "image", "bbox": bbox,
        "xref": 10, "width": int(bbox[2] - bbox[0]),
        "height": int(bbox[3] - bbox[1]),
    }


def _drawing(identifier, bbox, *, color=None, width=1.0):
    return {
        "id": identifier, "bbox": bbox, "type": "s", "color": color,
        "fill": None, "width": width, "stroke_opacity": 1.0,
        "fill_opacity": 1.0, "items": [["l", bbox[:2], bbox[2:]]],
    }


def _manifest(pages, *, source_sha=SOURCE_SHA):
    result = []
    for number, supplied in enumerate(pages, 1):
        blocks = supplied.get("blocks", [])
        drawings = supplied.get("drawings", [])
        has_text = any(item.get("kind") == "text" for item in blocks)
        has_images = any(item.get("kind") == "image" for item in blocks)
        result.append({
            "number": number, "width": supplied.get("width", 600),
            "height": supplied.get("height", 800), "rotation": 0,
            "content_type": "mixed" if has_text and has_images else "text" if has_text else "image" if has_images else "vector" if drawings else "empty",
            "has_native_text": has_text, "has_images": has_images,
            "has_vector_drawings": bool(drawings),
            "requires_ocr_review": has_images,
            "blocks": blocks, "drawings": drawings,
            "text_character_count": sum(len(item.get("text", "")) for item in blocks if item.get("kind") == "text"),
        })
    return {
        "schema_version": "0.2.0",
        "extraction_engine": {"name": "PyMuPDF", "version": "2.0"},
        "source_name": "editorial-synthetic.pdf", "source_sha256": source_sha,
        "page_count": len(result), "coordinate_unit": "PDF points",
        "pages": result,
    }


def _build(manifest):
    return build_editorial_structure(manifest, classify_manifest(manifest))


def _units(result, page=1):
    return [item for item in result["editorial_units"] if item["page"] == page]


class ReadingOrderTests(unittest.TestCase):
    def test_single_column_uses_vertical_order(self):
        manifest = _manifest([{"blocks": [
            _block("second", "Second paragraph.", [40, 180, 560, 205]),
            _block("first", "First paragraph.", [40, 100, 560, 125]),
        ]}])
        result = _build(manifest)
        self.assertEqual([item["text"] for item in _units(result)], [
            "First paragraph.", "Second paragraph.",
        ])
        self.assertEqual(result["pages"][0]["reading_order"]["column_count"], 1)

    def test_full_width_heading_precedes_complete_columns(self):
        manifest = _manifest([{"blocks": [
            _block("right-one", "Right one.", [330, 110, 560, 135]),
            _block("left-one", "Left one.", [40, 110, 270, 135]),
            _block("right-two", "Right two.", [330, 160, 560, 185]),
            _block("title", "A WIDE TITLE", [40, 40, 560, 75], size=22, flags=16),
            _block("left-two", "Left two.", [40, 160, 270, 185]),
        ]}])
        result = _build(manifest)
        self.assertEqual([item["text"] for item in _units(result)], [
            "A WIDE TITLE", "Left one.", "Left two.", "Right one.", "Right two.",
        ])
        self.assertEqual(result["pages"][0]["reading_order"]["column_count"], 2)
        self.assertEqual(_units(result)[0]["editorial_type"], "heading")

    def test_image_is_interleaved_without_alternating_columns(self):
        manifest = _manifest([{"blocks": [
            _block("left-one", "Left one.", [30, 100, 250, 125]),
            _block("left-two", "Left two.", [30, 150, 250, 175]),
            _image("middle-image", [270, 120, 330, 210]),
            _block("right-one", "Right one.", [350, 100, 570, 125]),
            _block("right-two", "Right two.", [350, 150, 570, 175]),
        ]}])
        result = _build(manifest)
        text = [item["text"] for item in _units(result)]
        self.assertEqual(text, ["Left one.", "Left two.", "Right one.", "Right two."])
        self.assertIn("middle-image", result["pages"][0]["reading_order"]["source_ids"])

    def test_three_simple_columns_are_read_column_by_column(self):
        manifest = _manifest([{"blocks": [
            _block("c3-1", "Third A.", [410, 100, 570, 125]),
            _block("c1-1", "First A.", [30, 100, 190, 125]),
            _block("c2-1", "Second A.", [220, 100, 380, 125]),
            _block("c3-2", "Third B.", [410, 150, 570, 175]),
            _block("c1-2", "First B.", [30, 150, 190, 175]),
            _block("c2-2", "Second B.", [220, 150, 380, 175]),
        ]}])
        result = _build(manifest)
        self.assertEqual(result["pages"][0]["reading_order"]["column_count"], 3)
        self.assertEqual([item["text"] for item in _units(result)], [
            "First A.", "First B.", "Second A.", "Second B.", "Third A.", "Third B.",
        ])

    def test_ambiguous_columns_record_candidate_and_review(self):
        manifest = _manifest([{"blocks": [
            _block("left", "Left.", [100, 100, 280, 125]),
            _block("right", "Right.", [290, 105, 470, 130]),
        ]}])
        result = _build(manifest)
        order = result["pages"][0]["reading_order"]
        self.assertTrue(order["review_required"])
        self.assertTrue(order["candidates"])
        self.assertEqual(result["statistics"]["ambiguity_count"], 1)


class EditorialStructureTests(unittest.TestCase):
    def test_repeated_header_footer_and_page_numbers_are_classified(self):
        pages = []
        for number in range(1, 4):
            pages.append({"blocks": [
                _block(f"header-{number}", "RUNNING TITLE", [40, 20, 560, 40], size=9),
                _block(f"body-{number}", f"Body page {number}.", [40, 120, 560, 145]),
                _block(f"footer-{number}", "Publisher imprint", [40, 750, 560, 770], size=8),
                _block(f"number-{number}", str(number), [290, 775, 310, 792], size=8),
            ]})
        result = _build(_manifest(pages))
        counts = result["statistics"]["editorial_type_counts"]
        self.assertEqual(counts["header"], 3)
        self.assertEqual(counts["footer"], 3)
        self.assertEqual(counts["page_number"], 3)
        self.assertEqual(result["statistics"]["segment_count"], 3)

    def test_structure_types_and_fragmented_lines(self):
        lines = [
            {"bbox": [40, 300, 560, 322], "spans": [_span("A paragraph ", [40, 300, 200, 322]), _span("split in spans", [200, 300, 380, 322])]},
            {"bbox": [40, 323, 560, 345], "spans": [_span("and several physical lines.", [40, 323, 300, 345])]},
        ]
        manifest = _manifest([{"blocks": [
            _block("title", "Main title", [40, 40, 560, 75], size=22, flags=16),
            _block("list", "• First item", [60, 110, 400, 132]),
            _block("note", "Note: keep this together.", [40, 160, 560, 182]),
            _block("quote", '“Quoted passage.”', [90, 210, 510, 232], flags=2),
            _block("fragmented", "", [40, 300, 560, 345], lines=lines),
            _block("footnote", "1 A compact footnote.", [40, 700, 360, 714], size=8),
        ]}])
        result = _build(manifest)
        by_text = {item["text"]: item for item in _units(result)}
        self.assertEqual(by_text["Main title"]["editorial_type"], "heading")
        self.assertEqual(by_text["• First item"]["editorial_type"], "list_item")
        self.assertEqual(by_text["Note: keep this together."]["editorial_type"], "note")
        self.assertEqual(by_text['“Quoted passage.”']["editorial_type"], "quote")
        self.assertEqual(by_text["1 A compact footnote."]["editorial_type"], "footnote")
        fragmented = next(item for item in _units(result) if item["source_ids"] == ["fragmented"])
        self.assertEqual(fragmented["text"], "A paragraph split in spans and several physical lines.")

    def test_caption_is_related_to_graphic(self):
        manifest = _manifest([{"blocks": [
            _image("figure", [100, 100, 500, 300]),
            _block("caption", "Figure 1. The diagram.", [140, 308, 460, 328], size=9),
        ]}])
        unit = _units(_build(manifest))[0]
        self.assertEqual(unit["editorial_type"], "caption")
        self.assertIn("below_graphic", unit["evidence"])

    def test_protected_literal_stays_inside_coherent_segment(self):
        manifest = _manifest([{"blocks": [
            _block("mixed", "Say DEUS MEUMQUE JUS three times.", [40, 100, 560, 125]),
        ]}])
        result = _build(manifest)
        segment = result["segments"][0]
        protected = [part["text"] for part in segment["parts"] if part["type"] == "protected"]
        translatable = "".join(part["text"] for part in segment["parts"] if part["type"] == "translatable")
        self.assertEqual(protected, ["DEUS MEUMQUE JUS"])
        self.assertIn("Say ", translatable)
        self.assertIn(" three times.", translatable)
        self.assertTrue(segment["contains_protected_parts"])

    def test_separate_formula_remains_with_its_translatable_label(self):
        manifest = _manifest([{"blocks": [
            _block("instruction", "Repeat the following phrase three times.", [40, 100, 560, 125]),
            _block("formula", "DEUS MEUMQUE JUS", [40, 135, 560, 160], flags=16),
        ]}])
        result = _build(manifest)
        self.assertEqual(len(result["segments"]), 1)
        segment = result["segments"][0]
        self.assertEqual(segment["source_ids"], ["instruction", "formula"])
        self.assertTrue(segment["contains_protected_parts"])
        self.assertEqual(
            [part["text"] for part in segment["parts"] if part["type"] == "protected"],
            ["DEUS MEUMQUE JUS"],
        )


class EditorialIdentityTests(unittest.TestCase):
    def test_ids_are_deterministic_and_tolerate_small_bbox_change(self):
        manifest = _manifest([{"blocks": [
            _block("paragraph", "Stable editorial content.", [40, 100, 560, 125]),
        ]}])
        first = _build(manifest)
        self.assertEqual(first, _build(copy.deepcopy(manifest)))
        shifted = copy.deepcopy(manifest)
        shifted["pages"][0]["blocks"][0]["bbox"] = [40.4, 100.4, 560.4, 125.4]
        shifted["pages"][0]["blocks"][0]["lines"][0]["bbox"] = [40.4, 100.4, 560.4, 125.4]
        shifted["pages"][0]["blocks"][0]["lines"][0]["spans"][0]["bbox"] = [40.4, 100.4, 560.4, 125.4]
        second = _build(shifted)
        self.assertEqual(
            first["editorial_units"][0]["editorial_id"],
            second["editorial_units"][0]["editorial_id"],
        )

    def test_id_survives_span_regrouping(self):
        one_span = _manifest([{"blocks": [
            _block("source-a", "One logical paragraph.", [40, 100, 560, 125]),
        ]}])
        two_spans = copy.deepcopy(one_span)
        two_spans["pages"][0]["blocks"][0]["lines"][0]["spans"] = [
            _span("One logical ", [40, 100, 250, 125]),
            _span("paragraph.", [250, 100, 560, 125]),
        ]
        first, second = _build(one_span), _build(two_spans)
        self.assertEqual(first["editorial_units"][0]["editorial_id"], second["editorial_units"][0]["editorial_id"])

    def test_id_survives_block_regrouping(self):
        lines = [
            {"bbox": [40, 100, 560, 120], "spans": [_span("One logical", [40, 100, 200, 120])]},
            {"bbox": [40, 122, 560, 142], "spans": [_span("paragraph continues.", [40, 122, 260, 142])]},
        ]
        grouped = _manifest([{"blocks": [
            _block("grouped-source", "", [40, 100, 560, 142], lines=lines),
        ]}])
        split = _manifest([{"blocks": [
            _block("split-one", "One logical", [40, 100, 560, 120]),
            _block("split-two", "paragraph continues.", [40, 122, 560, 142]),
        ]}])
        first, second = _build(grouped), _build(split)
        self.assertEqual(len(first["editorial_units"]), 1)
        self.assertEqual(len(second["editorial_units"]), 1)
        self.assertEqual(first["editorial_units"][0]["editorial_id"], second["editorial_units"][0]["editorial_id"])

    def test_reconciliation_covers_all_relationships(self):
        def unit(identifier, text, order=0):
            return {"editorial_id": identifier, "text": text, "editorial_type": "paragraph", "page": 1, "reading_order": order, "bbox": [0, order * 20, 100, order * 20 + 10]}

        previous = {"source_document": {"sha256": SOURCE_SHA}, "editorial_units": [
            unit("same", "Same"), unit("move-old", "Move", 1),
            unit("modify-old", "The old sentence", 2), unit("split-old", "Alpha beta", 3),
            unit("merge-a", "Gamma", 4), unit("merge-b", "delta", 5),
            unit("removed", "Removed", 6),
        ]}
        current = {"source_document": {"sha256": SOURCE_SHA}, "editorial_units": [
            unit("same", "Same"), unit("move-new", "Move", 3),
            unit("modify-new", "The newer sentence", 2),
            unit("split-a", "Alpha", 3), unit("split-b", "beta", 4),
            unit("merged", "Gamma delta", 5), unit("new", "New", 6),
        ]}
        result = reconcile_editorial_structures(previous, current)
        statuses = {item["status"] for item in result["changes"]}
        self.assertEqual(statuses, {"same", "moved", "modified", "split", "merged", "new", "removed"})


class GraphicGroupingTests(unittest.TestCase):
    def test_related_strokes_group_without_losing_members(self):
        drawings = [
            _drawing("figure-a", [100, 100, 150, 101]),
            _drawing("figure-b", [149, 100, 150, 150]),
            _drawing("figure-c", [100, 149, 150, 150]),
            _drawing("separate-a", [400, 500, 450, 501]),
            _drawing("separate-b", [449, 500, 450, 550]),
        ]
        result = _build(_manifest([{"blocks": [], "drawings": drawings}]))
        self.assertEqual(result["statistics"]["graphic_group_count"], 2)
        self.assertEqual(result["statistics"]["grouped_vector_count"], 5)
        members = [set(group["source_ids"]) for group in result["graphic_groups"]]
        self.assertIn({"figure-a", "figure-b", "figure-c"}, members)
        self.assertIn({"separate-a", "separate-b"}, members)

    def test_distant_editorial_lines_do_not_chain_into_giant_group(self):
        drawings = [
            _drawing("line-one", [20, 100, 580, 101]),
            _drawing("line-two", [20, 300, 580, 301]),
            _drawing("line-three", [20, 500, 580, 501]),
        ]
        result = _build(_manifest([{"blocks": [], "drawings": drawings}]))
        self.assertEqual(result["graphic_groups"], [])

    def test_vector_between_text_is_preserved_in_reading_sources(self):
        manifest = _manifest([{"blocks": [
            _block("before", "Before graphic.", [40, 100, 560, 125]),
            _block("after", "After graphic.", [40, 220, 560, 245]),
        ], "drawings": [_drawing("rule", [100, 170, 500, 171])]}])
        result = _build(manifest)
        sources = result["pages"][0]["reading_order"]["source_ids"]
        self.assertIn("rule", sources)
        self.assertFalse(any("rule" in segment["source_ids"] for segment in result["segments"]))


class EditorialCliTests(unittest.TestCase):
    def test_inputs_are_not_modified(self):
        manifest = _manifest([{"blocks": [
            _block("body", "A paragraph.", [40, 100, 560, 125]),
        ]}])
        protection = classify_manifest(manifest)
        original_manifest = copy.deepcopy(manifest)
        original_protection = copy.deepcopy(protection)
        build_editorial_structure(manifest, protection)
        self.assertEqual(manifest, original_manifest)
        self.assertEqual(protection, original_protection)

    def test_protection_hash_must_match_manifest(self):
        manifest = _manifest([{"blocks": []}])
        protection = classify_manifest(manifest)
        changed = copy.deepcopy(manifest)
        changed["source_name"] = "different-name.pdf"
        with self.assertRaisesRegex(ValueError, "does not match the extraction manifest hash"):
            build_editorial_structure(changed, protection)

    def test_atomic_serialization_cli_and_schema_rejection(self):
        manifest = _manifest([{"blocks": [
            _block("body", "A paragraph.", [40, 100, 560, 125]),
        ]}])
        protection = classify_manifest(manifest)
        result = build_editorial_structure(manifest, protection)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nested = root / "nested" / "editorial.json"
            write_editorial_structure(result, nested)
            self.assertEqual(json.loads(nested.read_text(encoding="utf-8")), result)

            manifest_path = root / "manifest.json"
            protection_path = root / "protection.json"
            output = root / "output.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            protection_path.write_text(json.dumps(protection), encoding="utf-8")
            command = [sys.executable, "-m", "scripts.build_editorial_structure",
                       str(manifest_path), str(protection_path), "--out", str(output)]
            completed = subprocess.run(command, cwd=Path(__file__).parents[1], capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(output.exists())

            invalid = copy.deepcopy(protection)
            invalid["schema_version"] = "9.9.9"
            protection_path.write_text(json.dumps(invalid), encoding="utf-8")
            output.unlink()
            completed = subprocess.run(command, cwd=Path(__file__).parents[1], capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("Unsupported protection schema", completed.stderr)
            self.assertFalse(output.exists())

    def test_cli_refuses_to_overwrite_input(self):
        manifest = _manifest([{"blocks": []}])
        protection = classify_manifest(manifest)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            protection_path = root / "protection.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            protection_path.write_text(json.dumps(protection), encoding="utf-8")
            command = [sys.executable, "-m", "scripts.build_editorial_structure",
                       str(manifest_path), str(protection_path), "--out", str(manifest_path)]
            completed = subprocess.run(command, cwd=Path(__file__).parents[1], capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("must not overwrite", completed.stderr)


if __name__ == "__main__":
    unittest.main()
