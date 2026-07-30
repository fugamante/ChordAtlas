from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

from chordatlas._fs import (
    PublishedCleanupError,
    TargetOccupiedError,
    create_text_exclusive,
    replace_text,
)
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


class _ParserExit(SystemExit):
    pass


class _ManagedStderr:
    def __init__(self, stream) -> None:
        self._stream = stream
        self.failed = False

    def write(self, content: str) -> int:
        if self.failed:
            return len(content)
        try:
            return self._stream.write(content)
        except OSError:
            self._fail()
            return len(content)

    def flush(self) -> None:
        if self.failed:
            return
        try:
            self._stream.flush()
        except OSError:
            self._fail()

    def _fail(self) -> None:
        self.failed = True
        devnull_fd: int | None = None
        try:
            stderr_fd = self._stream.fileno()
        except (AttributeError, OSError, ValueError):
            stderr_fd = None

        if stderr_fd == 2:
            try:
                devnull_fd = os.open(os.devnull, os.O_WRONLY)
                if devnull_fd != stderr_fd:
                    os.dup2(devnull_fd, stderr_fd)
                    os.close(devnull_fd)
                return
            except OSError:
                if devnull_fd is not None and devnull_fd != stderr_fd:
                    try:
                        os.close(devnull_fd)
                    except OSError:
                        pass

        try:
            self._stream = open(os.devnull, "w", encoding="utf-8")
        except OSError:
            pass

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


class _CliParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stdout_failed = False

    def _print_message(self, message: str, file=None) -> None:
        if message and file is sys.stdout:
            self._stdout_failed = _write_stdout(message) != 0
            return
        super()._print_message(message, file)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            self._print_message(message, sys.stderr)
        if status == 0 and self._stdout_failed:
            status = 1
        raise _ParserExit(status)


def main(argv: list[str] | None = None) -> int:
    original_stderr = sys.stderr
    managed_stderr = _ManagedStderr(original_stderr)
    sys.stderr = managed_stderr
    try:
        try:
            result = _main(argv)
        except _ParserExit as error:
            managed_stderr.flush()
            if managed_stderr.failed:
                raise _ParserExit(1) from None
            raise
        except BaseException:
            try:
                managed_stderr.flush()
            except BaseException:
                pass
            raise

        managed_stderr.flush()
        return 1 if managed_stderr.failed else result
    finally:
        if not managed_stderr.failed:
            sys.stderr = original_stderr


def _main(argv: list[str] | None = None) -> int:
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
        return run_release_check(stderr=sys.stderr)

    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = _CliParser(prog="chordchart", description="Render structured guitar charts.")
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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if force:
                _write_new_chart_atomic(path)
            else:
                _write_new_chart_exclusive(path)
        except TargetOccupiedError:
            print(f"Refusing to overwrite existing file: {path}", file=sys.stderr)
            return 2
        except PublishedCleanupError as error:
            print(
                f"Chart created, but temporary cleanup failed: {error}",
                file=sys.stderr,
            )
            return 1
    except OSError as error:
        print(f"Unable to create chart: {error}", file=sys.stderr)
        return 1
    print(f"Created {path}", file=sys.stderr)
    return 0


def _write_new_chart_exclusive(path: Path) -> None:
    create_text_exclusive(
        path,
        example_song_yaml(),
        stage_prefix=".chordatlas-new-",
        cleanup_reporter=_report_cleanup_failure,
    )


def _write_new_chart_atomic(path: Path) -> None:
    replace_text(
        path,
        example_song_yaml(),
        stage_prefix=".chordatlas-new-",
        cleanup_reporter=_report_cleanup_failure,
    )


class _MetadataAliasesInput(Exception):
    pass


def _report_cleanup_failure(error: BaseException, cleanup_error: OSError) -> None:
    message = f"Temporary cleanup failed: {cleanup_error}"
    print(message, file=sys.stderr)
    error.add_note(message)


def _write_stdout(content: str) -> int:
    try:
        sys.stdout.write(content)
        sys.stdout.flush()
    except BrokenPipeError:
        _neutralize_broken_stdout()
        return 1
    except OSError as error:
        print(f"Unable to write output: {error}", file=sys.stderr)
        return 1
    return 0


def _neutralize_broken_stdout() -> None:
    stdout_fd: int | None = None
    devnull_fd: int | None = None
    try:
        stdout_fd = sys.stdout.fileno()
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        if devnull_fd != stdout_fd:
            os.dup2(devnull_fd, stdout_fd)
            os.close(devnull_fd)
        return
    except (AttributeError, OSError, ValueError):
        if devnull_fd is not None and devnull_fd != stdout_fd:
            try:
                os.close(devnull_fd)
            except OSError:
                pass

    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except OSError:
        pass


def _render_chart(path: Path, *, output_format: str, provenance_mode: str) -> int:
    try:
        chart = load_song_chart(path)
    except (OSError, ValueError) as error:
        print(f"Invalid chart: {error}", file=sys.stderr)
        return 1

    if output_format == "md":
        content = render_markdown(chart, provenance_mode=provenance_mode)
    elif output_format == "txt":
        content = render_text(chart, provenance_mode=provenance_mode)
    elif output_format == "json":
        content = song_chart_to_json(chart)
    else:
        print(f"Unsupported format: {output_format}", file=sys.stderr)
        return 2
    return _write_stdout(content)


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
                metadata_content = comparison_metadata_to_json(
                    chart,
                    filters=filters,
                    provenance_mode="research",
                )
            except OSError as error:
                print(f"Unable to write metadata JSON: {error}", file=sys.stderr)
                return 1
            try:
                if _same_existing_file(path, metadata_json):
                    print(
                        "--metadata-json must not overwrite the input chart",
                        file=sys.stderr,
                    )
                    return 2
            except (OSError, RuntimeError) as error:
                print(f"Unable to inspect metadata JSON path: {error}", file=sys.stderr)
                return 1
            try:
                metadata_json.parent.mkdir(parents=True, exist_ok=True)
                _write_metadata_json_atomic(
                    path,
                    metadata_json,
                    metadata_content,
                )
            except _MetadataAliasesInput:
                print(
                    "--metadata-json must not overwrite the input chart",
                    file=sys.stderr,
                )
                return 2
            except PublishedCleanupError as error:
                print(
                    f"Metadata JSON written, but temporary cleanup failed: {error}",
                    file=sys.stderr,
                )
                return 1
            except OSError as error:
                print(f"Unable to write metadata JSON: {error}", file=sys.stderr)
                return 1

        if output_format == "json":
            content = comparison_to_json(
                chart,
                filters=filters,
                provenance_mode=provenance_mode,
            )
        elif output_format == "md":
            content = render_comparison_markdown(
                chart,
                filters=filters,
                provenance_mode=provenance_mode,
            )
        elif output_format == "txt":
            content = render_comparison_text(
                chart,
                filters=filters,
                provenance_mode=provenance_mode,
            )
        elif output_format == "csv":
            content = comparison_to_csv(
                chart,
                filters=filters,
                provenance_mode=provenance_mode,
            )
        else:
            print(f"Unsupported format: {output_format}", file=sys.stderr)
            return 2
        return _write_stdout(content)
    except ValueError as error:
        print(f"Invalid comparison filter: {error}", file=sys.stderr)
        return 1


def _write_metadata_json_atomic(input_path: Path, output_path: Path, content: str) -> None:
    def reject_input_alias(parent_fd: int, leaf: str) -> None:
        try:
            input_stat = os.stat(input_path)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"input chart disappeared before metadata publication: {input_path}"
            ) from None
        try:
            output_stat = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=True)
        except FileNotFoundError:
            return
        if os.path.samestat(input_stat, output_stat):
            raise _MetadataAliasesInput

    replace_text(
        output_path,
        content,
        stage_prefix=".chordatlas-metadata-",
        prepublish=reject_input_alias,
        cleanup_reporter=_report_cleanup_failure,
    )


def _same_existing_file(first: Path, second: Path) -> bool:
    resolved_first = first.resolve(strict=False)
    resolved_second = second.resolve(strict=False)
    if resolved_first == resolved_second:
        return True
    try:
        return first.samefile(second)
    except FileNotFoundError:
        try:
            return resolved_first.samefile(resolved_second)
        except FileNotFoundError:
            return False


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
        try:
            result = sync_schema_mirror()
        except PublishedCleanupError as error:
            print(
                f"Schema mirror synced, but temporary cleanup failed: {error}",
                file=sys.stderr,
            )
            return 1
        except (OSError, ValueError) as error:
            print(
                f"Schema mirror sync failed; mirror may be partially updated: {error}",
                file=sys.stderr,
            )
            for note in getattr(error, "__notes__", ()):
                print(note, file=sys.stderr)
            return 1
        print(f"Synced schema mirror: {result.mirror_dir}", file=sys.stderr)
        if result.drift:
            for name in result.drift:
                print(f"- updated {name}", file=sys.stderr)
        return 0

    try:
        result = check_schema_mirror()
    except (OSError, ValueError) as error:
        print(f"Schema mirror check failed: {error}", file=sys.stderr)
        return 1
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
        content = "".join(f"{name}\n" for name in available_snapshot_targets())
        return _write_stdout(content)
    if action == "check":
        stdout = io.StringIO() if output_format == "json" else sys.stdout
        result = run_snapshot_check(
            target=target,
            diff_dir=diff_dir,
            output_format=output_format,
            stdout=stdout,
            stderr=sys.stderr,
        )
        if output_format == "json":
            payload = stdout.getvalue()
            if payload and _write_stdout(payload) != 0:
                return 1
        return result
    if action == "regenerate":
        return run_snapshot_regenerate(target=target, stderr=sys.stderr)
    print(f"Unsupported snapshot action: {action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
