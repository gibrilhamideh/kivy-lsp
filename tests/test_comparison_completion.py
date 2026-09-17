"""Finite comparison values from real indexed properties and annotations."""

from pathlib import Path

import pytest

from kivy_lsp.analysis.completion import KvCompletionEngine
from kivy_lsp.analysis.comparison_value_context import (
    comparison_value_context_at,
)
from kivy_lsp.analysis.scope_builder import build_kv_semantic_model
from kivy_lsp.config import ServerConfig
from kivy_lsp.kv.index import KvIndex
from kivy_lsp.kv.parser import parse
from kivy_lsp.model.span import Span
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.workspace.document import TextDocument


PYTHON = '''from typing import Literal
from kivy.properties import OptionProperty, StringProperty
from modes import Mode

class Ventilation:
    mode = OptionProperty(
        "continuous", options=["emergency", "cycle", "continuous"],
    )
    typed: Mode
    broad: str | Literal["cycle"]
    dynamic = OptionProperty("cycle", options=unknown_options)
    mixed = OptionProperty("cycle", options=["cycle", unknown_option])
    text = StringProperty("cycle")
    levels: Literal[1, 2]
    enabled: Literal[True, False]
    optional: Mode | None
    strange: Literal["cy.cle", "cy cle", "café", "it's", "a\\nb"]

    @property
    def current(self) -> Mode:
        return "continuous"

    def selected(self) -> Mode:
        return "continuous"

class Panel:
    ventilation: Ventilation
    halign = OptionProperty("left", options=["left", "right"])
    display: bool
    title: str

    def choose(self, value: Literal["argument"]) -> bool:
        return True
'''


def complete(expression, *, property_name="display"):
    source = f"<Panel>:\n    {property_name}: {expression}\n"
    offset = source.index("|")
    source = source.replace("|", "")
    document = TextDocument("file:///tmp/view.kv", source)
    index = PythonIndex()
    for name, text in (
        ("modes", 'from typing import Literal\n'
         'Mode = Literal["emergency", "cycle", "continuous"]\n'),
        ("widgets", PYTHON),
    ):
        indexed = index_python_module(
            TextDocument(f"file:///tmp/{name}.py", text), name,
        )
        assert indexed.module is not None
        index.replace(indexed.module)
    config = ServerConfig(Path("/tmp"), (), ())
    parsed = parse(source)
    kv_index = KvIndex()
    model = build_kv_semantic_model(
        document, parsed, index, config, kv_index,
    )
    result = KvCompletionEngine(index, kv_index, config).complete(
        document, parsed, model, offset,
    )
    return document, result


def values(result):
    return {item.insert_text for item in result.items}


@pytest.mark.parametrize("member", [
    "mode", "typed", "current", "selected()",
])
@pytest.mark.parametrize("operator", ["==", "!="])
def test_option_and_literal_comparison_values(member, operator):
    _, result = complete(f'root.ventilation.{member} {operator} "|"')
    assert values(result) == {'"emergency"', '"cycle"', '"continuous"'}


@pytest.mark.parametrize("expression", [
    'root.ventilation.mode == "co|ntinuuous"',
    '"co|ntinuuous" == root.ventilation.mode',
    '("co|ntinuuous") != (root.ventilation.mode)',
    '(root.ventilation.mode == "co|ntinuuous") and root.display',
    'root.choose(root.ventilation.mode == "co|ntinuuous")',
    'root.ventilation.mode == "co|ntinuuous" == root.ventilation.typed',
])
def test_prefix_suffix_and_reversed_comparisons(expression):
    document, result = complete(expression, property_name="halign")
    assert values(result) == {'"continuous"'}
    span = result.target.replacement_span
    assert document.text[span.start:span.end] == '"continuuous"'
    changed = (
        document.text[:span.start] + '"continuous"'
        + document.text[span.end:]
    )
    assert changed == document.text.replace('"continuuous"', '"continuous"')


@pytest.mark.parametrize("expression", [
    'root.ventilation.mode == "cy|',
    '(root.ventilation.mode == "cy|',
    'root.choose(root.ventilation.mode == "cy|',
    'root.ventilation.mode == cy|cle',
    'root.ventilation.mode == |',
])
def test_incomplete_expressions(expression):
    _, result = complete(expression)
    assert '"cycle"' in values(result)
    assert '"left"' not in values(result)


def test_single_quote_style_is_preserved():
    _, result = complete("root.ventilation.mode != 'cy|cle'")
    assert values(result) == {"'cycle'"}


@pytest.mark.parametrize(("expression", "expected"), [
    ('root.ventilation.strange == "cy.|cle"', '"cy.cle"'),
    ('root.ventilation.strange == "cy |cle"', '"cy cle"'),
    ('root.ventilation.strange == "caf|é"', '"café"'),
    ("root.ventilation.strange == 'it|s'", "'it\\'s'"),
    ("root.ventilation.strange == 'a|'", "'a\\nb'"),
])
def test_complete_string_token_and_unicode(expression, expected):
    _, result = complete(expression)
    assert values(result) == {expected}


def test_multiline_continuations_and_unicode_before_cursor():
    expression = (
        '\n        "😀" and root.ventilation.mode \\\n'
        '        == "cy|cle"'
    )
    document, result = complete(expression)
    assert values(result) == {'"cycle"'}
    span = result.target.replacement_span
    assert document.text[span.start:span.end] == '"cycle"'


@pytest.mark.parametrize("member", ["broad", "text", "dynamic", "mixed"])
def test_broad_or_incomplete_domains_do_not_guess_options(member):
    _, result = complete(
        f'root.ventilation.{member} == "|"', property_name="halign",
    )
    assert values(result) == set()


@pytest.mark.parametrize("expression", [
    'root.unknown == "|"',
    'root.ventilation.mode < "|"',
    'root.ventilation.mode == str("|", "unused")',
    '"root.ventilation.mode == cy|"',
    'root.ventilation.mode == {"key": "cy|"}',
    'root.ventilation.mode == f"cy|"',
    'root.ventilation.mode == b"cy|"',
])
def test_options_do_not_leak_into_unrelated_strings(expression):
    _, result = complete(expression, property_name="halign")
    candidates = values(result) if result is not None else set()
    assert not candidates.intersection({
        '"cycle"', '"continuous"', '"emergency"', '"left"', '"right"',
    })


def test_existing_property_and_call_argument_values_are_preserved():
    _, property_result = complete('"le|"', property_name="halign")
    assert values(property_result) == {'"left"'}
    _, call_result = complete('root.choose("arg|")')
    assert values(call_result) == {'"argument"'}


def test_optional_and_non_string_literals():
    _, optional = complete('root.ventilation.optional == |')
    assert {'None', '"cycle"'} <= values(optional)
    _, numbers = complete('root.ventilation.levels == |')
    assert {"1", "2"} <= values(numbers)
    _, quoted_numbers = complete('root.ventilation.levels == "|"')
    assert not values(quoted_numbers)
    _, booleans = complete('root.ventilation.enabled == |')
    assert {"True", "False"} <= values(booleans)


def test_context_ignores_comment_and_string_operators():
    for expression in ('root.mode == 1 # "|"', '"root.mode == |"'):
        offset = expression.index("|")
        text = expression.replace("|", "")
        document = TextDocument("file:///view.kv", text)
        context = comparison_value_context_at(
            document, Span(0, len(text)), offset,
        )
        assert context is None
