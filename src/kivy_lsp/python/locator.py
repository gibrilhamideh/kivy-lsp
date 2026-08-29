# src/kivy_lsp/python/locator.py

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from kivy_lsp.python.environment import PythonEnvironment

_IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "test",
    "tests",
}


class PythonSourceKind(StrEnum):
    """The kind of Python module source found on disk."""

    STUB = "stub"
    SOURCE = "source"


@dataclass(frozen=True, slots=True)
class PythonModuleSource:
    """A Python module name and the file that defines it."""

    module_name: str
    path: Path
    kind: PythonSourceKind
    is_package: bool
    search_root: Path

    @property
    def uri(self) -> str:
        return self.path.as_uri()

    @property
    def is_stub(self) -> bool:
        return self.kind is PythonSourceKind.STUB


class PythonModuleLocator:
    """Locate Python modules without importing or executing them."""

    def __init__(self, environment: PythonEnvironment) -> None:
        self._environment = environment

    @property
    def environment(self) -> PythonEnvironment:
        return self._environment

    def find_module(
        self,
        module_name: str,
    ) -> PythonModuleSource | None:
        """Find the preferred source for one qualified module."""
        if not _is_module_name(module_name):
            return None

        for search_root in self._environment.search_paths:
            for base, stub_only in _module_bases(
                search_root,
                module_name,
            ):
                source = _find_at_base(
                    module_name,
                    base,
                    search_root,
                    stub_only=stub_only,
                )

                if source is not None:
                    return source

        return None

    def package_exists(self, package_name: str) -> bool:
        if not _is_module_name(package_name):
            return False

        for search_root in self._environment.search_paths:
            for base, _ in _module_bases(
                search_root,
                package_name,
            ):
                if base.is_dir():
                    return True

        return False

    def walk_package(
        self,
        package_name: str,
    ) -> tuple[PythonModuleSource, ...]:
        """Find every source module contained in a package."""
        if not _is_module_name(package_name):
            return ()

        selected: dict[str, PythonModuleSource] = {}

        for search_root in self._environment.search_paths:
            bases = _module_bases(
                search_root,
                package_name,
            )

            for base, stub_only in bases:
                if not base.is_dir():
                    continue

                sources = _walk_package_directory(
                    package_name,
                    base,
                    search_root,
                    stub_only=stub_only,
                )

                for source in sources:
                    selected.setdefault(
                        source.module_name,
                        source,
                    )

        return tuple(
            selected[module_name]
            for module_name in sorted(selected)
        )


def _module_bases(
    search_root: Path,
    module_name: str,
) -> tuple[tuple[Path, bool], ...]:
    parts = module_name.split(".")
    first = parts[0]
    remaining = parts[1:]

    stub_base = search_root / f"{first}-stubs"

    if remaining:
        stub_base = stub_base.joinpath(*remaining)

    source_base = search_root.joinpath(*parts)

    return (
        (stub_base, True),
        (source_base, False),
    )


def _find_at_base(
    module_name: str,
    base: Path,
    search_root: Path,
    *,
    stub_only: bool,
) -> PythonModuleSource | None:
    candidates: list[tuple[Path, PythonSourceKind, bool]] = [
        (
            base / "__init__.pyi",
            PythonSourceKind.STUB,
            True,
        ),
        (
            base.with_suffix(".pyi"),
            PythonSourceKind.STUB,
            False,
        ),
    ]

    if not stub_only:
        candidates.extend(
            (
                (
                    base / "__init__.py",
                    PythonSourceKind.SOURCE,
                    True,
                ),
                (
                    base.with_suffix(".py"),
                    PythonSourceKind.SOURCE,
                    False,
                ),
            )
        )

    for path, kind, is_package in candidates:
        if not path.is_file():
            continue

        return PythonModuleSource(
            module_name=module_name,
            path=path.resolve(),
            kind=kind,
            is_package=is_package,
            search_root=search_root,
        )

    return None


def _walk_package_directory(
    package_name: str,
    package_directory: Path,
    search_root: Path,
    *,
    stub_only: bool,
) -> tuple[PythonModuleSource, ...]:
    selected: dict[str, PythonModuleSource] = {}

    for directory, directory_names, filenames in os.walk(
        str(package_directory),
    ):
        current_directory = Path(directory)

        directory_names[:] = [
            name
            for name in sorted(directory_names)
            if name not in _IGNORED_DIRECTORIES
            and not name.startswith(".")
        ]

        filenames = sorted(
            filenames,
            key=_filename_sort_key,
        )

        for filename in filenames:
            path = current_directory / filename

            if path.suffix not in {".py", ".pyi"}:
                continue

            if stub_only and path.suffix != ".pyi":
                continue

            module_name = _module_name_for_path(
                package_name,
                package_directory,
                path,
            )

            if module_name is None:
                continue

            source = PythonModuleSource(
                module_name=module_name,
                path=path.resolve(),
                kind=_source_kind(path),
                is_package=path.stem == "__init__",
                search_root=search_root,
            )
            existing = selected.get(module_name)

            if existing is None:
                selected[module_name] = source
                continue

            if _source_priority(source) < _source_priority(existing):
                selected[module_name] = source

    return tuple(
        selected[module_name]
        for module_name in sorted(selected)
    )


def _module_name_for_path(
    package_name: str,
    package_directory: Path,
    path: Path,
) -> str | None:
    try:
        relative = path.relative_to(package_directory)
    except ValueError:
        return None

    parts = list(relative.with_suffix("").parts)

    if parts and parts[-1] == "__init__":
        parts.pop()

    if not all(part.isidentifier() for part in parts):
        return None

    if not parts:
        return package_name

    return ".".join(
        (
            package_name,
            *parts,
        )
    )


def _source_kind(path: Path) -> PythonSourceKind:
    if path.suffix == ".pyi":
        return PythonSourceKind.STUB

    return PythonSourceKind.SOURCE


def _source_priority(
    source: PythonModuleSource,
) -> tuple[int, int]:
    kind_priority = 0 if source.kind is PythonSourceKind.STUB else 1

    package_priority = 0 if source.is_package else 1

    return (
        kind_priority,
        package_priority,
    )


def _filename_sort_key(
    filename: str,
) -> tuple[str, int]:
    path = Path(filename)

    priority = 0 if path.suffix == ".pyi" else 1

    return (
        path.stem,
        priority,
    )


def _is_module_name(module_name: str) -> bool:
    if not module_name:
        return False

    return all(
        part.isidentifier()
        for part in module_name.split(".")
    )
