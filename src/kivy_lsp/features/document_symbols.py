from __future__ import annotations

from collections.abc import Callable

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from kivy_lsp.analysis.document_symbols import (
    KvDocumentSymbolAnalyzer,
)
from kivy_lsp.model.document_symbol import (
    KvDocumentSymbol,
    KvDocumentSymbolKind,
)
from kivy_lsp.model.span import Span
from kivy_lsp.workspace.document import TextDocument
from kivy_lsp.workspace.project import ProjectWorkspace

type WorkspaceProvider = Callable[[], ProjectWorkspace | None]


_SYMBOL_KINDS: dict[
    KvDocumentSymbolKind,
    types.SymbolKind,
] = {
    KvDocumentSymbolKind.CLASS: types.SymbolKind.Class,
    KvDocumentSymbolKind.CONSTRUCTOR: types.SymbolKind.Constructor,
    KvDocumentSymbolKind.EVENT: types.SymbolKind.Event,
    KvDocumentSymbolKind.NAMESPACE: types.SymbolKind.Namespace,
    KvDocumentSymbolKind.PROPERTY: types.SymbolKind.Property,
}


def register_document_symbols(
    server: LanguageServer,
    workspace_provider: WorkspaceProvider,
) -> None:
    """Register hierarchical KV document symbols."""

    def document_symbols(
        params: types.DocumentSymbolParams,
    ) -> list[types.DocumentSymbol]:
        workspace = workspace_provider()

        if workspace is None:
            return []

        uri = params.text_document.uri
        document = workspace.document(uri)
        parse_result = workspace.kv_result(uri)

        if document is None or parse_result is None:
            return []

        symbols = KvDocumentSymbolAnalyzer().analyze(
            document,
            parse_result,
        )

        return [
            _to_lsp_symbol(document, symbol)
            for symbol in symbols
        ]

    register = server.feature(
        types.TEXT_DOCUMENT_DOCUMENT_SYMBOL,
    )
    register(document_symbols)


def _to_lsp_symbol(
    document: TextDocument,
    symbol: KvDocumentSymbol,
) -> types.DocumentSymbol:
    return types.DocumentSymbol(
        name=symbol.name,
        detail=symbol.detail,
        kind=_SYMBOL_KINDS[symbol.kind],
        range=_to_lsp_range(document, symbol.span),
        selection_range=_to_lsp_range(
            document,
            symbol.selection_span,
        ),
        children=[
            _to_lsp_symbol(document, child)
            for child in symbol.children
        ],
    )


def _to_lsp_range(
    document: TextDocument,
    span: Span,
) -> types.Range:
    text_range = document.range_at(span)

    return types.Range(
        start=types.Position(
            line=text_range.start.line,
            character=text_range.start.character,
        ),
        end=types.Position(
            line=text_range.end.line,
            character=text_range.end.character,
        ),
    )
