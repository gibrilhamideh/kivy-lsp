# src/kivy_lsp/features/documents.py

from collections.abc import Callable

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from kivy_lsp.model.diagnostic import Diagnostic
from kivy_lsp.workspace.document import TextDocument
from kivy_lsp.workspace.project import ProjectWorkspace

type WorkspaceProvider = Callable[[], ProjectWorkspace | None]


_DIAGNOSTIC_SEVERITIES: dict[
    str,
    types.DiagnosticSeverity,
] = {
    "ERROR": types.DiagnosticSeverity.Error,
    "WARNING": types.DiagnosticSeverity.Warning,
    "INFORMATION": types.DiagnosticSeverity.Information,
    "HINT": types.DiagnosticSeverity.Hint,
}


def register_document_sync(
    server: LanguageServer,
    workspace_provider: WorkspaceProvider,
) -> None:
    """Register editor document synchronization handlers."""

    def did_open(
        params: types.DidOpenTextDocumentParams,
    ) -> None:
        workspace = workspace_provider()

        if workspace is None:
            return

        uri = params.text_document.uri
        editor_document = server.workspace.get_text_document(uri)

        update = workspace.open_document(
            uri=uri,
            text=editor_document.source,
            version=editor_document.version,
        )
        publish_affected(server, workspace, update.affected_uris)

    def did_change(
        params: types.DidChangeTextDocumentParams,
    ) -> None:
        workspace = workspace_provider()

        if workspace is None:
            return

        uri = params.text_document.uri
        editor_document = server.workspace.get_text_document(uri)

        update = workspace.update_document(
            uri=uri,
            text=editor_document.source,
            version=editor_document.version,
        )
        publish_affected(server, workspace, update.affected_uris)

    def did_close(
        params: types.DidCloseTextDocumentParams,
    ) -> None:
        workspace = workspace_provider()

        if workspace is None:
            return

        uri = params.text_document.uri
        update = workspace.close_document(uri)
        publish_affected(server, workspace, update.affected_uris)

    register_open = server.feature(
        types.TEXT_DOCUMENT_DID_OPEN,
    )
    register_change = server.feature(
        types.TEXT_DOCUMENT_DID_CHANGE,
    )
    register_close = server.feature(
        types.TEXT_DOCUMENT_DID_CLOSE,
    )

    register_open(did_open)
    register_change(did_change)
    register_close(did_close)


def publish_affected(
    server: LanguageServer,
    workspace: ProjectWorkspace,
    uris: tuple[str, ...],
) -> None:
    """Publish every affected snapshot, including cleared diagnostics."""
    for uri in uris:
        _publish_diagnostics(server, workspace, uri)


def _publish_diagnostics(
    server: LanguageServer,
    workspace: ProjectWorkspace,
    uri: str,
) -> None:
    document = workspace.document(uri)
    if document is None and uri in workspace.configuration_uris:
        document = workspace.source_document(uri) or TextDocument(
            uri=uri, text="", position_encoding=workspace.position_encoding,
        )

    if document is None:
        server.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(uri=uri, diagnostics=[])
        )
        return

    diagnostics = [
        _to_lsp_diagnostic(document, diagnostic)
        for diagnostic in workspace.diagnostics_for(uri)
    ]

    server.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(
            uri=uri,
            version=document.version,
            diagnostics=diagnostics,
        ),
    )


def _to_lsp_diagnostic(
    document: TextDocument,
    diagnostic: Diagnostic,
) -> types.Diagnostic:
    diagnostic_range = document.range_at(diagnostic.span)
    severity = _DIAGNOSTIC_SEVERITIES.get(
        diagnostic.severity.name,
        types.DiagnosticSeverity.Error,
    )

    return types.Diagnostic(
        range=types.Range(
            start=types.Position(
                line=diagnostic_range.start.line,
                character=diagnostic_range.start.character,
            ),
            end=types.Position(
                line=diagnostic_range.end.line,
                character=diagnostic_range.end.character,
            ),
        ),
        message=diagnostic.message,
        severity=severity,
        source="kivy-lsp",
        code=diagnostic.code,
    )
