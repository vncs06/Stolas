#!/usr/bin/env python3
"""Build deterministic contextual batches without translating text."""
import argparse
from pathlib import Path
import sys

from stolas.batching import build_translation_batches, load_json, write_translation_batches


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("editorial", help="Stolas editorial artifact (schema 0.1.0)")
    parser.add_argument("memory", help="Stolas editorial memory (schema 0.1.0)")
    parser.add_argument("protection", help="Stolas protection artifact (schema/ruleset 0.2.0)")
    parser.add_argument("--out", required=True, help="Destination translation batches JSON")
    parser.add_argument("--existing-batches", help="Optional prior batching artifact for checkpoints")
    parser.add_argument("--max-input-tokens", type=int, default=8192)
    parser.add_argument("--reserved-output-tokens", type=int, default=2048)
    parser.add_argument("--reserved-system-tokens", type=int, default=768)
    parser.add_argument("--context-budget", type=int, default=512)
    parser.add_argument("--memory-budget", type=int, default=768)
    parser.add_argument("--profile", default="generic-local-v1")
    args = parser.parse_args(argv)
    try:
        inputs = {
            Path(value).resolve() for value in (
                args.editorial, args.memory, args.protection, args.existing_batches,
            ) if value
        }
        output = Path(args.out).resolve()
        if output in inputs:
            raise ValueError("Batch output must not overwrite any input artifact")
        artifact = build_translation_batches(
            load_json(args.editorial), load_json(args.memory), load_json(args.protection),
            existing_batches=load_json(args.existing_batches) if args.existing_batches else None,
            max_input_tokens=args.max_input_tokens,
            reserved_output_tokens=args.reserved_output_tokens,
            reserved_system_tokens=args.reserved_system_tokens,
            context_budget_tokens=args.context_budget,
            memory_budget_tokens=args.memory_budget,
            profile=args.profile,
        )
        write_translation_batches(artifact, output)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        f"Built {len(artifact['batches'])} batches covering "
        f"{artifact['coverage']['actual_payload_segment_count']} segments "
        f"with {artifact['checkpoint_state']['stale_count']} stale checkpoints to {output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
