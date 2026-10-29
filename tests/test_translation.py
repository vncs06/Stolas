"""Tests for provider-neutral contextual translation, QA and resume."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stolas.translation import (
    FakeTranslationProvider,
    TranslationProvider,
    build_translation_request,
    reconcile_translation,
    translate_batches,
    write_translation_results,
)
from stolas.translation.qa import textual_qa
from tests.test_batching import _build as _build_batches
from tests.test_batching import _fixtures


def _hash(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _inputs(**batch_options):
    editorial, memory, protection = _fixtures()
    batches = _build_batches(editorial, memory, protection, **batch_options)
    return batches, memory, protection


def _translate(provider=None, **kwargs):
    batches, memory, protection = _inputs()
    provider = provider or FakeTranslationProvider()
    return translate_batches(
        batches, memory, protection, provider=provider, **kwargs
    )


class ProviderInterfaceAndRequestTests(unittest.TestCase):
    def test_provider_interface_is_abstract_and_fake_is_structured(self):
        with self.assertRaises(TypeError):
            TranslationProvider()
        self.assertIsInstance(FakeTranslationProvider(), TranslationProvider)

    def test_request_contains_context_style_memory_and_output_contract(self):
        batches, _, _ = _inputs(max_input_tokens=520)
        batch = batches["batches"][1]
        request = build_translation_request(
            batch, source_sha=batches["document"]["sha256"],
            source_language="en", target_language="pt-BR",
            previous_handoff={"summary": "prior"},
        )
        self.assertEqual(request["batch_id"], batch["batch_id"])
        self.assertTrue(request["context_before"] or request["context_after"])
        self.assertEqual(request["style_guide"]["target_language"], "pt-BR")
        self.assertTrue(request["glossary_rules"])
        self.assertEqual(request["previous_handoff"]["summary"], "prior")
        self.assertTrue(request["output_contract"]["context_is_not_output"])

    def test_glossary_authorities_distinguish_approved_preserved_and_suggested(self):
        batches, _, _ = _inputs()
        rules = {
            rule["term_id"]: rule
            for batch in batches["batches"]
            for rule in build_translation_request(
                batch, source_sha=batches["document"]["sha256"],
                source_language="en", target_language="pt-BR", previous_handoff=None,
            )["glossary_rules"]
        }
        self.assertEqual(rules["term-sigil"]["authority"], "mandatory_translation")
        self.assertEqual(rules["term-adonai"]["authority"], "mandatory_preservation")
        self.assertEqual(rules["term-mercury"]["authority"], "suggested")


class CoverageProtectionAndGlossaryTests(unittest.TestCase):
    def test_valid_provider_covers_all_payload_segments_exactly_once(self):
        results = _translate()
        self.assertTrue(results["coverage"]["complete"])
        self.assertEqual(results["coverage"]["expected_segment_count"], 7)
        self.assertEqual(len(results["segment_results"]), 7)
        self.assertTrue(all(item["status"] == "approved" for item in results["batch_results"]))

    def test_omission_duplicate_and_unknown_id_block_batch(self):
        for scenario in ("omit_segment", "duplicate_segment", "unknown_segment"):
            with self.subTest(scenario=scenario):
                batches, memory, protection = _inputs()
                provider = FakeTranslationProvider(scenario)
                results = translate_batches(
                    batches, memory, protection, provider=provider,
                    batch_ids=[batches["batches"][0]["batch_id"]],
                )
                self.assertEqual(results["batch_results"][0]["status"], "blocked")
                self.assertFalse(results["coverage"]["complete"])

    def test_structural_protected_name_is_restored_exactly(self):
        results = _translate()
        result = results["segment_results"]["segment-p2"]
        self.assertIn("ADONAI", result["translated_text"])
        self.assertNotIn("[[STOLAS_PROTECTED_", result["translated_text"])
        self.assertTrue(result["protected_placeholders"])
        self.assertTrue(all(item["restored"] for item in result["protected_placeholders"]))

    def test_formula_and_transliteration_are_restored_byte_exactly(self):
        editorial, memory, protection = _fixtures()
        for identifier, role in (("decision-formula", "ritual_formula"),
                                 ("decision-transliteration", "phonetic_formula")):
            protection["classifications"].append({
                "id": identifier, "source_id": f"source-{identifier}", "page": 1,
                "element_type": "text_fragment", "classification": "protected",
                "reason": "contextual_literal", "bbox": [0, 0, 10, 10],
                "decision_source": "heuristic", "review_required": False,
                "override_allowed": True, "linguistic_role": role,
                "confidence": 0.99, "evidence": ["synthetic"],
            })
        segment = next(item for item in editorial["segments"] if item["segment_id"] == "segment-p2")
        segment["parts"].extend([
            {"type": "protected", "text": " DEUS MEUMQUE JUS",
             "source_id": "source-formula", "decision_ids": ["decision-formula"],
             "review_required": False},
            {"type": "protected", "text": " AH-RAH-MAH",
             "source_id": "source-transliteration",
             "decision_ids": ["decision-transliteration"], "review_required": False},
        ])
        editorial["source_protection"]["sha256"] = _hash(protection)
        memory["source_inputs"]["protection_sha256"] = _hash(protection)
        memory["source_inputs"]["editorial_sha256"] = _hash(editorial)
        batches = _build_batches(editorial, memory, protection)
        results = translate_batches(
            batches, memory, protection, provider=FakeTranslationProvider()
        )
        text = results["segment_results"]["segment-p2"]["translated_text"]
        self.assertIn("DEUS MEUMQUE JUS", text)
        self.assertIn("AH-RAH-MAH", text)
        self.assertNotIn("[[STOLAS_PROTECTED_", text)

    def test_approved_override_is_applied_but_suggestion_is_not_mandatory(self):
        results = _translate()
        self.assertIn("sigilo", results["segment_results"]["segment-p1"]["translated_text"])
        self.assertNotIn(" sigil ", results["segment_results"]["segment-p1"]["translated_text"])
        self.assertIn("Mercury", results["segment_results"]["segment-p3"]["translated_text"])

    def test_memory_preserved_term_inside_translatable_part_uses_placeholder(self):
        editorial, memory, protection = _fixtures()
        memory["glossary"].append({
            "term_id": "term-michael", "source_term": "Michael", "normalized_term": "michael",
            "variants": ["Michael"], "preferred_translation": None, "preserve": True,
            "category": "proper_name", "context_key": "role:proper_name",
            "decision_id": "decision-michael", "decision_source": "human_override",
            "status": "approved", "occurrences": [{"segment_id": "segment-p1"}],
        })
        batches = _build_batches(editorial, memory, protection)
        results = translate_batches(
            batches, memory, protection, provider=FakeTranslationProvider()
        )
        result = results["segment_results"]["segment-p1"]
        self.assertIn("Michael", result["translated_text"])
        self.assertTrue(any(
            item["term_id"] == "term-michael" for item in result["protected_placeholders"]
        ))

    def test_missing_duplicate_or_altered_placeholder_blocks(self):
        for scenario in ("missing_placeholder", "duplicate_placeholder", "alter_protected"):
            with self.subTest(scenario=scenario):
                batches, memory, protection = _inputs()
                protected_batch = next(
                    item for item in batches["batches"] if "segment-p2" in item["payload_segment_ids"]
                )
                results = translate_batches(
                    batches, memory, protection, provider=FakeTranslationProvider(scenario),
                    batch_ids=[protected_batch["batch_id"]],
                )
                record = next(
                    item for item in results["batch_results"]
                    if item["batch_id"] == protected_batch["batch_id"]
                )
                self.assertEqual(record["status"], "blocked")
                self.assertIn("Placeholder", record["error"]["message"])


class ContextHandoffAndProposalTests(unittest.TestCase):
    def test_context_reaches_provider_but_never_becomes_segment_result(self):
        batches, memory, protection = _inputs(max_input_tokens=520)
        provider = FakeTranslationProvider()
        results = translate_batches(batches, memory, protection, provider=provider)
        self.assertTrue(any(
            request["context_before"] or request["context_after"] for request in provider.requests
        ))
        context_ids = {
            identifier for batch in batches["batches"]
            for identifier in [*batch["context_before_ids"], *batch["context_after_ids"]]
        }
        payload_ids = {identifier for batch in batches["batches"] for identifier in batch["payload_segment_ids"]}
        self.assertEqual(set(results["segment_results"]), payload_ids)
        self.assertNotIn("unknown-context-only", results["segment_results"])
        self.assertTrue(context_ids)

    def test_context_leak_is_blocked_by_textual_qa(self):
        batches, memory, protection = _inputs(max_input_tokens=520)
        target = next(
            item for item in batches["batches"]
            if item["context_before"] or item["context_after"]
        )
        results = translate_batches(
            batches, memory, protection, provider=FakeTranslationProvider("context_leak"),
            batch_ids=[target["batch_id"]],
        )
        record = next(item for item in results["batch_results"] if item["batch_id"] == target["batch_id"])
        self.assertEqual(record["status"], "blocked")
        self.assertIn("context_copied_into_translation", record["error"]["message"])

    def test_handoff_is_compact_and_passed_to_next_batch(self):
        batches, memory, protection = _inputs(max_input_tokens=520)
        provider = FakeTranslationProvider()
        results = translate_batches(batches, memory, protection, provider=provider)
        self.assertEqual(len(results["handoffs"]), len(batches["batches"]))
        self.assertIsNone(provider.requests[0]["previous_handoff"])
        self.assertIsNotNone(provider.requests[1]["previous_handoff"])
        self.assertLessEqual(len(provider.requests[1]["previous_handoff"]["summary"]), 500)

    def test_provider_proposal_is_queued_and_does_not_mutate_memory(self):
        batches, memory, protection = _inputs()
        original = copy.deepcopy(memory)
        results = translate_batches(
            batches, memory, protection, provider=FakeTranslationProvider("proposal"),
            batch_ids=[batches["batches"][0]["batch_id"]],
        )
        self.assertTrue(results["proposals"])
        self.assertTrue(all(item["status"] == "pending_review" for item in results["proposals"]))
        self.assertEqual(memory, original)


class RetryResumeAndInvalidationTests(unittest.TestCase):
    def test_temporary_error_and_timeout_retry_then_succeed(self):
        for scenario in ("temporary_error", "timeout"):
            with self.subTest(scenario=scenario):
                batches, memory, protection = _inputs()
                provider = FakeTranslationProvider(scenario, failures_before_success=1)
                results = translate_batches(
                    batches, memory, protection, provider=provider, max_retries=1,
                    batch_ids=[batches["batches"][0]["batch_id"]],
                )
                self.assertEqual(results["batch_results"][0]["status"], "approved")
                self.assertEqual(results["batch_results"][0]["attempts"], 2)

    def test_permanent_error_is_not_retried(self):
        batches, memory, protection = _inputs()
        results = translate_batches(
            batches, memory, protection, provider=FakeTranslationProvider("permanent_error"),
            max_retries=5, batch_ids=[batches["batches"][0]["batch_id"]],
        )
        self.assertEqual(results["batch_results"][0]["status"], "blocked")
        self.assertEqual(results["batch_results"][0]["attempts"], 1)
        self.assertFalse(results["batch_results"][0]["error"]["retryable"])

    def test_retry_limit_is_bounded(self):
        batches, memory, protection = _inputs()
        provider = FakeTranslationProvider("temporary_error", failures_before_success=10)
        results = translate_batches(
            batches, memory, protection, provider=provider, max_retries=2,
            batch_ids=[batches["batches"][0]["batch_id"]],
        )
        self.assertEqual(results["batch_results"][0]["status"], "blocked")
        self.assertEqual(results["batch_results"][0]["attempts"], 3)

    def test_malformed_response_is_non_retryable(self):
        batches, memory, protection = _inputs()
        provider = FakeTranslationProvider("malformed")
        results = translate_batches(
            batches, memory, protection, provider=provider, max_retries=4,
            batch_ids=[batches["batches"][0]["batch_id"]],
        )
        self.assertEqual(results["batch_results"][0]["status"], "blocked")
        self.assertEqual(provider.call_count, 1)

    def test_approved_results_resume_without_provider_calls(self):
        batches, memory, protection = _inputs()
        initial = translate_batches(
            batches, memory, protection, provider=FakeTranslationProvider()
        )
        provider = FakeTranslationProvider()
        resumed = translate_batches(
            batches, memory, protection, provider=provider, existing_results=initial
        )
        self.assertEqual(resumed, initial)
        self.assertEqual(provider.call_count, 0)

    def test_partial_run_resumes_without_reexecuting_approved_batch(self):
        batches, memory, protection = _inputs(max_input_tokens=520)
        first_id = batches["batches"][0]["batch_id"]
        partial = translate_batches(
            batches, memory, protection, provider=FakeTranslationProvider(),
            batch_ids=[first_id],
        )
        provider = FakeTranslationProvider()
        resumed = translate_batches(
            batches, memory, protection, provider=provider, existing_results=partial
        )
        self.assertTrue(resumed["coverage"]["complete"])
        self.assertEqual(provider.call_count, len(batches["batches"]) - 1)

    def test_changed_batch_dependency_is_reexecuted_while_others_are_reused(self):
        batches, memory, protection = _inputs(max_input_tokens=520)
        initial = translate_batches(
            batches, memory, protection, provider=FakeTranslationProvider()
        )
        changed = copy.deepcopy(batches)
        target = changed["batches"][0]
        target["dependency_fingerprint"] = "changed-fingerprint"
        target["batch_id"] = target["batch_id"] + "-changed"
        target["dependencies"]["batch_id"] = target["batch_id"]
        provider = FakeTranslationProvider()
        updated = translate_batches(
            changed, memory, protection, provider=provider, existing_results=initial
        )
        self.assertEqual(provider.call_count, 1)
        self.assertTrue(updated["coverage"]["complete"])


class QaReconciliationAndDeterminismTests(unittest.TestCase):
    def test_textual_qa_detects_numbers_urls_notes_duplication_and_truncation(self):
        source = "See 12 at https://example.com [1] for the complete reference."
        issues = textual_qa(source, "short", context_texts=[])
        types = {item["type"] for item in issues}
        self.assertTrue({"number_missing", "url_missing", "note_reference_missing", "possible_truncation"} <= types)
        duplicated = textual_qa(source, f"{source} {source}", context_texts=[])
        self.assertIn("source_text_duplicated", {item["type"] for item in duplicated})

    def test_reconciliation_detects_term_proposal_and_preserved_name_conflicts(self):
        _, memory, _ = _inputs()
        memory = copy.deepcopy(memory)
        sigil = next(item for item in memory["glossary"] if item["term_id"] == "term-sigil")
        sigil["occurrences"].append({"segment_id": "segment-p2"})
        segment_results = {
            "segment-p1": {"translated_text": "um sigilo", "register": "formal", "continuity_flags": []},
            "segment-p2": {"translated_text": "a sigil without the approved name", "register": "informal", "continuity_flags": ["anaphora_uncertain"]},
        }
        proposals = [
            {"source_term": "term", "preferred_translation": "termo A"},
            {"source_term": "term", "preferred_translation": "termo B"},
        ]
        issues = reconcile_translation(segment_results, memory, proposals, "b" * 64)
        types = {item["type"] for item in issues}
        self.assertIn("approved_term_inconsistent", types)
        self.assertIn("preserved_name_or_term_inconsistent", types)
        self.assertIn("terminology_proposal_conflict", types)
        self.assertIn("register_inconsistent", types)
        self.assertIn("continuity_flag", types)

    def test_valid_fake_run_has_no_reconciliation_issues(self):
        results = _translate()
        self.assertEqual(results["issues"], [])

    def test_three_runs_are_deterministic_and_inputs_immutable(self):
        batches, memory, protection = _inputs()
        originals = copy.deepcopy((batches, memory, protection))
        outputs = [
            translate_batches(
                batches, memory, protection, provider=FakeTranslationProvider()
            ) for _ in range(3)
        ]
        self.assertEqual(_hash(outputs[0]), _hash(outputs[1]))
        self.assertEqual(_hash(outputs[1]), _hash(outputs[2]))
        self.assertEqual((batches, memory, protection), originals)

    def test_atomic_serialization_cli_and_cross_process_determinism(self):
        batches, memory, protection = _inputs()
        results = translate_batches(
            batches, memory, protection, provider=FakeTranslationProvider()
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nested = root / "nested" / "results.json"
            write_translation_results(results, nested)
            self.assertEqual(json.loads(nested.read_text(encoding="utf-8")), results)
            paths = []
            for name, value in (("batches", batches), ("memory", memory), ("protection", protection)):
                path = root / f"{name}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                paths.append(path)
            first = root / "first.json"
            command = [
                sys.executable, "-m", "scripts.translate_batches",
                *(str(path) for path in paths), "--provider", "fake", "--out", str(first),
            ]
            environment = os.environ.copy()
            environment["PYTHONHASHSEED"] = "1"
            completed = subprocess.run(
                command, cwd=Path(__file__).parents[1], capture_output=True,
                text=True, check=False, env=environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            second = root / "second.json"
            second_command = list(command)
            second_command[second_command.index(str(first))] = str(second)
            environment["PYTHONHASHSEED"] = "2"
            completed = subprocess.run(
                second_command, cwd=Path(__file__).parents[1], capture_output=True,
                text=True, check=False, env=environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_cli_rejects_overwrite_resume_without_input_and_bad_schema(self):
        batches, memory, protection = _inputs()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            for name, value in (("batches", batches), ("memory", memory), ("protection", protection)):
                path = root / f"{name}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                paths.append(path)
            base = [sys.executable, "-m", "scripts.translate_batches", *(str(path) for path in paths)]
            completed = subprocess.run(
                [*base, "--out", str(paths[0])], cwd=Path(__file__).parents[1],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("must not overwrite", completed.stderr)
            completed = subprocess.run(
                [*base, "--resume", "--out", str(root / "resume.json")],
                cwd=Path(__file__).parents[1], capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("requires --existing-results", completed.stderr)
            batches["schema_version"] = "9.9.9"
            paths[0].write_text(json.dumps(batches), encoding="utf-8")
            completed = subprocess.run(
                [*base, "--out", str(root / "bad.json")], cwd=Path(__file__).parents[1],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("Unsupported batching schema", completed.stderr)


if __name__ == "__main__":
    unittest.main()
