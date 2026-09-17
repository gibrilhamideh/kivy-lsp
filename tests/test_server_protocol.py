"""Exercise the actual stdio server and negotiated LSP serialization."""

import json
import os
from queue import Queue
import subprocess
import sys
from threading import Thread
import time

import pytest


class Client:
    def __init__(self):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "kivy_lsp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ.copy(),
        )
        self.messages = Queue()
        self.notifications = []
        self.sequence = 0
        Thread(target=self._read, daemon=True).start()

    def _read(self):
        stream = self.process.stdout
        while True:
            headers = {}
            while line := stream.readline():
                if line in (b"\r\n", b"\n"):
                    break
                key, value = line.decode().split(":", 1)
                headers[key.lower()] = value.strip()
            if not line:
                return
            body = stream.read(int(headers["content-length"]))
            self.messages.put(json.loads(body))

    def send(self, method, params=None, request_id=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if request_id is not None:
            message["id"] = request_id
        body = json.dumps(message).encode()
        self.process.stdin.write(
            f"Content-Length: {len(body)}\r\n\r\n".encode() + body
        )
        self.process.stdin.flush()

    def request(self, method, params=None):
        self.sequence += 1
        request_id = self.sequence
        self.send(method, params, request_id)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            message = self.messages.get(timeout=15)
            if message.get("id") == request_id:
                assert "error" not in message, message
                return message.get("result")
            self.notifications.append(message)
        raise AssertionError(f"No response to {method}")

    def diagnostics(self, uri):
        matches = [
            item["params"]["diagnostics"]
            for item in self.notifications
            if item.get("method") == "textDocument/publishDiagnostics"
            and item["params"]["uri"] == uri
        ]
        assert matches, "Expected published diagnostics"
        return matches[-1]

    def close(self):
        try:
            self.request("shutdown")
            self.send("exit")
            self.process.wait(timeout=5)
            assert self.process.returncode == 0
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_real_server_features_and_python_diagnostic_refresh(
    tmp_path, encoding
):
    # A literal percent sequence catches accidental URI double decoding.
    project = tmp_path / "project%20name"
    src = project / "src"
    src.mkdir(parents=True)
    environment = project / ".venv"
    package = environment / "lib/python3.12/site-packages/kivy"
    package.mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("version = 3.12\n")
    (package / "__init__.py").write_text("")
    (project / "kivy-lsp.toml").write_text(
        'python-environment = ".venv"\n'
        "[format]\nline-length = 42\n"
    )
    python_file = src / "widgets.py"
    python_text = (
        "from kivy.properties import StringProperty\n"
        "class Panel:\n"
        '    text = StringProperty("")\n'
        '    caption = StringProperty("")\n'
        "    def describe(self, value: str) -> str:\n"
        "        return value\n"
    )
    python_file.write_text(python_text)
    uri = (src / "main.kv").as_uri()
    text = (
        "<Panel>:\n"
        '    text: "hello"\n'
        '    caption: "😀" + root.describe("a fairly long argument")\n'
    )
    client = Client()
    try:
        result = client.request(
            "initialize",
            {
                "processId": None,
                "rootUri": src.as_uri(),
                "capabilities": {
                    "general": {"positionEncodings": [encoding]},
                },
            },
        )
        capabilities = result["capabilities"]
        assert capabilities["positionEncoding"] == encoding
        for capability in (
            "documentFormattingProvider",
            "hoverProvider",
            "signatureHelpProvider",
            "foldingRangeProvider",
            "selectionRangeProvider",
            "codeActionProvider",
            "referencesProvider",
            "renameProvider",
        ):
            assert capabilities[capability]
        client.send("initialized", {})
        client.send(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "kv",
                    "version": 1,
                    "text": text,
                },
            },
        )
        edits = client.request(
            "textDocument/formatting",
            {
                "textDocument": {"uri": uri},
                "options": {"tabSize": 4, "insertSpaces": True},
            },
        )
        assert len(edits) == 1
        formatted = edits[0]["newText"]
        assert "    caption:\n        " in formatted
        assert not client.diagnostics(uri)

        prefix = '    caption: "😀" + root.'
        character = len(prefix.encode(encoding))
        if encoding == "utf-16":
            character = (character - 2) // 2  # Exclude the BOM.
        hover = client.request(
            "textDocument/hover",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 2, "character": character + 1},
            },
        )
        assert "describe" in hover["contents"]["value"]
        assert hover["range"]["start"]["character"] == character

        client.send(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": python_file.as_uri(),
                    "languageId": "python",
                    "version": 1,
                    "text": python_text.replace(
                        "StringProperty", "NumericProperty"
                    ),
                },
            },
        )
        status = client.request(
            "workspace/executeCommand",
            {
                "command": "kivy-lsp.projectStatus",
                "arguments": [],
            },
        )
        assert status["projectRoot"] == str(project)
        assert any(
            item.get("code") == "kv-incompatible-property-value"
            for item in client.diagnostics(uri)
        )
        client.send(
            "textDocument/didClose",
            {
                "textDocument": {"uri": python_file.as_uri()},
            },
        )
        client.request(
            "textDocument/documentSymbol",
            {
                "textDocument": {"uri": uri},
            },
        )
        assert not client.diagnostics(uri)
    finally:
        client.close()
