from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from kivy_lsp.check_cli import main
from kivy_lsp.config import load_config
from kivy_lsp.workspace import discovery
from kivy_lsp.workspace.project import ProjectWorkspace


@pytest.fixture
def project(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    env = tmp_path / ".venv"
    env.mkdir()
    (env / "pyvenv.cfg").write_text(
        f"home = {Path(sys.base_prefix) / 'bin'}\n"
        f"version = {sys.version.split()[0]}\n"
    )
    deps = env / "lib/python3.12/site-packages/kivy"
    deps.mkdir(parents=True)
    (deps / "__init__.py").write_text("")
    (deps / "properties.pyi").write_text(
        "class NumericProperty: ...\n"
        "class StringProperty: ...\n"
    )
    (tmp_path / "kivy-lsp.toml").write_text(
        'python-environment = ".venv"\n'
        'excludes = ["ignored", ".venv"]\n'
    )
    (src / "main.py").write_text(
        "from kivy.properties import NumericProperty, StringProperty\n"
        "class Widget:\n"
        "    count = NumericProperty(0)\n"
        '    text = StringProperty("")\n'
        'raise RuntimeError("Application source must not execute")\n'
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def json_check(capsys, *arguments):
    status = main([*arguments, "--output-format", "json"])
    captured = capsys.readouterr()
    assert captured.err == ""
    return status, json.loads(captured.out)


def test_checks_saved_unopened_files_with_editor_engine(project, capsys):
    path = project / "src/view.kv"
    path.write_text('Widget:\n    count: "bad"\n')
    workspace = ProjectWorkspace(load_config(project))
    workspace.initialize()
    assert not workspace.open_documents
    expected = workspace.diagnostics_for(path.as_uri())
    status, report = json_check(capsys)
    assert status == 1
    assert report["files"] == ["src/view.kv"]
    assert report["summary"]["checked_files"] == 1
    assert report["summary"]["complete"]
    assert [(d["code"], d["message"]) for d in report["diagnostics"]] == [
        (d.code, d.message) for d in expected
    ]
    assert report["diagnostics"][0]["line"] == 2
    assert report["diagnostics"][0]["column"] == 12
    assert path.read_text() == 'Widget:\n    count: "bad"\n'


def test_target_filter_keeps_surrounding_python_context(project, capsys):
    views = project / "src/views"
    views.mkdir()
    (views / "good.kv").write_text("Widget:\n    count: 2\n")
    (project / "src/bad.kv").write_text('Widget:\n    count: "bad"\n')
    status, report = json_check(capsys, "src/views")
    assert status == 0
    assert report["files"] == ["src/views/good.kv"]
    assert not report["diagnostics"]


def test_explicit_kv_outside_configured_kv_paths(project, capsys):
    (project / "other.kv").write_text('Widget:\n    count: "bad"\n')
    status, report = json_check(capsys, "other.kv")
    assert status == 1
    assert report["files"] == ["other.kv"]
    assert report["diagnostics"][0]["code"] == (
        "kv-incompatible-property-value"
    )


def test_excluded_explicit_target_does_not_check_whole_project(
    project, capsys,
):
    ignored = project / "src/ignored"
    ignored.mkdir()
    (ignored / "bad.kv").write_text('Widget:\n    count: "bad"\n')
    (project / "src/bad.kv").write_text('Widget:\n    count: "bad"\n')
    status, report = json_check(capsys, "src/ignored/bad.kv")
    assert status == 0
    assert report["files"] == []
    assert report["diagnostics"] == []


def test_recursive_discovery_respects_excludes(project, capsys):
    ignored = project / "src/ignored"
    ignored.mkdir()
    (ignored / "bad.kv").write_bytes(b"\xff")
    (ignored / "bad.py").write_text("def broken(\n")
    (project / "src/good.kv").write_text("Widget:\n    count: 2\n")
    status, report = json_check(capsys)
    assert status == 0
    assert report["files"] == ["src/good.kv"]


def test_warning_exit_policy_and_text_output(project, capsys):
    (project / "src/view.kv").write_text(
        "#:include missing.kv\nWidget:\n    count: 2\n"
    )
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "warning:" in output
    assert "[kv-include-unresolved]" in output
    assert "Checked 1 KV file: 0 errors, 1 warning" in output
    status, report = json_check(capsys, "--warnings-as-errors")
    assert status == 1
    assert report["summary"]["warnings"] == 1
    assert report["summary"]["complete"]


@pytest.mark.parametrize("name", ["gone.kv", "not_source.txt"])
def test_invalid_path_is_incomplete_json(project, capsys, name):
    if name.endswith(".txt"):
        (project / name).write_text("content")
    status, report = json_check(capsys, name)
    assert status == 2
    assert not report["summary"]["complete"]
    assert report["issues"][0]["code"] == "check-path"


def test_invalid_config_is_incomplete_json(project, capsys):
    (project / "kivy-lsp.toml").write_text("[broken\n")
    status, report = json_check(capsys)
    assert status == 2
    assert report["issues"][0]["code"] == "check-project"


def test_unreadable_kv_is_reported_instead_of_silently_skipped(
    project, capsys,
):
    (project / "src/bad.kv").write_bytes(b"\xff")
    status, report = json_check(capsys)
    assert status == 2
    assert report["files"] == []
    assert report["issues"][0]["path"] == "src/bad.kv"
    assert report["issues"][0]["code"] == "check-source-read"


def test_missing_configured_kv_root_is_incomplete(project, capsys):
    config = project / "kivy-lsp.toml"
    config.write_text(config.read_text() + 'kv-paths = ["missing"]\n')
    status, report = json_check(capsys)
    assert status == 2
    assert report["issues"][0]["path"] == "missing"


def test_discovered_kv_that_disappears_is_incomplete(
    project, capsys, monkeypatch,
):
    path = project / "src/gone.kv"
    path.write_text("Widget:\n    count: 2\n")
    original = ProjectWorkspace.source_document

    def source_document(workspace, uri):
        if uri == path.as_uri():
            return None
        return original(workspace, uri)

    monkeypatch.setattr(ProjectWorkspace, "source_document", source_document)
    status, report = json_check(capsys)
    assert status == 2
    assert not report["summary"]["complete"]
    assert report["issues"][0]["path"] == "src/gone.kv"
    assert report["issues"][0]["code"] == "check-source-read"


def test_directory_read_failure_is_incomplete(project, capsys, monkeypatch):
    original = discovery.os.walk

    def walk(root, **kwargs):
        if Path(root) == project / "src":
            kwargs["onerror"](PermissionError(13, "denied", str(root)))
            return iter(())
        return original(root, **kwargs)

    monkeypatch.setattr(discovery.os, "walk", walk)
    status, report = json_check(capsys)
    assert status == 2
    assert any("denied" in item["message"] for item in report["issues"])


def test_environment_issue_is_incomplete(project, capsys):
    config = project / "kivy-lsp.toml"
    config.write_text(config.read_text().replace('".venv"', '"missing"'))
    status, report = json_check(capsys)
    assert status == 2
    assert any(
        item["code"] == "check-environment" for item in report["issues"]
    )


def test_python_syntax_error_affecting_context_is_reported(project, capsys):
    (project / "src/broken.py").write_text("def broken(\n")
    (project / "src/view.kv").write_text("Widget:\n    count: 2\n")
    status, report = json_check(capsys, "src/view.kv")
    assert status == 1
    assert report["summary"]["complete"]
    assert any(
        item["path"] == "src/broken.py" for item in report["diagnostics"]
    )


def test_unicode_columns_are_characters_and_output_is_deterministic(
    project, capsys,
):
    source = 'Widget:\n    text: "😀" + missing\n'
    (project / "src/view.kv").write_text(source)
    status, first = json_check(capsys)
    _, second = json_check(capsys)
    assert status == 1
    assert first == second
    diagnostic = next(
        item for item in first["diagnostics"] if "missing" in item["message"]
    )
    assert diagnostic["column"] == source.splitlines()[1].index("missing") + 1


def test_module_dispatch_without_server_import(project):
    source_root = Path(__file__).resolve().parents[1] / "src"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(source_root)
    script = (
        "import runpy, sys\n"
        "class Guard:\n"
        "    def find_spec(self, name, *args):\n"
        "        if name.startswith(('pygls', 'kivy_lsp.server')):\n"
        "            raise RuntimeError('server import forbidden')\n"
        "sys.meta_path.insert(0, Guard())\n"
        "sys.argv = ['kivy-lsp', 'check', '--output-format', 'json']\n"
        "runpy.run_module('kivy_lsp', run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=project, env=environment,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["summary"]["complete"]
    assert not result.stderr


def test_format_explicit_excluded_file_is_unchanged(project, capsys):
    from kivy_lsp.cli import main as format_main

    ignored = project / "src/ignored"
    ignored.mkdir()
    path = ignored / "view.kv"
    before = "Widget:\n    count:  2\n"
    path.write_text(before)
    assert format_main(["--in-place", str(path)]) == 0
    assert path.read_text() == before
    assert not capsys.readouterr().out


def test_format_nested_path_uses_project_relative_excludes(project, capsys):
    from kivy_lsp.cli import main as format_main

    config = project / "kivy-lsp.toml"
    config.write_text(
        config.read_text().replace('"ignored"', '"src/views/ignored"')
    )
    ignored = project / "src/views/ignored"
    ignored.mkdir(parents=True)
    path = ignored / "view.kv"
    before = "Widget:\n    count:  2\n"
    path.write_text(before)
    assert format_main(["--in-place", "src/views"]) == 0
    assert path.read_text() == before
    assert not capsys.readouterr().out


def test_check_finds_standalone_config_above_nested_package(
    project, capsys, monkeypatch,
):
    nested = project / "src/views"
    nested.mkdir()
    (project / "pyproject.toml").write_text("[invalid project metadata\n")
    (nested / "pyproject.toml").write_text(
        '[tool.kivy-lsp]\nsource-roots = ["missing"]\n'
    )
    (nested / "panel.kv").write_text('Widget:\n    count: "bad"\n')
    monkeypatch.chdir(nested)
    status, report = json_check(capsys)
    assert status == 1
    assert report["projects"] == [str(project)]
    assert report["files"] == ["panel.kv"]
    assert report["summary"]["complete"]
    assert report["diagnostics"][0]["code"] == (
        "kv-incompatible-property-value"
    )


def test_format_finds_standalone_config_above_nested_package(
    project, capsys, monkeypatch,
):
    from kivy_lsp.cli import main as format_main

    nested = project / "src/views"
    nested.mkdir()
    (nested / "pyproject.toml").write_text("[invalid package metadata\n")
    config = project / "kivy-lsp.toml"
    config.write_text(config.read_text() + "[format]\nline-length = 18\n")
    source = "Widget:\n    text: root.measurement\n"
    monkeypatch.chdir(nested)
    monkeypatch.setattr(sys, "stdin", StringIO(source))
    assert format_main(["--stdin-filename", "panel.kv", "-"]) == 0
    output = capsys.readouterr()
    assert output.out == "Widget:\n    text:\n        root.measurement\n"
    assert not output.err


class TerminalOutput(StringIO):
    def __init__(self, *, tty):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


def terminal_check(
    monkeypatch, arguments, *, stdout_tty=True, stderr_tty=True,
):
    output = TerminalOutput(tty=stdout_tty)
    errors = TerminalOutput(tty=stderr_tty)
    with monkeypatch.context() as streams:
        streams.setattr(sys, "stdout", output)
        streams.setattr(sys, "stderr", errors)
        result = main(arguments)
    return result, output.getvalue(), errors.getvalue()


def strip_colors(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@pytest.mark.parametrize(
    "source, arguments, expected_status",
    [
        ("Widget:\n    count: 2\n", [], 0),
        (
            "#:include missing.kv\nWidget:\n    count: 2\n",
            ["--warnings-as-errors"], 1,
        ),
        ('Widget:\n    count: "bad"\n', [], 1),
        ("Widget:\n    count: 2\n", ["missing.kv"], 2),
    ],
)
def test_forced_colors_preserve_text_locations_and_exit_codes(
    project, monkeypatch, source, arguments, expected_status,
):
    (project / "src/view.kv").write_text(source)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    plain_status, plain_out, plain_err = terminal_check(
        monkeypatch, [*arguments, "--color", "never"],
    )
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    color_status, color_out, color_err = terminal_check(
        monkeypatch, [*arguments, "--color", "always"],
        stdout_tty=False, stderr_tty=False,
    )
    assert plain_status == color_status == expected_status
    assert "\x1b[" not in plain_out + plain_err
    assert "\x1b[" in color_out + color_err
    assert strip_colors(color_out) == plain_out
    assert strip_colors(color_err) == plain_err


@pytest.mark.parametrize(
    "flags, stdout_tty, stderr_tty",
    [
        ([], True, False),
        (["--color", "auto"], False, True),
        ([], False, False),
    ],
)
def test_auto_colors_use_each_actual_output_stream(
    project, monkeypatch, flags, stdout_tty, stderr_tty,
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    status, output, errors = terminal_check(
        monkeypatch, ["missing.kv", *flags],
        stdout_tty=stdout_tty, stderr_tty=stderr_tty,
    )
    assert status == 2
    assert ("\x1b[" in output) == stdout_tty
    assert ("\x1b[" in errors) == stderr_tty
    assert "incomplete" in strip_colors(output)
    assert "missing.kv" in strip_colors(errors)


@pytest.mark.parametrize(
    "no_color, term, colored",
    [("1", "xterm", False), ("", "xterm", True), ("", "dumb", False)],
)
def test_auto_colors_honor_terminal_environment(
    project, monkeypatch, no_color, term, colored,
):
    monkeypatch.setenv("NO_COLOR", no_color)
    monkeypatch.setenv("TERM", term)
    status, output, errors = terminal_check(monkeypatch, ["missing.kv"])
    assert status == 2
    assert ("\x1b[" in output) == colored
    assert ("\x1b[" in errors) == colored


def test_json_ignores_forced_color_even_on_terminal(project, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    arguments = ["missing.kv", "--output-format", "json"]
    plain = terminal_check(monkeypatch, [*arguments, "--color", "never"])
    colored = terminal_check(monkeypatch, [*arguments, "--color", "always"])
    assert plain == colored
    status, output, errors = colored
    assert status == 2
    assert "\x1b" not in output
    assert not errors
    assert json.loads(output)["summary"]["complete"] is False


def test_keyboard_type_alias_from_dependency_matches_editor(project, capsys):
    package = (
        project / ".venv/lib/python3.12/site-packages/kivyfn/ui/keyboard"
    )
    package.mkdir(parents=True)
    for parent in (package.parent, package.parent.parent):
        (parent / "__init__.py").write_text("")
    (package / "__init__.py").write_text(
        "from .keyboard import FnKeyboard\nfrom .key import FnKey\n"
    )
    (package / "types.py").write_text(
        "from collections.abc import Sequence\n"
        "from typing import Literal, TypeAlias\n"
        "FnKeycode: TypeAlias = str | None\n"
        'FnKeyboardModifier = Literal["alt", "ctrl", "shift"]\n'
        "FnKeyboardModifiers: TypeAlias = Sequence[FnKeyboardModifier]\n"
    )
    (package / "key.py").write_text(
        "from dataclasses import dataclass\n"
        "from .types import FnKeycode\n"
        "@dataclass(frozen=True, slots=True)\n"
        "class FnKey:\n"
        "    keycode: FnKeycode = None\n"
        '    text: str = ""\n'
    )
    (package / "keyboard.py").write_text(
        "from __future__ import annotations\n"
        "from .key import FnKey\n"
        "from .types import FnKeycode, FnKeyboardModifiers\n"
        "class FnKeyboard:\n"
        + "".join(
            f"    def {method}(\n"
            '        self, key: FnKey | FnKeycode, text: str = "",\n'
            "        modifiers: FnKeyboardModifiers = (),\n"
            "    ) -> None: ...\n"
            for method in ("do_key_down", "do_key_up", "do_key_press")
        )
        + 'raise RuntimeError("Dependency code must not execute")\n'
    )
    (project / "src/keyboard.py").write_text(
        "from kivyfn.ui.keyboard import FnKeyboard, FnKey\n"
        "class UIKeyboard(FnKeyboard):\n"
        "    key: FnKey\n"
    )
    path = project / "src/keyboard.kv"
    source = "<UIKeyboard>:\n" + "".join(
        "    Widget:\n"
        f'        on_press: root.do_key_down("{key}")\n'
        f'        on_release: root.do_key_up("{key}")\n'
        for key in ("backspace", "left", "right")
    ) + (
        '    on_submit: root.do_key_press("enter", "\\n")\n'
        "    on_reset: root.do_key_down(None)\n"
        "    on_custom: root.do_key_down(root.key)\n"
    )
    path.write_text(source)
    status, report = json_check(capsys)
    assert status == 0
    assert report["summary"]["complete"]
    assert report["files"] == ["src/keyboard.kv"]
    assert report["diagnostics"] == []

    workspace = ProjectWorkspace(load_config(project))
    workspace.initialize()
    assert workspace.diagnostics_for(path.as_uri()) == ()

    path.write_text(source + "    on_invalid: root.do_key_down(42)\n")
    status, report = json_check(capsys)
    assert status == 1
    assert len(report["diagnostics"]) == 1
    assert report["diagnostics"][0]["code"] == "kv-argument-type"
    assert "42" in report["diagnostics"][0]["message"]
