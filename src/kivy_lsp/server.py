# src/kivy_lsp/server.py

from pathlib import Path

from lsprotocol import types
from pygls.lsp.server import LanguageServer
from pygls.capabilities import ServerCapabilitiesBuilder

from kivy_lsp import __version__
from kivy_lsp.config import find_project_root, load_config
from kivy_lsp.features.completion import register_completion
from kivy_lsp.features.document_symbols import (
    register_document_symbols,
)
from kivy_lsp.features.documents import register_document_sync
from kivy_lsp.features.editor_features import register_editor_features
from kivy_lsp.features.formatting import register_formatting
from kivy_lsp.features.navigation import register_navigation
from kivy_lsp.features.refactoring import register_refactoring
from kivy_lsp.features.semantic_tokens import (
    register_semantic_tokens,
)
from kivy_lsp.features.workspace import register_workspace
from kivy_lsp.model.uri import file_uri_to_path
from kivy_lsp.workspace.project import ProjectWorkspace
from kivy_lsp.workspace.document import PositionEncoding


class KivyLanguageServer(LanguageServer):
    """Language server and active Kivy project state."""

    def __init__(self) -> None:
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            "kivy-lsp",
            __version__,
        )
        self._project_workspace: ProjectWorkspace | None = None

    def get_project_workspace(
        self,
    ) -> ProjectWorkspace | None:
        return self._project_workspace

    def set_project_workspace(
        self,
        workspace: ProjectWorkspace,
    ) -> None:
        self._project_workspace = workspace


def create_server() -> KivyLanguageServer:
    """Create a fully configured Kivy language server."""

    server = KivyLanguageServer()

    _register_initialization(server)
    register_document_sync(
        server,
        server.get_project_workspace,
    )
    register_completion(
        server,
        server.get_project_workspace,
    )
    register_navigation(
        server,
        server.get_project_workspace,
    )
    register_document_symbols(
        server,
        server.get_project_workspace,
    )
    register_semantic_tokens(
        server,
        server.get_project_workspace,
    )
    register_formatting(server, server.get_project_workspace)
    register_editor_features(server, server.get_project_workspace)
    register_refactoring(server, server.get_project_workspace)
    register_workspace(server, server.get_project_workspace)

    return server


def _register_initialization(
    server: KivyLanguageServer,
) -> None:
    def initialize_project(
        params: types.InitializeParams,
    ) -> None:
        project_root = _project_root(params)
        config = load_config(project_root)
        workspace = ProjectWorkspace(config)
        workspace.position_encoding = PositionEncoding(
            ServerCapabilitiesBuilder.choose_position_encoding(
                params.capabilities
            )
        )

        workspace.initialize()
        server.set_project_workspace(workspace)

        server.window_log_message(
            types.LogMessageParams(
                type=types.MessageType.Info,
                message=(f"kivy-lsp initialized for {project_root}"),
            ),
        )

    register = server.feature(types.INITIALIZE)
    register(initialize_project)


def _project_root(
    params: types.InitializeParams,
) -> Path:
    if params.workspace_folders:
        folder = params.workspace_folders[0]
        path = _file_uri_to_path(folder.uri)

        if path is not None:
            return find_project_root(path)

    if params.root_uri is not None:
        path = _file_uri_to_path(params.root_uri)

        if path is not None:
            return find_project_root(path)

    if params.root_path is not None:
        return find_project_root(Path(params.root_path))

    return find_project_root(Path.cwd())


def _file_uri_to_path(
    uri: str,
) -> Path | None:
    return file_uri_to_path(uri)


server = create_server()
