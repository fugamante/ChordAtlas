from __future__ import annotations

import argparse
import sys
from pathlib import Path

from chordatlas.io import example_song_yaml, load_song_chart, song_chart_to_json
from chordatlas.render import render_markdown, render_text


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "new":
        return _new_chart(args.path, force=args.force)
    if args.command == "render":
        return _render_chart(args.path, output_format=args.format, provenance_mode=args.provenance)
    if args.command == "validate":
        return _validate_chart(args.path)

    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chordchart", description="Render structured guitar charts.")
    subcommands = parser.add_subparsers(dest="command")

    new_parser = subcommands.add_parser("new", help="Create a starter song YAML file.")
    new_parser.add_argument("path", type=Path)
    new_parser.add_argument("--force", action="store_true", help="Overwrite an existing file.")

    render_parser = subcommands.add_parser("render", help="Render a song YAML file.")
    render_parser.add_argument("path", type=Path)
    render_parser.add_argument("--format", choices=("md", "txt", "json"), default="md")
    render_parser.add_argument(
        "--provenance",
        choices=("minimal", "standard", "research"),
        default="minimal",
        help="How much provenance to show in Markdown and text output.",
    )

    validate_parser = subcommands.add_parser(
        "validate",
        help="Load a song YAML file and report schema or provenance warnings.",
    )
    validate_parser.add_argument("path", type=Path)

    return parser


def _new_chart(path: Path, *, force: bool) -> int:
    if path.exists() and not force:
        print(f"Refusing to overwrite existing file: {path}", file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(example_song_yaml(), encoding="utf-8")
    print(f"Created {path}", file=sys.stderr)
    return 0


def _render_chart(path: Path, *, output_format: str, provenance_mode: str) -> int:
    try:
        chart = load_song_chart(path)
    except (OSError, ValueError) as error:
        print(f"Invalid chart: {error}", file=sys.stderr)
        return 1

    if output_format == "md":
        print(render_markdown(chart, provenance_mode=provenance_mode), end="")
    elif output_format == "txt":
        print(render_text(chart, provenance_mode=provenance_mode), end="")
    elif output_format == "json":
        print(song_chart_to_json(chart), end="")
    else:
        print(f"Unsupported format: {output_format}", file=sys.stderr)
        return 2
    return 0


def _validate_chart(path: Path) -> int:
    try:
        chart = load_song_chart(path)
    except (OSError, ValueError) as error:
        print(f"Invalid chart: {error}", file=sys.stderr)
        return 1

    warnings = chart.provenance_warnings()
    if not warnings:
        print(f"Valid chart: {path}", file=sys.stderr)
        return 0

    print(f"Valid chart with {len(warnings)} provenance warning(s): {path}", file=sys.stderr)
    for warning in warnings:
        print(f"- {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
