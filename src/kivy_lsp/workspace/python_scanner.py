# src/kivy_lsp/workspace/python_scanner.py

from __future__ import annotations

import os
import tokenize
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from kivy_lsp.config import ServerConfig
from kivy_lsp.model.diagnostic import Diagnostic
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.workspace.document import TextDocument


@dataclass(frozen=True, slots=True)
class PythonFileDiagnostics:
    """Diagnostics produced while indexing one Python file."""

    path: Path
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True, slots=True)
class PythonScanError:
    """A file-system or decoding error encountered during scanning."""

    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class PythonScanResult:
    """The complete result of scanning project Python sources."""

    index: PythonIndex
    indexed_files: tuple[Path, ...]
    diagnostics: tuple[PythonFileDiagnostics, ...]
    errors: tuple[PythonScanError, ...]

    @property
    def succeeded(self) -> bool:
        return not self.errors


class PythonScanner:
    """Discover and statically index project Python source files."""

    def __init__(self, config: ServerConfig) -> None:
        self._config = config

    def scan(self) -> PythonScanResult:
        index = PythonIndex()
        discovered, discovery_errors = self._discover_files()
        selected = self._select_modules(discovered)

        indexed_files: list[Path] = []
        diagnostics: list[PythonFileDiagnostics] = []
        errors = list(discovery_errors)

        for module_name, path in sorted(selected.items()):
            try:
                source = self._read_source(path)
            except (OSError, SyntaxError, UnicodeError) as error:
                errors.append(
                    PythonScanError(
                        path=path,
                        message=_error_message(error),
                    )
                )
                continue

            document = TextDocument(
                uri=path.as_uri(),
                text=source,
            )
            result = index_python_module(
                document,
                module_name,
            )

            if result.diagnostics:
                diagnostics.append(
                    PythonFileDiagnostics(
                        path=path,
                        diagnostics=result.diagnostics,
                    )
                )

            if result.module is None:
                continue

            index.replace(result.module)
            indexed_files.append(path)

        return PythonScanResult(
            index=index,
            indexed_files=tuple(indexed_files),
            diagnostics=tuple(diagnostics),
            errors=tuple(errors),
        )

    def _discover_files(
        self,
    ) -> tuple[list[Path], list[PythonScanError]]:
        files: list[Path] = []
        errors: list[PythonScanError] = []
        seen: set[Path] = set()

        for configured_root in self._config.source_roots:
            root = configured_root.resolve()

            if not root.exists():
                errors.append(
                    PythonScanError(
                        path=root,
                        message="Python source root does not exist.",
                    )
                )
                continue

            if not root.is_dir():
                errors.append(
                    PythonScanError(
                        path=root,
                        message="Python source root is not a directory.",
                    )
                )
                continue

            for path in self._walk_source_root(root):
                resolved = path.resolve()

                if resolved in seen:
                    continue

                seen.add(resolved)
                files.append(resolved)

        files.sort()
        return files, errors

    def _walk_source_root(
        self,
        root: Path,
    ) -> list[Path]:
        files: list[Path] = []

        for directory, directory_names, filenames in os.walk(str(root)):
            current = Path(directory)

            directory_names[:] = [
                name
                for name in sorted(directory_names)
                if not self._is_excluded(current / name, root)
            ]

            for filename in sorted(filenames):
                path = current / filename

                if path.suffix not in {".py", ".pyi"}:
                    continue

                if self._is_excluded(path, root):
                    continue

                files.append(path)

        return files

    def _select_modules(
        self,
        files: list[Path],
    ) -> dict[str, Path]:
        selected: dict[str, Path] = {}

        for path in files:
            module_name = self._config.module_name_for(path)

            if module_name is None:
                continue

            current = selected.get(module_name)

            if current is None:
                selected[module_name] = path
                continue

            if current.suffix == ".py" and path.suffix == ".pyi":
                selected[module_name] = path

        return selected

    def _is_excluded(
        self,
        path: Path,
        root: Path,
    ) -> bool:
        try:
            relative = path.relative_to(root)
        except ValueError:
            return True

        relative_text = relative.as_posix()

        for pattern in self._config.excludes:
            normalized = pattern.replace("\\", "/").strip("/")

            if not normalized:
                continue

            if "/" not in normalized and any(
                fnmatchcase(part, normalized)
                for part in relative.parts
            ):
                return True

            if fnmatchcase(relative_text, normalized):
                return True

            if fnmatchcase(f"{relative_text}/", normalized):
                return True

        return False

    @staticmethod
    def _read_source(path: Path) -> str:
        with tokenize.open(str(path)) as source_file:
            return source_file.read()


def _error_message(error: BaseException) -> str:
    message = str(error).strip()

    if message:
        return message

    return type(error).__name__
