#!/usr/bin/env python3
"""Translate prepared batches through a structured provider interface."""
import argparse
from pathlib import Path
import sys

from stolas.translation import (
    create_provider,
    load_json,
    translate_batches,
    write_translation_results,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batches", help="Stolas translation batches (schema 0.1.0)")
    parser.add_argument("memory", help="Stolas editorial memory (schema 0.1.0)")
    parser.add_argument("protection", help="Stolas protection artifact (schema/ruleset 0.2.0)")
    parser.add_argument("--out", required=True, help="Destination translation results JSON")
    parser.add_argument("--provider", default="fake", help="Registered provider name")
    parser.add_argument("--source-language", default="en")
    parser.add_argument("--target-language", default="pt-BR")
    parser.add_argument("--existing-results", help="Prior results for resume/invalidation")
    parser.add_argument("--resume", action="store_true", help="Require and reuse existing results")
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--batch-id", action="append", dest="batch_ids")
    parser.add_argument("--fake-scenario", default="valid", help=argparse.SUPPRESS)
    parser.add_argument("--fake-failures-before-success", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        input_paths = {
            Path(value).resolve() for value in (
                args.batches, args.memory, args.protection, args.existing_results,
            ) if value
        }
        output = Path(args.out).resolve()
        if output in input_paths:
            raise ValueError("Translation output must not overwrite any input artifact")
        if args.resume and not args.existing_results:
            raise ValueError("--resume requires --existing-results")
        provider_options = {}
        if args.provider == "fake":
            provider_options = {
                "scenario": args.fake_scenario,
                "failures_before_success": args.fake_failures_before_success,
            }
        provider = create_provider(args.provider, **provider_options)
        results = translate_batches(
            load_json(args.batches), load_json(args.memory), load_json(args.protection),
            provider=provider,
            existing_results=load_json(args.existing_results) if args.existing_results else None,
            source_language=args.source_language,
            target_language=args.target_language,
            max_retries=args.max_retries,
            batch_ids=args.batch_ids,
        )
        write_translation_results(results, output)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        f"Translation results: {results['summary']['approved_batch_count']}/"
        f"{results['summary']['batch_count']} batches approved, "
        f"{results['coverage']['approved_segment_count']}/"
        f"{results['coverage']['expected_segment_count']} segments covered, "
        f"{results['summary']['blocked_batch_count']} blocked to {output}"
    )
    return 0 if results["summary"]["blocked_batch_count"] == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
