# src/kivy_lsp/python/environment.py

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from kivy_lsp.config import ServerConfig


@dataclass(frozen=True, slots=True)
class PythonEnvironmentIssue:
    """A non-fatal problem found during environment discovery."""

    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class PythonEnvironment:
    """Python import locations available to the analyzed project."""

    project_root: Path
    virtual_environment: Path | None
    version: str | None
    search_paths: tuple[Path, ...]
    site_packages: tuple[Path, ...]
    issues: tuple[PythonEnvironmentIssue, ...]

    @property
    def uses_project_environment(self) -> bool:
        environment = self.virtual_environment

        if environment is None:
            return False

        return environment.parent == self.project_root


def discover_python_environment(
    config: ServerConfig,
) -> PythonEnvironment:
    """Discover import paths without importing or executing the app."""
    issues: list[PythonEnvironmentIssue] = []
    search_paths: list[Path] = []
    site_packages: list[Path] = []

    for source_root in config.source_roots:
        _append_directory(
            search_paths,
            source_root,
        )

    virtual_environment = _find_virtual_environment(
        config.project_root,
    )

    if virtual_environment is not None:
        discovered = _find_site_packages(
            virtual_environment,
        )

        for path in discovered:
            _append_directory(
                site_packages,
                path,
            )
            _append_directory(
                search_paths,
                path,
            )

        for path in discovered:
            _append_linked_paths(
                path,
                search_paths,
                issues,
            )

    for entry in sys.path:
        if not entry:
            continue

        path = Path(entry)

        if not path.is_dir():
            continue

        resolved = path.resolve()
        _append_directory(
            search_paths,
            resolved,
        )

        if _is_site_packages(resolved):
            _append_directory(
                site_packages,
                resolved,
            )

    version = _environment_version(
        virtual_environment,
        issues,
    )

    return PythonEnvironment(
        project_root=config.project_root,
        virtual_environment=virtual_environment,
        version=version,
        search_paths=tuple(search_paths),
        site_packages=tuple(site_packages),
        issues=tuple(issues),
    )


def _find_virtual_environment(
    project_root: Path,
) -> Path | None:
    candidates = (
        project_root / ".venv",
        project_root / "venv",
    )

    for candidate in candidates:
        if _is_virtual_environment(candidate):
            return candidate.resolve()

    current_environment = Path(sys.prefix)

    if _is_virtual_environment(current_environment):
        return current_environment.resolve()

    return None


def _is_virtual_environment(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "pyvenv.cfg").is_file()
    )


def _find_site_packages(
    environment: Path,
) -> tuple[Path, ...]:
    paths: list[Path] = []
    patterns = (
        "lib/python*/site-packages",
        "lib/python*/dist-packages",
        "lib64/python*/site-packages",
        "lib64/python*/dist-packages",
        "Lib/site-packages",
    )

    for pattern in patterns:
        for path in sorted(environment.glob(pattern)):
            _append_directory(
                paths,
                path,
            )

    return tuple(paths)


def _append_linked_paths(
    site_packages: Path,
    search_paths: list[Path],
    issues: list[PythonEnvironmentIssue],
) -> None:
    for path_file in sorted(site_packages.glob("*.pth")):
        _read_path_file(
            path_file,
            site_packages,
            search_paths,
            issues,
        )

    for link_file in sorted(site_packages.glob("*.egg-link")):
        _read_path_file(
            link_file,
            site_packages,
            search_paths,
            issues,
        )


def _read_path_file(
    path_file: Path,
    base_directory: Path,
    search_paths: list[Path],
    issues: list[PythonEnvironmentIssue],
) -> None:
    try:
        content = path_file.read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError as error:
        issues.append(
            PythonEnvironmentIssue(
                path=path_file,
                message=_error_message(error),
            )
        )
        return

    for raw_line in content.splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("import ") or line.startswith("import\t"):
            continue

        candidate = Path(line)

        if not candidate.is_absolute():
            candidate = base_directory / candidate

        _append_directory(
            search_paths,
            candidate,
        )


def _environment_version(
    environment: Path | None,
    issues: list[PythonEnvironmentIssue],
) -> str | None:
    if environment is None:
        return _current_python_version()

    configuration = environment / "pyvenv.cfg"

    try:
        content = configuration.read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError as error:
        issues.append(
            PythonEnvironmentIssue(
                path=configuration,
                message=_error_message(error),
            )
        )
        return None

    values: dict[str, str] = {}

    for raw_line in content.splitlines():
        key, separator, value = raw_line.partition("=")

        if not separator:
            continue

        values[key.strip().casefold()] = value.strip()

    return (
        values.get("version")
        or values.get("version_info")
        or _current_python_version()
    )


def _current_python_version() -> str:
    version = sys.version_info

    return (
        f"{version.major}."
        f"{version.minor}."
        f"{version.micro}"
    )


def _append_directory(
    paths: list[Path],
    path: Path,
) -> None:
    if not path.is_dir():
        return

    resolved = path.resolve()

    if resolved not in paths:
        paths.append(resolved)


def _is_site_packages(path: Path) -> bool:
    return path.name in {
        "site-packages",
        "dist-packages",
    }


def _error_message(error: BaseException) -> str:
    message = str(error).strip()

    if message:
        return message

    return type(error).__name__
