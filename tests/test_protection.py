"""Synthetic tests for protection and translation-eligibility classification."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stolas.protection import classify_manifest, write_protection_manifest


SOURCE_SHA = "a" * 64


def _text_block(identifier, text, bbox, *, flags=0, font="Synthetic"):
    return {
        "id": identifier,
        "kind": "text",
        "bbox": bbox,
        "text": text,
        "lines": [{"bbox": bbox, "spans": [{
            "text": text,
            "bbox": bbox,
            "font": font,
            "size": 12.0,
            "flags": flags,
            "color": 0,
        }]}],
    }


def _text_block_spans(identifier, spans, bbox):
    width = (bbox[2] - bbox[0]) / len(spans)
    span_items = []
    for index, value in enumerate(spans):
        text, flags = value if isinstance(value, tuple) else (value, 0)
        span_items.append({
            "text": text,
            "bbox": [bbox[0] + width * index, bbox[1],
                     bbox[0] + width * (index + 1), bbox[3]],
            "font": "Synthetic", "size": 12.0, "flags": flags, "color": 0,
        })
    return {
        "id": identifier, "kind": "text", "bbox": bbox,
        "text": "".join(item["text"] for item in span_items),
        "lines": [{"bbox": bbox, "spans": span_items}],
    }


def _custom_manifest(page_blocks, *, source_sha="c" * 64):
    pages = []
    language_sample = (
        "This is an English document and the text explains what the reader should "
        "do before and after each step of the method."
    )
    for page_number, supplied_blocks in enumerate(page_blocks, 1):
        blocks = [_text_block(
            f"language-sample-{page_number}", language_sample, [20, 10, 280, 30]
        ), *supplied_blocks]
        has_images = any(block.get("kind") == "image" for block in blocks)
        pages.append({
            "number": page_number, "width": 300, "height": 500, "rotation": 0,
            "content_type": "mixed" if has_images else "text",
            "has_native_text": True, "has_images": has_images,
            "has_vector_drawings": False, "requires_ocr_review": has_images,
            "blocks": blocks, "drawings": [],
            "text_character_count": sum(
                len(block.get("text", "")) for block in blocks
                if block.get("kind") == "text"
            ),
        })
    return {
        "schema_version": "0.2.0",
        "extraction_engine": {"name": "PyMuPDF", "version": "2.0"},
        "source_name": "custom-context.pdf", "source_sha256": source_sha,
        "page_count": len(pages), "coordinate_unit": "PDF points",
        "pages": pages,
    }


def _manifest():
    image_one = {
        "id": "image-mixed", "kind": "image", "bbox": [20, 80, 140, 180],
        "xref": 10, "width": 120, "height": 100,
    }
    image_two = {
        "id": "image-only", "kind": "image", "bbox": [0, 0, 300, 400],
        "xref": 11, "width": 300, "height": 400,
    }
    drawing = {
        "id": "vector-one", "bbox": [10, 10, 90, 90], "type": "fs",
        "items": [["re", [10, 10, 90, 90], 1]],
    }
    pages = [
        {
            "number": 1, "width": 300, "height": 400, "rotation": 0,
            "content_type": "mixed", "has_native_text": True,
            "has_images": True, "has_vector_drawings": False,
            "requires_ocr_review": True,
            "blocks": [
                _text_block("latin", "Ordinary Latin text.", [20, 20, 160, 35]),
                _text_block("hebrew", "שלום", [20, 40, 80, 55]),
                _text_block("mixed", "Name שלום", [20, 60, 100, 75]),
                _text_block("symbol", "☿", [180, 20, 195, 35]),
                _text_block("private", "\ue000", [200, 20, 215, 35]),
                _text_block("short-non-latin", "ЖЖ", [220, 20, 245, 35]),
                _text_block("foreign-prose", "Ж" * 40, [20, 185, 280, 200]),
                image_one,
            ],
            "drawings": [], "text_character_count": 38,
        },
        {
            "number": 2, "width": 300, "height": 400, "rotation": 0,
            "content_type": "image", "has_native_text": False,
            "has_images": True, "has_vector_drawings": False,
            "requires_ocr_review": True, "blocks": [image_two], "drawings": [],
            "text_character_count": 0,
        },
        {
            "number": 3, "width": 300, "height": 400, "rotation": 0,
            "content_type": "vector", "has_native_text": False,
            "has_images": False, "has_vector_drawings": True,
            "requires_ocr_review": False, "blocks": [], "drawings": [drawing],
            "text_character_count": 0,
        },
    ]
    return {
        "schema_version": "0.2.0",
        "extraction_engine": {"name": "PyMuPDF", "version": "2.0"},
        "source_name": "synthetic.pdf",
        "source_sha256": SOURCE_SHA,
        "page_count": len(pages),
        "coordinate_unit": "PDF points",
        "pages": pages,
    }


def _by_source(result, source_id):
    return [item for item in result["classifications"]
            if item["source_id"] == source_id]


def _source_text(manifest, decision):
    for page in manifest["pages"]:
        for block in page.get("blocks", []):
            if block.get("id") != decision["source_id"]:
                continue
            locator = decision["source_locator"]
            span = block["lines"][locator["line_index"]]["spans"][locator["span_index"]]
            return span["text"][locator["character_start"]:locator["character_end"]]
    raise AssertionError(f"Source not found: {decision['source_id']}")


def _context_manifest():
    page_texts = [
        [
            "The ritual takes approximately ten minutes.",
            "These instructions explain how the ceremony is performed and what you should do.",
            "This chapter explains pronunciation and the use of names.",
        ],
        ["Repeat: DEUS MEUMQUE JUS"],
        ["Say ADONAI three times."],
        ["The words are AH-RAH-MAH."],
        ["Vibrate YHVH."],
        ['The Latin phrase "Lux Mundi" is used here.'],
        ["Say the Latin words slowly."],
        ["The name is pronounced as AH-RAH-MAH."],
        ["Repeat the following phrase three times."],
        ["Latin was commonly used in older manuscripts."],
        ["NEW YORK", "CHAPTER THREE"],
        ["YHVH"],
        ["Pronunciation", "AH-RAH-MAH", "The syllables sound like this example."],
    ]
    pages = []
    for page_number, texts in enumerate(page_texts, 1):
        blocks = []
        for index, text in enumerate(texts):
            top = 20 + index * 30
            blocks.append(_text_block(
                f"context-{page_number}-{index}", text, [20, top, 280, top + 18],
                flags=16 if text == "AH-RAH-MAH" and page_number == 13 else 0,
            ))
        pages.append({
            "number": page_number, "width": 300, "height": 400, "rotation": 0,
            "content_type": "text", "has_native_text": True,
            "has_images": False, "has_vector_drawings": False,
            "requires_ocr_review": False, "blocks": blocks, "drawings": [],
            "text_character_count": sum(len(text) for text in texts),
        })
    return {
        "schema_version": "0.2.0",
        "extraction_engine": {"name": "PyMuPDF", "version": "2.0"},
        "source_name": "contextual-synthetic.pdf",
        "source_sha256": "b" * 64,
        "page_count": len(pages),
        "coordinate_unit": "PDF points",
        "pages": pages,
    }


class ProtectionTests(unittest.TestCase):
    def setUp(self):
        self.manifest = _manifest()
        self.result = classify_manifest(self.manifest)

    def test_common_latin_text_is_translatable(self):
        item = _by_source(self.result, "latin")[0]
        self.assertEqual(item["classification"], "translatable")
        self.assertFalse(item["review_required"])

    def test_image_is_protected_and_mixed_page_requests_review(self):
        item = _by_source(self.result, "image-mixed")[0]
        self.assertEqual(item["classification"], "protected")
        self.assertTrue(item["review_required"])
        self.assertEqual(item["reason"], "image_on_mixed_page_may_contain_text")

    def test_image_only_page_is_protected_and_requests_review(self):
        item = _by_source(self.result, "image-only")[0]
        self.assertEqual(item["classification"], "protected")
        self.assertTrue(item["review_required"])
        self.assertEqual(item["reason"], "image_only_page_may_contain_rasterized_text")

    def test_vector_is_protected(self):
        item = _by_source(self.result, "vector-one")[0]
        self.assertEqual(item["classification"], "protected")
        self.assertFalse(item["review_required"])

    def test_native_hebrew_is_preserved_without_semantic_inference(self):
        item = _by_source(self.result, "hebrew")[0]
        self.assertEqual(item["classification"], "protected")
        self.assertEqual(item["reason"], "foreign_language_preserved")
        self.assertIn("local_language=he", item["evidence"])

    def test_mixed_latin_and_hebrew_is_split_by_function(self):
        items = _by_source(self.result, "mixed")
        classified = {item["classification"]: _source_text(self.manifest, item)
                      for item in items}
        self.assertEqual(classified["protected"], "שלום")
        self.assertEqual(classified["translatable"].strip(), "Name")

    def test_isolated_symbol_requires_review(self):
        item = _by_source(self.result, "symbol")[0]
        self.assertEqual(item["classification"], "review_required")
        self.assertEqual(item["reason"], "symbol_sequence_requires_editorial_review")

    def test_private_use_character_requires_review(self):
        item = _by_source(self.result, "private")[0]
        self.assertEqual(item["classification"], "review_required")
        self.assertEqual(item["reason"], "private_use_character_requires_editorial_review")

    def test_short_uncommon_alphabet_sequence_is_preserved(self):
        item = _by_source(self.result, "short-non-latin")[0]
        self.assertEqual(item["classification"], "protected")
        self.assertEqual(item["reason"], "foreign_language_preserved")

    def test_deliberate_foreign_prose_is_preserved_in_english_document(self):
        item = _by_source(self.result, "foreign-prose")[0]
        self.assertEqual(item["classification"], "protected")

    def test_manual_override_prevails_over_heuristic_and_derived_region(self):
        image_decision = _by_source(self.result, "image-mixed")[0]
        overrides = {
            "schema_version": "0.1.0", "source_sha256": SOURCE_SHA,
            "element_overrides": [{
                "decision_id": image_decision["id"],
                "classification": "translatable",
                "reason": "editor_confirmed_textual_image",
                "review_required": False,
            }],
        }
        result = classify_manifest(self.manifest, overrides)
        item = _by_source(result, "image-mixed")[0]
        self.assertEqual(item["classification"], "translatable")
        self.assertEqual(item["decision_source"], "editorial_override")
        self.assertFalse(any(region["source_ids"] == ["image-mixed"]
                             for region in result["protected_regions"]))

    def test_source_override_can_protect_hebrew_span(self):
        overrides = {
            "schema_version": "0.1.0",
            "element_overrides": [{
                "source_id": "hebrew", "classification": "protected",
                "reason": "editor_confirmed_ritual_name",
            }],
        }
        item = _by_source(classify_manifest(self.manifest, overrides), "hebrew")[0]
        self.assertEqual(item["classification"], "protected")
        self.assertEqual(item["decision_source"], "editorial_override")
        self.assertNotIn("confidence", item)

    def test_manual_protected_region(self):
        overrides = {
            "schema_version": "0.1.0",
            "region_overrides": [{
                "page": 1, "bbox": [12, 190, 288, 350],
                "classification": "protected", "reason": "editor_marked_diagram",
                "source_ids": ["image-mixed", "symbol"],
            }],
        }
        result = classify_manifest(self.manifest, overrides)
        region = next(item for item in result["protected_regions"]
                      if item["region_type"] == "manual_region")
        self.assertEqual(region["classification"], "protected")
        self.assertEqual(region["decision_source"], "editorial_override")
        self.assertEqual(region["source_ids"], ["image-mixed", "symbol"])

    def test_manual_region_rejects_source_from_another_page(self):
        overrides = {
            "schema_version": "0.1.0",
            "region_overrides": [{
                "page": 1, "bbox": [12, 190, 288, 350],
                "classification": "protected", "reason": "invalid_cross_page_region",
                "source_ids": ["vector-one"],
            }],
        }
        with self.assertRaisesRegex(ValueError, "another page"):
            classify_manifest(self.manifest, overrides)

    def test_output_is_deterministic(self):
        self.assertEqual(self.result, classify_manifest(copy.deepcopy(self.manifest)))
        encoded_one = json.dumps(self.result, ensure_ascii=False, sort_keys=True)
        encoded_two = json.dumps(classify_manifest(self.manifest),
                                 ensure_ascii=False, sort_keys=True)
        self.assertEqual(encoded_one, encoded_two)

    def test_input_manifest_is_not_modified(self):
        original = copy.deepcopy(self.manifest)
        classify_manifest(self.manifest)
        self.assertEqual(self.manifest, original)

    def test_serialization_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "nested" / "protection.json"
            write_protection_manifest(self.result, output)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), self.result)

    def test_cli_smoke_and_invalid_schema_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            output = root / "protection.json"
            manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
            command = [sys.executable, "-m", "scripts.classify_protected",
                       str(manifest_path), "--out", str(output)]
            completed = subprocess.run(command, cwd=Path(__file__).parents[1],
                                       capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(output.exists())

            invalid = copy.deepcopy(self.manifest)
            invalid["schema_version"] = "9.9.9"
            manifest_path.write_text(json.dumps(invalid), encoding="utf-8")
            output.unlink()
            completed = subprocess.run(command, cwd=Path(__file__).parents[1],
                                       capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("Unsupported extraction schema", completed.stderr)
            self.assertFalse(output.exists())


class ContextualProtectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = _context_manifest()
        cls.result = classify_manifest(cls.manifest)

    def decisions(self, source_id):
        return _by_source(self.result, source_id)

    def classified_text(self, source_id, classification):
        return [
            _source_text(self.manifest, item) for item in self.decisions(source_id)
            if item["classification"] == classification
        ]

    def test_schema_and_document_language_are_recorded(self):
        self.assertEqual(self.result["schema_version"], "0.2.0")
        self.assertEqual(self.result["ruleset_version"], "0.2.0")
        self.assertEqual(
            self.result["source_document"]["predominant_language"]["language"], "en"
        )

    def test_repeat_splits_instruction_from_latin_formula(self):
        protected = self.classified_text("context-2-0", "protected")
        translated = self.classified_text("context-2-0", "translatable")
        self.assertEqual(protected, ["DEUS MEUMQUE JUS"])
        self.assertIn("Repeat: ", translated)
        decision = next(item for item in self.decisions("context-2-0")
                        if item["classification"] == "protected")
        self.assertEqual(decision["reason"], "foreign_language_preserved")
        self.assertIn("cue=repeat", decision["evidence"])

    def test_say_name_preserves_literal_and_translates_instruction(self):
        self.assertEqual(self.classified_text("context-3-0", "protected"), ["ADONAI"])
        self.assertTrue(self.classified_text("context-3-0", "translatable"))

    def test_words_are_preserves_phonetic_formula(self):
        self.assertEqual(
            self.classified_text("context-4-0", "protected"), ["AH-RAH-MAH"]
        )

    def test_vibrate_preserves_literal(self):
        self.assertEqual(self.classified_text("context-5-0", "protected"), ["YHVH"])

    def test_explicit_latin_phrase_preserves_only_quoted_content(self):
        self.assertEqual(self.classified_text("context-6-0", "protected"), ["Lux Mundi"])
        translated = "".join(self.classified_text("context-6-0", "translatable"))
        self.assertIn("The Latin phrase", translated)
        self.assertIn("is used here", translated)

    def test_explanation_without_literal_remains_translatable(self):
        for source_id in ("context-7-0", "context-9-0", "context-10-0"):
            items = self.decisions(source_id)
            self.assertTrue(all(item["classification"] == "translatable"
                                for item in items), source_id)

    def test_pronounced_as_splits_explanation_from_literal(self):
        self.assertEqual(
            self.classified_text("context-8-0", "protected"), ["AH-RAH-MAH"]
        )
        self.assertTrue(self.classified_text("context-8-0", "translatable"))

    def test_uppercase_alone_does_not_trigger_preservation(self):
        for source_id in ("context-11-0", "context-11-1"):
            self.assertTrue(all(item["classification"] == "translatable"
                                for item in self.decisions(source_id)))

    def test_term_memory_preserves_repeated_literal_consistently(self):
        item = self.decisions("context-12-0")[0]
        self.assertEqual(item["classification"], "protected")
        self.assertEqual(item["reason"], "consistent_preserved_term")
        self.assertTrue(any(value.startswith("term_memory=")
                            for value in item["evidence"]))
        self.assertGreaterEqual(self.result["summary"]["linguistic_role_counts"]
                                ["ritual_formula"], 2)

    def test_pronunciation_context_preserves_isolated_phonetic_line(self):
        item = self.decisions("context-13-1")[0]
        self.assertEqual(item["classification"], "protected")
        self.assertEqual(item["linguistic_role"], "phonetic_formula")

    def test_contextual_result_is_deterministic(self):
        self.assertEqual(self.result, classify_manifest(copy.deepcopy(self.manifest)))


class ContextBoundaryAndFalsePositiveTests(unittest.TestCase):
    def test_cue_and_literal_across_spans_and_lines(self):
        manifest = _custom_manifest([[
            _text_block_spans(
                "same-line-spans", ["Repeat: ", "DEUS MEUMQUE JUS"],
                [20, 80, 280, 98],
            ),
            _text_block(
                "previous-line-instruction",
                "Repeat the following phrase three times.", [20, 130, 280, 148],
            ),
            _text_block("isolated-formula", "LUX MUNDI", [20, 158, 280, 176]),
            _text_block_spans(
                "split-pronunciation",
                ["BAEL", " is pronounced as ", "BAY-EL"],
                [20, 210, 280, 228],
            ),
        ]])
        result = classify_manifest(manifest)

        same_line = _by_source(result, "same-line-spans")
        self.assertEqual(
            [_source_text(manifest, item) for item in same_line
             if item["classification"] == "protected"],
            ["DEUS MEUMQUE JUS"],
        )
        isolated = _by_source(result, "isolated-formula")[0]
        self.assertEqual(isolated["classification"], "protected")
        self.assertIn("preceding_instruction=true", isolated["evidence"])

        pronunciation = _by_source(result, "split-pronunciation")
        protected = {
            _source_text(manifest, item): item for item in pronunciation
            if item["classification"] == "protected"
        }
        self.assertEqual(set(protected), {"BAEL", "BAY-EL"})
        self.assertEqual(protected["BAEL"]["linguistic_role"], "proper_name")
        self.assertEqual(protected["BAY-EL"]["linguistic_role"], "phonetic_formula")

    def test_ambiguous_literal_stays_translatable_and_requests_review(self):
        manifest = _custom_manifest([[
            _text_block("weak-cue", "The phrase appears below.", [20, 80, 280, 98]),
            _text_block("ambiguous", "XARON", [20, 108, 280, 126], flags=16),
        ]])
        item = _by_source(classify_manifest(manifest), "ambiguous")[0]
        self.assertEqual(item["classification"], "translatable")
        self.assertTrue(item["review_required"])
        self.assertEqual(item["reason"], "ambiguous_contextual_literal")

    def test_false_positive_categories_remain_translatable(self):
        image = {
            "id": "unrelated-image", "kind": "image", "bbox": [105, 330, 260, 410],
            "xref": 77, "width": 155, "height": 80,
        }
        candidates = [
            _text_block("ordinary-prose", "This is ordinary English prose.",
                        [20, 70, 280, 88]),
            _text_block("normal-name", "Michael", [20, 105, 280, 123]),
            _text_block("uppercase-title", "ADVANCED METHODS", [20, 140, 280, 158]),
            _text_block("city", "PARIS", [20, 175, 280, 193]),
            _text_block("technical-term", "state-of-the-art", [20, 210, 280, 228]),
            _text_block("unknown-word", "Zorvax", [20, 245, 280, 263]),
            _text_block("technical-code", "CLIENT-SERVER", [20, 265, 280, 283]),
            _text_block(
                "discussed-foreign-phrase",
                'The expression "Deus Meumque Jus" is discussed as a historical example.',
                [20, 290, 280, 308],
            ),
            _text_block("near-unrelated-image", "XARON", [20, 340, 100, 358],
                        flags=16),
            image,
        ]
        manifest = _custom_manifest([candidates])
        result = classify_manifest(manifest)
        for source_id in (
            "ordinary-prose", "normal-name", "uppercase-title", "city",
            "technical-term", "unknown-word", "discussed-foreign-phrase",
            "technical-code", "near-unrelated-image",
        ):
            items = _by_source(result, source_id)
            self.assertTrue(items, source_id)
            self.assertTrue(all(item["classification"] == "translatable"
                                for item in items), source_id)
            self.assertFalse(any(item["review_required"] for item in items), source_id)

    def test_future_cue_does_not_retroactively_protect_text(self):
        manifest = _custom_manifest([[
            _text_block("before-cue", "XARON", [20, 80, 280, 98], flags=16),
            _text_block("later-cue", "Pronunciation is explained below.",
                        [20, 108, 280, 126]),
        ]])
        item = _by_source(classify_manifest(manifest), "before-cue")[0]
        self.assertEqual(item["classification"], "translatable")
        self.assertFalse(item["review_required"])

    def test_term_memory_does_not_override_a_different_function(self):
        manifest = _custom_manifest([
            [_text_block("ritual-use", "Say Mercury.", [20, 80, 280, 98])],
            [_text_block("editorial-use", "Mercury", [20, 80, 280, 98])],
        ])
        result = classify_manifest(manifest)
        ritual = _by_source(result, "ritual-use")
        self.assertTrue(any(item["classification"] == "protected" for item in ritual))
        editorial = _by_source(result, "editorial-use")[0]
        self.assertEqual(editorial["classification"], "translatable")
        self.assertNotEqual(editorial["reason"], "consistent_preserved_term")

    def test_formatted_phonetic_example_protects_label_not_example_word(self):
        manifest = _custom_manifest([[
            _text_block_spans(
                "phonetic-example", [("YAH", 16), " is like ", ("yarn", 22)],
                [20, 80, 280, 98],
            ),
        ]])
        items = _by_source(classify_manifest(manifest), "phonetic-example")
        classified = {
            _source_text(manifest, item): item["classification"] for item in items
        }
        self.assertEqual(classified["YAH"], "protected")
        self.assertEqual(classified["yarn"], "translatable")

    def test_strong_term_memory_can_resolve_an_ambiguous_occurrence(self):
        manifest = _custom_manifest([[
            _text_block("weak-memory-cue", "The phrase appears below.",
                        [20, 80, 280, 98]),
            _text_block("ambiguous-memory", "UH", [20, 108, 280, 126], flags=16),
        ], [
            _text_block_spans(
                "strong-memory", [("UH", 16), " sounds like ", ("up", 22)],
                [20, 80, 280, 98],
            ),
        ]])
        result = classify_manifest(manifest)
        ambiguous = _by_source(result, "ambiguous-memory")[0]
        self.assertEqual(ambiguous["classification"], "protected")
        self.assertFalse(ambiguous["review_required"])
        self.assertEqual(ambiguous["reason"], "consistent_preserved_term")

    def test_clustered_phonetic_formula_lines_are_protected(self):
        manifest = _custom_manifest([
            [
                _text_block(
                    "formula-long", "VEH-AH-TAH AH-DOH-NIGH MAH-GEN",
                    [20, 80, 280, 98],
                ),
                _text_block("formula-short", "TEASE-NACH", [20, 108, 280, 126]),
            ],
            [
                _text_block(
                    "wrapped-pronunciation-cue",
                    "Marchosias can be pronounced as MARK-OH-", [20, 80, 280, 98],
                ),
                _text_block("wrapped-pronunciation", "SEE-ASS",
                            [20, 108, 280, 126]),
            ],
        ])
        result = classify_manifest(manifest)
        for source_id in ("formula-long", "formula-short", "wrapped-pronunciation"):
            item = _by_source(result, source_id)[0]
            self.assertEqual(item["classification"], "protected")
            self.assertEqual(item["linguistic_role"], "phonetic_formula")


if __name__ == "__main__":
    unittest.main()
