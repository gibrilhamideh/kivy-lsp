"""Static candidates for the dotted target of a KV import directive."""

from __future__ import annotations

import keyword
import tokenize
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kivy_lsp.config import ServerConfig
from kivy_lsp.model.symbol import Symbol, SymbolKind
from kivy_lsp.model.uri import file_uri_to_path
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.python.locator import PythonModuleLocator
from kivy_lsp.python.module import ImportBinding, PythonModule
from kivy_lsp.workspace.discovery import is_excluded
from kivy_lsp.workspace.document import TextDocument

_MAX_SOURCE_READS = 32
_MAX_REEXPORT_DEPTH = 8


@dataclass(frozen=True, slots=True)
class ImportCandidate:
    """One immediate module or exported name at an import target."""

    name: str
    qualified_name: str
    symbol: Symbol | None = None


class PythonImportCompleter:
    """Combine indexed overlays with shallow environment discovery."""

    def __init__(
        self,
        index: PythonIndex,
        locator: PythonModuleLocator | None = None,
        source_document: Callable[[str], TextDocument | None] | None = None,
        config: ServerConfig | None = None,
    ) -> None:
        self._index = index
        self._locator = locator
        self._source_document = source_document
        self._config = config
        self._modules: dict[str, PythonModule | None] = {}

    def candidates(
        self, parent: str, prefix: str
    ) -> tuple[ImportCandidate, ...]:
        """Return immediate children without changing the shared index."""
        if parent and not all(_identifier(part) for part in parent.split(".")):
            return ()
        if prefix and not prefix.isidentifier():
            return ()

        # Requests never retain stale files or snapshots from earlier edits.
        self._modules.clear()
        selected: dict[str, ImportCandidate] = {}
        start = f"{parent}." if parent else ""

        for module in self._index.modules:
            if not module.name.startswith(start):
                continue
            name = module.name[len(start) :].partition(".")[0]
            if name in selected or not _matches(name, prefix):
                continue
            if self._allowed_uri(module.uri):
                selected[name] = ImportCandidate(name, start + name)

        if self._locator is not None:
            for qualified_name in self._locator.module_children(
                parent, prefix=prefix, allow_path=self._allowed_path
            ):
                name = qualified_name.rpartition(".")[2]
                if _matches(name, prefix):
                    selected[name] = ImportCandidate(name, qualified_name)

        module = self._module(parent) if parent else None
        if module is not None:
            for binding in module.imports:
                name = binding.local_name
                if not _matches(name, prefix):
                    continue
                symbol = self._binding_symbol(module, binding, ())
                selected[name] = ImportCandidate(name, start + name, symbol)
            symbols = (
                *(item.symbol for item in module.symbol.classes),
                *module.symbol.symbols,
            )
            for symbol in symbols:
                if _matches(symbol.name, prefix):
                    selected[symbol.name] = ImportCandidate(
                        symbol.name, start + symbol.name, symbol
                    )

        return tuple(selected[name] for name in sorted(selected))

    def _module(self, name: str) -> PythonModule | None:
        module = self._index.module_named(name)
        if module is not None:
            return module if self._allowed_uri(module.uri) else None
        if name in self._modules:
            return self._modules[name]
        if self._locator is None or len(self._modules) >= _MAX_SOURCE_READS:
            return None

        self._modules[name] = None
        located = self._locator.find_module(name)
        if located is None or not self._allowed_path(located.path):
            return None
        document = (
            self._source_document(located.uri)
            if self._source_document is not None
            else _read_document(located.path)
        )
        if document is not None:
            module = index_python_module(document, name).module
            self._modules[name] = module
        return module

    def _binding_symbol(
        self,
        module: PythonModule,
        binding: ImportBinding,
        visited: tuple[tuple[str, str], ...],
    ) -> Symbol | None:
        if binding.target_name is None:
            return None
        marker = (module.name, binding.local_name)
        fallback = Symbol(
            name=binding.local_name,
            qualified_name=f"{module.name}.{binding.local_name}",
            kind=SymbolKind.VARIABLE,
            location=binding.location,
        )
        if marker in visited or len(visited) >= _MAX_REEXPORT_DEPTH:
            return fallback

        target = _absolute_import_module(module, binding)
        if target is None:
            return fallback
        qualified_name = f"{target}.{binding.target_name}"
        symbol = self._index.resolve_symbol(qualified_name)
        if symbol is not None and self._allowed_uri(symbol.uri):
            return symbol

        imported = self._module(target)
        if imported is not None:
            symbol = imported.symbol.symbol_named(binding.target_name)
            if symbol is not None:
                return symbol
            exported = imported.import_named(binding.target_name)
            if exported is not None:
                return self._binding_symbol(
                    imported, exported, (*visited, marker)
                )

        if self._module(qualified_name) is not None:
            return None
        if self._locator is not None and self._locator.package_exists(
            qualified_name
        ):
            return None
        return fallback

    def _allowed_uri(self, uri: str) -> bool:
        path = file_uri_to_path(uri)
        return path is None or self._allowed_path(path)

    def _allowed_path(self, path: Path) -> bool:
        config = self._config
        if config is None:
            return True
        if self._locator is not None and any(
            path.is_relative_to(root)
            for root in self._locator.environment.site_packages
        ):
            return True
        return not any(
            is_excluded(path, root, config.excludes)
            for root in config.source_roots
        )


def _identifier(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name)


def _matches(name: str, prefix: str) -> bool:
    return (
        _identifier(name)
        and name.startswith(prefix)
        and (not name.startswith("_") or prefix.startswith("_"))
    )


def _read_document(path: Path) -> TextDocument | None:
    try:
        with tokenize.open(str(path)) as stream:
            return TextDocument(path.as_uri(), stream.read())
    except (OSError, SyntaxError, UnicodeError):
        return None


def _absolute_import_module(
    module: PythonModule, binding: ImportBinding
) -> str | None:
    if binding.relative_level == 0:
        return binding.target_module
    path = file_uri_to_path(module.uri)
    parts = module.name.split(".")
    if path is None or path.stem != "__init__":
        parts.pop()
    levels = binding.relative_level - 1
    if levels >= len(parts):
        return None
    if levels:
        parts = parts[:-levels]
    if binding.target_module:
        parts.extend(binding.target_module.split("."))
    return ".".join(parts)
