from pathlib import Path

import pytest

from kivy_lsp.analysis.diagnostics import KvDiagnosticAnalyzer
from kivy_lsp.analysis.expression import KvExpressionResolver
from kivy_lsp.analysis.scope_builder import build_kv_semantic_model
from kivy_lsp.config import ServerConfig
from kivy_lsp.kv.parser import parse
from kivy_lsp.model.value_type import ValueTypeKind
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.python.type_resolver import PythonTypeResolver
from kivy_lsp.workspace.document import TextDocument


def build_index(modules):
    index = PythonIndex()
    for name, source in modules.items():
        result = index_python_module(
            TextDocument(f"file:///tmp/{name}.py", source), name,
        )
        assert result.module is not None
        assert not result.diagnostics
        index.replace(result.module)
    return index


def analyze(modules, source):
    index = build_index(modules)
    config = ServerConfig(Path("/tmp"), (), ())
    document = TextDocument("file:///tmp/view.kv", source)
    parsed = parse(source)
    assert not parsed.diagnostics
    model = build_kv_semantic_model(document, parsed, index, config)
    diagnostics = KvDiagnosticAnalyzer(index, config).analyze(
        document, parsed, model,
    )
    return diagnostics, index, model, config


KEYBOARD = '''from keys import FnKey, FnKeycode
class Keyboard:
    def press(self, key: FnKey | FnKeycode) -> None: ...
'''


@pytest.mark.parametrize("key", ['"backspace"', '"left"', '"right"', "8"])
def test_pep695_literal_and_primitive_alias_arguments(key):
    modules = {
        "keys": '''from typing import Literal
type FnKey = Literal["backspace", "left", "right"]
type FnKeycode = int
''',
        "widgets": KEYBOARD,
    }
    diagnostics, *_ = analyze(
        modules, f"<Keyboard>:\n    on_press: root.press({key})\n",
    )
    assert not diagnostics


def test_known_alias_does_not_hide_literal_typo():
    modules = {
        "keys": '''from typing import Literal
type FnKey = Literal["backspace", "left", "right"]
type FnKeycode = int
''',
        "widgets": KEYBOARD,
    }
    diagnostics, *_ = analyze(
        modules, '<Keyboard>:\n    on_press: root.press("backspce")\n',
    )
    assert [item.code for item in diagnostics] == ["kv-argument-type"]


@pytest.mark.parametrize("target", [
    "str | Literal['left']",
    "Missing | Literal['left']",
    "Recursive | Literal['left']",
])
def test_broad_unknown_and_recursive_alias_union_is_conservative(target):
    source = f'''from typing import Literal
type Recursive = FnKey
type FnKey = {target}
type FnKeycode = int
'''
    diagnostics, *_ = analyze(
        {"keys": source, "widgets": KEYBOARD},
        '<Keyboard>:\n    on_press: root.press("other")\n',
    )
    assert not diagnostics


def test_imported_alias_chain_uses_declaration_module():
    index = build_index({
        "keys": '''from typing import Literal
type Key = Literal["left", "right"]
class Payload:
    text: str
type Message = Payload
''',
        "public": "from keys import Key as FnKey, Message\n",
        "widgets": '''from public import FnKey, Message
type Action = FnKey | Message
class Payload:
    wrong: bool
''',
    })
    resolver = PythonTypeResolver(index, ServerConfig(Path("/tmp"), (), ()))
    resolved = resolver.resolve_annotation("Action", from_module="widgets")
    assert resolved.is_union
    assert resolved.arguments[0].value_type.literals == ("left", "right")
    assert resolved.arguments[1].name == "keys.Payload"
    assert [m.name for m in resolver.members_of(resolved.arguments[1])] == [
        "text",
    ]


@pytest.mark.parametrize("aliases", [
    "type FnKey[T] = T\ntype FnKeycode = int\n",
    "type FnKey = FnKeycode\ntype FnKeycode = FnKey\n",
    'if flag:\n    type FnKey = Literal["left"]\n'
    'else:\n    type FnKey = Literal["right"]\ntype FnKeycode = int\n',
    'type FnKey = Literal["left"]\ntype FnKey = Literal["right"]\n'
    'type FnKeycode = int\n',
])
def test_unsupported_aliases_do_not_invent_definite_argument_errors(aliases):
    diagnostics, *_ = analyze(
        {"keys": "from typing import Literal\n" + aliases,
         "widgets": KEYBOARD},
        '<Keyboard>:\n    on_press: root.press("backspace")\n',
    )
    assert not diagnostics


def test_generic_alias_application_stays_unknown():
    index = build_index({"keys": "type Alias[T] = list[T]\n"})
    resolver = PythonTypeResolver(index, ServerConfig(Path("/tmp"), (), ()))
    assert resolver.resolve_annotation(
        "Alias[str]", from_module="keys",
    ).is_unknown


def test_pep695_literal_alias_property_constraint():
    source = '''from typing import Literal
from kivy.properties import StringProperty
type Alignment = Literal["left", "right"]
class Panel:
    halign: Alignment = StringProperty("left")
'''
    diagnostics, *_ = analyze(
        {"widgets": source}, '<Panel>:\n    halign: "hello"\n',
    )
    assert [item.code for item in diagnostics] == [
        "kv-incompatible-property-value",
    ]


def test_ordinary_variable_annotation_is_not_a_type_alias():
    index = build_index({"keys": "key: str = 'left'\n"})
    resolver = PythonTypeResolver(index, ServerConfig(Path("/tmp"), (), ()))
    resolved = resolver.resolve_annotation("key", from_module="keys")
    assert resolved.value_type.kind is ValueTypeKind.OBJECT


@pytest.mark.parametrize("classes", [
    "class Panel(Missing):\n    text: str\n",
    "class Base(Missing):\n    text: str\nclass Panel(Base):\n    pass\n",
    "class Base:\n    text: str\nclass Panel(Base, Missing):\n    pass\n",
    "class Panel(Other):\n    text: str\nclass Other(Panel):\n    pass\n",
])
def test_incomplete_inheritance_does_not_reject_possible_base_member(classes):
    diagnostics, index, model, config = analyze(
        {"widgets": classes}, '<Panel>:\n    text: str(root.disabled)\n',
    )
    assert not diagnostics
    resolver = KvExpressionResolver(index, config)
    scope = model.scopes[0]
    owner = resolver.resolve("root", scope)
    assert "text" in [member.name for member in resolver.members_of(owner)]
    assert not resolver.has_complete_members(owner)


@pytest.mark.parametrize("base", ["", "(object)", "(Base)"])
def test_complete_class_still_rejects_unknown_member(base):
    classes = (
        f"class Base:\n    value: str\nclass Panel{base}:\n    text: str\n"
    )
    diagnostics, *_ = analyze(
        {"widgets": classes}, '<Panel>:\n    text: str(root.typo)\n',
    )
    assert [item.code for item in diagnostics] == ["kv-unknown-member"]


def test_inheritance_completeness_refreshes_with_module_index():
    index = build_index({
        "widgets": (
            "from base import Base\nclass Panel(Base):\n    text: str\n"
        ),
    })
    panel = index.resolve_class("Panel", from_module="widgets")
    assert panel is not None
    assert not index.has_complete_mro(panel)
    base = build_index({"base": "class Base:\n    disabled: bool\n"})
    index.replace(base.modules[0])
    assert index.has_complete_mro(panel)
    assert index.member_named(panel, "disabled") is not None
    index.remove("base")
    assert not index.has_complete_mro(panel)
