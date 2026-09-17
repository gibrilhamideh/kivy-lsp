"""Editor-facing project refresh, file notifications, and status commands."""

from __future__ import annotations

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from kivy_lsp.config import CONFIG_FILENAME, ConfigError
from kivy_lsp.features.documents import WorkspaceProvider, publish_affected

RELOAD_COMMAND = "kivy-lsp.reloadProject"
STATUS_COMMAND = "kivy-lsp.projectStatus"


def register_workspace(
    server: LanguageServer,
    workspace_provider: WorkspaceProvider,
) -> None:
    """Register workspace refresh without discarding unsaved buffers."""

    def refresh(uris: tuple[str, ...] | None = None) -> dict[str, object]:
        workspace = workspace_provider()
        if workspace is None:
            return {"initialized": False}
        try:
            update = (
                workspace.reload_project()
                if uris is None
                else workspace.refresh_files(uris)
            )
        except (ConfigError, OSError, ValueError) as error:
            server.window_show_message(
                types.ShowMessageParams(
                    type=types.MessageType.Error,
                    message=f"Could not refresh kivy-lsp project: {error}",
                )
            )
            return {"error": str(error)}
        publish_affected(server, workspace, update.affected_uris)
        return workspace.status()

    def reload_project(*args: object) -> dict[str, object]:
        return refresh()

    def project_status(*args: object) -> dict[str, object]:
        workspace = workspace_provider()
        return workspace.status() if workspace else {"initialized": False}

    def watched(params: types.DidChangeWatchedFilesParams) -> None:
        refresh(tuple(change.uri for change in params.changes))

    def renamed(params: types.RenameFilesParams) -> None:
        refresh(
            tuple(
                uri
                for item in params.files
                for uri in (item.old_uri, item.new_uri)
            )
        )

    def created(params: types.CreateFilesParams) -> None:
        refresh(tuple(item.uri for item in params.files))

    def deleted(params: types.DeleteFilesParams) -> None:
        refresh(tuple(item.uri for item in params.files))

    def configuration(params: types.DidChangeConfigurationParams) -> None:
        # Reload the sole project configuration source, kivy-lsp.toml.
        refresh()

    async def initialized(params: types.InitializedParams) -> None:
        workspace = workspace_provider()
        if workspace is not None:
            publish_affected(server, workspace, workspace.configuration_uris)
        capabilities = server.client_capabilities.workspace
        watched_files = (
            capabilities.did_change_watched_files if capabilities else None
        )
        if not watched_files or not watched_files.dynamic_registration:
            return
        kinds = (
            types.WatchKind.Create
            | types.WatchKind.Change
            | types.WatchKind.Delete
        )
        watchers = [types.FileSystemWatcher(
            glob_pattern="**/*.{py,pyi,kv,json,toml}", kind=kinds,
        )]
        if (
            workspace is not None
            and watched_files.relative_pattern_support
        ):
            watchers.extend(
                types.FileSystemWatcher(
                    glob_pattern=types.RelativePattern(
                        base_uri=path.parent.as_uri(), pattern=CONFIG_FILENAME,
                    ),
                    kind=kinds,
                )
                for path in workspace.library_config_paths
                if not path.is_relative_to(workspace.config.project_root)
            )
        options = types.DidChangeWatchedFilesRegistrationOptions(
            watchers=watchers,
        )
        registration = types.Registration(
            id="kivy-lsp-project-files",
            method=types.WORKSPACE_DID_CHANGE_WATCHED_FILES,
            register_options=options,
        )
        await server.client_register_capability_async(
            types.RegistrationParams(registrations=[registration])
        )

    options = types.FileOperationRegistrationOptions(
        filters=[
            types.FileOperationFilter(
                scheme="file",
                pattern=types.FileOperationPattern(glob="**/*"),
            ),
        ]
    )
    server.command(RELOAD_COMMAND)(reload_project)
    server.command(STATUS_COMMAND)(project_status)
    server.feature(types.WORKSPACE_DID_CHANGE_WATCHED_FILES)(watched)
    server.feature(types.WORKSPACE_DID_RENAME_FILES, options)(renamed)
    server.feature(types.WORKSPACE_DID_CREATE_FILES, options)(created)
    server.feature(types.WORKSPACE_DID_DELETE_FILES, options)(deleted)
    server.feature(types.WORKSPACE_DID_CHANGE_CONFIGURATION)(configuration)
    server.feature(types.INITIALIZED)(initialized)
