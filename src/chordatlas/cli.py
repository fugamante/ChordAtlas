from __future__ import annotations

import argparse
import sys
from pathlib import Path

from chordatlas.compare import (
    ComparisonFilters,
    comparison_metadata_to_json,
    comparison_to_csv,
    comparison_to_json,
    render_comparison_markdown,
    render_comparison_text,
)
from chordatlas.io import example_song_yaml, load_song_chart, song_chart_to_json
from chordatlas.render import render_markdown, render_text
from chordatlas.release import run_release_check
from chordatlas.schema import check_schema_mirror, sync_schema_mirror, validate_chart_schema
from chordatlas.snapshots import (
    available_snapshot_targets,
    run_snapshot_check,
    run_snapshot_regenerate,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "new":
        return _new_chart(args.path, force=args.force)
    if args.command == "render":
        return _render_chart(args.path, output_format=args.format, provenance_mode=args.provenance)
    if args.command == "compare":
        return _compare_chart(
            args.path,
            output_format=args.format,
            provenance_mode=args.provenance,
            metadata_json=args.metadata_json,
            filters=ComparisonFilters.from_values(
                categories=args.categories,
                recording_ids=args.recording_ids,
                severities=args.severities,
                source_specific_only=args.source_specific_only,
            ),
        )
    if args.command == "validate":
        return _validate_chart(args.path)
    if args.command == "schemas":
        return _schemas(args.sync)
    if args.command == "snapshots":
        return _snapshots(
            args.action,
            target=args.target,
            diff_dir=args.diff_dir,
            output_format=args.format,
        )
    if args.command == "release-check":
        return run_release_check()

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

    compare_parser = subcommands.add_parser(
        "compare",
        help="Export derived recording-source comparison analytics.",
    )
    compare_parser.add_argument("path", type=Path)
    compare_parser.add_argument("--format", choices=("json", "md", "txt", "csv"), default="json")
    compare_parser.add_argument(
        "--provenance",
        choices=("minimal", "standard", "research"),
        default="minimal",
        help="How much provenance to include in comparison exports.",
    )
    compare_parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        default=[],
        help="Include only one recording-note category. May be repeated.",
    )
    compare_parser.add_argument(
        "--recording",
        action="append",
        dest="recording_ids",
        default=[],
        help="Include only one recording/source ID. May be repeated.",
    )
    compare_parser.add_argument(
        "--severity",
        action="append",
        dest="severities",
        default=[],
        help="Include only one severity label. May be repeated.",
    )
    compare_parser.add_argument(
        "--source-specific-only",
        action="store_true",
        help="Include only claims that differ across the selected recording sources.",
    )
    compare_parser.add_argument(
        "--metadata-json",
        type=Path,
        help="Write a JSON sidecar for CSV research exports.",
    )

    validate_parser = subcommands.add_parser(
        "validate",
        help="Load a song YAML file and report schema or provenance warnings.",
    )
    validate_parser.add_argument("path", type=Path)

    schemas_parser = subcommands.add_parser(
        "schemas",
        help="Check or sync the source-tree JSON Schema mirror.",
    )
    schemas_group = schemas_parser.add_mutually_exclusive_group()
    schemas_group.add_argument(
        "--check",
        action="store_true",
        help="Check whether repo-root schemas match packaged schemas. This is the default.",
    )
    schemas_group.add_argument(
        "--sync",
        action="store_true",
        help="Rewrite repo-root schemas from packaged canonical schemas.",
    )

    subcommands.add_parser(
        "release-check",
        help="Run the full non-interactive pre-release validation stack.",
    )

    snapshots_parser = subcommands.add_parser(
        "snapshots",
        help="List, check, or regenerate golden snapshots.",
    )
    snapshots_parser.add_argument(
        "action",
        choices=("list", "check", "regenerate"),
        help="Snapshot operation to run.",
    )
    snapshots_parser.add_argument(
        "target",
        nargs="?",
        default="all",
        help="Snapshot target: all, open-string, or research-comparison.",
    )
    snapshots_parser.add_argument(
        "--diff-dir",
        type=Path,
        help="When checking snapshots, write full unified diff artifacts to this directory.",
    )
    snapshots_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format for snapshot check results.",
    )

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


def _compare_chart(
    path: Path,
    *,
    output_format: str,
    provenance_mode: str,
    metadata_json: Path | None,
    filters: ComparisonFilters,
) -> int:
    try:
        chart = load_song_chart(path)
    except (OSError, ValueError) as error:
        print(f"Invalid chart: {error}", file=sys.stderr)
        return 1

    try:
        if metadata_json is not None:
            if output_format != "csv":
                print("--metadata-json is only supported with --format csv", file=sys.stderr)
                return 2
            if provenance_mode != "research":
                print("--metadata-json requires --provenance research", file=sys.stderr)
                return 2
            try:
                metadata_json.parent.mkdir(parents=True, exist_ok=True)
                metadata_json.write_text(
                    comparison_metadata_to_json(
                        chart,
                        filters=filters,
                        provenance_mode="research",
                    ),
                    encoding="utf-8",
                )
            except OSError as error:
                print(f"Unable to write metadata JSON: {error}", file=sys.stderr)
                return 1

        if output_format == "json":
            print(
                comparison_to_json(
                    chart,
                    filters=filters,
                    provenance_mode=provenance_mode,
                ),
                end="",
            )
        elif output_format == "md":
            print(
                render_comparison_markdown(
                    chart,
                    filters=filters,
                    provenance_mode=provenance_mode,
                ),
                end="",
            )
        elif output_format == "txt":
            print(
                render_comparison_text(
                    chart,
                    filters=filters,
                    provenance_mode=provenance_mode,
                ),
                end="",
            )
        elif output_format == "csv":
            print(
                comparison_to_csv(
                    chart,
                    filters=filters,
                    provenance_mode=provenance_mode,
                ),
                end="",
            )
        else:
            print(f"Unsupported format: {output_format}", file=sys.stderr)
            return 2
    except ValueError as error:
        print(f"Invalid comparison filter: {error}", file=sys.stderr)
        return 1
    return 0


def _validate_chart(path: Path) -> int:
    try:
        chart = load_song_chart(path)
    except (OSError, ValueError) as error:
        print(f"Invalid chart: {error}", file=sys.stderr)
        return 1

    schema_result = validate_chart_schema(chart)
    warnings = chart.provenance_warnings()
    if schema_result.failed:
        print(f"Invalid chart: normalized JSON Schema validation failed for {path}", file=sys.stderr)
        for error in schema_result.errors:
            print(f"- {error}", file=sys.stderr)
        if warnings:
            print(f"Provenance warning(s): {len(warnings)}", file=sys.stderr)
            for warning in warnings:
                print(f"- {warning}", file=sys.stderr)
        return 1

    if not warnings:
        print(f"Valid chart: {path}", file=sys.stderr)
        if schema_result.skipped:
            print(f"Schema validation skipped: {schema_result.reason}", file=sys.stderr)
        return 0

    print(f"Valid chart with {len(warnings)} provenance warning(s): {path}", file=sys.stderr)
    if schema_result.skipped:
        print(f"Schema validation skipped: {schema_result.reason}", file=sys.stderr)
    for warning in warnings:
        print(f"- {warning}", file=sys.stderr)
    return 0


def _schemas(sync: bool) -> int:
    if sync:
        result = sync_schema_mirror()
        print(f"Synced schema mirror: {result.mirror_dir}", file=sys.stderr)
        if result.drift:
            for name in result.drift:
                print(f"- updated {name}", file=sys.stderr)
        return 0

    result = check_schema_mirror()
    if result.clean:
        print(f"Schema mirror is in sync: {result.mirror_dir}", file=sys.stderr)
        return 0

    print(f"Schema mirror drift detected: {result.mirror_dir}", file=sys.stderr)
    for name in result.drift:
        print(f"- {name}", file=sys.stderr)
    print("Run `chordchart schemas --sync` to update the mirror.", file=sys.stderr)
    return 1


def _snapshots(
    action: str,
    *,
    target: str,
    diff_dir: Path | None,
    output_format: str,
) -> int:
    if action == "list":
        for name in available_snapshot_targets():
            print(name)
        return 0
    if action == "check":
        return run_snapshot_check(
            target=target,
            diff_dir=diff_dir,
            output_format=output_format,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    if action == "regenerate":
        return run_snapshot_regenerate(target=target, stderr=sys.stderr)
    print(f"Unsupported snapshot action: {action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
