"""LSP adapters for signatures, folding, selections and quick fixes."""

from __future__ import annotations

from collections.abc import Callable

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from kivy_lsp.analysis.editor_features import (
    KvEditorFeatures,
    folding_spans,
    selection_spans,
)
from kivy_lsp.features.navigation import _request_offset, _to_lsp_range
from kivy_lsp.model.span import Span
from kivy_lsp.workspace.project import ProjectWorkspace

type WorkspaceProvider = Callable[[], ProjectWorkspace | None]


def register_editor_features(
    server: LanguageServer,
    workspace_provider: WorkspaceProvider,
) -> None:
    """Register editor capabilities backed by the existing parsed workspace."""

    def signature_help(params: types.SignatureHelpParams):
        workspace = workspace_provider()
        if workspace is None:
            return None
        uri = params.text_document.uri
        document = workspace.document(uri)
        parsed = workspace.kv_result(uri)
        model = workspace.semantic_model(uri)
        if document is None or parsed is None or model is None:
            return None
        offset = _request_offset(document, params.position)
        if offset is None:
            return None
        engine = KvEditorFeatures(
            workspace.python_index,
            workspace.kv_index,
            workspace.config,
        )
        result = engine.signature_help(document, parsed, model, offset)
        if result is None:
            return None
        signature = types.SignatureInformation(
            label=result.label,
            documentation=result.documentation,
            parameters=[
                types.ParameterInformation(label=parameter)
                for parameter in result.parameters
            ],
            active_parameter=result.active_parameter,
        )
        return types.SignatureHelp(
            signatures=[signature],
            active_signature=0,
            active_parameter=result.active_parameter,
        )

    def folding_range(params: types.FoldingRangeParams):
        workspace = workspace_provider()
        if workspace is None:
            return []
        uri = params.text_document.uri
        document = workspace.document(uri)
        parsed = workspace.kv_result(uri)
        if document is None or parsed is None:
            return []
        result: list[types.FoldingRange] = []
        seen: set[tuple[int, int]] = set()
        for span in folding_spans(parsed):
            # Dedent/EOF recovery can include a following newline. Fold
            # through the final content line, never the next sibling.
            end = span.end
            while end > span.start and document.text[end - 1].isspace():
                end -= 1
            start_line = document.position_at(span.start).line
            end_line = document.position_at(end).line
            key = (start_line, end_line)
            if end_line > start_line and key not in seen:
                seen.add(key)
                result.append(
                    types.FoldingRange(
                        start_line=start_line,
                        end_line=end_line,
                        kind=types.FoldingRangeKind.Region,
                    )
                )
        return result

    def selection_range(params: types.SelectionRangeParams):
        workspace = workspace_provider()
        if workspace is None:
            return []
        uri = params.text_document.uri
        document = workspace.document(uri)
        parsed = workspace.kv_result(uri)
        if document is None or parsed is None:
            return []
        result: list[types.SelectionRange] = []
        for position in params.positions:
            offset = _request_offset(document, position)
            if offset is None:
                result.append(
                    types.SelectionRange(
                        range=types.Range(start=position, end=position),
                    )
                )
                continue
            spans = selection_spans(parsed, offset) or (Span.empty(offset),)
            parent = None
            for span in reversed(spans):
                parent = types.SelectionRange(
                    range=_to_lsp_range(document, span),
                    parent=parent,
                )
            if parent is not None:
                result.append(parent)
        return result

    def code_action(params: types.CodeActionParams):
        workspace = workspace_provider()
        if workspace is None:
            return []
        if params.context.only and not any(
            kind == types.CodeActionKind.QuickFix
            or types.CodeActionKind.QuickFix.startswith(kind + ".")
            for kind in params.context.only
        ):
            return []
        uri = params.text_document.uri
        document = workspace.document(uri)
        parsed = workspace.kv_result(uri)
        model = workspace.semantic_model(uri)
        if document is None or parsed is None or model is None:
            return []
        start = _request_offset(document, params.range.start)
        end = _request_offset(document, params.range.end)
        if start is None or end is None:
            return []
        requested = Span(start, max(start, end))
        diagnostics = tuple(
            diagnostic
            for diagnostic in workspace.diagnostics_for(uri)
            if diagnostic.span.overlaps(requested)
            or diagnostic.span.contains_cursor(start)
        )
        engine = KvEditorFeatures(
            workspace.python_index,
            workspace.kv_index,
            workspace.config,
        )
        result: list[types.CodeAction] = []
        for fix in engine.quick_fixes(document, parsed, model, diagnostics):
            diagnostic = types.Diagnostic(
                range=_to_lsp_range(document, fix.diagnostic.span),
                message=fix.diagnostic.message,
                code=fix.diagnostic.code,
                source="kivy-lsp",
            )
            edit = types.TextDocumentEdit(
                text_document=types.OptionalVersionedTextDocumentIdentifier(
                    uri=uri,
                    version=document.version,
                ),
                edits=[
                    types.TextEdit(
                        range=_to_lsp_range(document, fix.span),
                        new_text=fix.replacement,
                    )
                ],
            )
            result.append(
                types.CodeAction(
                    title=fix.title,
                    kind=types.CodeActionKind.QuickFix,
                    diagnostics=[diagnostic],
                    edit=types.WorkspaceEdit(document_changes=[edit]),
                )
            )
        return result

    server.feature(
        types.TEXT_DOCUMENT_SIGNATURE_HELP,
        types.SignatureHelpOptions(trigger_characters=["(", ",", "="]),
    )(signature_help)
    server.feature(types.TEXT_DOCUMENT_FOLDING_RANGE)(folding_range)
    server.feature(types.TEXT_DOCUMENT_SELECTION_RANGE)(selection_range)
    server.feature(
        types.TEXT_DOCUMENT_CODE_ACTION,
        types.CodeActionOptions(
            code_action_kinds=[types.CodeActionKind.QuickFix]
        ),
    )(code_action)
