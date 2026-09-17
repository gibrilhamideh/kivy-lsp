"""Validate value completion and diagnostics through real stdio LSP."""

from kivy_lsp.workspace.document import (
    PositionEncoding,
    TextDocument,
    TextPosition,
)
from test_server_protocol import Client


def test_option_completion_diagnostics_and_strict_reload(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    environment = tmp_path / ".venv"
    package = environment / "lib/python3.12/site-packages/kivy"
    package.mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("version = 3.12\n")
    (package / "__init__.py").write_text("")
    configuration = 'python-environment = ".venv"\n'
    config_file = tmp_path / "kivy-lsp.toml"
    config_file.write_text(configuration)
    (src / "widgets.py").write_text(
        "from kivy.properties import (\n"
        "    BooleanProperty, OptionProperty, StringProperty,\n"
        ")\n"
        "class Panel:\n"
        '    halign = OptionProperty("left", options=["left", "right"])\n'
        '    caption = OptionProperty("left", options=["left", "right"])\n'
        '    alignment = StringProperty("left")\n'
        '    mode = OptionProperty("cycle", options=["cycle", "continuous"])\n'
        "    display = BooleanProperty(False)\n"
    )
    uri = (src / "view.kv").as_uri()
    source = (
        "<Panel>:\n"
        '    halign: "hello"\n'
        '    display: "😀" and root.mode == "cy"\n'
        "    caption: root.alignment\n"
    )
    document = TextDocument(
        uri, source, position_encoding=PositionEncoding.UTF16
    )
    cursor = document.position_at(source.index('"cy"') + 3)
    client = Client()
    try:
        client.request(
            "initialize",
            {
                "processId": None,
                "rootUri": tmp_path.as_uri(),
                "capabilities": {
                    "general": {"positionEncodings": ["utf-16"]},
                },
            },
        )
        client.send("initialized", {})
        client.send(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "kivy",
                    "version": 1,
                    "text": source,
                },
            },
        )
        completion = client.request(
            "textDocument/completion",
            {
                "textDocument": {"uri": uri},
                "position": {
                    "line": cursor.line,
                    "character": cursor.character,
                },
            },
        )
        items = completion["items"]
        assert len(items) == 1
        edit = items[0]["textEdit"]
        assert edit["newText"] == '"cycle"'
        start = document.offset_at(TextPosition(**edit["range"]["start"]))
        end = document.offset_at(TextPosition(**edit["range"]["end"]))
        assert source[start:end] == '"cy"'

        diagnostics = client.diagnostics(uri)
        assert any(
            item["severity"] == 1
            and item["range"]["start"]["line"] == 1
            for item in diagnostics
        )
        assert any(
            item["severity"] == 2
            and item["range"]["start"]["line"] == 2
            for item in diagnostics
        )
        assert not any(
            item["range"]["start"]["line"] == 3 for item in diagnostics
        )

        updated = source[:start] + edit["newText"] + source[end:]
        updated = updated.replace('halign: "hello"', 'halign: "left"')
        client.send(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": updated}],
            },
        )
        client.request(
            "textDocument/documentSymbol", {"textDocument": {"uri": uri}}
        )
        assert not client.diagnostics(uri)

        config_file.write_text(
            configuration + "[diagnostics]\nstrict = true\n"
        )
        client.send(
            "workspace/didChangeWatchedFiles",
            {"changes": [{"uri": config_file.as_uri(), "type": 2}]},
        )
        client.request(
            "textDocument/documentSymbol", {"textDocument": {"uri": uri}},
        )
        diagnostics = client.diagnostics(uri)
        assert len(diagnostics) == 1
        assert diagnostics[0]["severity"] == 2
        assert diagnostics[0]["range"]["start"]["line"] == 3
        config_file.write_text(configuration)
        client.request(
            "workspace/executeCommand",
            {"command": "kivy-lsp.reloadProject", "arguments": []},
        )
        assert not client.diagnostics(uri)
    finally:
        client.close()
