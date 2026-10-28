#!/usr/bin/env python3
"""Build reading order, editorial units and future translation segments."""
import argparse
from pathlib import Path
import sys

from stolas.editorial import build_editorial_structure, load_json, write_editorial_structure


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="Stolas extraction manifest (schema 0.2.0)")
    parser.add_argument("protection", help="Stolas protection artifact (schema/ruleset 0.2.0)")
    parser.add_argument("--out", required=True, help="Destination editorial JSON")
    args = parser.parse_args(argv)
    try:
        inputs = {Path(args.manifest).resolve(), Path(args.protection).resolve()}
        output = Path(args.out).resolve()
        if output in inputs:
            raise ValueError("Editorial output must not overwrite either input artifact")
        manifest = load_json(args.manifest)
        protection = load_json(args.protection)
        result = build_editorial_structure(manifest, protection)
        write_editorial_structure(result, output)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        f"Built {len(result['editorial_units'])} editorial units, "
        f"{len(result['segments'])} segments and "
        f"{len(result['graphic_groups'])} graphic groups to {output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
