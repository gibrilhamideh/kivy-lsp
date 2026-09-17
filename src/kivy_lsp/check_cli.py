"""Check saved KV files with the same project analysis used by the LSP."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TextIO

from kivy_lsp.config import (
    ConfigError, ServerConfig, find_project_root, load_config,
)
from kivy_lsp.model.diagnostic import Diagnostic
from kivy_lsp.model.uri import file_uri_to_path
from kivy_lsp.workspace.discovery import is_excluded
from kivy_lsp.workspace.document import PositionEncoding, TextDocument
from kivy_lsp.workspace.project import ProjectWorkspace


_COLORS = {
    "error": 31,
    "warning": 33,
    "information": 36,
    "hint": 36,
    "success": 32,
}


@dataclass(frozen=True, slots=True)
class CheckDiagnostic:
    """A source diagnostic with one-based Unicode character locations."""

    path: str
    line: int
    column: int
    end_line: int
    end_column: int
    severity: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class CheckIssue:
    """An operational failure that makes the check incomplete."""

    path: str
    code: str
    message: str


@dataclass(slots=True)
class CheckReport:
    projects: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    diagnostics: list[CheckDiagnostic] = field(default_factory=list)
    issues: list[CheckIssue] = field(default_factory=list)

    def issue(self, path: Path | str, code: str, message: str) -> None:
        self.issues.append(CheckIssue(_display_path(path), code, message))

    def add(
        self, document: TextDocument, items: tuple[Diagnostic, ...]
    ) -> None:
        path = file_uri_to_path(document.uri)
        name = _display_path(path if path is not None else document.uri)
        for item in items:
            start = document.position_at(
                item.span.start, PositionEncoding.UTF32
            )
            end = document.position_at(item.span.end, PositionEncoding.UTF32)
            self.diagnostics.append(
                CheckDiagnostic(
                    name, start.line + 1, start.character + 1,
                    end.line + 1, end.character + 1,
                    item.severity.value, item.code, item.message,
                )
            )

    def finish(self, warnings_as_errors: bool) -> int:
        self.projects = sorted(set(self.projects))
        self.files = sorted(set(self.files))
        self.diagnostics = sorted(
            set(self.diagnostics),
            key=lambda item: (
                item.path, item.line, item.column, item.code, item.message,
            ),
        )
        self.issues = sorted(
            set(self.issues),
            key=lambda item: (item.path, item.code, item.message),
        )
        if self.issues:
            return 2
        if any(
            item.severity == "error"
            or warnings_as_errors and item.severity == "warning"
            for item in self.diagnostics
        ):
            return 1
        return 0

    def summary(self) -> dict[str, int | bool]:
        return {
            "checked_files": len(self.files),
            "errors": self.count("error"),
            "warnings": self.count("warning"),
            "information": self.count("information"),
            "hints": self.count("hint"),
            "complete": not self.issues,
        }

    def count(self, severity: str) -> int:
        return sum(item.severity == severity for item in self.diagnostics)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kivy-lsp check",
        description="Check saved KV files using project Python type metadata.",
    )
    parser.add_argument(
        "paths", nargs="*", metavar="PATH",
        help="files/directories to check; default: the current project",
    )
    parser.add_argument(
        "--output-format", choices=("text", "json"), default="text",
    )
    parser.add_argument(
        "--color", choices=("auto", "always", "never"), default="auto",
        help="color text output; default: auto (terminals only)",
    )
    parser.add_argument("--warnings-as-errors", action="store_true")
    arguments = parser.parse_args(argv)
    report = CheckReport()
    groups: dict[Path, list[Path]] = {}
    if not arguments.paths:
        groups[find_project_root(Path.cwd())] = []
    for name in arguments.paths:
        path = Path(name).expanduser().resolve()
        if not path.exists():
            report.issue(path, "check-path", "Path does not exist.")
        elif path.is_file() and path.suffix not in {".kv", ".py", ".pyi"}:
            report.issue(path, "check-path", "Expected a KV or Python file.")
        else:
            groups.setdefault(find_project_root(path), []).append(path)
    for root, targets in sorted(groups.items()):
        report.projects.append(str(root))
        try:
            _check_project(root, targets, report)
        except (OSError, UnicodeError, ConfigError, ValueError) as error:
            report.issue(root, "check-project", str(error))
    result = report.finish(arguments.warnings_as_errors)
    if arguments.output_format == "json":
        print(json.dumps({
            "schema_version": 1,
            **asdict(report),
            "summary": report.summary(),
        }, ensure_ascii=False, indent=2))
    else:
        _print_text(report, color=arguments.color)
    return result


def _check_project(
    root: Path, targets: list[Path], report: CheckReport,
) -> None:
    config = load_config(root)
    requested_targets = bool(targets)
    targets = [path for path in targets if not _excluded(path, config)]
    if requested_targets and not targets:
        return
    # Explicit paths may extend KV discovery while retaining surrounding
    # Python sources and the project's normal KV declarations and includes.
    additional = tuple(
        path for path in targets
        if (path.is_dir() or path.suffix == ".kv")
        and not any(_contains(parent, path) for parent in config.kv_paths)
    )
    if additional:
        config = replace(config, kv_paths=config.kv_paths + additional)
    workspace = ProjectWorkspace(config)
    initialized = workspace.initialize()
    for error in workspace.scan_errors:
        report.issue(error.path, "check-source-read", error.message)
    for issue in initialized.environment.issues:
        report.issue(issue.path, "check-environment", issue.message)
    for issue in initialized.dependencies.package_issues:
        report.issue(issue.package_name, "check-dependency", issue.message)
    for error in initialized.dependencies.file_errors:
        report.issue(error.path, "check-dependency-read", error.message)
    # Python syntax failures affect the context even outside target filters.
    python_diagnostics = (
        *initialized.project.diagnostics,
        *initialized.dependencies.diagnostics,
    )
    for item in python_diagnostics:
        document = workspace.source_document(item.path.as_uri())
        if document is None:
            report.issue(
                item.path, "check-source-read",
                "Cannot read the Python source for its diagnostics.",
            )
        else:
            report.add(document, item.diagnostics)
    for document in workspace.kv_documents():
        path = file_uri_to_path(document.uri)
        if path is None:
            continue
        if targets and not any(_contains(target, path) for target in targets):
            continue
        report.files.append(_display_path(path))
        report.add(document, workspace.diagnostics_for(document.uri))


def _contains(parent: Path, child: Path) -> bool:
    return child == parent or parent.is_dir() and child.is_relative_to(parent)


def _excluded(path: Path, config: ServerConfig) -> bool:
    roots = (config.project_root, *config.kv_paths, *config.source_roots)
    return any(is_excluded(path, root, config.excludes) for root in roots)


def _display_path(path: Path | str) -> str:
    if isinstance(path, str):
        return path
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


def _print_text(report: CheckReport, *, color: str = "auto") -> None:
    stdout_color = _color_enabled(color, sys.stdout)
    stderr_color = _color_enabled(color, sys.stderr)
    for item in report.diagnostics:
        severity = _colored(item.severity, item.severity, stdout_color)
        print(
            f"{item.path}:{item.line}:{item.column}: {severity}: "
            f"{item.message} [{item.code}]"
        )
    for issue in report.issues:
        message = _colored(issue.message, "error", stderr_color)
        print(
            f"{issue.path}: {message} [{issue.code}]", file=sys.stderr,
        )
    errors = report.count("error")
    warnings = report.count("warning")
    error_count = _colored(
        _quantity(errors, "error"),
        "error" if errors else "success", stdout_color,
    )
    warning_count = _colored(
        _quantity(warnings, "warning"),
        "warning" if warnings else "success", stdout_color,
    )
    summary = (
        f"Checked {_quantity(len(report.files), 'KV file')}: "
        f"{error_count}, {warning_count}"
    )
    if report.issues:
        incomplete = _colored("incomplete", "error", stdout_color)
        summary += (
            f"; {incomplete} ({_quantity(len(report.issues), 'issue')})"
        )
    print(summary)


def _color_enabled(mode: str, stream: TextIO) -> bool:
    if mode != "auto":
        return mode == "always"
    return (
        stream.isatty()
        and not os.environ.get("NO_COLOR")
        and os.environ.get("TERM") != "dumb"
    )


def _colored(text: str, style: str, enabled: bool) -> str:
    color = _COLORS.get(style)
    if enabled and color is not None:
        return f"\x1b[{color}m{text}\x1b[0m"
    return text


def _quantity(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"
