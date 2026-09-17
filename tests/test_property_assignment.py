"""Property input contracts differ from their annotated stored values."""

from pathlib import Path

import pytest

from kivy_lsp.analysis.diagnostics import KvDiagnosticAnalyzer
from kivy_lsp.analysis.scope_builder import build_kv_semantic_model
from kivy_lsp.config import ServerConfig
from kivy_lsp.kv.parser import parse
from kivy_lsp.model.value_type import ValueTypeKind
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.python.type_resolver import PythonTypeResolver
from kivy_lsp.workspace.document import TextDocument


PYTHON = '''from typing import Literal
from kivy.event import EventDispatcher
from kivy.properties import (
    BoundedNumericProperty, ColorProperty, ListProperty, NumericProperty,
    ObjectProperty, ReferenceListProperty, StringProperty,
    VariableListProperty,
)
class Panel(EventDispatcher):
    adaptive_size: list[bool] = ListProperty([False, False])
    radius: list[float] = VariableListProperty([0], length=4)
    padding: list[float] = VariableListProperty([0], length=2)
    default_radius = VariableListProperty([0])
    color: list[float] = ColorProperty([0, 0, 0, 1])
    amount: int = NumericProperty(0)
    bounded: int = BoundedNumericProperty(0, min=0, max=100)
    x = NumericProperty(0)
    y = NumericProperty(0)
    position: tuple[float, float] = ReferenceListProperty(x, y)
    typed: Literal["left", "right"] = StringProperty("left")
    object_list: list[int] = ObjectProperty([])

    def accept_list(self, values: list[int]) -> None:
        pass
'''


def analyze(property_name, expression, python=PYTHON, modules=()):
    index = PythonIndex()
    for name, source in (*modules, ("widgets", python)):
        result = index_python_module(
            TextDocument(f"file:///tmp/{name}.py", source), name,
        )
        assert result.module is not None
        assert not result.diagnostics
        index.replace(result.module)
    source = f"<Panel>:\n    {property_name}: {expression}\n"
    document = TextDocument("file:///tmp/panel.kv", source)
    parsed = parse(source)
    assert not parsed.diagnostics
    config = ServerConfig(Path("/tmp"), (), ())
    model = build_kv_semantic_model(document, parsed, index, config)
    diagnostics = KvDiagnosticAnalyzer(index, config).analyze(
        document, parsed, model,
    )
    return diagnostics, index


@pytest.fixture(scope="module")
def runtime_panel():
    pytest.importorskip("kivy")
    namespace = {}
    exec(PYTHON, namespace)
    return namespace["Panel"]()


@pytest.mark.parametrize(
    ("property_name", "expression"),
    [
        ("adaptive_size", "(True, False)"),
        ("adaptive_size", "[True, False]"),
        ("radius", "10"),
        ("radius", "[10]"),
        ("radius", "(10,)"),
        ("radius", "[10, 20]"),
        ("radius", "[10, 20, 30, 40]"),
        ("radius", '"10dp"'),
        ("padding", "10"),
        ("padding", "[10]"),
        ("padding", "(10, 20)"),
        ("default_radius", "[10]"),
        ("default_radius", "[10, 20]"),
        ("default_radius", "[10, 20, 30, 40]"),
        ("color", "(0, 1, 0)"),
        ("color", "[0, 1, 0, 1]"),
        ("color", '"red"'),
        ("amount", "1.5"),
        ("amount", '"10dp"'),
        ("bounded", "1.5"),
        ("position", "[10, 20]"),
        ("position", "(10, 20)"),
    ],
)
def test_descriptor_conversions_match_kivy(
    runtime_panel, property_name, expression,
):
    value = eval(expression, {"__builtins__": {}})
    setattr(runtime_panel, property_name, value)
    diagnostics, _ = analyze(property_name, expression)
    assert not diagnostics


@pytest.mark.parametrize(
    ("property_name", "expression"),
    [
        ("adaptive_size", "1"),
        ("radius", "[]"),
        ("radius", "[1, 2, 3]"),
        ("radius", "[1, 2, 3, 4, 5]"),
        ("padding", "[]"),
        ("padding", "[1, 2, 3]"),
        ("padding", "[1, 2, 3, 4]"),
        ("default_radius", "[1, 2, 3]"),
        ("color", "[0, 1]"),
        ("color", "[0, 1, 0, 1, 0]"),
        ("amount", '"hello"'),
        ("bounded", "101"),
    ],
)
def test_definite_invalid_inputs_still_fail(
    runtime_panel, property_name, expression,
):
    value = eval(expression, {"__builtins__": {}})
    with pytest.raises((TypeError, ValueError)):
        setattr(runtime_panel, property_name, value)
    diagnostics, _ = analyze(property_name, expression)
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "kv-incompatible-property-value"


def test_stored_list_annotation_remains_available_for_read_inference():
    _, index = analyze("adaptive_size", "(True, False)")
    symbol = index.member_named("widgets.Panel", "adaptive_size")
    assert symbol is not None
    assert symbol.annotation == "list[bool]"


def test_literal_annotation_is_still_an_explicit_constraint():
    diagnostics, _ = analyze("typed", '"hello"')
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "kv-incompatible-property-value"


def test_object_property_does_not_convert_tuple_to_annotated_list():
    diagnostics, _ = analyze("object_list", "(1, 2)")
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "kv-incompatible-property-value"


def test_python_list_parameter_still_rejects_tuple():
    diagnostics, _ = analyze("on_amount", "root.accept_list((1, 2))")
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "kv-argument-type"


def test_unknown_variable_length_does_not_assume_four():
    python = PYTHON.replace("length=4", "length=calculate_length()")
    diagnostics, _ = analyze("radius", "[1, 2, 3]", python)
    assert not diagnostics


ALIASES = '''from typing import Literal
type Flags = list[bool]
type Values = list[float]
type Count = int
type Position = tuple[float, float]
type Mode = Literal["left", "right"]
'''


def alias_source(imported):
    python = (
        PYTHON.replace("list[bool]", "Flags")
        .replace("list[float]", "Values")
        .replace(": int =", ": Count =")
        .replace("tuple[float, float]", "Position")
        .replace('Literal["left", "right"]', "Mode")
    )
    if imported:
        return (
            "from aliases import Flags, Values, Count, Position, Mode\n"
            + python,
            (("aliases", ALIASES),),
        )
    return ALIASES + python, ()


@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize(
    ("property_name", "expression", "valid"),
    [
        ("adaptive_size", "(True, False)", True),
        ("radius", "[10]", True),
        ("radius", "[10, 20, 30]", False),
        ("color", '"red"', True),
        ("amount", "1.5", True),
        ("bounded", "1.5", True),
        ("bounded", "101", False),
        ("position", "[10, 20]", True),
        ("typed", '"left"', True),
        ("typed", '"hello"', False),
    ],
)
def test_aliased_read_types_preserve_setter_inputs_and_constraints(
    imported, property_name, expression, valid,
):
    python, modules = alias_source(imported)
    diagnostics, _ = analyze(property_name, expression, python, modules)
    expected = [] if valid else ["kv-incompatible-property-value"]
    assert [item.code for item in diagnostics] == expected


@pytest.mark.parametrize("imported", [False, True])
def test_alias_read_type_stays_list_after_setter_normalization(imported):
    python, modules = alias_source(imported)
    _, index = analyze("adaptive_size", "(True, False)", python, modules)
    symbol = index.member_named("widgets.Panel", "adaptive_size")
    assert symbol is not None
    assert symbol.annotation == "Flags"
    resolver = PythonTypeResolver(index, ServerConfig(Path("/tmp"), (), ()))
    read_type = resolver.resolve_annotation(
        symbol.annotation, from_module="widgets",
    )
    assert read_type.value_type.kind is ValueTypeKind.LIST
