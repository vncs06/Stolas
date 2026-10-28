"""Tests for persistent editorial memory, merge and invalidation."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stolas.memory import build_editorial_memory, write_editorial_memory


SOURCE_SHA = "e" * 64


def _canonical_hash(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decision(identifier, role, *, confidence=0.95, reason="contextual_literal"):
    return {
        "id": identifier, "source_id": f"source-{identifier}", "page": 1,
        "element_type": "text_fragment", "classification": "protected",
        "reason": reason, "bbox": [0, 0, 10, 10],
        "decision_source": "heuristic", "review_required": False,
        "override_allowed": True, "linguistic_role": role,
        "confidence": confidence, "evidence": ["synthetic_evidence"],
    }


def _unit(identifier, text, page, order, *, editorial_type="paragraph", protected=None):
    decision_id = protected[0] if protected else None
    part_type = "protected" if protected else "translatable"
    part = {
        "type": part_type, "text": text, "source_id": f"source-{identifier}",
        "decision_ids": [decision_id] if decision_id else [], "review_required": False,
    }
    return {
        "editorial_id": identifier, "page": page, "bbox": [20, 20 + order * 20, 580, 38 + order * 20],
        "source_ids": [f"source-{identifier}"], "source_lines": [],
        "reading_order": order, "editorial_type": editorial_type,
        "confidence": 0.9, "evidence": ["synthetic"], "text": text,
        "parts": [part],
        "protection": {
            "contains_protected": bool(protected), "fully_protected": bool(protected),
            "review_required": False, "decision_ids": [decision_id] if decision_id else [],
        },
    }


def _artifacts(*, include_mercury=False):
    protection = {
        "schema_version": "0.2.0", "ruleset_version": "0.2.0",
        "source_manifest": {"schema_version": "0.2.0", "sha256": "f" * 64},
        "source_document": {
            "name": "memory-synthetic.pdf", "sha256": SOURCE_SHA, "page_count": 3,
            "predominant_language": {"language": "en", "confidence": 0.9},
        },
        "term_memory": [],
        "classifications": [
            _decision("phonetic", "phonetic_formula"),
            _decision("name", "proper_name"),
        ],
        "protected_regions": [], "summary": {},
    }
    units = [
        _unit("u1", "A sigil is a symbol. Michael studies the state-of-the-art sigil.", 1, 0),
        _unit("u2", "The sigil appears beside Michael.", 1, 1),
        _unit("u3", "SIGIL practice uses another sigil, sigil, sigil, sigil, sigil, sigil, sigil, and sigil.", 1, 2),
        _unit("u4", "AH-RAH-MAH", 2, 0, protected=("phonetic",)),
        _unit("u5", "ADONAI", 2, 1, protected=("name",)),
        _unit("u6", "The Method", 3, 0, editorial_type="heading"),
        _unit("u7", "The Method", 3, 1, editorial_type="subheading"),
    ]
    if include_mercury:
        protection["classifications"].append(_decision("mercury", "proper_name"))
        units.extend([
            _unit("u8", "Mercury", 3, 2, protected=("mercury",)),
            _unit("u9", "The name Mercury appears in this example.", 3, 3),
            _unit("u10", "The planet Mercury appears again.", 3, 4),
        ])
    segments = []
    for unit in units:
        if unit["protection"]["fully_protected"]:
            continue
        segments.append({
            "segment_id": f"segment-{unit['editorial_id']}",
            "editorial_id": unit["editorial_id"], "page": unit["page"],
            "editorial_type": unit["editorial_type"], "source_ids": unit["source_ids"],
            "parts": copy.deepcopy(unit["parts"]), "review_required": False,
            "contains_protected_parts": False,
        })
    editorial = {
        "schema_version": "0.1.0", "algorithm_version": "0.1.0",
        "source_manifest": {"schema_version": "0.2.0", "sha256": "f" * 64},
        "source_protection": {
            "schema_version": "0.2.0", "ruleset_version": "0.2.0",
            "sha256": _canonical_hash(protection),
        },
        "source_document": {"name": "memory-synthetic.pdf", "sha256": SOURCE_SHA, "page_count": 3},
        "pages": [], "editorial_units": units, "segments": segments,
        "graphic_groups": [], "warnings": [], "statistics": {},
    }
    return editorial, protection


def _terms(memory, normalized):
    return [item for item in memory["glossary"] if item["normalized_term"] == normalized]


class GlossaryTests(unittest.TestCase):
    def setUp(self):
        self.editorial, self.protection = _artifacts()
        self.memory = build_editorial_memory(self.editorial, self.protection)

    def test_schema_source_hashes_and_initial_version(self):
        self.assertEqual(self.memory["schema_version"], "0.1.0")
        self.assertEqual(self.memory["memory_version"], 1)
        self.assertEqual(self.memory["source_inputs"]["editorial_sha256"], _canonical_hash(self.editorial))
        self.assertEqual(self.memory["source_inputs"]["protection_sha256"], _canonical_hash(self.protection))

    def test_repeated_term_and_spelling_variants(self):
        sigil = next(item for item in _terms(self.memory, "sigil") if item["context_key"].startswith("lexical:"))
        self.assertEqual(sigil["frequency"], 12)
        self.assertEqual(set(sigil["variants"]), {"sigil", "SIGIL"})
        self.assertEqual(sigil["suggested_action"], "review_translation")

    def test_proper_name_and_technical_term_candidates(self):
        michael = next(item for item in _terms(self.memory, "michael") if item["category"] == "proper_name")
        technical = _terms(self.memory, "state-of-the-art")[0]
        self.assertEqual(michael["frequency"], 2)
        self.assertEqual(technical["category"], "technical_term")
        self.assertIsNone(technical["preserve"])

    def test_protected_name_and_transliteration_are_contextual_decisions(self):
        phonetic = next(item for item in _terms(self.memory, "ah-rah-mah") if item["context_key"] == "role:phonetic_formula")
        name = next(item for item in _terms(self.memory, "adonai") if item["context_key"] == "role:proper_name")
        self.assertTrue(phonetic["preserve"])
        self.assertEqual(phonetic["category"], "transliteration")
        self.assertTrue(name["preserve"])
        self.assertIn(phonetic["term_id"], self.memory["preserved_terms"])
        self.assertIsNotNone(phonetic["decision_id"])

    def test_context_excerpt_is_bounded_and_references_units(self):
        for entry in self.memory["glossary"]:
            for occurrence in entry["occurrences"]:
                self.assertLessEqual(len(occurrence["context_excerpt"]), 122)
                self.assertTrue(occurrence["editorial_id"])


class ConsistencyAndOverrideTests(unittest.TestCase):
    def test_same_surface_in_different_contexts_is_not_forced(self):
        editorial, protection = _artifacts(include_mercury=True)
        memory = build_editorial_memory(editorial, protection)
        mercury = _terms(memory, "mercury")
        self.assertGreaterEqual(len(mercury), 2)
        self.assertTrue(any(item["context_key"] == "role:proper_name" and item["preserve"] for item in mercury))
        self.assertTrue(any(item["context_key"] == "lexical:proper_name" and item["preserve"] is None for item in mercury))
        self.assertTrue(any(ref["normalized_term"] == "mercury" for ref in memory["cross_references"]))

    def test_human_translation_override_has_maximum_authority(self):
        editorial, protection = _artifacts()
        overrides = {
            "schema_version": "0.1.0", "source_document_sha256": SOURCE_SHA,
            "term_overrides": [{
                "source_term": "sigil", "context_key": "lexical:technical_term",
                "preferred_translation": "sigilo", "preserve": False,
                "category": "technical_term", "reason": "approved_translation",
            }],
        }
        memory = build_editorial_memory(editorial, protection, overrides=overrides)
        entry = next(item for item in _terms(memory, "sigil") if item["context_key"] == "lexical:technical_term")
        self.assertEqual(entry["preferred_translation"], "sigilo")
        self.assertFalse(entry["preserve"])
        self.assertEqual(entry["decision_source"], "human_override")
        decision = next(item for item in memory["decisions"] if item["decision_id"] == entry["decision_id"])
        self.assertEqual(decision["decision_source"], "human")

    def test_conflict_is_detected_and_resolved_by_human_override(self):
        editorial, protection = _artifacts()
        initial = build_editorial_memory(editorial, protection)
        overrides = {
            "schema_version": "0.1.0",
            "term_overrides": [{
                "source_term": "ADONAI", "context_key": "role:proper_name",
                "preserve": False, "preferred_translation": "Adonai traduzido",
                "reason": "explicit_editorial_exception",
            }],
        }
        updated = build_editorial_memory(
            editorial, protection, existing_memory=initial, overrides=overrides
        )
        self.assertEqual(updated["memory_version"], 2)
        self.assertTrue(any(item["status"] == "resolved_by_human" for item in updated["conflicts"]))

    def test_existing_human_decision_defeats_later_automatic_candidate(self):
        editorial, protection = _artifacts()
        overrides = {
            "schema_version": "0.1.0",
            "term_overrides": [{
                "source_term": "ADONAI", "context_key": "role:proper_name",
                "preserve": False, "preferred_translation": "Adonai traduzido",
                "reason": "explicit_editorial_exception",
            }],
        }
        initial = build_editorial_memory(editorial, protection, overrides=overrides)
        updated = build_editorial_memory(
            editorial, protection, existing_memory=initial
        )
        entry = next(
            item for item in _terms(updated, "adonai")
            if item["context_key"] == "role:proper_name"
        )
        self.assertFalse(entry["preserve"])
        self.assertEqual(entry["decision_source"], "human_override")
        self.assertTrue(any(
            item["type"] == "automatic_candidate_conflicts_with_human_decision"
            and item["status"] == "resolved_by_existing_human_authority"
            for item in updated["conflicts"]
        ))

    def test_duplicate_override_target_is_rejected(self):
        editorial, protection = _artifacts()
        overrides = {
            "schema_version": "0.1.0",
            "term_overrides": [
                {"source_term": "sigil", "preserve": True},
                {"source_term": "sigil", "preserve": False},
            ],
        }
        with self.assertRaisesRegex(ValueError, "Duplicate term override"):
            build_editorial_memory(editorial, protection, overrides=overrides)


class MergeVersionAndInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.editorial, self.protection = _artifacts()

    def test_identical_merge_reuses_memory_without_new_revision(self):
        initial = build_editorial_memory(self.editorial, self.protection)
        repeated = build_editorial_memory(
            self.editorial, self.protection, existing_memory=copy.deepcopy(initial)
        )
        self.assertEqual(repeated, initial)
        self.assertEqual(len(repeated["history"]), 1)

    def test_new_occurrence_merges_without_losing_previous_decisions(self):
        initial = build_editorial_memory(self.editorial, self.protection)
        changed = copy.deepcopy(self.editorial)
        unit = _unit("u-new", "Another sigil appears here.", 3, 9)
        changed["editorial_units"].append(unit)
        changed["segments"].append({
            "segment_id": "segment-u-new", "editorial_id": "u-new", "page": 3,
            "editorial_type": "paragraph", "source_ids": unit["source_ids"],
            "parts": copy.deepcopy(unit["parts"]), "review_required": False,
            "contains_protected_parts": False,
        })
        updated = build_editorial_memory(changed, self.protection, existing_memory=initial)
        self.assertEqual(updated["memory_version"], 2)
        sigil = next(item for item in _terms(updated, "sigil") if item["context_key"].startswith("lexical:"))
        self.assertEqual(sigil["frequency"], 13)
        self.assertTrue(set(initial["preserved_terms"]).issubset(updated["preserved_terms"]))

    def test_changed_decision_marks_previous_dependents_stale(self):
        initial = build_editorial_memory(self.editorial, self.protection)
        entry = next(item for item in _terms(initial, "adonai") if item["context_key"] == "role:proper_name")
        self.assertTrue(any(item["target_id"] == entry["term_id"] for item in initial["dependencies"]))
        overrides = {
            "schema_version": "0.1.0",
            "term_overrides": [{
                "term_id": entry["term_id"], "preserve": False,
                "preferred_translation": "Adonai traduzido",
            }],
        }
        updated = build_editorial_memory(
            self.editorial, self.protection, existing_memory=initial, overrides=overrides
        )
        self.assertTrue(any(item["target_id"] == entry["term_id"] for item in updated["stale_dependents"]))
        self.assertGreater(updated["summary"]["stale_dependency_count"], 0)

    def test_identical_human_decision_does_not_create_another_change(self):
        overrides = {
            "schema_version": "0.1.0",
            "term_overrides": [{"source_term": "ADONAI", "context_key": "role:proper_name", "preserve": True}],
        }
        initial = build_editorial_memory(self.editorial, self.protection, overrides=overrides)
        repeated = build_editorial_memory(
            self.editorial, self.protection, existing_memory=initial, overrides=overrides
        )
        self.assertEqual(repeated, initial)


class StyleGuideTests(unittest.TestCase):
    def test_style_guide_loads_and_human_override_wins(self):
        editorial, protection = _artifacts()
        style = {
            "schema_version": "0.1.0", "target_language": "pt-BR",
            "preferences": {"formality": "formal", "quotation_style": "curly"},
        }
        overrides = {
            "schema_version": "0.1.0",
            "style_overrides": {"formality": "informal"},
        }
        memory = build_editorial_memory(
            editorial, protection, style_guide=style, overrides=overrides
        )
        self.assertEqual(memory["style_guide"]["target_language"], "pt-BR")
        self.assertEqual(memory["style_guide"]["preferences"]["formality"]["value"], "informal")
        self.assertEqual(memory["style_guide"]["preferences"]["formality"]["source"], "human_override")
        self.assertEqual(memory["style_guide"]["preferences"]["quotation_style"]["value"], "curly")

    def test_target_language_is_configurable(self):
        editorial, protection = _artifacts()
        memory = build_editorial_memory(editorial, protection, target_language="es-ES")
        self.assertEqual(memory["style_guide"]["target_language"], "es-ES")
        self.assertEqual(memory["style_guide"]["preferences"]["language_variant"]["value"], "es-ES")


class MemoryDeterminismAndCliTests(unittest.TestCase):
    def test_determinism_hash_and_input_immutability(self):
        editorial, protection = _artifacts(include_mercury=True)
        original_editorial = copy.deepcopy(editorial)
        original_protection = copy.deepcopy(protection)
        first = build_editorial_memory(editorial, protection)
        second = build_editorial_memory(copy.deepcopy(editorial), copy.deepcopy(protection))
        self.assertEqual(first, second)
        self.assertEqual(_canonical_hash(first), _canonical_hash(second))
        for entry in first["glossary"]:
            self.assertEqual(
                entry["variants"],
                sorted(entry["variants"], key=lambda value: (value.casefold(), value)),
            )
        self.assertEqual(editorial, original_editorial)
        self.assertEqual(protection, original_protection)

    def test_atomic_serialization_and_cli(self):
        editorial, protection = _artifacts()
        memory = build_editorial_memory(editorial, protection)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nested = root / "nested" / "memory.json"
            write_editorial_memory(memory, nested)
            self.assertEqual(json.loads(nested.read_text(encoding="utf-8")), memory)
            editorial_path = root / "editorial.json"
            protection_path = root / "protection.json"
            output = root / "output.json"
            editorial_path.write_text(json.dumps(editorial), encoding="utf-8")
            protection_path.write_text(json.dumps(protection), encoding="utf-8")
            command = [
                sys.executable, "-m", "scripts.build_editorial_memory",
                str(editorial_path), str(protection_path), "--out", str(output),
            ]
            completed = subprocess.run(
                command, cwd=Path(__file__).parents[1], capture_output=True,
                text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(output.exists())

    def test_cli_rejects_input_overwrite_and_incompatible_schema(self):
        editorial, protection = _artifacts()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            editorial_path = root / "editorial.json"
            protection_path = root / "protection.json"
            editorial_path.write_text(json.dumps(editorial), encoding="utf-8")
            protection_path.write_text(json.dumps(protection), encoding="utf-8")
            overwrite = [
                sys.executable, "-m", "scripts.build_editorial_memory",
                str(editorial_path), str(protection_path), "--out", str(editorial_path),
            ]
            completed = subprocess.run(overwrite, cwd=Path(__file__).parents[1], capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("must not overwrite", completed.stderr)

            invalid = copy.deepcopy(editorial)
            invalid["schema_version"] = "9.9.9"
            editorial_path.write_text(json.dumps(invalid), encoding="utf-8")
            command = [
                sys.executable, "-m", "scripts.build_editorial_memory",
                str(editorial_path), str(protection_path), "--out", str(root / "output.json"),
            ]
            completed = subprocess.run(command, cwd=Path(__file__).parents[1], capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("Unsupported editorial schema", completed.stderr)


if __name__ == "__main__":
    unittest.main()
