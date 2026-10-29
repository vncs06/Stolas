"""Provider interfaces and deterministic local providers for translation tests."""
from __future__ import annotations

from abc import ABC, abstractmethod
import copy
import re


class ProviderError(RuntimeError):
    """Base error raised by a translation provider."""


class RetryableProviderError(ProviderError):
    """A provider failure that may succeed on a bounded retry."""


class ProviderTimeoutError(RetryableProviderError):
    """A provider timeout suitable for a bounded retry."""


class PermanentProviderError(ProviderError):
    """A provider failure that must not be retried."""


class TranslationProvider(ABC):
    """Provider-neutral structured translation interface."""

    name = "abstract"

    @abstractmethod
    def translate_batch(self, request: dict) -> dict:
        """Translate one structured request and return a structured response."""


def _replace_variant(text: str, variants: list[str], replacement: str) -> tuple[str, bool]:
    changed = False
    for variant in sorted(set(variants), key=lambda value: (-len(value), value.casefold(), value)):
        if not variant:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(variant)}(?!\w)", re.IGNORECASE)
        text, count = pattern.subn(replacement, text)
        changed = changed or bool(count)
    return text, changed


class FakeTranslationProvider(TranslationProvider):
    """Deterministic provider that exercises the pipeline without network or translation."""

    name = "fake"
    SCENARIOS = frozenset({
        "valid", "omit_segment", "duplicate_segment", "unknown_segment",
        "missing_placeholder", "duplicate_placeholder", "alter_protected",
        "malformed", "temporary_error", "permanent_error", "timeout",
        "context_leak", "proposal",
    })

    def __init__(self, scenario: str = "valid", *, failures_before_success: int = 0):
        if scenario not in self.SCENARIOS:
            raise ValueError(f"Unsupported fake provider scenario {scenario!r}")
        if not isinstance(failures_before_success, int) or failures_before_success < 0:
            raise ValueError("failures_before_success must be a non-negative integer")
        self.scenario = scenario
        self.failures_before_success = failures_before_success
        self.call_count = 0
        self.requests: list[dict] = []

    def _maybe_fail(self) -> None:
        if self.scenario == "permanent_error":
            raise PermanentProviderError("simulated permanent provider error")
        if self.scenario in {"temporary_error", "timeout"} and (
            self.call_count <= max(1, self.failures_before_success)
        ):
            if self.scenario == "timeout":
                raise ProviderTimeoutError("simulated provider timeout")
            raise RetryableProviderError("simulated temporary provider error")

    def translate_batch(self, request: dict) -> dict:
        self.call_count += 1
        self.requests.append(copy.deepcopy(request))
        self._maybe_fail()
        if self.scenario == "malformed":
            return {"unexpected": "response"}

        results = []
        for segment in request["payload"]:
            translated = segment["source_text_with_placeholders"]
            applied = []
            for rule in request["glossary_rules"]:
                if rule["authority"] != "mandatory_translation":
                    continue
                translated, changed = _replace_variant(
                    translated, rule["variants"], rule["preferred_translation"]
                )
                if changed:
                    applied.append(rule["term_id"])
            translated = f"⟦FAKE:{request['target_language']}⟧ {translated}"
            results.append({
                "segment_id": segment["segment_id"],
                "translated_text": translated,
                "confidence": 1.0,
                "warnings": [],
                "applied_term_ids": sorted(applied),
                "proposals": [],
                "register": request.get("style_guide", {}).get("preferences", {})
                    .get("formality", {}).get("value"),
                "continuity_flags": [],
            })

        if self.scenario == "omit_segment" and results:
            results.pop()
        elif self.scenario == "duplicate_segment" and results:
            results.append(copy.deepcopy(results[0]))
        elif self.scenario == "unknown_segment":
            results.append({
                "segment_id": "unknown-segment", "translated_text": "unexpected",
                "confidence": 1.0, "warnings": [], "applied_term_ids": [],
                "proposals": [], "register": None, "continuity_flags": [],
            })
        elif self.scenario in {
            "missing_placeholder", "duplicate_placeholder", "alter_protected"
        }:
            binding = next(
                (item for segment in request["payload"]
                 for item in segment["protected_bindings"]), None
            )
            if binding and results:
                target = next(
                    item for item in results
                    if item["segment_id"] == binding["segment_id"]
                )
                if self.scenario == "missing_placeholder":
                    target["translated_text"] = target["translated_text"].replace(
                        binding["placeholder"], ""
                    )
                elif self.scenario == "duplicate_placeholder":
                    target["translated_text"] += f" {binding['placeholder']}"
                else:
                    target["translated_text"] = target["translated_text"].replace(
                        binding["placeholder"], binding["original_text"].lower()
                    )
        elif self.scenario == "context_leak" and results:
            context = request.get("context_before") or request.get("context_after")
            if context:
                results[0]["translated_text"] += f" {context[0]['text']}"
        elif self.scenario == "proposal" and results:
            results[0]["proposals"].append({
                "proposal_type": "glossary_entry",
                "source_term": "new synthetic term",
                "preferred_translation": "novo termo sintético",
                "preserve": False,
                "reason": "fake_provider_test",
            })

        last_id = results[-1]["segment_id"] if results else None
        return {
            "schema_version": "0.1.0",
            "batch_id": request["batch_id"],
            "segments": results,
            "handoff": {
                "summary": f"Processed {len(results)} segment(s); last={last_id}",
                "new_terms": [],
                "decisions_proposed": [],
                "important_referents": [],
                "ambiguities": [],
                "pending_references": [],
                "continuity_notes": [],
            },
        }


def create_provider(name: str, **options) -> TranslationProvider:
    """Create a configured provider without importing optional SDKs in the core."""
    if name == "fake":
        return FakeTranslationProvider(**options)
    raise ValueError(
        f"Unknown provider {name!r}; install and register an external TranslationProvider "
        "implementation or use 'fake'"
    )
