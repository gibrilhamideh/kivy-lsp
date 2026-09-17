"""LSP adapter for the standalone document formatter."""

from __future__ import annotations

from collections.abc import Callable

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from kivy_lsp.formatting import format_source
from kivy_lsp.workspace.project import ProjectWorkspace

type WorkspaceProvider = Callable[[], ProjectWorkspace | None]


def register_formatting(
    server: LanguageServer, workspace_provider: WorkspaceProvider,
) -> None:
    """Register whole-document formatting with project formatting options."""

    def document_formatting(
        params: types.DocumentFormattingParams,
    ) -> list[types.TextEdit]:
        workspace = workspace_provider()
        if workspace is None:
            return []
        document = workspace.document(params.text_document.uri)
        if document is None:
            return []
        result = format_source(document.text, workspace.config.format)
        if result.issues:
            details = "; ".join(
                f"line {issue.line}: {issue.message}"
                for issue in result.issues[:5]
            )
            server.window_log_message(types.LogMessageParams(
                type=types.MessageType.Warning,
                message=f"KV formatter: {details}",
            ))
        if result.text == document.text:
            return []
        end = document.position_at(len(document.text))
        return [types.TextEdit(
            range=types.Range(
                start=types.Position(line=0, character=0),
                end=types.Position(line=end.line, character=end.character),
            ),
            new_text=result.text,
        )]

    server.feature(types.TEXT_DOCUMENT_FORMATTING)(document_formatting)
