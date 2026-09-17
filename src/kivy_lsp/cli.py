"""Command-line KV formatting without starting the language server."""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from kivy_lsp.config import ConfigError, find_project_root, load_config
from kivy_lsp.formatting import FormatOptions, FormatResult, format_source
from kivy_lsp.workspace.discovery import discover_files, is_excluded


def main(argv: list[str] | None = None) -> int:
    """Format files/stdin; check/diff return 1 for changes and 2 for errors."""

    parser = argparse.ArgumentParser(prog="kivy-lsp format")
    parser.add_argument("paths", nargs="*", default=["-"])
    parser.add_argument("--line-length", type=int, metavar="N")
    parser.add_argument(
        "--stdin-filename", default="stdin.kv",
        help="path used to discover project configuration for stdin",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--diff", action="store_true")
    mode.add_argument("--in-place", action="store_true")
    arguments = parser.parse_args(argv)
    paths = arguments.paths or ["-"]
    if "-" in paths and len(paths) != 1:
        parser.error("stdin must be the only input")
    if "-" in paths and arguments.in_place:
        parser.error("--in-place requires file paths")

    try:
        if arguments.line_length is not None:
            FormatOptions(arguments.line_length)
        expanded = _expand_paths(paths)
        if len(expanded) > 1 and not (
            arguments.check or arguments.diff or arguments.in_place
        ):
            parser.error(
                "multiple inputs require --check, --diff, or --in-place"
            )
        changed = False
        for name in expanded:
            filename = Path(arguments.stdin_filename if name == "-" else name)
            config = load_config(find_project_root(filename))
            options = config.format
            if arguments.line_length is not None:
                options = FormatOptions(arguments.line_length)
            if name == "-":
                source = _read_stdin()
            else:
                with filename.open(encoding="utf-8", newline="") as stream:
                    source = stream.read()
            result = format_source(source, options)
            _report_issues(str(filename), result)
            differs = result.text != source
            changed |= differs
            if arguments.check:
                if differs:
                    print(f"Would reformat {filename}", file=sys.stderr)
            elif arguments.diff:
                _write_stdout(_unified_diff(source, result.text, filename))
            elif arguments.in_place:
                if differs:
                    with filename.open(
                        "w", encoding="utf-8", newline="",
                    ) as out:
                        out.write(result.text)
            else:
                _write_stdout(result.text)
        return int(changed and (arguments.check or arguments.diff))
    except (OSError, UnicodeError, ConfigError, ValueError) as error:
        print(f"kivy-lsp format: {error}", file=sys.stderr)
        return 2


def _expand_paths(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        path = Path(name)
        if name == "-":
            candidates = [name]
        else:
            config = load_config(find_project_root(path))
            roots = (
                config.project_root, *config.kv_paths, *config.source_roots
            )
            if any(
                is_excluded(path.resolve(), root, config.excludes)
                for root in roots
            ):
                continue
            if not path.is_dir():
                candidates = [name]
            else:
                candidates = [
                    str(item) for item in discover_files(
                        (path,), {".kv"}, config.excludes,
                        on_error=_discovery_error,
                    )
                    if not any(
                        is_excluded(item, root, config.excludes)
                        for root in roots
                    )
                ]
        for candidate in candidates:
            key = candidate
            if candidate != "-":
                key = str(Path(candidate).resolve())
            if key not in seen:
                seen.add(key)
                result.append(candidate)
    return result


def _discovery_error(path: Path, message: str) -> None:
    raise OSError(f"{path}: {message}")


def _read_stdin() -> str:
    stream = getattr(sys.stdin, "buffer", None)
    return stream.read().decode("utf-8") if stream else sys.stdin.read()


def _write_stdout(text: str) -> None:
    stream = getattr(sys.stdout, "buffer", None)
    if stream is None:
        sys.stdout.write(text)
    else:
        stream.write(text.encode("utf-8"))


def _report_issues(filename: str, result: FormatResult) -> None:
    for issue in result.issues:
        print(
            f"{filename}:{issue.line}: {issue.code}: {issue.message}",
            file=sys.stderr,
        )


def _unified_diff(before: str, after: str, filename: Path) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=str(filename), tofile=str(filename) + " (formatted)",
    )
    output: list[str] = []
    for line in lines:
        output.append(line)
        if not line.endswith(("\n", "\r")):
            output.append("\n\\ No newline at end of file\n")
    return "".join(output)
