from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from lsprotocol import types

from kivy_lsp.config import load_config
from kivy_lsp.features.documents import register_document_sync
from kivy_lsp.features.workspace import RELOAD_COMMAND, register_workspace
from kivy_lsp.workspace import project
from kivy_lsp.workspace.project import ProjectWorkspace


@pytest.fixture
def workspace(tmp_path: Path) -> ProjectWorkspace:
    src = tmp_path / "src"
    src.mkdir()
    env = tmp_path / ".venv"
    env.mkdir()
    (env / "pyvenv.cfg").write_text("version = 3.12\n")
    package = env / "lib/python3.12/site-packages/kivy"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (tmp_path / "kivy-lsp.toml").write_text(
        'python-environment = ".venv"\n'
        'excludes = ["excluded", "cache/**"]\n'
    )
    (src / "main.py").write_text(
        "from kivy.properties import StringProperty\n"
        "class Widget:\n"
        '    text = StringProperty("")\n'
    )
    result = ProjectWorkspace(load_config(tmp_path))
    result.initialize()
    return result


def source(workspace: ProjectWorkspace, name: str) -> Path:
    return workspace.config.source_roots[0] / name


def text_errors(workspace: ProjectWorkspace, uri: str) -> list[str]:
    return [item.code for item in workspace.diagnostics_for(uri)]


def test_python_change_and_close_update_consumers(workspace):
    py = source(workspace, "main.py")
    kv = source(workspace, "view.kv").as_uri()
    workspace.open_document(kv, 'Widget:\n    text: "hello"\n', 1)
    assert not text_errors(workspace, kv)
    changed = workspace.open_document(
        py.as_uri(),
        py.read_text().replace("StringProperty", "NumericProperty"),
    )
    assert kv in changed.affected_uris
    assert "kv-incompatible-property-value" in text_errors(workspace, kv)
    closed = workspace.close_document(py.as_uri())
    assert py.as_uri() in closed.affected_uris
    assert kv in closed.affected_uris
    assert not text_errors(workspace, kv)


def test_invalid_python_overlay_retains_last_valid_until_close(workspace):
    py = source(workspace, "main.py")
    workspace.open_document(py.as_uri(), py.read_text(), 1)
    workspace.update_document(py.as_uri(), "class Widget(\n", 2)
    assert workspace.python_index.class_named("main.Widget") is not None
    assert workspace.diagnostics_for(py.as_uri())
    py.unlink()
    workspace.close_document(py.as_uri())
    assert workspace.python_index.class_named("main.Widget") is None


def test_kv_parsed_once_and_dynamic_consumers_rebuilt(workspace, monkeypatch):
    original = project.parse
    calls = []

    def counted(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(project, "parse", counted)
    declaration = source(workspace, "special.kv").as_uri()
    consumer = source(workspace, "view.kv").as_uri()
    workspace.open_document(consumer, "Special:\n    custom: 2\n")
    calls.clear()
    update = workspace.open_document(
        declaration, '<Special@Widget>:\n    custom: "text"\n'
    )
    assert len(calls) == 1
    assert consumer in update.affected_uris
    symbol = workspace.kv_index.find("Special")[0]
    assert [node.name for node in symbol.properties] == ["custom"]
    assert workspace.semantic_model(consumer) is not None
    calls.clear()
    workspace.update_document(
        declaration, "<Special@Widget>:\n    custom: 2\n"
    )
    assert len(calls) == 1
    assert (
        workspace.kv_index.find("Special")[0].properties[0].value.text == "2"
    )


def test_class_selectors_are_not_indexed_as_widgets(workspace):
    uri = source(workspace, "style.kv").as_uri()
    workspace.open_document(uri, '<.warning>:\n    text: "alert"\n')
    assert not workspace.kv_index.find("warning")


def test_reload_preserves_unsaved_python_and_kv(workspace):
    py = source(workspace, "main.py")
    kv_path = source(workspace, "view.kv")
    kv_path.write_text('Widget:\n    text: "saved"\n')
    kv = kv_path.as_uri()
    workspace.open_document(kv, "Widget:\n    text: 3\n", 7)
    workspace.open_document(
        py.as_uri(),
        py.read_text().replace("StringProperty", "NumericProperty"),
    )
    refreshed = workspace.reload_project()
    assert {kv, py.as_uri()} <= set(refreshed.affected_uris)
    assert workspace.document(kv).version == 7
    assert workspace.source_document(kv).text == "Widget:\n    text: 3\n"
    assert not text_errors(workspace, kv)
    workspace.close_document(py.as_uri())
    assert "kv-incompatible-property-value" in text_errors(workspace, kv)


def test_external_python_events_respect_overlay_and_clear_symbols(workspace):
    py = source(workspace, "main.py")
    kv = source(workspace, "view.kv").as_uri()
    workspace.open_document(kv, 'Widget:\n    text: "hello"\n')
    saved = py.read_text()
    workspace.open_document(py.as_uri(), saved)
    py.write_text(saved.replace("StringProperty", "NumericProperty"))
    workspace.refresh_files((py.as_uri(),))
    assert not text_errors(workspace, kv)
    workspace.close_document(py.as_uri())
    assert "kv-incompatible-property-value" in text_errors(workspace, kv)
    py.unlink()
    workspace.refresh_files((py.as_uri(),))
    assert workspace.python_index.class_named("main.Widget") is None


def test_external_kv_create_change_delete_and_rename(workspace):
    first = source(workspace, "one.kv")
    second = source(workspace, "two.kv")
    first.write_text("<One@Widget>:\n")
    workspace.refresh_files((first.as_uri(),))
    assert workspace.kv_index.find("One")
    first.write_text("<Changed@Widget>:\n")
    workspace.refresh_files((first.as_uri(),))
    assert not workspace.kv_index.find("One")
    first.rename(second)
    workspace.refresh_files((first.as_uri(), second.as_uri()))
    assert workspace.kv_index.find("Changed")[0].uri == second.as_uri()
    second.unlink()
    workspace.refresh_files((second.as_uri(),))
    assert not workspace.kv_index.find("Changed")


def test_include_graph_navigation_cycle_and_deleted_target(workspace):
    root = source(workspace, "root.kv")
    outside = workspace.config.project_root / "included.kv"
    outside.write_text("#:include src/root.kv\n<Included@Widget>:\n")
    root.write_text("#:include ../included.kv\nWidget:\n")
    workspace.refresh_files((root.as_uri(),))
    workspace.open_document(root.as_uri(), root.read_text())
    assert workspace.kv_index.find("Included")
    assert (
        workspace.include_locations(root.as_uri(), 5)[0][0] == outside.as_uri()
    )
    all_codes = [
        item.code
        for document in workspace.kv_documents()
        for item in workspace.diagnostics_for(document.uri)
    ]
    assert "kv-include-cycle" in all_codes
    outside.unlink()
    workspace.refresh_files((outside.as_uri(),))
    assert "kv-include-unresolved" in text_errors(workspace, root.as_uri())
    assert not workspace.kv_index.find("Included")


def test_include_removal_prunes_only_unreachable_included_sources(workspace):
    included = workspace.config.project_root / "other.kv"
    included.write_text("<Included@Widget>:\n")
    uri = source(workspace, "root.kv").as_uri()
    workspace.open_document(uri, "#:include ../other.kv\nWidget:\n")
    assert workspace.kv_index.find("Included")
    workspace.update_document(uri, "Widget:\n")
    assert not workspace.kv_index.find("Included")


def test_shared_exclusions_apply_to_scan_and_external_events(workspace):
    excluded = source(workspace, "excluded")
    excluded.mkdir()
    py = excluded / "hidden.py"
    kv = excluded / "hidden.kv"
    py.write_text("class Hidden:\n    pass\n")
    kv.write_text("<Hidden@Widget>:\n")
    workspace.refresh_files((py.as_uri(), kv.as_uri()))
    assert workspace.python_index.class_named("excluded.hidden.Hidden") is None
    assert not workspace.kv_index.find("Hidden")
    workspace.reload_project()
    assert not workspace.kv_index.find("Hidden")


def test_new_kivy_dependency_import_is_discovered(workspace):
    site = workspace.environment.site_packages[0]
    package = site / "kivy_new"
    package.mkdir()
    (package / "__init__.py").write_text("class New:\n    pass\n")
    py = source(workspace, "main.py")
    workspace.open_document(
        py.as_uri(), py.read_text() + "\nimport kivy_new\n"
    )
    assert workspace.python_index.class_named("kivy_new.New") is not None
    assert "kivy_new" in workspace.dependency_scan.packages


def test_stale_versions_do_not_replace_current_snapshot(workspace):
    uri = source(workspace, "view.kv").as_uri()
    workspace.open_document(uri, "Widget:\n    text: 3\n", 5)
    update = workspace.update_document(uri, "Widget:\n", 4)
    assert update.document.version == 5
    assert update.affected_uris == ()


class RecordingServer:
    def __init__(self):
        self.handlers = {}
        self.commands = {}
        self.published = []
        self.sources = {}
        self.workspace = SimpleNamespace(
            get_text_document=lambda uri: self.sources[uri]
        )

    def feature(self, name, options=None):
        def register(handler):
            self.handlers[name] = handler
            return handler

        return register

    def command(self, name):
        def register(handler):
            self.commands[name] = handler
            return handler

        return register

    def text_document_publish_diagnostics(self, params):
        self.published.append(params)

    def window_show_message(self, params):
        raise AssertionError(params.message)


def test_lsp_publishes_all_affected_diagnostics_with_codes(workspace):
    server = RecordingServer()
    register_document_sync(server, lambda: workspace)
    kv = source(workspace, "view.kv").as_uri()
    py = source(workspace, "main.py")
    workspace.open_document(kv, 'Widget:\n    text: "hello"\n', 4)
    server.sources[py.as_uri()] = SimpleNamespace(
        source=py.read_text().replace("StringProperty", "NumericProperty"),
        version=2,
    )
    server.handlers[types.TEXT_DOCUMENT_DID_CHANGE](
        types.DidChangeTextDocumentParams(
            text_document=types.VersionedTextDocumentIdentifier(
                uri=py.as_uri(), version=2
            ),
            content_changes=[],
        )
    )
    published = {entry.uri: entry for entry in server.published}
    assert published[kv].version == 4
    assert (
        published[kv].diagnostics[0].code == "kv-incompatible-property-value"
    )
    server.published.clear()
    server.handlers[types.TEXT_DOCUMENT_DID_CLOSE](
        types.DidCloseTextDocumentParams(
            text_document=types.TextDocumentIdentifier(uri=py.as_uri())
        )
    )
    published = {entry.uri: entry for entry in server.published}
    assert not published[kv].diagnostics
    assert not published[py.as_uri()].diagnostics


def test_workspace_handlers_register_and_reload_keeps_overlay(workspace):
    server = RecordingServer()
    register_workspace(server, lambda: workspace)
    uri = source(workspace, "view.kv").as_uri()
    workspace.open_document(uri, "Widget:\n", 6)
    result = server.commands[RELOAD_COMMAND]()
    assert result["openDocuments"] == 1
    assert workspace.document(uri).version == 6
    for method in (
        types.WORKSPACE_DID_CHANGE_WATCHED_FILES,
        types.WORKSPACE_DID_RENAME_FILES,
        types.WORKSPACE_DID_CREATE_FILES,
        types.WORKSPACE_DID_DELETE_FILES,
        types.WORKSPACE_DID_CHANGE_CONFIGURATION,
    ):
        assert method in server.handlers


@pytest.mark.parametrize("quote", ['"', "'", '"""', "'''"])
def test_quoted_include_paths_match_kivy_quote_stripping(workspace, quote):
    included = workspace.config.project_root / "with spaces.kv"
    included.write_text("<Quoted@Widget>:\n")
    uri = source(workspace, "root.kv").as_uri()
    workspace.open_document(
        uri, f"#:include force {quote}../with spaces.kv{quote}\nWidget:\n"
    )
    assert workspace.kv_index.find("Quoted")
    assert not any(
        code.startswith("kv-include") for code in text_errors(workspace, uri)
    )


def test_directory_rename_refreshes_python_and_kv(workspace):
    package = source(workspace, "before")
    package.mkdir()
    (package / "__init__.py").write_text("class Renamed:\n    pass\n")
    (package / "widget.kv").write_text("<RenamedKv@Widget>:\n")
    workspace.refresh_files((package.as_uri(),))
    assert workspace.python_index.class_named("before.Renamed") is not None
    new = source(workspace, "after")
    package.rename(new)
    workspace.refresh_files((package.as_uri(), new.as_uri()))
    assert workspace.python_index.class_named("before.Renamed") is None
    assert workspace.python_index.class_named("after.Renamed") is not None
    assert workspace.kv_index.find("RenamedKv")[0].uri.startswith(new.as_uri())


def test_stub_removal_restores_implementation_module(workspace):
    py = source(workspace, "main.py")
    stub = py.with_suffix(".pyi")
    stub.write_text("class StubWidget: ...\n")
    workspace.refresh_files((stub.as_uri(),))
    assert workspace.python_index.class_named("main.StubWidget") is not None
    py.write_text(py.read_text() + "\n# external edit\n")
    workspace.refresh_files((py.as_uri(),))
    assert workspace.python_index.class_named("main.StubWidget") is not None
    stub.unlink()
    workspace.refresh_files((stub.as_uri(),))
    assert workspace.python_index.class_named("main.StubWidget") is None
    assert workspace.python_index.class_named("main.Widget") is not None


def test_translation_overlay_and_external_changes_refresh_consumers(workspace):
    root = workspace.config.project_root
    catalog = root / "en.json"
    catalog.write_text('{"hello": "Hello"}')
    config = root / "kivy-lsp.toml"
    config.write_text(
        config.read_text() + ('\n[i18n]\nsource = "en.json"\n')
    )
    workspace.refresh_files((config.as_uri(),))
    kv = source(workspace, "translations.kv").as_uri()
    workspace.open_document(kv, 'Widget:\n    i18n_key: "hello"\n')
    assert workspace.translation_index.entry("hello") is not None
    update = workspace.open_document(catalog.as_uri(), '{"new": "New"}')
    assert kv in update.affected_uris
    assert workspace.translation_index.entry("hello") is None
    assert workspace.translation_index.entry("new") is not None
    workspace.close_document(catalog.as_uri())
    assert workspace.translation_index.entry("hello") is not None
    catalog.write_text('{"saved": "Saved"}')
    workspace.refresh_files((catalog.as_uri(),))
    assert workspace.translation_index.entry("hello") is None
    assert workspace.translation_index.entry("saved") is not None


def test_dynamic_base_edit_refreshes_inherited_property_diagnostics(workspace):
    py = source(workspace, "main.py")
    workspace.open_document(
        py.as_uri(),
        py.read_text()
        + (
            "\nfrom kivy.properties import NumericProperty\n"
            "class OtherWidget:\n    text = NumericProperty(0)\n"
        ),
    )
    declaration = source(workspace, "special.kv").as_uri()
    consumer = source(workspace, "view.kv").as_uri()
    workspace.open_document(declaration, "<Special@Widget>:\n")
    workspace.open_document(consumer, 'Special:\n    text: "hello"\n')
    assert not text_errors(workspace, consumer)
    update = workspace.update_document(declaration, "<Special@OtherWidget>:\n")
    assert consumer in update.affected_uris
    assert "kv-incompatible-property-value" in text_errors(workspace, consumer)
    workspace.update_document(declaration, "<Special@Widget>:\n")
    assert not text_errors(workspace, consumer)


def test_literal_edit_does_not_reanalyze_unrelated_documents(
    workspace, monkeypatch
):
    first = source(workspace, "first.kv").as_uri()
    second = source(workspace, "second.kv").as_uri()
    workspace.open_document(first, 'Widget:\n    text: "first"\n')
    workspace.open_document(second, 'Widget:\n    text: "second"\n')
    original = project.build_kv_semantic_model
    analyzed = []

    def counted(document, *args, **kwargs):
        analyzed.append(document.uri)
        return original(document, *args, **kwargs)

    monkeypatch.setattr(project, "build_kv_semantic_model", counted)
    workspace.update_document(first, "Widget:\n    text: 2\n")
    assert analyzed == [first]
    assert "kv-incompatible-property-value" in text_errors(workspace, first)
    assert not text_errors(workspace, second)
