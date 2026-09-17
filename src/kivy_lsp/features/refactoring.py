"""Find references and versioned, conservative rename workspace edits."""

from __future__ import annotations

from collections.abc import Callable

from lsprotocol import types
from pygls.exceptions import JsonRpcInvalidParams
from pygls.lsp.server import LanguageServer

from kivy_lsp.analysis.references import (
    KvReferenceEngine,
    ReferenceDocument,
    RenameError,
)
from kivy_lsp.features.navigation import _request_offset, _to_lsp_range
from kivy_lsp.model.uri import file_uri_to_path
from kivy_lsp.workspace.discovery import is_excluded
from kivy_lsp.workspace.project import ProjectWorkspace

type WorkspaceProvider = Callable[[], ProjectWorkspace | None]


def _reference_engine(workspace: ProjectWorkspace) -> KvReferenceEngine:
    documents: list[ReferenceDocument] = []
    for document in workspace.kv_documents():
        parsed = workspace.kv_result(document.uri)
        model = workspace.semantic_model(document.uri)
        if parsed is not None and model is not None:
            documents.append(ReferenceDocument(document, parsed, model))
    # Restrict rename analysis to project Python. Dependencies are read-only
    # metadata and must never become workspace-edit targets.
    roots = workspace.config.source_roots
    uris = {module.uri for module in workspace.python_index.modules}
    python_documents = []
    for uri in sorted(uris):
        path = file_uri_to_path(uri)
        if path is None:
            continue
        if not any(
            path.is_relative_to(root)
            and not is_excluded(path, root, workspace.config.excludes)
            for root in roots
        ):
            continue
        document = workspace.source_document(uri)
        if document is not None:
            python_documents.append(document)
    return KvReferenceEngine(
        tuple(documents),
        workspace.python_index,
        workspace.kv_index,
        tuple(python_documents),
    )


def register_refactoring(
    server: LanguageServer,
    workspace_provider: WorkspaceProvider,
) -> None:
    """Register reference and rename requests using resolved identities."""

    def resolve_target(params):
        workspace = workspace_provider()
        if workspace is None:
            return None
        document = workspace.document(params.text_document.uri)
        if document is None:
            return None
        offset = _request_offset(document, params.position)
        if offset is None:
            return None
        engine = _reference_engine(workspace)
        target = engine.target_at(document.uri, offset)
        if target is None:
            return None
        return workspace, document, engine, target

    def references(params: types.ReferenceParams):
        resolved = resolve_target(params)
        if resolved is None:
            return []
        workspace, _, engine, target = resolved
        results = []
        for reference in engine.references_to(
            target.identity,
            params.context.include_declaration,
        ):
            document = workspace.source_document(reference.uri)
            if document is not None:
                results.append(
                    types.Location(
                        uri=document.uri,
                        range=_to_lsp_range(document, reference.span),
                    )
                )
        return results

    def prepare_rename(params: types.PrepareRenameParams):
        resolved = resolve_target(params)
        if resolved is None:
            return None
        _, document, engine, target = resolved
        try:
            engine.prepare_rename(target)
        except RenameError as error:
            raise JsonRpcInvalidParams(str(error)) from error
        return types.PrepareRenamePlaceholder(
            range=_to_lsp_range(document, target.span),
            placeholder=target.identity.name,
        )

    def rename(params: types.RenameParams):
        resolved = resolve_target(params)
        if resolved is None:
            raise JsonRpcInvalidParams(
                "Rename supports unambiguous KV IDs and dynamic KV classes."
            )
        workspace, _, engine, target = resolved
        try:
            references = engine.rename(target, params.new_name)
        except RenameError as error:
            raise JsonRpcInvalidParams(str(error)) from error
        edits_by_uri: dict[str, list[types.TextEdit]] = {}
        for reference in references:
            document = workspace.source_document(reference.uri)
            if document is None:
                raise JsonRpcInvalidParams(
                    "A referenced document is unavailable."
                )
            edits_by_uri.setdefault(reference.uri, []).append(
                types.TextEdit(
                    range=_to_lsp_range(document, reference.span),
                    new_text=params.new_name,
                )
            )
        changes = []
        for uri, edits in sorted(edits_by_uri.items()):
            document = workspace.source_document(uri)
            if document is None:
                raise JsonRpcInvalidParams("A document became unavailable.")
            identifier = types.OptionalVersionedTextDocumentIdentifier(
                uri=uri,
                version=document.version,
            )
            changes.append(types.TextDocumentEdit(identifier, edits))
        return types.WorkspaceEdit(document_changes=changes)

    server.feature(types.TEXT_DOCUMENT_REFERENCES)(references)
    server.feature(
        types.TEXT_DOCUMENT_RENAME,
        types.RenameOptions(prepare_provider=True),
    )(rename)
    server.feature(types.TEXT_DOCUMENT_PREPARE_RENAME)(prepare_rename)
