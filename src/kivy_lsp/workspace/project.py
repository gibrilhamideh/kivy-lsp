"""Project snapshots, indexes, include dependencies, and editor overlays."""

from __future__ import annotations

import tokenize
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from kivy_lsp.analysis.diagnostics import KvDiagnosticAnalyzer
from kivy_lsp.analysis.i18n import TranslationDiagnosticAnalyzer
from kivy_lsp.analysis.scope import KvSemanticModel
from kivy_lsp.analysis.scope_builder import build_kv_semantic_model
from kivy_lsp.config import CONFIG_FILENAME, ServerConfig, load_config
from kivy_lsp.i18n.index import TranslationIndex
from kivy_lsp.kv.index import KvIndex
from kivy_lsp.kv.nodes import DirectiveNode
from kivy_lsp.kv.parser import ParseResult, parse
from kivy_lsp.model.diagnostic import Diagnostic, DiagnosticSeverity
from kivy_lsp.model.span import Span
from kivy_lsp.model.uri import file_uri_to_path
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
from kivy_lsp.workspace.document import PositionEncoding, TextDocument
from kivy_lsp.workspace.includes import IncludeReference, resolve_includes
from kivy_lsp.workspace.kv_scanner import KvScanner
from kivy_lsp.workspace.library_config import LibraryConfigResolver
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
    """A document update and every URI needing diagnostic publication."""

    document: TextDocument
    diagnostics: tuple[Diagnostic, ...]
    affected_uris: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceRefresh:
    """A project refresh's affected open documents."""

    affected_uris: tuple[str, ...]


class ProjectWorkspace:
    """Own source snapshots and rebuild shared consumers consistently.

    Invalid Python overlays retain the last valid module for editor recovery.
    Closing restores the saved source; an invalid or deleted saved module is
    removed. Every KV update is parsed once, then shared by all consumers.
    """

    def __init__(self, config: ServerConfig) -> None:
        self._base_config = config
        self._config = config
        self.position_encoding = PositionEncoding.UTF16
        self._python_index = PythonIndex()
        self._kv_index = KvIndex()
        self._environment: PythonEnvironment | None = None
        self._dependency_scan: DependencyScanResult | None = None
        self._dependency_signature: tuple[str, ...] = ()
        self._dependency_packages: dict[str, DependencyScanResult] = {}
        self._dependency_modules: dict[str, Path] = {}
        self._dependency_index_revision = -1
        self._library_config_resolver: LibraryConfigResolver | None = None
        self._library_config_paths: tuple[Path, ...] = ()
        self._library_config_issues: tuple[PythonScanError, ...] = ()
        self._configuration_affected: set[str] = set()
        self._project_modules: set[str] = set()
        self._documents: dict[str, TextDocument] = {}
        self._kv_sources: dict[str, TextDocument] = {}
        self._kv_roots: set[str] = set()
        self._kv_results: dict[str, ParseResult] = {}
        self._kv_semantics: dict[str, KvSemanticModel] = {}
        self._kv_semantic_diagnostics: dict[str, tuple[Diagnostic, ...]] = {}
        self._python_diagnostics: dict[str, tuple[Diagnostic, ...]] = {}
        self._includes: dict[str, tuple[IncludeReference, ...]] = {}
        self._include_diagnostics: dict[str, tuple[Diagnostic, ...]] = {}
        self._scan_errors: tuple[PythonScanError, ...] = ()
        self._kv_scan_errors: dict[Path, PythonScanError] = {}
        self._initialized = False
        self._configure_services()

    @classmethod
    def from_root(cls, project_root: Path) -> ProjectWorkspace:
        return cls(load_config(project_root))

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
        return self._scan_errors + self._library_config_issues + tuple(
            self._kv_scan_errors[path] for path in sorted(self._kv_scan_errors)
        )

    @property
    def library_config_paths(self) -> tuple[Path, ...]:
        """Active package metadata paths, including missing watch targets."""
        return self._library_config_paths

    @property
    def library_config_issues(self) -> tuple[PythonScanError, ...]:
        return self._library_config_issues

    @property
    def configuration_uris(self) -> tuple[str, ...]:
        """Configuration sources needing diagnostic publication/clearing."""
        return tuple(sorted(self._configuration_affected | {
            issue.path.as_uri() for issue in self._library_config_issues
        }))

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def open_documents(self) -> tuple[TextDocument, ...]:
        return tuple(self._documents.values())

    def kv_documents(self) -> tuple[TextDocument, ...]:
        """All indexed KV sources, with unsaved overlays authoritative."""
        return tuple(self._kv_sources[uri] for uri in sorted(self._kv_sources))

    def initialize(self) -> WorkspaceInitializationResult:
        """Index saved sources, overlay editor snapshots, then dependencies."""
        self._config = self._base_config
        previous_index = self._python_index
        result = PythonScanner(self._config).scan()
        self._python_index = result.index
        self._project_modules = {
            module.name for module in result.index.modules
        }
        self._scan_errors = result.errors
        self._kv_scan_errors.clear()
        self._python_diagnostics = {
            item.path.as_uri(): item.diagnostics for item in result.diagnostics
        }
        self._kv_index = KvIndex()
        self._kv_sources.clear()
        self._kv_results.clear()
        self._configure_services()
        self._kv_roots = {
            path.as_uri()
            for path in self._kv_scanner.paths(on_error=self._kv_read_error)
        }
        for uri in sorted(self._kv_roots):
            document = self.source_document(uri)
            if document is not None:
                self._index_kv(document)
            else:
                path = _path_from_uri(uri)
                if path is not None and path not in self._kv_scan_errors:
                    self._kv_read_error(
                        path, "Cannot read the discovered KV source."
                    )
        for document in self._documents.values():
            suffix = _uri_suffix(document.uri)
            if suffix == ".kv" and document.uri not in self._kv_sources:
                self._index_kv(document)
            elif suffix in {".py", ".pyi"}:
                path = _path_from_uri(document.uri)
                name = self._config.module_name_for(path) if path else None
                old = previous_index.module_named(name) if name else None
                if old is not None and old.uri == document.uri:
                    self._python_index.replace(old)
                    self._project_modules.add(old.name)
                self._index_python(document)
        self._sync_includes()
        self._refresh_translation()
        self._environment = discover_python_environment(self._config)
        self._library_config_resolver = LibraryConfigResolver(
            self._environment,
        )
        self._refresh_dependencies(force=True)
        self._initialized = True
        self._rebuild_open_kv_semantics()
        assert self._dependency_scan is not None
        return WorkspaceInitializationResult(
            result, self._environment, self._dependency_scan
        )

    def reload_project(self) -> WorkspaceRefresh:
        """Reload persisted configuration and indexes, keeping editor text."""
        config = load_config(self._config.project_root)
        self._base_config = config
        self._config = config
        self.initialize()
        return WorkspaceRefresh(self._affected_uris())

    def document(self, uri: str) -> TextDocument | None:
        return self._documents.get(uri)

    def source_document(self, uri: str) -> TextDocument | None:
        """Read an authoritative open snapshot or a saved source."""
        if uri in self._documents:
            return self._documents[uri]
        if uri in self._kv_sources:
            return self._kv_sources[uri]
        return self._read_document(uri)

    def _read_document(self, uri: str) -> TextDocument | None:
        path = _path_from_uri(uri)
        if path is None or not path.is_file():
            if path is not None:
                self._kv_scan_errors.pop(path, None)
            return None
        try:
            if path.suffix.lower() in {".py", ".pyi"}:
                with tokenize.open(str(path)) as source_file:
                    source = source_file.read()
            else:
                with path.open(encoding="utf-8", newline="") as source_file:
                    source = source_file.read()
        except (OSError, SyntaxError, UnicodeError) as error:
            if path.suffix.lower() == ".kv":
                self._kv_read_error(path, str(error))
            return None
        self._kv_scan_errors.pop(path, None)
        return TextDocument(
            uri=uri, text=source, position_encoding=self.position_encoding
        )

    def _kv_read_error(self, path: Path, message: str) -> None:
        self._kv_scan_errors[path] = PythonScanError(path, message)

    def kv_result(self, uri: str) -> ParseResult | None:
        return self._kv_results.get(uri)

    def semantic_model(self, uri: str) -> KvSemanticModel | None:
        if uri not in self._kv_semantics:
            document = self._kv_sources.get(uri)
            parsed = self._kv_results.get(uri)
            if document is not None and parsed is not None:
                self._analyze_kv(document, parsed)
        return self._kv_semantics.get(uri)

    def diagnostics_for(self, uri: str) -> tuple[Diagnostic, ...]:
        parsed = self._kv_results.get(uri)
        if parsed is not None:
            self.semantic_model(uri)
        return (
            (parsed.diagnostics if parsed is not None else ())
            + self._kv_semantic_diagnostics.get(uri, ())
            + self._include_diagnostics.get(uri, ())
            + self._python_diagnostics.get(uri, ())
            + tuple(
                Diagnostic(
                    message=issue.message,
                    span=Span.empty(0),
                    severity=DiagnosticSeverity.ERROR,
                    code="library-config",
                )
                for issue in self._library_config_issues
                if issue.path.as_uri() == uri
            )
        )

    def include_references(self, uri: str) -> tuple[IncludeReference, ...]:
        return self._includes.get(uri, ())

    def include_locations(
        self, uri: str, offset: int
    ) -> tuple[tuple[str, Span], ...]:
        """Return included-file definition targets at a directive offset."""
        return tuple(
            (item.target_uri, Span.empty(0))
            for item in self._includes.get(uri, ())
            if item.target_uri is not None
            and item.span.start <= offset < item.span.end
        )

    def open_document(
        self, uri: str, text: str, version: int | None = None
    ) -> WorkspaceUpdate:
        return self.update_document(uri, text, version)

    def update_document(
        self, uri: str, text: str, version: int | None = None
    ) -> WorkspaceUpdate:
        previous = self._documents.get(uri)
        if (
            previous is not None
            and previous.version is not None
            and version is not None
            and version < previous.version
        ):
            return WorkspaceUpdate(previous, self.diagnostics_for(uri))
        document = TextDocument(
            uri=uri,
            text=text,
            version=version,
            position_encoding=self.position_encoding,
        )
        self._documents[uri] = document
        suffix = _uri_suffix(uri)
        local_kv_edit = False
        if suffix == ".kv":
            old_symbols = self._kv_index.symbols()
            python_revision = self._python_index.revision
            old_config = self._config
            self._index_kv(document)
            self._sync_includes()
            self._refresh_dependencies()
            local_kv_edit = (
                old_symbols == self._kv_index.symbols()
                and python_revision == self._python_index.revision
                and old_config == self._config
            )
        elif suffix in {".py", ".pyi"}:
            self._index_python(document)
            self._refresh_dependencies()
        elif uri == self._translation_index.source_uri:
            self._refresh_translation()
        if local_kv_edit:
            self._analyze_kv(document, self._kv_results[uri])
        else:
            self._rebuild_open_kv_semantics()
        return WorkspaceUpdate(
            document, self.diagnostics_for(uri), self._affected_uris(uri)
        )

    def close_document(self, uri: str) -> WorkspaceRefresh:
        """Restore saved state and include the closed URI to clear errors."""
        if self._documents.pop(uri, None) is None:
            return WorkspaceRefresh((uri,))
        suffix = _uri_suffix(uri)
        if suffix == ".kv":
            self._restore_kv(uri)
            self._sync_includes()
        elif suffix in {".py", ".pyi"}:
            self._restore_python(uri)
        elif uri == self._translation_index.source_uri:
            self._refresh_translation()
        self._refresh_dependencies()
        self._rebuild_open_kv_semantics()
        return WorkspaceRefresh(self._affected_uris(uri))

    def refresh_files(self, uris: Iterable[str]) -> WorkspaceRefresh:
        """Handle saved create/change/delete events without replacing overlays.

        Renames are a batch containing the old and new URI. Repeated watched
        notifications are harmless; unchanged KV snapshots are not reparsed.
        """
        changed = set(uris)
        config_uri = (self._config.project_root / CONFIG_FILENAME).as_uri()
        if config_uri in changed:
            return self.reload_project()
        if self._library_config_resolver is not None and any(
            (path := _path_from_uri(uri)) is not None
            and path.name == CONFIG_FILENAME
            for uri in changed
        ):
            self._library_config_resolver.clear()
        source_uris = set(self._kv_sources) | {
            module.uri for module in self._python_index.modules
        }
        if any(
            _uri_suffix(uri) not in {".py", ".pyi", ".kv", ".json", ".toml"}
            and (
                (path := _path_from_uri(uri)) is not None
                and path.is_dir()
                or any(
                    item.startswith(uri.rstrip("/") + "/")
                    for item in source_uris
                )
            )
            for uri in changed
        ):
            self.initialize()
            return WorkspaceRefresh(self._affected_uris(*sorted(changed)))
        self._kv_roots = {path.as_uri() for path in self._kv_scanner.paths()}
        for uri in sorted(changed):
            if uri in self._documents:
                continue
            suffix = _uri_suffix(uri)
            if suffix == ".kv":
                if uri in self._kv_roots or uri in self._kv_sources:
                    self._restore_kv(uri)
            elif suffix in {".py", ".pyi"}:
                self._restore_python(uri)
        self._sync_includes()
        self._refresh_translation()
        self._refresh_dependencies()
        self._rebuild_open_kv_semantics()
        return WorkspaceRefresh(self._affected_uris(*sorted(changed)))

    def status(self) -> dict[str, object]:
        """Return serializable indexing and environment status."""
        environment = self._environment
        dependencies = self._dependency_scan
        return {
            "projectRoot": str(self._config.project_root),
            "sourceRoots": [str(path) for path in self._config.source_roots],
            "kvPaths": [str(path) for path in self._config.kv_paths],
            "environment": (
                str(environment.virtual_environment)
                if environment and environment.virtual_environment
                else None
            ),
            "pythonInterpreter": (
                str(self._config.python_interpreter)
                if self._config.python_interpreter
                else None
            ),
            "configuredEnvironment": (
                str(self._config.python_environment)
                if self._config.python_environment
                else None
            ),
            "pythonVersion": environment.version if environment else None,
            "pythonModules": len(self._python_index.modules),
            "kvDocuments": len(self._kv_sources),
            "openDocuments": len(self._documents),
            "libraryConfigs": [
                str(path) for path in self.library_config_paths
            ],
            "loadedStubs": [
                str(path)
                for path in dependencies.indexed_files
                if path.suffix == ".pyi"
            ]
            if dependencies
            else [],
            "issues": [
                f"{item.path}: {item.message}" for item in self.scan_errors
            ]
            + (
                [f"{item.path}: {item.message}" for item in environment.issues]
                if environment
                else []
            )
            + (
                [
                    f"{item.package_name}: {item.message}"
                    for item in dependencies.package_issues
                ]
                + [
                    f"{item.path}: {item.message}"
                    for item in dependencies.file_errors
                ]
                + [
                    f"{item.path}: {diagnostic.message}"
                    for item in dependencies.diagnostics
                    for diagnostic in item.diagnostics
                ]
                if dependencies
                else []
            ),
        }

    def _configure_services(self) -> None:
        self._kv_scanner = KvScanner(
            self._config.kv_paths, self._config.excludes
        )
        self._translation_index = TranslationIndex(self._config.i18n)
        self._translation_diagnostic_analyzer = (
            TranslationDiagnosticAnalyzer(
                self._translation_index, self._config.i18n
            )
            if self._config.i18n is not None
            else None
        )
        self._kv_diagnostic_analyzer = KvDiagnosticAnalyzer(
            self._python_index, self._config, self._kv_index
        )

    def _index_kv(self, document: TextDocument) -> None:
        old = self._kv_sources.get(document.uri)
        self._kv_sources[document.uri] = document
        if old is not None and old.text == document.text:
            return
        parsed = parse(document.text)
        self._kv_results[document.uri] = parsed
        self._kv_index.replace(
            document.uri, self._kv_scanner.scan_result(document.uri, parsed)
        )

    def _index_python(self, document: TextDocument) -> None:
        path = _path_from_uri(document.uri)
        name = self._config.module_name_for(path) if path else None
        if name is None:
            return
        result = index_python_module(document, name)
        self._python_diagnostics[document.uri] = result.diagnostics
        if result.module is not None:
            self._python_index.replace(result.module)
            self._project_modules.add(name)

    def _restore_python(self, uri: str) -> None:
        path = _path_from_uri(uri)
        name = self._config.module_name_for(path) if path else None
        if name is None:
            return
        selected = PythonScanner(self._config).module_paths().get(name)
        for overlay in self._documents.values():
            overlay_path = _path_from_uri(overlay.uri)
            if (
                overlay_path
                and self._config.module_name_for(overlay_path) == name
            ):
                self._index_python(overlay)
                return
        self._python_index.remove(name)
        self._project_modules.discard(name)
        self._python_diagnostics.pop(uri, None)
        document = self._read_document(selected.as_uri()) if selected else None
        if document is not None:
            self._index_python(document)

    def _restore_kv(self, uri: str) -> None:
        document = self._read_document(uri)
        if document is not None:
            self._index_kv(document)
        else:
            self._kv_sources.pop(uri, None)
            self._kv_results.pop(uri, None)
            self._kv_index.remove(uri)

    def _refresh_translation(self) -> None:
        uri = self._translation_index.source_uri
        document = self._documents.get(uri) if uri else None
        self._translation_index.set_overlay(
            document.text if document else None
        )
        self._translation_index.refresh(force=True)

    def _refresh_dependencies(self, *, force: bool = False) -> None:
        if self._environment is None:
            return
        packages = {"kivy"}
        references = {"kivy"}
        for name in self._project_modules:
            module = self._python_index.module_named(name)
            if module is None:
                continue
            for imported in module.imports:
                top = imported.target_module.partition(".")[0]
                if imported.relative_level:
                    continue
                references.add(imported.target)
                references.add(imported.target_module)
                if top.startswith("kivy"):
                    packages.add(top)
        for parsed in self._kv_results.values():
            for node in parsed.document.items:
                if isinstance(node, DirectiveNode) and node.name == "import":
                    parts = node.arguments.split()
                    if len(parts) == 2:
                        packages.add(parts[1].partition(".")[0])
                        references.add(parts[1])
        for imported in self._base_config.global_imports:
            packages.add(imported.target.partition(".")[0])
            references.add(imported.target)
        for target in self._base_config.globals.values():
            packages.add(target.partition(".")[0])
            references.add(target)
        references.update(
            name for name in self._base_config.member_projections
            if "." in name
        )
        references.update(
            name for name in self._base_config.subscript_projections
            if "." in name
        )
        packages.update(self._refresh_library_config(references))
        signature = tuple(sorted(packages))
        if force:
            # initialize() has already rebuilt the index from project files.
            self._dependency_packages.clear()
            self._dependency_modules.clear()
        self._remove_unused_dependencies(packages)
        restored = self._restore_missing_dependencies(packages)
        if (
            not force
            and not restored
            and signature == self._dependency_signature
            and self._python_index.revision == self._dependency_index_revision
        ):
            return
        for package in self._dependency_packages.keys() - packages:
            del self._dependency_packages[package]
        scanner = DependencyScanner(PythonModuleLocator(self._environment))
        for package in sorted(packages - self._dependency_packages.keys()):
            result = scanner.scan(
                self._python_index,
                packages=(package,),
                discover_imports=False,
            )
            self._dependency_packages[package] = result
            self._dependency_modules.update(
                zip(result.indexed_modules, result.indexed_files)
            )
        self._dependency_signature = signature
        self._dependency_scan = self._combined_dependency_scan(signature)
        self._dependency_index_revision = self._python_index.revision

    def _refresh_library_config(self, references: set[str]) -> tuple[str, ...]:
        resolver = self._library_config_resolver
        if resolver is None:
            return ()
        result = resolver.resolve(self._base_config, references)
        if result.issues != self._library_config_issues:
            self._configuration_affected.update(
                issue.path.as_uri()
                for issue in (*self._library_config_issues, *result.issues)
            )
        self._library_config_issues = result.issues
        self._library_config_paths = result.paths
        if result.config != self._config:
            self._config = result.config
            self._kv_diagnostic_analyzer = KvDiagnosticAnalyzer(
                self._python_index, self._config, self._kv_index,
            )
            self._kv_semantics.clear()
            self._kv_semantic_diagnostics.clear()
        return result.packages

    def _remove_unused_dependencies(self, packages: set[str]) -> None:
        for name, path in tuple(self._dependency_modules.items()):
            if name.partition(".")[0] in packages:
                continue
            module = self._python_index.module_named(name)
            if (
                module is None
                or module.uri != path.as_uri()
                or name in self._project_modules
            ):
                del self._dependency_modules[name]
            elif module.uri not in self._documents:
                self._python_index.remove(name)
                del self._dependency_modules[name]

    def _restore_missing_dependencies(self, packages: set[str]) -> bool:
        missing: dict[str, set[str]] = {}
        for name in self._dependency_modules:
            package = name.partition(".")[0]
            if (
                package in packages
                and package in self._dependency_packages
                and name not in self._project_modules
                and self._python_index.module_named(name) is None
            ):
                missing.setdefault(package, set()).add(name)
        if not missing:
            return False
        assert self._environment is not None
        scanner = DependencyScanner(PythonModuleLocator(self._environment))
        for package, names in missing.items():
            previous = self._dependency_packages[package]
            paths = {self._dependency_modules.pop(name) for name in names}
            result = scanner.scan_modules(self._python_index, names)
            retained = [
                (name, path)
                for name, path in zip(
                    previous.indexed_modules, previous.indexed_files
                )
                if name not in names
            ]
            self._dependency_modules.update(
                zip(result.indexed_modules, result.indexed_files)
            )
            self._dependency_packages[package] = DependencyScanResult(
                packages=previous.packages,
                indexed_modules=tuple(name for name, _ in retained)
                + result.indexed_modules,
                indexed_files=tuple(path for _, path in retained)
                + result.indexed_files,
                diagnostics=tuple(
                    item
                    for item in previous.diagnostics
                    if item.path not in paths
                )
                + result.diagnostics,
                file_errors=tuple(
                    item
                    for item in previous.file_errors
                    if item.path not in paths
                )
                + result.file_errors,
                package_issues=previous.package_issues + result.package_issues,
            )
        return True

    def _combined_dependency_scan(
        self, packages: tuple[str, ...]
    ) -> DependencyScanResult:
        scans = [self._dependency_packages[name] for name in packages]
        indexed = [
            (name, path)
            for name, path in sorted(self._dependency_modules.items())
            if name.partition(".")[0] in packages
            and name not in self._project_modules
            and (module := self._python_index.module_named(name)) is not None
            and module.uri == path.as_uri()
        ]
        return DependencyScanResult(
            packages=packages,
            indexed_modules=tuple(name for name, _ in indexed),
            indexed_files=tuple(path for _, path in indexed),
            diagnostics=tuple(
                item for scan in scans for item in scan.diagnostics
            ),
            file_errors=tuple(
                item for scan in scans for item in scan.file_errors
            ),
            package_issues=tuple(
                item for scan in scans for item in scan.package_issues
            ),
        )

    def _sync_includes(self) -> None:
        references: dict[str, tuple[IncludeReference, ...]] = {}
        diagnostics: dict[str, list[Diagnostic]] = {}
        visited: set[str] = set()
        active: set[str] = set()
        roots = self._kv_roots | {
            uri for uri in self._documents if _uri_suffix(uri) == ".kv"
        }

        def visit(uri: str) -> None:
            if uri in visited:
                return
            document = self._kv_sources.get(uri)
            if document is None:
                document = self.source_document(uri)
                if document is None:
                    return
                self._index_kv(document)
            visited.add(uri)
            active.add(uri)
            path = _path_from_uri(uri)
            if path is None:
                active.remove(uri)
                return
            refs = resolve_includes(
                self._kv_results[uri],
                path,
                self._config.project_root,
                set(self._documents),
            )
            references[uri] = refs
            diagnostics[uri] = []
            for reference in refs:
                if reference.diagnostic is not None:
                    diagnostics[uri].append(reference.diagnostic)
                target = reference.target_uri
                if target is None:
                    continue
                if target in active:
                    diagnostics[uri].append(
                        Diagnostic(
                            message=(
                                f"Include cycle through {reference.path!r}."
                            ),
                            span=reference.span,
                            severity=DiagnosticSeverity.WARNING,
                            code="kv-include-cycle",
                        )
                    )
                    continue
                visit(target)
                if target not in visited:
                    diagnostics[uri].append(
                        Diagnostic(
                            message=f"Cannot read include {reference.path!r}.",
                            span=reference.span,
                            severity=DiagnosticSeverity.WARNING,
                            code="kv-include-unreadable",
                        )
                    )
            active.remove(uri)

        for uri in sorted(roots):
            visit(uri)
        for uri in set(self._kv_sources) - visited:
            self._kv_sources.pop(uri, None)
            self._kv_results.pop(uri, None)
            self._kv_index.remove(uri)
        self._includes = references
        self._include_diagnostics = {
            uri: tuple(items) for uri, items in diagnostics.items()
        }

    def _rebuild_open_kv_semantics(self) -> None:
        self._kv_semantics.clear()
        self._kv_semantic_diagnostics.clear()
        for uri, document in self._documents.items():
            parsed = self._kv_results.get(uri)
            if parsed is not None:
                self._analyze_kv(document, parsed)

    def _analyze_kv(self, document: TextDocument, parsed: ParseResult) -> None:
        semantics = build_kv_semantic_model(
            document, parsed, self._python_index, self._config, self._kv_index
        )
        self._kv_semantics[document.uri] = semantics
        diagnostics = self._kv_diagnostic_analyzer.analyze(
            document, parsed, semantics
        )
        translations = (
            self._translation_diagnostic_analyzer.analyze(document, parsed)
            if self._translation_diagnostic_analyzer
            else ()
        )
        self._kv_semantic_diagnostics[document.uri] = (
            semantics.diagnostics + diagnostics + translations
        )

    def _affected_uris(self, *extra: str) -> tuple[str, ...]:
        uris = set(self._documents).union(extra, self.configuration_uris)
        self._configuration_affected.clear()
        return tuple(sorted(uris))


def _path_from_uri(uri: str) -> Path | None:
    return file_uri_to_path(uri)


def _uri_suffix(uri: str) -> str:
    path = _path_from_uri(uri)
    return path.suffix.lower() if path is not None else ""
