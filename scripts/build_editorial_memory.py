#!/usr/bin/env python3
"""Build or merge persistent editorial memory without translating text."""
import argparse
from pathlib import Path
import sys

from stolas.memory import build_editorial_memory, load_json, write_editorial_memory


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("editorial", help="Stolas editorial artifact (schema 0.1.0)")
    parser.add_argument("protection", help="Stolas protection artifact (schema/ruleset 0.2.0)")
    parser.add_argument("--out", required=True, help="Destination editorial memory JSON")
    parser.add_argument("--existing-memory", help="Optional existing memory to merge")
    parser.add_argument("--overrides", help="Optional human memory overrides")
    parser.add_argument("--style-guide", help="Optional human style guide")
    parser.add_argument("--target-language", default="pt-BR", help="Default target language")
    args = parser.parse_args(argv)
    try:
        input_paths = {
            Path(value).resolve() for value in (
                args.editorial, args.protection, args.existing_memory,
                args.overrides, args.style_guide,
            ) if value
        }
        output = Path(args.out).resolve()
        if output in input_paths:
            raise ValueError("Memory output must not overwrite any input artifact")
        editorial = load_json(args.editorial)
        protection = load_json(args.protection)
        memory = build_editorial_memory(
            editorial, protection,
            existing_memory=load_json(args.existing_memory) if args.existing_memory else None,
            overrides=load_json(args.overrides) if args.overrides else None,
            style_guide=load_json(args.style_guide) if args.style_guide else None,
            target_language=args.target_language,
        )
        write_editorial_memory(memory, output)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        f"Built memory v{memory['memory_version']} with "
        f"{len(memory['glossary'])} glossary entries, "
        f"{len(memory['decisions'])} decisions and "
        f"{len(memory['conflicts'])} conflicts to {output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
