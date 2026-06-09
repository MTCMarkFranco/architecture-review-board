"""High-fidelity PDF to DOCX converter.

Uses the `pdf2docx` library, which parses PDF layout via PyMuPDF and rebuilds
it as a Word document — preserving text, fonts, colors, images, tables,
columns, headers/footers, and page geometry as closely as possible.

Usage:
    python pdf_to_docx.py <input.pdf> [output.docx] [--start N] [--end N] [--pages 1,3,5]

Examples:
    python pdf_to_docx.py report.pdf
    python pdf_to_docx.py report.pdf out.docx
    python pdf_to_docx.py report.pdf --start 0 --end 5
    python pdf_to_docx.py report.pdf --pages 0,2,4

Install dependency:
    pip install pdf2docx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a PDF file to DOCX with maximum layout fidelity.",
    )
    parser.add_argument("input", type=Path, help="Path to the source PDF file.")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        default=None,
        help="Path to the destination DOCX file (defaults to <input>.docx).",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="First page index to convert (0-based, inclusive). Default: 0.",
    )
    parser.add_argument(
        "--end",
        type=int,
        default=None,
        help="Last page index to convert (0-based, exclusive). Default: end of document.",
    )
    parser.add_argument(
        "--pages",
        type=str,
        default=None,
        help="Comma-separated 0-based page indices to convert (overrides --start/--end).",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=None,
        help="Password for an encrypted PDF (optional).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    src: Path = args.input.expanduser().resolve()
    if not src.is_file():
        print(f"ERROR: input PDF not found: {src}", file=sys.stderr)
        return 2
    if src.suffix.lower() != ".pdf":
        print(f"WARNING: input does not have a .pdf extension: {src.name}", file=sys.stderr)

    dst: Path = (args.output.expanduser().resolve() if args.output else src.with_suffix(".docx"))
    dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        from pdf2docx import Converter
    except ImportError:
        print(
            "ERROR: 'pdf2docx' is not installed.\n"
            "Install it with:  pip install pdf2docx",
            file=sys.stderr,
        )
        return 1

    pages = None
    if args.pages:
        try:
            pages = [int(p.strip()) for p in args.pages.split(",") if p.strip()]
        except ValueError:
            print("ERROR: --pages must be a comma-separated list of integers.", file=sys.stderr)
            return 2

    print(f"Converting: {src}")
    print(f"     ->    {dst}")

    cv = Converter(str(src), password=args.password) if args.password else Converter(str(src))
    try:
        if pages is not None:
            cv.convert(str(dst), pages=pages)
        else:
            cv.convert(str(dst), start=args.start, end=args.end)
    finally:
        cv.close()

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
