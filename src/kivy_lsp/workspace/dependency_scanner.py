# src/kivy_lsp/workspace/dependency_scanner.py

from __future__ import annotations

import tokenize
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from kivy_lsp.model.diagnostic import Diagnostic
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.python.locator import (
    PythonModuleLocator,
    PythonModuleSource,
)
from kivy_lsp.workspace.document import TextDocument


@dataclass(frozen=True, slots=True)
class DependencyFileDiagnostics:
    """Diagnostics produced while indexing one dependency file."""

    path: Path
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True, slots=True)
class DependencyFileError:
    """A dependency file that could not be read or indexed."""

    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class DependencyPackageIssue:
    """A requested dependency package that could not be located."""

    package_name: str
    message: str


@dataclass(frozen=True, slots=True)
class DependencyScanResult:
    """The result of statically indexing dependency packages."""

    packages: tuple[str, ...]
    indexed_modules: tuple[str, ...]
    indexed_files: tuple[Path, ...]
    diagnostics: tuple[DependencyFileDiagnostics, ...]
    file_errors: tuple[DependencyFileError, ...]
    package_issues: tuple[DependencyPackageIssue, ...]

    @property
    def succeeded(self) -> bool:
        return not self.file_errors and not self.package_issues


class DependencyScanner:
    """Index Kivy ecosystem packages without importing them."""

    def __init__(
        self,
        locator: PythonModuleLocator,
    ) -> None:
        self._locator = locator

    def scan(
        self,
        python_index: PythonIndex,
        *,
        packages: Iterable[str] = (),
        discover_imports: bool = True,
    ) -> DependencyScanResult:
        """Index requested packages, optionally discovering Kivy imports.

        Incremental workspace updates pass only newly requested packages and
        disable discovery so retained dependencies are never walked again.
        """
        project_modules = {module.name for module in python_index.modules}
        package_names = self._package_names(
            python_index,
            packages,
            discover_imports=discover_imports,
        )
        selected_sources: dict[
            str,
            PythonModuleSource,
        ] = {}
        package_issues: list[DependencyPackageIssue] = []

        for package_name in package_names:
            sources = self._locator.walk_package(
                package_name,
            )

            if not sources:
                package_issues.append(
                    DependencyPackageIssue(
                        package_name=package_name,
                        message=("No Python source or type stubs were found."),
                    )
                )
                continue

            for source in sources:
                if source.module_name in project_modules:
                    continue

                selected_sources.setdefault(
                    source.module_name,
                    source,
                )

        return self._index_sources(
            python_index, package_names, selected_sources, package_issues
        )

    def scan_modules(
        self, python_index: PythonIndex, modules: Iterable[str]
    ) -> DependencyScanResult:
        """Restore selected modules without walking their packages again."""
        selected_sources: dict[str, PythonModuleSource] = {}
        package_issues: list[DependencyPackageIssue] = []
        names = sorted(set(modules))
        for name in names:
            source = self._locator.find_module(name)
            if source is None:
                package_issues.append(
                    DependencyPackageIssue(name, "No source or stubs found.")
                )
            else:
                selected_sources[name] = source
        packages = tuple(sorted({name.partition(".")[0] for name in names}))
        return self._index_sources(
            python_index, packages, selected_sources, package_issues
        )

    def _index_sources(
        self,
        python_index: PythonIndex,
        package_names: tuple[str, ...],
        selected_sources: dict[str, PythonModuleSource],
        package_issues: list[DependencyPackageIssue],
    ) -> DependencyScanResult:
        indexed_modules: list[str] = []
        indexed_files: list[Path] = []
        diagnostics: list[DependencyFileDiagnostics] = []
        file_errors: list[DependencyFileError] = []

        for module_name in sorted(selected_sources):
            source = selected_sources[module_name]

            try:
                text = _read_source(source.path)
            except (OSError, SyntaxError, UnicodeError) as error:
                file_errors.append(
                    DependencyFileError(
                        path=source.path,
                        message=_error_message(error),
                    )
                )
                continue

            document = TextDocument(
                uri=source.uri,
                text=text,
            )
            result = index_python_module(
                document,
                module_name,
            )

            if result.diagnostics:
                diagnostics.append(
                    DependencyFileDiagnostics(
                        path=source.path,
                        diagnostics=result.diagnostics,
                    )
                )

            if result.module is None:
                continue

            python_index.replace(result.module)
            indexed_modules.append(module_name)
            indexed_files.append(source.path)

        return DependencyScanResult(
            packages=package_names,
            indexed_modules=tuple(indexed_modules),
            indexed_files=tuple(indexed_files),
            diagnostics=tuple(diagnostics),
            file_errors=tuple(file_errors),
            package_issues=tuple(package_issues),
        )

    def _package_names(
        self,
        python_index: PythonIndex,
        packages: Iterable[str],
        *,
        discover_imports: bool,
    ) -> tuple[str, ...]:
        names = {package.strip() for package in packages if package.strip()}
        if not discover_imports:
            return tuple(sorted(names))
        names.add("kivy")

        for module in python_index.modules:
            for imported in module.imports:
                if imported.relative_level:
                    continue

                top_level = imported.target_module.partition(".")[0]

                if top_level.startswith("kivy"):
                    names.add(top_level)

        return tuple(sorted(names))


def _read_source(path: Path) -> str:
    with tokenize.open(str(path)) as source_file:
        return source_file.read()


def _error_message(error: BaseException) -> str:
    message = str(error).strip()

    if message:
        return message

    return type(error).__name__
