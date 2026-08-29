# src/kivy_lsp/workspace/project.py

from __future__ import annotations

import tokenize
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from kivy_lsp.analysis.diagnostics import KvDiagnosticAnalyzer
from kivy_lsp.analysis.i18n import TranslationDiagnosticAnalyzer
from kivy_lsp.analysis.scope import KvSemanticModel
from kivy_lsp.analysis.scope_builder import (
    build_kv_semantic_model,
)
from kivy_lsp.config import ServerConfig, load_config
from kivy_lsp.i18n.index import TranslationIndex
from kivy_lsp.kv.index import KvIndex
from kivy_lsp.kv.parser import ParseResult, parse
from kivy_lsp.model.diagnostic import Diagnostic
from kivy_lsp.python.environment import (
    PythonEnvironment,
    discover_python_environment,
)
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.python.locator import PythonModuleLocator
from kivy_lsp.workspace.dependency_scanner import (
    DependencyScanner,
    DependencyScanResult,
)
from kivy_lsp.workspace.document import TextDocument
from kivy_lsp.workspace.kv_scanner import KvScanner
from kivy_lsp.workspace.python_scanner import (
    PythonScanError,
    PythonScanner,
    PythonScanResult,
)


@dataclass(frozen=True, slots=True)
class WorkspaceInitializationResult:
    """The complete result of initializing a project workspace."""

    project: PythonScanResult
    environment: PythonEnvironment
    dependencies: DependencyScanResult


@dataclass(frozen=True, slots=True)
class WorkspaceUpdate:
    """The result of adding or changing an editor document."""

    document: TextDocument
    diagnostics: tuple[Diagnostic, ...]


class ProjectWorkspace:
    """Own project documents, parse trees, indexes, and semantics."""

    def __init__(self, config: ServerConfig) -> None:
        self._config = config
        self._python_index = PythonIndex()
        self._kv_index = KvIndex()
        self._kv_scanner = KvScanner(
            self._config.kv_paths,
        )
        self._kv_diagnostic_analyzer = KvDiagnosticAnalyzer(
            self._python_index,
            self._config,
        )
        self._translation_index = TranslationIndex(
            self._config.i18n,
        )
        self._translation_diagnostic_analyzer = (
            TranslationDiagnosticAnalyzer(
                self._translation_index,
                self._config.i18n,
            )
            if self._config.i18n is not None
            else None
        )

        self._environment: PythonEnvironment | None = None
        self._dependency_scan: DependencyScanResult | None = None

        self._documents: dict[str, TextDocument] = {}
        self._kv_results: dict[str, ParseResult] = {}
        self._kv_semantics: dict[str, KvSemanticModel] = {}

        self._kv_syntax_diagnostics: dict[
            str,
            tuple[Diagnostic, ...],
        ] = {}
        self._kv_semantic_diagnostics: dict[
            str,
            tuple[Diagnostic, ...],
        ] = {}
        self._python_diagnostics: dict[
            str,
            tuple[Diagnostic, ...],
        ] = {}

        self._scan_errors: tuple[PythonScanError, ...] = ()
        self._initialized = False

    @classmethod
    def from_root(cls, project_root: Path) -> ProjectWorkspace:
        config = load_config(project_root)
        return cls(config)

    @property
    def config(self) -> ServerConfig:
        return self._config

    @property
    def python_index(self) -> PythonIndex:
        return self._python_index

    @property
    def kv_index(self) -> KvIndex:
        return self._kv_index

    @property
    def translation_index(self) -> TranslationIndex:
        return self._translation_index

    @property
    def environment(self) -> PythonEnvironment | None:
        return self._environment

    @property
    def dependency_scan(self) -> DependencyScanResult | None:
        return self._dependency_scan

    @property
    def scan_errors(self) -> tuple[PythonScanError, ...]:
        return self._scan_errors

    @property
    def initialized(self) -> bool:
        return self._initialized

    def initialize(self) -> WorkspaceInitializationResult:
        """Index the project and its Kivy ecosystem dependencies."""

        project_result = PythonScanner(
            self._config,
        ).scan()
        self._python_index = project_result.index
        self._scan_errors = project_result.errors
        self._python_diagnostics = {
            item.path.as_uri(): item.diagnostics
            for item in project_result.diagnostics
        }

        self._kv_index = self._kv_scanner.scan()
        self._translation_index.refresh(force=True)

        environment = discover_python_environment(
            self._config,
        )
        locator = PythonModuleLocator(environment)
        dependency_result = DependencyScanner(
            locator,
        ).scan(self._python_index)

        self._kv_diagnostic_analyzer = KvDiagnosticAnalyzer(
            self._python_index,
            self._config,
        )
        self._environment = environment
        self._dependency_scan = dependency_result
        self._initialized = True

        self._rebuild_open_kv_semantics()

        return WorkspaceInitializationResult(
            project=project_result,
            environment=environment,
            dependencies=dependency_result,
        )

    def document(self, uri: str) -> TextDocument | None:
        return self._documents.get(uri)

    def source_document(self, uri: str) -> TextDocument | None:
        """Load an open or saved document used by a source location."""
        document = self.document(uri)

        if document is not None:
            return document

        path = _path_from_uri(uri)

        if path is None or not path.is_file():
            return None

        try:
            if path.suffix.lower() in {".py", ".pyi"}:
                with tokenize.open(str(path)) as source_file:
                    text = source_file.read()
            else:
                text = path.read_text(encoding="utf-8")
        except (OSError, SyntaxError, UnicodeError):
            return None

        return TextDocument(
            uri=uri,
            text=text,
        )

    def kv_result(self, uri: str) -> ParseResult | None:
        return self._kv_results.get(uri)

    def semantic_model(
        self,
        uri: str,
    ) -> KvSemanticModel | None:
        return self._kv_semantics.get(uri)

    def diagnostics_for(
        self,
        uri: str,
    ) -> tuple[Diagnostic, ...]:
        syntax_diagnostics = self._kv_syntax_diagnostics.get(
            uri,
            (),
        )
        semantic_diagnostics = self._kv_semantic_diagnostics.get(
            uri,
            (),
        )
        python_diagnostics = self._python_diagnostics.get(
            uri,
            (),
        )

        return (
            syntax_diagnostics
            + semantic_diagnostics
            + python_diagnostics
        )

    def open_document(
        self,
        uri: str,
        text: str,
        version: int | None = None,
    ) -> WorkspaceUpdate:
        return self.update_document(
            uri,
            text,
            version,
        )

    def update_document(
        self,
        uri: str,
        text: str,
        version: int | None = None,
    ) -> WorkspaceUpdate:
        """Store and analyze the newest editor document snapshot."""

        document = TextDocument(
            uri=uri,
            text=text,
            version=version,
        )
        self._documents[uri] = document

        suffix = _uri_suffix(uri)

        if suffix == ".kv":
            self._update_kv_document(document)
        elif suffix in {".py", ".pyi"}:
            self._update_python_document(document)

        return WorkspaceUpdate(
            document=document,
            diagnostics=self.diagnostics_for(uri),
        )

    def close_document(self, uri: str) -> None:
        """Remove an editor overlay and restore saved project state."""

        document = self._documents.pop(uri, None)

        if document is None:
            return

        suffix = _uri_suffix(uri)

        if suffix == ".kv":
            self._remove_kv_document(uri)
            return

        if suffix not in {".py", ".pyi"}:
            return

        path = _path_from_uri(uri)

        if path is None:
            return

        self._restore_python_file(path)
        self._rebuild_open_kv_semantics()

    def _update_kv_document(
        self,
        document: TextDocument,
    ) -> None:
        self._kv_index.replace(
            document.uri,
            self._kv_scanner.scan_text(
                document.uri,
                document.text,
            ),
        )

        parse_result = parse(document.text)
        semantic_model = build_kv_semantic_model(
            document,
            parse_result,
            self._python_index,
            self._config,
        )

        self._kv_results[document.uri] = parse_result
        self._kv_semantics[document.uri] = semantic_model
        self._kv_syntax_diagnostics[
            document.uri
        ] = parse_result.diagnostics
        self._kv_semantic_diagnostics[
            document.uri
        ] = self._analyze_kv_document(
            document,
            parse_result,
            semantic_model,
        )

    def _update_python_document(
        self,
        document: TextDocument,
    ) -> None:
        path = _path_from_uri(document.uri)

        if path is None:
            return

        module_name = self._config.module_name_for(path)

        if module_name is None:
            return

        result = index_python_module(
            document,
            module_name,
        )
        self._python_diagnostics[
            document.uri
        ] = result.diagnostics

        if result.module is not None:
            self._python_index.replace(result.module)

        self._rebuild_open_kv_semantics()

    def _restore_python_file(self, path: Path) -> None:
        uri = path.as_uri()
        module_name = self._config.module_name_for(path)

        if module_name is None:
            self._python_diagnostics.pop(uri, None)
            return

        if not path.is_file():
            self._python_index.remove(module_name)
            self._python_diagnostics.pop(uri, None)
            return

        try:
            with tokenize.open(str(path)) as source_file:
                text = source_file.read()
        except (OSError, SyntaxError, UnicodeError):
            self._python_index.remove(module_name)
            self._python_diagnostics.pop(uri, None)
            return

        document = TextDocument(
            uri=uri,
            text=text,
        )
        result = index_python_module(
            document,
            module_name,
        )
        self._python_diagnostics[
            uri
        ] = result.diagnostics

        if result.module is None:
            self._python_index.remove(module_name)
            return

        self._python_index.replace(result.module)

    def _rebuild_open_kv_semantics(self) -> None:
        for uri, parse_result in self._kv_results.items():
            document = self._documents.get(uri)

            if document is None:
                continue

            semantic_model = build_kv_semantic_model(
                document,
                parse_result,
                self._python_index,
                self._config,
            )
            self._kv_semantics[uri] = semantic_model
            self._kv_semantic_diagnostics[
                uri
            ] = self._analyze_kv_document(
                document,
                parse_result,
                semantic_model,
            )

    def _analyze_kv_document(
        self,
        document: TextDocument,
        parse_result: ParseResult,
        semantic_model: KvSemanticModel,
    ) -> tuple[Diagnostic, ...]:
        expression_diagnostics = (
            self._kv_diagnostic_analyzer.analyze(
                document,
                parse_result,
                semantic_model,
            )
        )
        translation_diagnostics = (
            self._translation_diagnostic_analyzer.analyze(
                document,
                parse_result,
            )
            if self._translation_diagnostic_analyzer is not None
            else ()
        )

        return (
            semantic_model.diagnostics
            + expression_diagnostics
            + translation_diagnostics
        )

    def _remove_kv_document(self, uri: str) -> None:
        self._kv_results.pop(uri, None)
        self._kv_semantics.pop(uri, None)
        self._kv_syntax_diagnostics.pop(uri, None)
        self._kv_semantic_diagnostics.pop(uri, None)

        path = _path_from_uri(uri)

        if path is None or not path.is_file():
            self._kv_index.remove(uri)
            return

        self._kv_index.replace(
            uri,
            self._kv_scanner.scan_path(path),
        )


def _path_from_uri(uri: str) -> Path | None:
    parsed = urlparse(uri)

    if parsed.scheme != "file":
        return None

    path = url2pathname(parsed.path)

    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"

    return Path(path).resolve()


def _uri_suffix(uri: str) -> str:
    path = _path_from_uri(uri)

    if path is None:
        return ""

    return path.suffix.lower()

