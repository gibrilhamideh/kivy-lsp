"""Directive contexts, replacement spans, and actual LSP import completion."""

import pytest
from lsprotocol import types
from pygls.lsp.server import LanguageServer

from kivy_lsp.analysis.import_completion import import_target_at
from kivy_lsp.config import load_config
from kivy_lsp.features.completion import register_completion
from kivy_lsp.kv.parser import parse
from kivy_lsp.workspace.document import TextDocument
from kivy_lsp.workspace.project import ProjectWorkspace

from test_server_protocol import Client


@pytest.mark.parametrize(
    ("source", "parent", "prefix"),
    [
        ("#:import Formatter |", "", ""),
        ("#:import Formatter ap|", "", "ap"),
        ("#:import Formatter app.|", "app", ""),
        ("#:import Formatter app.sha|", "app", "sha"),
        ("#: import Formatter app.sha|", "app", "sha"),
        ("#:\timport Formatter app.sha|", "app", "sha"),
        (
            "#:import Formatter app.shared.formatter.For|",
            "app.shared.formatter",
            "For",
        ),
        ("\t#:import\tFormatter\tapp.sha|", "app", "sha"),
        ("#:import 別名 app.共有.形|", "app.共有", "形"),
    ],
)
def test_import_context(source, parent, prefix):
    offset = source.index("|")
    text = source.replace("|", "")
    document = TextDocument("file:///view.kv", text)
    target = import_target_at(document, parse(text), offset)
    assert target is not None
    assert target.receiver == parent
    assert target.prefix == prefix


@pytest.mark.parametrize(
    "source",
    [
        "#:import Form|atter app.shared.formatter.Formatter",
        "#:import Formatter|",
        "#:import Formatter app..|",
        "#:import Formatter .app|",
        "#:import Formatter app.shared |",
        "#:import Formatter app.shared # app.|",
        "# #:import Formatter app.|",
        "#:set Formatter app.|",
        'Widget:\n    text: "#:import Formatter app.|"',
    ],
)
def test_import_completion_ignores_other_contexts(source):
    offset = source.index("|")
    text = source.replace("|", "")
    document = TextDocument("file:///view.kv", text)
    assert import_target_at(document, parse(text), offset) is None


def test_mid_component_replacement_keeps_alias_and_remaining_path():
    source = "#:import Formatter app.sha|red.formatter.Formatter"
    offset = source.index("|")
    text = source.replace("|", "")
    document = TextDocument("file:///view.kv", text)
    target = import_target_at(document, parse(text), offset)
    span = target.replacement_span
    assert text[span.start : span.end] == "shared"
    changed = text[: span.start] + "shared" + text[span.end :]
    assert changed == text


def test_tab_separated_import_keeps_its_semantic_arguments():
    result = parse("#:import\tFormatter\tapp.shared.formatter.Formatter")
    directive = result.document.items[0]
    assert directive.name == "import"
    assert directive.arguments == "Formatter\tapp.shared.formatter.Formatter"


@pytest.fixture
def project(tmp_path):
    src = tmp_path / "src"
    package = src / "app/shared"
    package.mkdir(parents=True)
    (package / "formatter.py").write_text(
        'raise RuntimeError("Completion must not execute this module")\n'
        "class Formatter:\n"
        '    """Measurement formatting helpers."""\n'
        "    pass\n"
    )
    (tmp_path / "kivy-lsp.toml").write_text(
        'python-environment = ".venv"\n'
    )
    environment = tmp_path / ".venv"
    kivy = environment / "lib/python3.12/site-packages/kivy"
    (kivy / "uix").mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("version = 3.12\n")
    (kivy / "__init__.py").write_text("")
    (kivy / "uix/__init__.py").write_text("")
    (kivy / "uix/widget.py").write_text("class Widget:\n    pass\n")
    return tmp_path


def test_completion_adapter_unicode_range_and_unsaved_python(project):
    workspace = ProjectWorkspace(load_config(project))
    workspace.initialize()
    text = "# 😀\r\n#:import 𐐀 app.shared.formatter.For\r\n"
    uri = (project / "src/main.kv").as_uri()
    workspace.open_document(uri, text, 1)
    server = LanguageServer("test", "0")
    register_completion(server, lambda: workspace)
    complete = server.protocol.fm.features[types.TEXT_DOCUMENT_COMPLETION]
    document = workspace.document(uri)
    position = document.position_at(text.index("For") + 3)
    result = complete(
        types.CompletionParams(
            types.TextDocumentIdentifier(uri),
            types.Position(position.line, position.character),
        )
    )
    item = next(item for item in result.items if item.label == "Formatter")
    assert item.kind == types.CompletionItemKind.Class
    assert item.documentation == "Measurement formatting helpers."
    assert item.text_edit.new_text == "Formatter"
    assert item.text_edit.range.end.character == position.character
    assert item.text_edit.range.start.character == position.character - 3

    path = project / "src/app/shared/formatter.py"
    workspace.open_document(
        path.as_uri(), "class FormatOverlay:\n    pass\n", 2
    )
    result = complete(
        types.CompletionParams(
            types.TextDocumentIdentifier(uri),
            types.Position(position.line, position.character),
        )
    )
    assert {item.label for item in result.items} == {"FormatOverlay"}


@pytest.mark.parametrize("directive_prefix", ["#:import", "#: import"])
def test_real_server_completes_import_paths(project, directive_prefix):
    client = Client()
    uri = (project / "src/main.kv").as_uri()
    try:
        client.request(
            "initialize",
            {
                "processId": None,
                "rootUri": project.as_uri(),
                "capabilities": {},
            },
        )
        client.send("initialized", {})
        client.send(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "kivy",
                    "version": 0,
                    "text": "",
                },
            },
        )
        cases = [
            ("app.sha", "shared", types.CompletionItemKind.Module),
            ("app.shared.fo", "formatter", types.CompletionItemKind.Module),
            (
                "app.shared.formatter.For",
                "Formatter",
                types.CompletionItemKind.Class,
            ),
            ("kivy.ui", "uix", types.CompletionItemKind.Module),
            ("kivy.uix.wi", "widget", types.CompletionItemKind.Module),
            ("kivy.uix.widget.Wi", "Widget", types.CompletionItemKind.Class),
        ]
        for version, (path, expected, kind) in enumerate(cases, 1):
            text = directive_prefix + " Alias " + path
            client.send(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": version},
                    "contentChanges": [{"text": text}],
                },
            )
            result = client.request(
                "textDocument/completion",
                {
                    "textDocument": {"uri": uri},
                    "position": {"line": 0, "character": len(text)},
                },
            )
            item = next(
                item for item in result["items"] if item["label"] == expected
            )
            assert item["kind"] == kind.value
            assert item["textEdit"]["newText"] == expected
            assert item["textEdit"]["range"]["start"]["character"] == (
                len(text) - len(path.rsplit(".", 1)[-1])
            )
    finally:
        client.close()
