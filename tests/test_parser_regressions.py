from __future__ import annotations

import ast

import pytest

from kivy_lsp.kv.context import context_at
from kivy_lsp.kv.expression_source import EmbeddedPythonSource
from kivy_lsp.kv.nodes import PropertyNode, RuleNode, WidgetNode
from kivy_lsp.kv.parser import parse


def codes(source: str) -> set[str]:
    return {diagnostic.code for diagnostic in parse(source).diagnostics}


@pytest.mark.parametrize(
    "value",
    [
        "(\n        1, 2\n    )",
        "1, \\\n        2",
        "[\n        1, 2\n    ]",
    ],
)
def test_multiline_value_requires_its_own_property_block(value: str):
    source = f"Widget:\n    size: {value}\n    opacity: 1\n"
    result = parse(source)

    assert "kv-inline-multiline-value" in codes(source)
    widget = result.document.items[0]
    assert isinstance(widget, WidgetNode)
    assert isinstance(widget.body[-1], PropertyNode)
    assert widget.body[-1].name == "opacity"


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("indent", ["  ", "    ", "\t"])
def test_flat_multiline_expressions_follow_document_indentation(
    newline: str,
    indent: str,
):
    source = newline.join(
        [
            "Widget:",
            f"{indent}size:",
            f"{indent * 2}(",
            f"{indent * 2}1, 2",
            f"{indent * 2})",
            f"{indent}opacity: 1",
            "",
        ]
    )

    assert not parse(source).diagnostics


@pytest.mark.parametrize("indent", ["    ", "            "])
def test_continuation_indentation_is_exactly_one_kv_level(indent: str):
    source = (
        "Widget:\n"
        "    size:\n"
        "        (\n"
        f"{indent}1, 2\n"
        "        )\n"
    )

    assert "kv-invalid-expression-indentation" in codes(source)


def test_valid_backslash_conditional_preserves_the_following_property():
    source = (
        "Widget:\n"
        "    opacity:\n"
        "        1 if root.active else \\\n"
        "        0.5 if root.enabled else \\\n"
        "        0\n"
        "    disabled: False\n"
    )
    result = parse(source)
    widget = result.document.items[0]

    assert not result.diagnostics
    assert isinstance(widget, WidgetNode)
    assert [item.name for item in widget.body] == ["opacity", "disabled"]


def test_comment_indentation_does_not_change_expression_layout():
    source = (
        "Widget:\n"
        "    size:\n"
        "        (\n"
        "# this comment is removed by Kivy before parsing\n"
        "        1, 2\n"
        "        )\n"
    )

    assert not parse(source).diagnostics


def test_tabs_have_the_same_width_as_four_spaces():
    source = "Widget:\n\tsize: 1, 2\n    opacity: 1\n"

    assert not parse(source).diagnostics


@pytest.mark.parametrize(
    ("header", "clears_previous", "class_selectors"),
    [
        ("<-Button>", True, [False]),
        ("<.warning>", False, [True]),
        ("<-.warning, Button>", True, [True, False]),
    ],
)
def test_selector_prefixes_are_valid_and_preserved(
    header: str,
    clears_previous: bool,
    class_selectors: list[bool],
):
    source = f'{header}:\n    text: "hello"\n'
    result = parse(source)
    rule = result.document.items[0]

    assert not result.diagnostics
    assert isinstance(rule, RuleNode)
    assert (rule.clear_previous is not None) is clears_previous
    assert [item.is_class_selector for item in rule.selectors] == (
        class_selectors
    )
    assert "".join(token.text for token in result.tokens) == source

    for selector in rule.selectors:
        context = context_at(result, selector.span.start)
        assert context.selector is selector


@pytest.mark.parametrize("source", ["<", "<-", "<.", "Widget:\n    x:"])
def test_incomplete_declarations_remain_recoverable(source: str):
    result = parse(source)

    assert result.document.span.end == len(source)
    assert "".join(token.text for token in result.tokens) == source


def test_extra_indentation_in_an_event_suite_is_rejected():
    source = (
        "Widget:\n"
        "    on_press:\n"
        "        if root.enabled:\n"
        "            root.refresh()\n"
    )

    assert "kv-invalid-expression-indentation" in codes(source)


def test_one_line_try_suites_remain_event_statements():
    source = (
        "Widget:\n"
        "    on_press:\n"
        "        try: root.save()\n"
        "        except Exception: root.clear()\n"
    )
    result = parse(source)
    handler = result.document.items[0].body[0]

    assert not result.diagnostics
    assert not handler.body
    mapped = EmbeddedPythonSource.from_source(
        source, handler.value.span, statement_block=True
    )
    tree = ast.parse(mapped.text, mode=mapped.mode)
    assert isinstance(tree.body[0], ast.Try)


def test_canvas_keeps_its_instruction_body():
    source = (
        "Widget:\n"
        "    canvas.before:\n"
        "        Color:\n"
        "            rgba: 1, 0, 0, 1\n"
    )
    result = parse(source)
    canvas = result.document.items[0].body[0]

    assert not result.diagnostics
    assert canvas.value is None
    assert canvas.body[0].name.text == "Color"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_expression_source_maps_unicode_and_skipped_comment_lines(newline):
    source = newline.join(
        [
            "Widget:",
            "    text:",
            "        (",
            '        "é" + root.title',
            "# comment with different indentation",
            "",
            "        + root.subtitle",
            "        )",
            "",
        ]
    )
    expression = parse(source).document.items[0].body[0].value
    mapped = EmbeddedPythonSource.from_source(source, expression.span)
    tree = ast.parse(mapped.text, mode=mapped.mode)
    attributes = [
        node for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    ]
    spans = [mapped.node_span(node) for node in attributes]

    assert {source[span.start:span.end] for span in spans} == {
        "root.title",
        "root.subtitle",
    }


def test_expression_source_removes_comments_between_backslash_lines():
    source = (
        "Widget:\n"
        "    text:\n"
        "        root.title \\\n"
        "        # ignored by Kivy before Python compilation\n"
        "        + root.subtitle\n"
    )
    expression = parse(source).document.items[0].body[0].value
    mapped = EmbeddedPythonSource.from_source(source, expression.span)
    tree = ast.parse(mapped.text, mode=mapped.mode)

    assert isinstance(tree.body, ast.BinOp)
    span = mapped.node_span(tree.body.right)
    assert source[span.start:span.end] == "root.subtitle"


def test_statement_source_preserves_individual_statement_locations():
    source = (
        "Widget:\n"
        "    on_press:\n"
        "        root.refresh()\n"
        "        root.save()\n"
    )
    expression = parse(source).document.items[0].body[0].value
    mapped = EmbeddedPythonSource.from_source(
        source, expression.span, statement_block=True
    )
    tree = ast.parse(mapped.text, mode=mapped.mode)

    assert len(tree.body) == 2
    span = mapped.node_span(tree.body[1])
    assert source[span.start:span.end] == "root.save()"
