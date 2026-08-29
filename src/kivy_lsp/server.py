# src/kivy_lsp/server.py

from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from kivy_lsp import __version__
from kivy_lsp.config import load_config
from kivy_lsp.features.completion import register_completion
from kivy_lsp.features.document_symbols import (
    register_document_symbols,
)
from kivy_lsp.features.documents import register_document_sync
from kivy_lsp.features.navigation import register_navigation
from kivy_lsp.features.semantic_tokens import (
    register_semantic_tokens,
)
from kivy_lsp.workspace.project import ProjectWorkspace


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

        workspace.initialize()
        server.set_project_workspace(workspace)

        server.window_log_message(
            types.LogMessageParams(
                type=types.MessageType.Info,
                message=(
                    "kivy-lsp initialized for "
                    f"{project_root}"
                ),
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
            return path

    if params.root_uri is not None:
        path = _file_uri_to_path(params.root_uri)

        if path is not None:
            return path

    if params.root_path is not None:
        return Path(params.root_path).resolve()

    return Path.cwd().resolve()


def _file_uri_to_path(
    uri: str,
) -> Path | None:
    parsed = urlparse(uri)

    if parsed.scheme != "file":
        return None

    path_text = url2pathname(
        unquote(parsed.path),
    )

    if parsed.netloc:
        path_text = f"//{parsed.netloc}{path_text}"

    return Path(path_text).resolve()


server = create_server()
