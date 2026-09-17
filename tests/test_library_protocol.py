"""Library metadata diagnostics and exports through the real stdio LSP."""

import sys
from pathlib import Path

import pytest

from test_server_protocol import Client


@pytest.mark.parametrize("problem", ["malformed", "conflicting"])
def test_closed_library_metadata_diagnostics_clear_after_watched_repair(
    tmp_path, problem,
):
    source = tmp_path / "src"
    source.mkdir()
    environment = tmp_path / ".venv"
    site = environment / "lib/python3.12/site-packages"
    (site / "kivy").mkdir(parents=True)
    (site / "kivy/__init__.py").write_text("")
    (environment / "pyvenv.cfg").write_text(
        f"home = {Path(sys.base_prefix) / 'bin'}\n"
        f"version = {sys.version.split()[0]}\n"
    )
    (tmp_path / "kivy-lsp.toml").write_text(
        'python-environment = ".venv"\n'
    )
    for name in ("healthy", "broken"):
        package = site / name
        package.mkdir()
        (package / "__init__.py").write_text(
            'raise AssertionError("Application imports must not run")\n'
            "class Value:\n    label: str\n"
        )
    healthy_metadata = site / "healthy/kivy-lsp.toml"
    healthy_exports = '[globals]\nHealthy = "healthy.Value"\n'
    if problem == "conflicting":
        healthy_exports += 'Recovered = "healthy.Value"\n'
    healthy_metadata.write_text(healthy_exports)
    broken_metadata = site / "broken/kivy-lsp.toml"
    broken_metadata.write_text(
        "[globals\n" if problem == "malformed"
        else '[globals]\nRecovered = "broken.Value"\n'
    )
    (source / "widgets.py").write_text(
        "import healthy, broken\n"
        "class Panel:\n    text: str\n    caption: str\n"
    )
    uri = (source / "panel.kv").as_uri()
    text = (
        "<Panel>:\n"
        "    text: Healthy.label\n"
        "    caption: Recovered.label\n"
    )
    client = Client()
    try:
        client.request(
            "initialize",
            {
                "processId": None,
                "rootUri": tmp_path.as_uri(),
                "capabilities": {},
            },
        )
        client.send("initialized", {})
        status = client.request(
            "workspace/executeCommand",
            {"command": "kivy-lsp.projectStatus", "arguments": []},
        )
        assert status["openDocuments"] == 0
        initial = client.diagnostics(broken_metadata.as_uri())
        assert initial
        assert all(item["code"] == "library-config" for item in initial)
        assert all(item["severity"] == 1 for item in initial)
        if problem == "conflicting":
            assert client.diagnostics(healthy_metadata.as_uri())
        client.send(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri, "languageId": "kivy",
                    "version": 1, "text": text,
                },
            },
        )
        healthy_completion = client.request(
            "textDocument/completion",
            {
                "textDocument": {"uri": uri},
                "position": {
                    "line": 1, "character": len("    text: Healthy."),
                },
            },
        )
        assert "label" in {
            item["label"] for item in healthy_completion["items"]
        }
        diagnostics = client.diagnostics(uri)
        assert diagnostics
        assert all(item["range"]["start"]["line"] == 2 for item in diagnostics)
        target = "broken.Value" if problem == "malformed" else "healthy.Value"
        broken_metadata.write_text(f'[globals]\nRecovered = "{target}"\n')
        client.send(
            "workspace/didChangeWatchedFiles",
            {"changes": [{"uri": broken_metadata.as_uri(), "type": 2}]},
        )
        repaired_completion = client.request(
            "textDocument/completion",
            {
                "textDocument": {"uri": uri},
                "position": {
                    "line": 2, "character": len("    caption: Recovered."),
                },
            },
        )
        assert "label" in {
            item["label"] for item in repaired_completion["items"]
        }
        assert not client.diagnostics(broken_metadata.as_uri())
        if problem == "conflicting":
            assert not client.diagnostics(healthy_metadata.as_uri())
        assert not client.diagnostics(uri)
    finally:
        client.close()
