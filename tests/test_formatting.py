from __future__ import annotations

import ast
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from kivy_lsp.formatting import FormatOptions, format_source
from kivy_lsp.kv.nodes import PropertyNode
from kivy_lsp.kv.parser import parse


def _value(source: str, name: str = "value") -> str:
    result = parse(source)
    rule = result.document.items[0]
    for node in rule.body:
        if isinstance(node, PropertyNode) and node.name == name:
            assert node.value is not None
            return source[node.value.span.start:node.value.span.end].strip()
    raise AssertionError(f"missing {name}")


def _assert_equivalent(before: str, after: str) -> None:
    expected = ast.dump(ast.parse(_value(before), mode="eval"))
    actual = ast.dump(ast.parse(_value(after), mode="eval"))
    assert actual == expected


def test_short_property_remains_on_one_line():
    source = "<Widget>:\n    text: root.title\n"
    result = format_source(source)
    assert result.text == source
    assert not result.issues


def test_width_excludes_leading_indentation():
    source = (
        "<Widget>:\n"
        "    BoxLayout:\n"
        "        BoxLayout:\n"
        "            text: root.title\n"
    )
    assert format_source(source, FormatOptions(16)).text == source
    assert max(map(len, source.splitlines())) > 16


def test_header_moves_when_property_width_exceeds_limit():
    source = "<Widget>:\n    value: root.measurement\n"
    result = format_source(source, FormatOptions(18))
    assert result.text == (
        "<Widget>:\n    value:\n        root.measurement\n"
    )
    assert not result.issues


def test_conditionals_break_after_else():
    source = (
        "<Widget>:\n"
        "    value: app.error if root.active else app.primary "
        "if root.enabled else app.disabled\n"
    )
    result = format_source(source, FormatOptions(36))
    assert result.text == (
        "<Widget>:\n"
        "    value:\n"
        "        app.error if root.active else \\\n"
        "        app.primary if root.enabled else \\\n"
        "        app.disabled\n"
    )
    _assert_equivalent(source, result.text)


def test_long_condition_breaks_before_boolean_operators():
    source = (
        "<Widget>:\n    value: app.error if root.active "
        "and root.alarm_enabled and not root.snoozed else app.primary\n"
    )
    result = format_source(source, FormatOptions(35))
    assert "        and root.alarm_enabled \\\n" in result.text
    assert "        and not root.snoozed else \\\n" in result.text
    _assert_equivalent(source, result.text)


def test_continuation_characters_count_towards_width():
    source = "<Widget>:\n    value: a if abcdefghij else b\n"
    result = format_source(source, FormatOptions(22))
    assert not result.issues
    assert all(len(line.lstrip()) <= 22 for line in result.text.splitlines())
    _assert_equivalent(source, result.text)


@pytest.mark.parametrize("expression", [
    ("[root.first_measurement, root.second_measurement, "
    "root.third_measurement]"),
    ('{"minimum": root.minimum_temperature, '
    '"maximum": root.maximum_temperature}'),
    ("Format.summary(root.first_measurement, "
    "root.second_measurement, unit=True)"),
    ("(root.first_measurement, root.second_measurement, "
    "root.third_measurement)"),
    ("{root.first_measurement, root.second_measurement, "
    "root.third_measurement}"),
    ("(root.first_measurement + root.second_measurement) "
    "* root.third_measurement"),
    ("root.first_measurement if root.active else (root.second_measurement "
    "if root.enabled else root.third_measurement)"),
    ("(root.first_measurement if root.active else root.second_measurement) "
    "if root.enabled else root.third_measurement"),
    "root.measurements[root.first_measurement:root.second_measurement]",
    "root.measurements[root.first_measurement, root.second_measurement]",
    "call(*root.first_measurement, **root.second_measurement)",
    'call("escaped\\nvalue", r"raw\\tvalue", f"{root.value:02.1f}")',
    ('{"limits": {"minimum": root.minimum_temperature, '
    '"maximum": root.maximum_temperature}, "fallback": '
    "theme.with_tone(theme.background, root.inactive_tone)}"),
])
def test_expression_meaning_and_idempotence(expression):
    source = f"<Widget>:\n    value: {expression}\n"
    result = format_source(source, FormatOptions(40))
    _assert_equivalent(source, result.text)
    assert format_source(result.text, FormatOptions(40)).text == result.text
    assert not parse(result.text).diagnostics
    lines = result.text.splitlines()
    if "    value:" in lines:
        for line in lines[2:]:
            assert len(line) - len(line.lstrip()) == 8
        for line in lines[2:-1]:
            assert line.endswith(" \\")


def test_redundant_outer_group_removed_without_losing_required_group():
    source = (
        "<Widget>:\n    value:\n"
        "        (\n"
        "        (root.first + root.second) * root.third\n"
        "        )\n"
    )
    result = format_source(source)
    assert result.text == (
        "<Widget>:\n"
        "    value: (root.first + root.second) * root.third\n"
    )
    _assert_equivalent(source, result.text)


def test_long_calls_have_one_argument_per_line():
    source = (
        "<Widget>:\n    value: Formatter.summary(root.measurement, "
        "root.target, precision=root.precision)\n"
    )
    result = format_source(source, FormatOptions(35))
    assert result.text == (
        "<Widget>:\n    value:\n"
        "        Formatter.summary( \\\n"
        "        root.measurement, \\\n"
        "        root.target, \\\n"
        "        precision=root.precision, \\\n"
        "        )\n"
    )


def test_event_statements_remain_separate():
    source = (
        "<Widget>:\n    on_release:\n"
        "        root.value = calculate(root.first_value, root.second_value)\n"
        "        root.refresh()\n"
    )
    result = format_source(source, FormatOptions(35))
    assert not result.issues
    assert "        )\n        root.refresh()" in result.text
    assert "        ) \\\n        root.refresh()" not in result.text
    assert result.text == format_source(result.text, FormatOptions(35)).text


def test_inline_semicolon_events_keep_statement_order():
    source = "<Widget>:\n    on_release: root.one(); root.two()\n"
    assert format_source(source).text == (
        "<Widget>:\n    on_release:\n"
        "        root.one()\n        root.two()\n"
    )


@pytest.mark.parametrize("value", [
    '"literal with  # and : and \\\" escapes"',
    'f"{root.value!r:>10}"',
    '"αβγδ"',
    "root.reactive.contiguous.chain",
])
def test_literals_and_dotted_chains_preserved(value):
    source = f"<Widget>:\n    value: {value}\n"
    result = format_source(source, FormatOptions(10))
    assert value in result.text
    assert bool(result.issues) == (len(value) > 10)


def test_unicode_width_uses_characters():
    source = '<Widget>:\n    value: "αβγδ"\n'
    assert format_source(source, FormatOptions(13)).text == source


def test_unbreakable_overflow_is_reported():
    source = "<Widget>:\n    value: app.theme.color.long_member_name\n"
    result = format_source(source, FormatOptions(20))
    assert "app.theme.color.long_member_name" in result.text
    assert any(issue.code == "format-overflow" for issue in result.issues)


def test_fmt_off_on_and_skip_complete_widget():
    source = (
        "<Widget>:\n"
        "    # fmt: off\n"
        "    value:  root.value\n"
        "    # fmt: on\n"
        "    text:  root.title\n"
        "    # fmt: skip\n"
        "    Label:\n"
        "        text:  root.label\n"
        "    Label:\n"
        "        text:  root.other\n"
    )
    result = format_source(source)
    assert "    value:  root.value" in result.text
    assert "    text: root.title" in result.text
    assert "        text:  root.label" in result.text
    assert "        text: root.other" in result.text


@pytest.mark.parametrize("source", [
    "<Widget>:\n    value: root.value  # keep comment\n",
    ("<Widget>:\n    value:\n        [\n        # inside list\n"
    "        1,\n        ]\n"),
    '<Widget>:\n    value: """first\n        second"""\n',
    "<Widget>:\n    value: root.value +\n",
    "<Widget>:\n    value: [item for item in root.very_long_measurements]\n",
    ("<Widget>:\n    on_release:\n        if root.active:\n"
    "            root.run()\n"),
])
def test_unsupported_or_incomplete_region_preserved(source):
    result = format_source(source, FormatOptions(30))
    assert result.text == source
    assert result.issues


def test_unsafe_region_does_not_block_independent_property():
    source = (
        "<Widget>:\n    value: root.value +\n"
        "    text:  root.title\n"
    )
    result = format_source(source)
    assert "    value: root.value +" in result.text
    assert "    text: root.title" in result.text


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("final_newline", [True, False])
def test_newline_style_and_final_newline_preserved(newline, final_newline):
    source = newline.join(["<Widget>:", "    value:  root.value"])
    if final_newline:
        source += newline
    result = format_source(source)
    expected = source.replace("value:  ", "value: ")
    assert result.text == expected


def test_structural_indent_normalized_to_four_spaces():
    source = "<Widget>:\n  BoxLayout:\n    text: root.title\n"
    assert format_source(source).text == (
        "<Widget>:\n    BoxLayout:\n        text: root.title\n"
    )


def test_preserved_region_does_not_get_wrong_structural_depth():
    source = (
        "<Widget>:\n  value: root.value # keep\n"
        "  text:  root.title\n"
    )
    result = format_source(source)
    assert result.text == source.replace("text:  ", "text: ")


def _cli(*arguments: str, source: str | None = None):
    return subprocess.run(
        [sys.executable, "-m", "kivy_lsp", "format", *arguments],
        input=source, capture_output=True, text=True, check=False,
    )


def test_cli_check_diff_write_and_stdin(tmp_path):
    filename = tmp_path / "sample.kv"
    before = "<Widget>:\n    value:  root.value\n"
    after = before.replace("value:  ", "value: ")
    filename.write_text(before)
    checked = _cli("--check", str(filename))
    assert checked.returncode == 1
    assert filename.read_text() == before
    diff = _cli("--diff", str(filename))
    assert diff.returncode == 1
    assert "-    value:  root.value" in diff.stdout
    assert "+    value: root.value" in diff.stdout
    written = _cli("--in-place", str(filename))
    assert written.returncode == 0
    assert filename.read_text() == after
    assert _cli("--check", str(filename)).returncode == 0
    stdin = _cli("-", source=before)
    assert stdin.returncode == 0
    assert stdin.stdout == after


def test_cli_config_width_and_explicit_override(tmp_path):
    (tmp_path / "kivy-lsp.toml").write_text(
        "[format]\nline-length = 18\n"
    )
    filename = tmp_path / "sample.kv"
    source = "<Widget>:\n    value: root.measurement\n"
    result = _cli("--stdin-filename", str(filename), "-", source=source)
    assert "    value:\n" in result.stdout
    overridden = _cli(
        "--stdin-filename", str(filename), "--line-length", "79", "-",
        source=source,
    )
    assert overridden.stdout == source


def test_cli_errors_have_distinct_exit_status(tmp_path):
    assert _cli(str(tmp_path / "absent.kv")).returncode == 2
    assert _cli("--line-length", "0", "-", source="").returncode == 2


def test_cli_preserves_crlf_when_writing(tmp_path):
    filename = tmp_path / "sample.kv"
    filename.write_bytes(b"<Widget>:\r\n    text:  root.title\r\n")
    result = _cli("--in-place", str(filename))
    assert result.returncode == 0
    assert filename.read_bytes() == b"<Widget>:\r\n    text: root.title\r\n"


def test_cli_directory_prunes_excluded_paths(tmp_path):
    (tmp_path / "normal.kv").write_text("<Widget>:\n    text:  root.title\n")
    excluded = tmp_path / ".venv"
    excluded.mkdir()
    ignored = excluded / "ignored.kv"
    ignored.write_text("<Widget>:\n    text:  root.title\n")
    assert _cli("--in-place", str(tmp_path)).returncode == 0
    assert "text: root.title" in (tmp_path / "normal.kv").read_text()
    assert "text:  root.title" in ignored.read_text()


def test_actual_kivy_parser_accepts_generated_layouts():
    os.environ["KIVY_NO_ARGS"] = "1"
    os.environ["KIVY_NO_CONSOLELOG"] = "1"
    parser = pytest.importorskip("kivy.lang.parser").Parser
    expressions = [
        "max(root.width, root.height) if root.disabled else root.width",
        "[root.width, root.height, root.x, root.y]",
        '{"width": root.width, "height": root.height, "x": root.x}',
        "(root.width + root.height) * (root.x + root.y)",
    ]
    for expression in expressions:
        source = f"<Widget>:\n    value: {expression}\n"
        result = format_source(source, FormatOptions(30))
        assert not result.issues
        before = parser(content=source, filename="<before>")
        after = parser(content=result.text, filename="<after>")
        before_value = before.rules[0][1].properties["value"]
        after_value = after.rules[0][1].properties["value"]
        assert before_value.watched_keys == after_value.watched_keys


def test_subscript_and_call_chain_expand_the_argument_group():
    source = (
        "<Widget>:\n    value: root.items[0].summary("
        "root.measurement, root.target, precision=root.precision)\n"
    )
    result = format_source(source, FormatOptions(38))
    assert "        root.items[0].summary( \\\n" in result.text
    assert not result.issues
    _assert_equivalent(source, result.text)


def test_long_dictionary_entry_wraps_value_after_colon():
    source = (
        '<Widget>:\n    value: {"minimum_temperature": '
        "root.minimum_temperature}\n"
    )
    result = format_source(source, FormatOptions(30))
    assert '        "minimum_temperature": \\\n' in result.text
    assert "        root.minimum_temperature, \\\n" in result.text
    assert not result.issues
    _assert_equivalent(source, result.text)
    assert result.text == format_source(result.text, FormatOptions(30)).text


def test_fmt_skip_directive_does_not_skip_following_rule():
    source = (
        "# fmt: skip\n#:kivy 2.3.1\n"
        "<Widget>:\n    value:  root.value\n"
    )
    result = format_source(source)
    assert result.text.endswith("    value: root.value\n")


def test_lsp_and_engine_return_same_content_and_full_encoded_range():
    from lsprotocol import types

    from kivy_lsp.features.formatting import register_formatting
    from kivy_lsp.workspace.document import TextDocument

    source = '<Widget>:\n    value:  "😀"'
    document = TextDocument("file:///sample.kv", source)
    options = FormatOptions(79)
    workspace = SimpleNamespace(
        config=SimpleNamespace(format=options), document=lambda uri: document,
    )
    handlers = {}

    def feature(method):
        return lambda handler: handlers.update({method: handler})

    server = SimpleNamespace(
        feature=feature,
        window_log_message=lambda message: None,
    )
    register_formatting(server, lambda: workspace)
    handler = handlers[types.TEXT_DOCUMENT_FORMATTING]
    edits = handler(types.DocumentFormattingParams(
        text_document=types.TextDocumentIdentifier(document.uri),
        options=types.FormattingOptions(tab_size=2, insert_spaces=False),
    ))
    assert len(edits) == 1
    assert edits[0].new_text == format_source(source, options).text
    assert edits[0].range.end.line == 1
    assert edits[0].range.end.character == len(source.splitlines()[1]) + 1
