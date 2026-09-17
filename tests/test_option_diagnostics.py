from pathlib import Path

import pytest

from kivy_lsp.analysis.diagnostics import KvDiagnosticAnalyzer
from kivy_lsp.analysis.expression import KvExpressionResolver
from kivy_lsp.analysis.scope_builder import build_kv_semantic_model
from kivy_lsp.analysis.value_constraints import ValueConstraintResolver
from kivy_lsp.config import DiagnosticOptions, ServerConfig
from kivy_lsp.kv.parser import parse
from kivy_lsp.model.diagnostic import DiagnosticSeverity
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.workspace.document import TextDocument


PYTHON = '''from typing import Any, Literal
from kivy.properties import OptionProperty, StringProperty, BooleanProperty
from options import ALIGNS, CONDITIONAL, REASSIGNED, Mode
class First:
    mode = OptionProperty("hello", options=["hello"])
class Second:
    mode = OptionProperty("left", options=["left"])
class Broad:
    mode = StringProperty("left")
class Panel:
    variant: First | Second
    uncertain: First | Broad
    conditional = OptionProperty("left", options=CONDITIONAL)
    reassigned = OptionProperty("left", options=REASSIGNED)
    alias_optional: Mode | None
    alias_typed: Mode = StringProperty("left")
    negative = OptionProperty(-1, options=[-1, -2])
    halign = OptionProperty("left", options=["left", "right"])
    named = OptionProperty("left", options=ALIGNS)
    optional = OptionProperty("left", options=["left"], allownone=True)
    partial = OptionProperty("left", options=["left", dynamic()])
    dynamic = OptionProperty("left", options=dynamic())
    text = StringProperty("left")
    typed: Literal["left", "right"] = StringProperty("left")
    annotated: Literal["hello"] = OptionProperty("hello", options=dynamic())
    choice: Literal["left", "right"]
    bad: Literal["hello", "world"]
    mixed: Literal["left", "hello"]
    broad: str | Literal["hello"]
    any_value: Any
    incomplete: Literal["left", DYNAMIC]
    numeric = OptionProperty(1, options=[1, 2])
    mode = OptionProperty(
        "continuous", options=["emergency", "cycle", "continuous"],
    )
    active = BooleanProperty(False)

    def get_mode(self) -> Literal["cycle", "continuous"]:
        ...

    def get_optional(self) -> Mode | None:
        ...
'''


OPTIONS = """from typing import Literal
ALIGNS = ["left", "right"]
Mode = Literal["left", "right"]
if platform_is_a:
    CONDITIONAL = ["left"]
else:
    CONDITIONAL = ["right"]
REASSIGNED = ["left"]
REASSIGNED = ["right"]
"""


def analyze(expression, property_name="halign", *, strict=False):
    source = f"<Panel>:\n    {property_name}: {expression}\n"
    return analyze_source(source, strict=strict)


def analyze_source(source, *, strict=False):
    index = PythonIndex()
    for name, text in (
        ("options", OPTIONS),
        ("widgets", PYTHON),
    ):
        result = index_python_module(
            TextDocument(f"file:///tmp/{name}.py", text), name,
        )
        assert result.module is not None
        assert not result.diagnostics
        index.replace(result.module)
    config = ServerConfig(
        Path("/tmp"), (), (), diagnostics=DiagnosticOptions(strict=strict),
    )
    document = TextDocument("file:///tmp/options.kv", source)
    parsed = parse(source)
    assert not parsed.diagnostics
    model = build_kv_semantic_model(document, parsed, index, config)
    diagnostics = KvDiagnosticAnalyzer(index, config).analyze(
        document, parsed, model,
    )
    return source, diagnostics, index, model, config


def codes(result):
    return [item.code for item in result[1]]


@pytest.mark.parametrize("property_name", ["halign", "named", "typed"])
def test_known_invalid_value_is_error(property_name):
    _, diagnostics, *_ = analyze('"hello"', property_name)
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "kv-incompatible-property-value"
    assert diagnostics[0].severity is DiagnosticSeverity.ERROR
    assert "hello" in diagnostics[0].message
    assert "left" in diagnostics[0].message


@pytest.mark.parametrize(
    "value",
    [
        '"left"', "root.text", "root.choice", "root.broad",
        "root.any_value", "root.mixed", "root.dynamic", "root.partial",
        '"left" if root.active else "hello"',
        '"hello" if root.active else root.text',
    ],
)
def test_default_skips_uncertain_and_valid_values(value):
    assert not codes(analyze(value))


@pytest.mark.parametrize(
    "value", ['"hello"', "root.bad", '"hello" if root.active else "world"'],
)
def test_entirely_invalid_finite_values_are_errors(value):
    assert codes(analyze(value)) == ["kv-incompatible-property-value"]


@pytest.mark.parametrize("property_name", ["dynamic", "partial"])
@pytest.mark.parametrize("value", ['"hello"', "None"])
def test_incomplete_options_never_become_exhaustive(property_name, value):
    assert not codes(analyze(value, property_name))


@pytest.mark.parametrize("property_name", ["optional", "halign"])
def test_none_respects_property_allow_none(property_name):
    expected = [] if property_name == "optional" else [
        "kv-incompatible-property-value",
    ]
    assert codes(analyze("None", property_name)) == expected


@pytest.mark.parametrize("value", ["root.text", "root.mixed", "root.broad"])
def test_strict_enables_possible_value_warnings(value):
    _, diagnostics, *_ = analyze(value, strict=True)
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "kv-possibly-incompatible-property-value"
    assert diagnostics[0].severity is DiagnosticSeverity.WARNING


def test_strict_keeps_any_unknown_silent():
    assert not codes(analyze("root.any_value", strict=True))


@pytest.mark.parametrize(
    "expression",
    [
        'root.mode == "contnuous"',
        '"contnuous" == root.mode',
        'root.mode != "contnuous"',
        'root.get_mode() == "contnuous"',
        'root.typed == "hello"',
        'root.named == "hello"',
        'root.choice == root.bad',
        'root.annotated == "left"',
        '"contnuous" == root.mode == "cycle"',
    ],
)
def test_impossible_comparisons_warn(expression):
    _, diagnostics, *_ = analyze(expression, "active")
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "kv-impossible-comparison"
    assert diagnostics[0].severity is DiagnosticSeverity.WARNING
    result = "true" if "!=" in expression else "false"
    assert f"always {result}" in diagnostics[0].message


@pytest.mark.parametrize(
    "expression",
    [
        'root.mode == "cycle"',
        'root.optional == None',
        'root.partial == "hello"',
        'root.dynamic == "hello"',
        'root.broad == "world"',
        'root.text == "world"',
        'root.incomplete == "world"',
        'root.any_value == "world"',
        'root.choice == root.mixed',
        "root.numeric == True",
        "root.numeric == 1.0",
        '(lambda root: root.mode == "hello")(root)',
    ],
)
def test_possible_unknown_and_shadowed_comparisons_stay_silent(expression):
    assert not codes(analyze(expression, "active"))


@pytest.mark.parametrize("value", ["True", "1.0"])
def test_option_membership_uses_python_equality(value):
    assert not codes(analyze(value, "numeric"))


def test_warning_literal_span_after_unicode_and_line_continuation():
    source = (
        '<Panel>:\n    active:\n        "😀" and \\\n'
        '        root.mode == "contnuous"\n'
    )
    _, diagnostics, *_ = analyze_source(source)
    assert len(diagnostics) == 1
    problem = diagnostics[0]
    assert problem.code == "kv-impossible-comparison"
    assert source[problem.span.start:problem.span.end] == '"contnuous"'


def test_comparison_in_event_handler_uses_source_mapping():
    source = (
        '<Panel>:\n    on_active:\n'
        '        if root.mode == "contnuous": print("never")\n'
    )
    _, diagnostics, *_ = analyze_source(source)
    assert len(diagnostics) == 1
    problem = diagnostics[0]
    assert problem.code == "kv-impossible-comparison"
    assert source[problem.span.start:problem.span.end] == '"contnuous"'


def test_shared_constraints_include_optional_none_and_named_values():
    _, _, index, model, config = analyze("root.text")
    scope = model.scopes[0]
    constraints = ValueConstraintResolver(
        index, KvExpressionResolver(index, config),
    )
    assert constraints.for_expression("root.optional", scope) == (
        "left", None,
    )
    assert constraints.for_expression("root.named", scope) == (
        "left", "right",
    )
    assert constraints.for_expression("root.partial", scope) is None


@pytest.mark.parametrize("member", ["variant", "uncertain"])
def test_union_owners_do_not_narrow_to_first_member(member):
    assert not codes(analyze(f'root.{member}.mode == "left"', "active"))
    assert not codes(analyze(f"root.{member}.mode"))


@pytest.mark.parametrize("operator", ["-", "~"])
def test_numeric_unary_operations_transform_literal_values(operator):
    match = "-1" if operator == "-" else "-2"
    assert not codes(analyze(f"{operator}root.numeric == {match}", "active"))
    assert not codes(analyze(f"{operator}root.numeric", "negative"))


@pytest.mark.parametrize("member", ["conditional", "reassigned"])
def test_branch_and_reassigned_constants_are_not_exhaustive(member):
    assert not codes(analyze(f'root.{member} == "left"', "active"))
    assert not codes(analyze('"hello"', member))


def test_literal_alias_unions_survive_method_return_resolution():
    assert not codes(analyze('root.get_optional() == "left"', "active"))
    assert codes(analyze('root.get_optional() == "hello"', "active")) == [
        "kv-impossible-comparison",
    ]
    assert codes(analyze('"hello"', "alias_typed")) == [
        "kv-incompatible-property-value",
    ]
