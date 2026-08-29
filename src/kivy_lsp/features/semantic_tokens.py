# src/kivy_lsp/features/semantic_tokens.py

from __future__ import annotations

from collections.abc import Callable

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from kivy_lsp.analysis.semantic_tokens import (
    KvSemanticTokenAnalyzer,
)
from kivy_lsp.model.semantic_token import (
    SemanticToken,
    SemanticTokenKind,
    SemanticTokenModifier,
)
from kivy_lsp.workspace.document import TextDocument
from kivy_lsp.workspace.project import ProjectWorkspace

type WorkspaceProvider = Callable[
    [],
    ProjectWorkspace | None,
]


_TOKEN_KINDS = tuple(SemanticTokenKind)
_TOKEN_MODIFIERS = tuple(SemanticTokenModifier)

_TOKEN_KIND_INDEX = {
    kind: index
    for index, kind in enumerate(_TOKEN_KINDS)
}

_TOKEN_MODIFIER_INDEX = {
    modifier: index
    for index, modifier in enumerate(_TOKEN_MODIFIERS)
}

_SEMANTIC_TOKEN_LEGEND = types.SemanticTokensLegend(
    token_types=[
        kind.value
        for kind in _TOKEN_KINDS
    ],
    token_modifiers=[
        modifier.value
        for modifier in _TOKEN_MODIFIERS
    ],
)


def register_semantic_tokens(
    server: LanguageServer,
    get_workspace: WorkspaceProvider,
) -> None:
    def semantic_tokens_full(
        _server: LanguageServer,
        params: types.SemanticTokensParams,
    ) -> types.SemanticTokens:
        workspace = get_workspace()

        if workspace is None:
            return _empty_semantic_tokens()

        uri = params.text_document.uri
        document = workspace.document(uri)
        parse_result = workspace.kv_result(uri)
        semantic_model = workspace.semantic_model(uri)

        if document is None:
            return _empty_semantic_tokens()

        if parse_result is None:
            return _empty_semantic_tokens()

        if semantic_model is None:
            return _empty_semantic_tokens()

        analyzer = KvSemanticTokenAnalyzer(
            workspace.python_index,
            workspace.config,
        )
        semantic_tokens = analyzer.analyze(
            document,
            parse_result,
            semantic_model,
        )

        return types.SemanticTokens(
            data=_encode_semantic_tokens(
                document,
                semantic_tokens,
            ),
        )

    register = server.feature(
        types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
        _SEMANTIC_TOKEN_LEGEND,
    )
    register(semantic_tokens_full)


def _empty_semantic_tokens() -> types.SemanticTokens:
    return types.SemanticTokens(
        data=[],
    )


def _encode_semantic_tokens(
    document: TextDocument,
    semantic_tokens: tuple[SemanticToken, ...],
) -> list[int]:
    data: list[int] = []
    previous_line = 0
    previous_character = 0
    previous_end = -1

    ordered_tokens = sorted(
        semantic_tokens,
        key=lambda semantic_token: (
            semantic_token.span.start,
            semantic_token.span.end,
        ),
    )

    for semantic_token in ordered_tokens:
        if semantic_token.span.start < previous_end:
            continue

        token_range = document.range_at(
            semantic_token.span,
        )
        start = token_range.start
        end = token_range.end

        if start.line != end.line:
            continue

        length = end.character - start.character

        if length <= 0:
            continue

        delta_line = start.line - previous_line

        if delta_line == 0:
            delta_character = (
                start.character - previous_character
            )
        else:
            delta_character = start.character

        data.extend(
            [
                delta_line,
                delta_character,
                length,
                _TOKEN_KIND_INDEX[semantic_token.kind],
                _modifier_bits(semantic_token.modifiers),
            ],
        )

        previous_line = start.line
        previous_character = start.character
        previous_end = semantic_token.span.end

    return data


def _modifier_bits(
    modifiers: tuple[SemanticTokenModifier, ...],
) -> int:
    result = 0

    for modifier in modifiers:
        index = _TOKEN_MODIFIER_INDEX[modifier]
        result |= 1 << index

    return result
