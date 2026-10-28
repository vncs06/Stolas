"""Tests for deterministic contextual batching and selective memory."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stolas.batching import (
    build_translation_batches,
    estimate_tokens,
    write_translation_batches,
)


SOURCE_SHA = "b" * 64


def _hash(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _part(text, kind="translatable", decision_ids=None):
    return {
        "type": kind, "text": text, "source_id": f"source-{_hash(text)[:8]}",
        "decision_ids": decision_ids or [], "review_required": kind == "review_required",
    }


def _fixtures(specs=None):
    specs = specs or [
        ("h1", "Chapter One", "heading", 1, [_part("Chapter One")]),
        ("p1", "The sigil appears near Michael.", "paragraph", 1, [_part("The sigil appears near Michael.")]),
        ("p2", "Say ", "paragraph", 1, [
            _part("Say "), _part("ADONAI", "protected", ["decision-adonai"]),
            _part(" with care."),
        ]),
        ("l1", "First list item.", "list_item", 2, [_part("First list item.")]),
        ("l2", "Second list item.", "list_item", 2, [_part("Second list item.")]),
        ("h2", "Chapter Two", "heading", 3, [_part("Chapter Two")]),
        ("p3", "Mercury closes the example.", "paragraph", 3, [_part("Mercury closes the example.")]),
    ]
    classifications = [{
        "id": "decision-adonai", "source_id": "source-adonai", "page": 1,
        "element_type": "text_fragment", "classification": "protected",
        "reason": "contextual_literal", "bbox": [0, 0, 10, 10],
        "decision_source": "heuristic", "review_required": False,
        "override_allowed": True, "linguistic_role": "proper_name",
        "confidence": 0.99, "evidence": ["synthetic"],
    }]
    protection = {
        "schema_version": "0.2.0", "ruleset_version": "0.2.0",
        "source_manifest": {"schema_version": "0.2.0", "sha256": "c" * 64},
        "source_document": {"name": "batching.pdf", "sha256": SOURCE_SHA, "page_count": 3},
        "term_memory": [], "classifications": classifications,
        "protected_regions": [], "summary": {},
    }
    units = []
    segments = []
    for order, (identifier, text, editorial_type, page, parts) in enumerate(specs):
        editorial_id = f"editorial-{identifier}"
        units.append({
            "editorial_id": editorial_id, "page": page, "reading_order": order,
            "editorial_type": editorial_type, "text": text, "parts": copy.deepcopy(parts),
        })
        segments.append({
            "segment_id": f"segment-{identifier}", "editorial_id": editorial_id,
            "page": page, "editorial_type": editorial_type,
            "source_ids": [f"source-{identifier}"], "parts": copy.deepcopy(parts),
            "review_required": False,
            "contains_protected_parts": any(item["type"] == "protected" for item in parts),
        })
    editorial = {
        "schema_version": "0.1.0", "algorithm_version": "0.1.0",
        "source_manifest": {"schema_version": "0.2.0", "sha256": "c" * 64},
        "source_protection": {
            "schema_version": "0.2.0", "ruleset_version": "0.2.0",
            "sha256": _hash(protection),
        },
        "source_document": {"name": "batching.pdf", "sha256": SOURCE_SHA, "page_count": 3},
        "pages": [], "editorial_units": units, "segments": segments,
        "graphic_groups": [], "warnings": [], "statistics": {},
    }
    glossary = [
        {
            "term_id": "term-sigil", "source_term": "sigil", "normalized_term": "sigil",
            "variants": ["sigil"], "preferred_translation": "sigilo", "preserve": False,
            "category": "technical_term", "context_key": "lexical:technical_term",
            "decision_id": "memory-decision-sigil", "decision_source": "human_override",
            "status": "approved", "occurrences": [{"segment_id": "segment-p1"}],
        },
        {
            "term_id": "term-adonai", "source_term": "ADONAI", "normalized_term": "adonai",
            "variants": ["ADONAI"], "preferred_translation": None, "preserve": True,
            "category": "proper_name", "context_key": "role:proper_name",
            "decision_id": "decision-adonai", "decision_source": "automatic_candidate",
            "status": "approved", "occurrences": [{"segment_id": "segment-p2"}],
        },
        {
            "term_id": "term-mercury", "source_term": "Mercury", "normalized_term": "mercury",
            "variants": ["Mercury"], "preferred_translation": None, "preserve": None,
            "category": "proper_name", "context_key": "lexical:proper_name",
            "decision_id": None, "decision_source": "automatic_candidate",
            "status": "candidate", "occurrences": [{"segment_id": "segment-p3"}],
        },
        {
            "term_id": "term-irrelevant", "source_term": "unrelated", "normalized_term": "unrelated",
            "variants": ["unrelated"], "preferred_translation": None, "preserve": None,
            "category": "other", "context_key": "lexical:other", "decision_id": None,
            "decision_source": "automatic_candidate", "status": "candidate", "occurrences": [],
        },
    ]
    dependencies = [
        {
            "dependency_id": "dep-sigil", "target_id": "term-sigil",
            "decision_id": "memory-decision-sigil", "dependent_type": "segment",
            "dependent_id": "segment-p1", "status": "current",
        },
        {
            "dependency_id": "dep-adonai", "target_id": "term-adonai",
            "decision_id": "decision-adonai", "dependent_type": "segment",
            "dependent_id": "segment-p2", "status": "current",
        },
    ]
    memory = {
        "schema_version": "0.1.0", "memory_version": 1, "ruleset_version": "0.1.0",
        "source_document": copy.deepcopy(editorial["source_document"]),
        "source_inputs": {
            "editorial_schema_version": "0.1.0", "editorial_algorithm_version": "0.1.0",
            "editorial_sha256": _hash(editorial), "protection_schema_version": "0.2.0",
            "protection_ruleset_version": "0.2.0", "protection_sha256": _hash(protection),
        },
        "style_guide": {
            "schema_version": "0.1.0", "target_language": "pt-BR",
            "preferences": {"formality": {"value": "formal", "source": "human_style_guide"}},
        },
        "glossary": glossary, "preserved_terms": ["term-adonai"],
        "decisions": [], "translation_decisions": [], "questions": [], "exceptions": [],
        "cross_references": [], "conflicts": [{
            "conflict_id": "conflict-sigil", "target_id": "term-sigil",
            "status": "resolved_by_human",
        }],
        "dependencies": dependencies, "stale_dependents": [], "history": [], "summary": {},
    }
    return editorial, memory, protection


def _build(editorial=None, memory=None, protection=None, **kwargs):
    if editorial is None:
        editorial, memory, protection = _fixtures()
    defaults = {
        "max_input_tokens": 700, "reserved_output_tokens": 100,
        "reserved_system_tokens": 80, "context_budget_tokens": 120,
        "memory_budget_tokens": 140,
    }
    defaults.update(kwargs)
    return build_translation_batches(editorial, memory, protection, **defaults)


class CoverageAndDivisionTests(unittest.TestCase):
    def test_every_segment_is_payload_exactly_once_in_order(self):
        editorial, memory, protection = _fixtures()
        artifact = _build(editorial, memory, protection)
        expected = [item["segment_id"] for item in editorial["segments"]]
        actual = [identifier for batch in artifact["batches"] for identifier in batch["payload_segment_ids"]]
        self.assertEqual(actual, expected)
        self.assertTrue(artifact["coverage"]["complete"])

    def test_protected_only_segment_is_rejected(self):
        editorial, memory, protection = _fixtures()
        editorial["segments"][0]["parts"] = [_part("ADONAI", "protected", ["decision-adonai"])]
        memory["source_inputs"]["editorial_sha256"] = _hash(editorial)
        with self.assertRaisesRegex(ValueError, "Protected-only"):
            _build(editorial, memory, protection)

    def test_small_chapters_and_headings_create_safe_boundaries(self):
        artifact = _build()
        batches = artifact["batches"]
        self.assertEqual(batches[0]["payload_segment_ids"][0], "segment-h1")
        self.assertEqual(batches[-1]["payload_segment_ids"][0], "segment-h2")
        self.assertIn("segment-p1", batches[0]["payload_segment_ids"])

    def test_heading_and_first_explanation_are_an_atomic_semantic_group(self):
        specs = [
            ("h", "A Name", "subheading", 1, [_part("A Name")]),
            ("p", "An explanation that is intentionally much longer. " * 6,
             "paragraph", 1, [_part("An explanation that is intentionally much longer. " * 6)]),
            ("tail", "Tail.", "paragraph", 1, [_part("Tail.")]),
        ]
        editorial, memory, protection = _fixtures(specs)
        artifact = _build(editorial, memory, protection, max_input_tokens=500)
        first = artifact["batches"][0]["payload_segment_ids"]
        self.assertEqual(first[:2], ["segment-h", "segment-p"])

    def test_large_chapter_splits_only_between_segments(self):
        specs = [("h", "Large Chapter", "heading", 1, [_part("Large Chapter")])]
        specs.extend((f"p{i}", "word " * 100, "paragraph", 1 + i // 3, [_part("word " * 100)]) for i in range(6))
        editorial, memory, protection = _fixtures(specs)
        artifact = _build(editorial, memory, protection, max_input_tokens=500)
        self.assertGreater(len(artifact["batches"]), 1)
        self.assertTrue(all(batch["payload"] for batch in artifact["batches"]))
        self.assertEqual(artifact["coverage"]["actual_payload_segment_count"], 7)

    def test_list_items_and_page_transition_are_not_split_internally(self):
        artifact = _build()
        payload = [item for batch in artifact["batches"] for item in batch["payload"]]
        lists = [item for item in payload if item["editorial_type"] == "list_item"]
        self.assertEqual([item["payload_text"] for item in lists], ["First list item.", "Second list item."])
        self.assertTrue(all(len(item["parts"]) == 1 for item in lists))

    def test_protected_part_remains_structured_inside_payload(self):
        artifact = _build()
        batch = next(item for item in artifact["batches"] if "segment-p2" in item["payload_segment_ids"])
        segment = next(item for item in batch["payload"] if item["segment_id"] == "segment-p2")
        self.assertEqual([part["type"] for part in segment["parts"]], ["translatable", "protected", "translatable"])
        self.assertIn("decision-adonai", batch["protected_decision_ids"])
        self.assertNotIn("ADONAI", segment["payload_text"])


class ContextMemoryAndTokenTests(unittest.TestCase):
    def test_neighbor_context_is_separate_and_never_duplicate_payload(self):
        artifact = _build(max_input_tokens=520, context_budget_tokens=160)
        self.assertGreater(len(artifact["batches"]), 2)
        for batch in artifact["batches"]:
            self.assertFalse(set(batch["payload_segment_ids"]) & set(batch["context_before_ids"]))
            self.assertFalse(set(batch["payload_segment_ids"]) & set(batch["context_after_ids"]))
        middle = artifact["batches"][1]
        self.assertTrue(middle["context_before_ids"] or middle["context_after_ids"])

    def test_memory_is_selective_auditable_and_includes_override(self):
        artifact = _build()
        sigil_batch = next(item for item in artifact["batches"] if "segment-p1" in item["payload_segment_ids"])
        self.assertIn("term-sigil", sigil_batch["memory_entry_ids"])
        self.assertNotIn("term-irrelevant", sigil_batch["memory_entry_ids"])
        entry = next(item for item in sigil_batch["memory_entries"] if item["term_id"] == "term-sigil")
        self.assertIn("occurrence_in_payload", entry["inclusion_reasons"])
        self.assertIn("dep-sigil", sigil_batch["dependencies"]["memory_dependency_ids"])
        self.assertIn("conflict-sigil", sigil_batch["relevant_conflict_ids"])

    def test_protected_term_and_style_are_always_explicit(self):
        artifact = _build()
        batch = next(item for item in artifact["batches"] if "segment-p2" in item["payload_segment_ids"])
        self.assertIn("term-adonai", batch["preserved_term_ids"])
        self.assertEqual(batch["style_guide"]["target_language"], "pt-BR")

    def test_normal_batches_respect_budget_and_reservations(self):
        artifact = _build()
        for batch in artifact["batches"]:
            estimate = batch["estimated_tokens"]
            self.assertLessEqual(estimate["total_input_with_system_reservation"], artifact["config"]["usable_input_tokens"])
            self.assertEqual(estimate["reserved_output"], 100)

    def test_indivisible_oversized_segment_is_kept_and_warned(self):
        specs = [("huge", "word " * 1000, "paragraph", 1, [_part("word " * 1000)])]
        editorial, memory, protection = _fixtures(specs)
        artifact = _build(editorial, memory, protection, max_input_tokens=500)
        self.assertEqual(len(artifact["batches"]), 1)
        self.assertIn("indivisible_segment_exceeds_payload_budget", artifact["batches"][0]["warnings"])
        self.assertIn("estimated_input_exceeds_usable_budget", artifact["batches"][0]["warnings"])

    def test_estimator_is_local_and_deterministic(self):
        self.assertEqual(estimate_tokens("same text"), estimate_tokens("same text"))
        self.assertGreater(estimate_tokens("á" * 20), 0)


class DeterminismCheckpointAndCliTests(unittest.TestCase):
    def test_three_builds_have_same_hash_and_do_not_mutate_inputs(self):
        editorial, memory, protection = _fixtures()
        originals = copy.deepcopy((editorial, memory, protection))
        outputs = [_build(editorial, memory, protection) for _ in range(3)]
        self.assertEqual(_hash(outputs[0]), _hash(outputs[1]))
        self.assertEqual(_hash(outputs[1]), _hash(outputs[2]))
        self.assertEqual((editorial, memory, protection), originals)

    def test_only_batch_using_changed_memory_becomes_stale(self):
        editorial, memory, protection = _fixtures()
        initial = _build(editorial, memory, protection, max_input_tokens=520)
        changed = copy.deepcopy(memory)
        changed_entry = next(item for item in changed["glossary"] if item["term_id"] == "term-sigil")
        changed_entry["preferred_translation"] = "selo mágico"
        updated = _build(
            editorial, changed, protection, max_input_tokens=520,
            existing_batches=initial,
        )
        stale_payloads = [
            tuple(batch["payload_segment_ids"])
            for batch, checkpoint in zip(updated["batches"], updated["checkpoints"])
            if checkpoint["status"] == "stale"
        ]
        self.assertTrue(any("segment-p1" in payload for payload in stale_payloads))
        self.assertTrue(any(
            "segment-p3" in batch["payload_segment_ids"] and checkpoint["status"] == "pending"
            for batch, checkpoint in zip(updated["batches"], updated["checkpoints"])
        ))

    def test_configuration_change_marks_overlapping_batches_stale(self):
        editorial, memory, protection = _fixtures()
        initial = _build(editorial, memory, protection)
        updated = _build(
            editorial, memory, protection, existing_batches=initial,
            max_input_tokens=560,
        )
        self.assertTrue(updated["checkpoints"])
        self.assertTrue(all(item["status"] == "stale" for item in updated["checkpoints"]))
        self.assertTrue(all(
            "batching_configuration_changed" in item["stale_reasons"]
            for item in updated["checkpoints"]
        ))

    def test_handoff_and_batch_links_form_a_chain(self):
        artifact = _build(max_input_tokens=520)
        for index, batch in enumerate(artifact["batches"]):
            self.assertEqual(batch["handoff"]["status"], "pending")
            if index:
                self.assertEqual(batch["previous_batch_id"], artifact["batches"][index - 1]["batch_id"])
            if index + 1 < len(artifact["batches"]):
                self.assertEqual(batch["next_batch_id"], artifact["batches"][index + 1]["batch_id"])

    def test_completed_checkpoint_and_handoff_resume_without_duplication(self):
        editorial, memory, protection = _fixtures()
        initial = _build(editorial, memory, protection, max_input_tokens=520)
        initial["checkpoints"][0]["status"] = "completed"
        initial["checkpoints"][0]["completed"] = True
        initial["batches"][0]["handoff"]["status"] = "completed"
        initial["batches"][0]["handoff"]["summary"] = "Synthetic handoff"
        resumed = _build(
            editorial, memory, protection, max_input_tokens=520,
            existing_batches=initial,
        )
        self.assertTrue(resumed["checkpoints"][0]["completed"])
        self.assertEqual(resumed["batches"][0]["handoff"]["summary"], "Synthetic handoff")
        self.assertNotEqual(
            resumed["checkpoint_state"]["next_pending_batch_id"],
            resumed["batches"][0]["batch_id"],
        )

    def test_atomic_serialization_and_cli(self):
        editorial, memory, protection = _fixtures()
        artifact = _build(editorial, memory, protection)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "nested" / "batches.json"
            write_translation_batches(artifact, output)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), artifact)
            paths = []
            for name, value in (("editorial", editorial), ("memory", memory), ("protection", protection)):
                path = root / f"{name}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                paths.append(path)
            first_cli = root / "cli-seed-1.json"
            command = [
                sys.executable, "-m", "scripts.build_translation_batches",
                *(str(path) for path in paths), "--out", str(first_cli),
                "--max-input-tokens", "700", "--reserved-output-tokens", "100",
                "--reserved-system-tokens", "80", "--context-budget", "120",
                "--memory-budget", "140",
            ]
            environment = os.environ.copy()
            environment["PYTHONHASHSEED"] = "1"
            completed = subprocess.run(
                command, cwd=Path(__file__).parents[1], capture_output=True,
                text=True, check=False, env=environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(first_cli.exists())
            second_cli = root / "cli-seed-2.json"
            second_command = list(command)
            second_command[second_command.index(str(first_cli))] = str(second_cli)
            environment["PYTHONHASHSEED"] = "2"
            completed = subprocess.run(
                second_command, cwd=Path(__file__).parents[1], capture_output=True,
                text=True, check=False, env=environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(first_cli.read_bytes(), second_cli.read_bytes())

    def test_cli_rejects_overwrite_and_bad_schema(self):
        editorial, memory, protection = _fixtures()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            for name, value in (("editorial", editorial), ("memory", memory), ("protection", protection)):
                path = root / f"{name}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                paths.append(path)
            overwrite = [
                sys.executable, "-m", "scripts.build_translation_batches",
                *(str(path) for path in paths), "--out", str(paths[0]),
            ]
            completed = subprocess.run(overwrite, cwd=Path(__file__).parents[1], capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("must not overwrite", completed.stderr)
            editorial["schema_version"] = "9.9.9"
            paths[0].write_text(json.dumps(editorial), encoding="utf-8")
            command = [
                sys.executable, "-m", "scripts.build_translation_batches",
                *(str(path) for path in paths), "--out", str(root / "bad.json"),
            ]
            completed = subprocess.run(command, cwd=Path(__file__).parents[1], capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("Unsupported editorial schema", completed.stderr)


if __name__ == "__main__":
    unittest.main()
