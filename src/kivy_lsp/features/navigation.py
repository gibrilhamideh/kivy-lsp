from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath
from urllib.parse import urlparse

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from kivy_lsp.analysis.definition import (
    KvDefinitionEngine,
    PythonIdsDefinitionEngine,
)
from kivy_lsp.analysis.i18n import translation_key_target_at
from kivy_lsp.model.span import Span
from kivy_lsp.model.symbol import SymbolLocation
from kivy_lsp.workspace.document import (
    TextDocument,
    TextPosition,
)
from kivy_lsp.workspace.project import ProjectWorkspace

type WorkspaceProvider = Callable[[], ProjectWorkspace | None]


def register_navigation(
    server: LanguageServer,
    workspace_provider: WorkspaceProvider,
) -> None:
    """Register go-to-definition and translation hover support."""

    def definition(
        params: types.DefinitionParams,
    ) -> list[types.Location] | None:
        workspace = workspace_provider()

        if workspace is None:
            return None

        uri = params.text_document.uri
        document = workspace.document(uri)

        if document is None:
            return None

        offset = _request_offset(document, params.position)

        if offset is None:
            return None

        suffix = _uri_suffix(uri)

        if suffix in {".py", ".pyi"}:
            locations = PythonIdsDefinitionEngine(
                workspace.python_index,
                workspace.kv_index,
            ).definition_at(document, offset)
        elif suffix == ".kv":
            parse_result = workspace.kv_result(uri)
            semantic_model = workspace.semantic_model(uri)

            if parse_result is None or semantic_model is None:
                return None

            locations = KvDefinitionEngine(
                workspace.python_index,
                workspace.kv_index,
                workspace.config,
                workspace.translation_index,
            ).definition_at(
                document,
                parse_result,
                semantic_model,
                offset,
            )
        else:
            return None

        result = [
            converted
            for location in locations
            if (
                converted := _to_lsp_location(
                    workspace,
                    location,
                )
            )
            is not None
        ]

        return result or None

    def hover(
        params: types.HoverParams,
    ) -> types.Hover | None:
        workspace = workspace_provider()

        if workspace is None:
            return None

        uri = params.text_document.uri

        if _uri_suffix(uri) != ".kv":
            return None

        document = workspace.document(uri)
        parse_result = workspace.kv_result(uri)
        config = workspace.config.i18n

        if (
            document is None
            or parse_result is None
            or config is None
        ):
            return None

        offset = _request_offset(document, params.position)

        if offset is None:
            return None

        target = translation_key_target_at(
            document,
            parse_result,
            offset,
            config,
            workspace.translation_index,
        )

        if target is None or target.entry is None:
            return None

        entry = target.entry
        parameters = entry.placeholder_names
        lines = [
            f"**{entry.key}**",
            "",
            entry.value,
        ]

        if parameters:
            lines.extend(
                (
                    "",
                    "Parameters: "
                    + ", ".join(
                        f"`{name}`"
                        for name in parameters
                    ),
                )
            )

        return types.Hover(
            contents=types.MarkupContent(
                kind=types.MarkupKind.Markdown,
                value="\n".join(lines),
            ),
            range=_to_lsp_range(document, target.span),
        )

    register_definition = server.feature(
        types.TEXT_DOCUMENT_DEFINITION,
    )
    register_hover = server.feature(
        types.TEXT_DOCUMENT_HOVER,
    )
    register_definition(definition)
    register_hover(hover)


def _request_offset(
    document: TextDocument,
    position: types.Position,
) -> int | None:
    try:
        return document.offset_at(
            TextPosition(
                line=position.line,
                character=position.character,
            )
        )
    except ValueError:
        return None


def _to_lsp_location(
    workspace: ProjectWorkspace,
    location: SymbolLocation,
) -> types.Location | None:
    document = workspace.source_document(location.uri)

    if document is None:
        return None

    return types.Location(
        uri=location.uri,
        range=_to_lsp_range(
            document,
            location.selection_span,
        ),
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


def _uri_suffix(uri: str) -> str:
    path = urlparse(uri).path
    return PurePosixPath(path).suffix.lower()

