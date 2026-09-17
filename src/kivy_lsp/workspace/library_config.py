"""Discover and merge declarative exports from used Python packages."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from kivy_lsp.config import (
    CONFIG_FILENAME,
    ConfigError,
    LibraryExports,
    ServerConfig,
    load_library_exports,
)
from kivy_lsp.python.environment import PythonEnvironment
from kivy_lsp.workspace.python_scanner import PythonScanError


@dataclass(frozen=True, slots=True)
class LibraryConfigResult:
    config: ServerConfig
    packages: tuple[str, ...]
    paths: tuple[Path, ...]
    issues: tuple[PythonScanError, ...]


@dataclass(frozen=True, slots=True)
class _PackageConfig:
    paths: tuple[Path, ...]
    exports: tuple[tuple[Path, LibraryExports], ...]
    issues: tuple[PythonScanError, ...]


class LibraryConfigResolver:
    """Cache static package metadata without importing dependency code."""

    def __init__(self, environment: PythonEnvironment) -> None:
        self._environment = environment
        self._cache: dict[str, _PackageConfig] = {}
        self._last_base: ServerConfig | None = None
        self._last_references: frozenset[str] = frozenset()
        self._last_result: LibraryConfigResult | None = None

    def clear(self) -> None:
        self._cache.clear()
        self._last_result = None

    def resolve(
        self, base: ServerConfig, references: set[str],
    ) -> LibraryConfigResult:
        requested = frozenset(references)
        if (
            self._last_result is not None
            and base == self._last_base
            and requested == self._last_references
        ):
            return self._last_result
        pending = _package_names(references)
        visited: set[str] = set()
        paths: set[Path] = set()
        providers: dict[Path, LibraryExports] = {}
        issues: dict[tuple[Path, str], PythonScanError] = {}
        packages: set[str] = set()
        while pending:
            name = pending.pop()
            if name in visited:
                continue
            visited.add(name)
            if name not in self._cache:
                self._cache[name] = self._load(name)
            metadata = self._cache[name]
            paths.update(metadata.paths)
            for issue in metadata.issues:
                issues[issue.path, issue.message] = issue
            for path, exports in metadata.exports:
                providers[path] = exports
                packages.add(name.partition(".")[0])
                pending.update(_package_names(_export_references(exports)))
            pending.difference_update(visited)

        # A later re-import should see fresh metadata even without a watcher.
        for name in self._cache.keys() - visited:
            del self._cache[name]
        config, conflicts = _merge_exports(base, providers)
        for issue in conflicts:
            issues[issue.path, issue.message] = issue
        packages.update(
            reference.partition(".")[0]
            for reference in _export_references(config)
            if reference.partition(".")[0].isidentifier()
        )
        result = LibraryConfigResult(
            config=config,
            packages=tuple(sorted(packages)),
            paths=tuple(sorted(paths)),
            issues=tuple(issues[key] for key in sorted(issues)),
        )
        self._last_base = base
        self._last_references = requested
        self._last_result = result
        return result

    def _load(self, name: str) -> _PackageConfig:
        paths = self._candidate_paths(name)
        exports: list[tuple[Path, LibraryExports]] = []
        issues: list[PythonScanError] = []
        for path in paths:
            if not path.exists():
                continue
            try:
                exports.append((path, load_library_exports(path)))
            except ConfigError as error:
                issues.append(PythonScanError(path, str(error)))
        return _PackageConfig(paths, tuple(exports), tuple(issues))

    def _candidate_paths(self, name: str) -> tuple[Path, ...]:
        # Follow regular-package shadowing, retaining namespace portions.
        roots = list(self._environment.search_paths)
        for component in name.split("."):
            namespaces: list[Path] = []
            for root in roots:
                package = root / component
                if any(
                    (package / filename).is_file()
                    for filename in ("__init__.pyi", "__init__.py")
                ):
                    roots = [package]
                    break
                if any(
                    package.with_suffix(suffix).is_file()
                    for suffix in (".pyi", ".py")
                ):
                    return ()
                if package.is_dir():
                    namespaces.append(package)
            else:
                roots = namespaces
                if not roots:
                    return ()
        return tuple(dict.fromkeys(
            (root / CONFIG_FILENAME).resolve() for root in roots
        ))


def _package_names(references: set[str]) -> set[str]:
    packages: set[str] = set()
    for reference in references:
        parts = reference.split(".")
        if not all(part.isidentifier() for part in parts):
            continue
        packages.update(
            ".".join(parts[:index]) for index in range(1, len(parts) + 1)
        )
    return packages


def _export_references(exports: LibraryExports | ServerConfig) -> set[str]:
    return (
        set(exports.globals.values())
        | {item.target for item in exports.global_imports}
        | {name for name in exports.member_projections if "." in name}
        | {name for name in exports.subscript_projections if "." in name}
    )


def _merge_exports(
    base: ServerConfig, providers: dict[Path, LibraryExports],
) -> tuple[ServerConfig, tuple[PythonScanError, ...]]:
    issues: list[PythonScanError] = []
    app_names = set(base.globals) | {
        item.name for item in base.global_imports
    }
    defaults: dict[str, list[tuple[Path, str]]] = {}
    for path, exports in sorted(providers.items()):
        bindings = {item.name: item.target for item in exports.global_imports}
        bindings.update(exports.globals)
        for name, target in bindings.items():
            if name not in app_names:
                defaults.setdefault(name, []).append((path, target))
    globals_: dict[str, str] = {}
    for name, candidates in defaults.items():
        if len({target for _, target in candidates}) == 1:
            globals_[name] = candidates[0][1]
        else:
            issues.extend(_conflict_issues("global", name, candidates))
    globals_.update(base.globals)
    projections = {}
    for field in ("member_projections", "subscript_projections"):
        explicit: dict[str, int] = getattr(base, field)
        candidates_by_name: dict[str, list[tuple[Path, int]]] = {}
        for path, exports in sorted(providers.items()):
            for name, value in getattr(exports, field).items():
                if name not in explicit:
                    candidates_by_name.setdefault(name, []).append(
                        (path, value),
                    )
        merged: dict[str, int] = {}
        for name, candidates in candidates_by_name.items():
            if len({value for _, value in candidates}) == 1:
                merged[name] = candidates[0][1]
            else:
                issues.extend(_conflict_issues(
                    field.replace("_", "-"), name, candidates,
                ))
        merged.update(explicit)
        projections[field] = merged
    return replace(
        base,
        globals=globals_,
        member_projections=projections["member_projections"],
        subscript_projections=projections["subscript_projections"],
    ), tuple(issues)


def _conflict_issues[T](
    kind: str, name: str, candidates: list[tuple[Path, T]],
) -> tuple[PythonScanError, ...]:
    providers = "; ".join(
        f"{path}: {value!r}" for path, value in candidates
    )
    message = (
        f"Conflicting library {kind} {name!r}: {providers}. "
        f"Override {name!r} in the application's {CONFIG_FILENAME}."
    )
    return tuple(PythonScanError(path, message) for path, _ in candidates)
