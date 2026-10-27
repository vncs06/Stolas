#!/usr/bin/env python3
"""Extract structured PDF data to JSON without OCR."""
import argparse
from stolas.extraction import extract_pdf, write_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    manifest = extract_pdf(args.pdf)
    write_manifest(manifest, args.out)
    print(f"Extracted {manifest['page_count']} pages to {args.out}")


if __name__ == "__main__":
    main()
