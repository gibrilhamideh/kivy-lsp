# src/kivy_lsp/features/completion.py

from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath
from urllib.parse import urlparse

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from kivy_lsp.analysis.completion import (
    KvCompletionEngine,
    KvCompletionItem,
    KvCompletionResult,
)
from kivy_lsp.analysis.i18n import TranslationCompletionEngine
from kivy_lsp.analysis.python_ids_completion import (
    PythonIdsCompletionEngine,
)
from kivy_lsp.workspace.document import TextDocument, TextPosition
from kivy_lsp.workspace.project import ProjectWorkspace

type WorkspaceProvider = Callable[[], ProjectWorkspace | None]


_COMPLETION_KINDS: dict[str, types.CompletionItemKind] = {
    "MODULE": types.CompletionItemKind.Module,
    "CLASS": types.CompletionItemKind.Class,
    "FUNCTION": types.CompletionItemKind.Function,
    "METHOD": types.CompletionItemKind.Method,
    "PROPERTY": types.CompletionItemKind.Property,
    "VARIABLE": types.CompletionItemKind.Variable,
    "CONSTANT": types.CompletionItemKind.Constant,
    "EVENT": types.CompletionItemKind.Event,
    "ID": types.CompletionItemKind.Reference,
    "KEYWORD": types.CompletionItemKind.Keyword,
}


def register_completion(
    server: LanguageServer,
    workspace_provider: WorkspaceProvider,
) -> None:
    """Register KV and Kivy-specific Python completion support."""

    def completion(
        params: types.CompletionParams,
    ) -> types.CompletionList:
        workspace = workspace_provider()

        if workspace is None:
            return _empty_completion_list()

        uri = params.text_document.uri
        document = workspace.document(uri)

        if document is None:
            return _empty_completion_list()

        position = TextPosition(
            line=params.position.line,
            character=params.position.character,
        )

        try:
            offset = document.offset_at(position)
        except ValueError:
            return _empty_completion_list()

        result = _completion_result(
            workspace,
            document,
            uri,
            offset,
        )

        if result is None:
            return _empty_completion_list()

        replacement = document.range_at(
            result.target.replacement_span,
        )
        replacement_range = types.Range(
            start=types.Position(
                line=replacement.start.line,
                character=replacement.start.character,
            ),
            end=types.Position(
                line=replacement.end.line,
                character=replacement.end.character,
            ),
        )

        items = [
            _to_lsp_item(
                item,
                replacement_range,
            )
            for item in result.items
        ]

        return types.CompletionList(
            is_incomplete=result.is_incomplete,
            items=items,
        )

    register = server.feature(
        types.TEXT_DOCUMENT_COMPLETION,
        types.CompletionOptions(
            trigger_characters=[
                ".",
                ":",
                "<",
                "@",
                ",",
                "[",
                "'",
                '"',
            ],
        ),
    )
    register(completion)


def _completion_result(
    workspace: ProjectWorkspace,
    document: TextDocument,
    uri: str,
    offset: int,
) -> KvCompletionResult | None:
    if _uri_suffix(uri) == ".py":
        engine = PythonIdsCompletionEngine(
            workspace.python_index,
            workspace.kv_index,
        )
        return engine.complete(document, offset)

    parse_result = workspace.kv_result(uri)
    semantic_model = workspace.semantic_model(uri)

    if parse_result is None or semantic_model is None:
        return None

    i18n_config = workspace.config.i18n

    if i18n_config is not None:
        translation_result = TranslationCompletionEngine(
            workspace.translation_index,
            i18n_config,
        ).complete(
            document,
            parse_result,
            offset,
        )

        if translation_result is not None:
            return translation_result

    engine = KvCompletionEngine(
        workspace.python_index,
        workspace.kv_index,
        workspace.config,
    )

    return engine.complete(
        document,
        parse_result,
        semantic_model,
        offset,
    )


def _uri_suffix(uri: str) -> str:
    path = urlparse(uri).path
    return PurePosixPath(path).suffix.lower()


def _to_lsp_item(
    item: KvCompletionItem,
    replacement_range: types.Range,
) -> types.CompletionItem:
    kind = _COMPLETION_KINDS.get(
        item.kind.name,
        types.CompletionItemKind.Text,
    )

    return types.CompletionItem(
        label=item.label,
        kind=kind,
        detail=item.detail,
        documentation=item.documentation,
        sort_text=item.sort_text,
        filter_text=_completion_filter_text(item),
        text_edit=types.TextEdit(
            range=replacement_range,
            new_text=item.insert_text,
        ),
    )


def _completion_filter_text(item: KvCompletionItem) -> str:
    if item.insert_text.startswith(("'", '"')):
        return item.insert_text

    return item.label


def _empty_completion_list() -> types.CompletionList:
    return types.CompletionList(
        is_incomplete=False,
        items=[],
    )
