"""Structured, provider-neutral contextual translation."""

from .engine import (
    TRANSLATION_SCHEMA_VERSION,
    TRANSLATION_VERSION,
    build_translation_request,
    load_json,
    translate_batches,
    write_translation_results,
)
from .providers import (
    FakeTranslationProvider,
    PermanentProviderError,
    ProviderError,
    ProviderTimeoutError,
    RetryableProviderError,
    TranslationProvider,
    create_provider,
)
from .qa import ResponseValidationError
from .reconciliation import reconcile_translation

__all__ = [
    "TRANSLATION_SCHEMA_VERSION", "TRANSLATION_VERSION",
    "FakeTranslationProvider", "PermanentProviderError", "ProviderError",
    "ProviderTimeoutError", "RetryableProviderError", "TranslationProvider",
    "ResponseValidationError", "build_translation_request", "create_provider",
    "load_json", "reconcile_translation", "translate_batches",
    "write_translation_results",
]
