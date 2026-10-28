#!/usr/bin/env python3
"""Classify extraction-manifest elements for protection and editorial review."""
import argparse
import sys

from stolas.protection import classify_manifest, load_json, write_protection_manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="Stolas extraction manifest (schema 0.2.0)")
    parser.add_argument("--out", required=True, help="Destination protection JSON")
    parser.add_argument("--overrides", help="Optional editorial overrides JSON")
    args = parser.parse_args(argv)
    try:
        manifest = load_json(args.manifest)
        overrides = load_json(args.overrides) if args.overrides else None
        result = classify_manifest(manifest, overrides)
        write_protection_manifest(result, args.out)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        f"Classified {len(result['classifications'])} elements and "
        f"{len(result['protected_regions'])} regions to {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
