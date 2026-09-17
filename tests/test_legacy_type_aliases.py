from pathlib import Path

import pytest

from kivy_lsp.config import ServerConfig
from kivy_lsp.model.value_type import ValueTypeKind
from kivy_lsp.python.type_resolver import PythonTypeResolver

from test_alias_inheritance_diagnostics import analyze, build_index


@pytest.mark.parametrize(("imports", "marker"), [
    ("from typing import TypeAlias", "TypeAlias"),
    ("from typing import TypeAlias as Alias", "Alias"),
    ("import typing", "typing.TypeAlias"),
    ("import typing as t", "t.TypeAlias"),
    ("from typing_extensions import TypeAlias", "TypeAlias"),
    ("from typing_extensions import TypeAlias as Alias", "Alias"),
    ("import typing_extensions", "typing_extensions.TypeAlias"),
    ("import typing_extensions as t", "t.TypeAlias"),
    ("from typing import TypeAlias", '"TypeAlias"'),
    ("import typing as t", '"t.TypeAlias"'),
])
def test_explicit_legacy_alias_requires_known_marker(imports, marker):
    source = f"{imports}\nFnKeycode: {marker} = str | None\n"
    index = build_index({"keys": source})
    symbol = index.resolve_symbol("FnKeycode", from_module="keys")
    assert symbol is not None and symbol.is_type_alias
    assert symbol.annotation == "str | None"
    resolver = PythonTypeResolver(index, ServerConfig(Path("/tmp"), (), ()))
    result = resolver.resolve_annotation("FnKeycode", from_module="keys")
    assert result.is_union
    assert {item.value_type.kind for item in result.arguments} == {
        ValueTypeKind.STRING, ValueTypeKind.NONE,
    }
    assert source[symbol.selection_span.start:symbol.selection_span.end] == (
        "FnKeycode"
    )


KEYS = '''from dataclasses import dataclass
from typing import TypeAlias

FnKeycode: TypeAlias = str | None

@dataclass
class FnKey:
    code: FnKeycode
'''

KEYBOARD = '''from keys import FnKey, FnKeycode
class Keyboard:
    key: FnKey
    def press(self, key: FnKey | FnKeycode) -> None: ...
'''


@pytest.mark.parametrize("key", [
    '"backspace"', '"left"', '"right"', '"enter"', "None", "root.key",
])
def test_keyboard_union_accepts_strings_none_and_key_instances(key):
    diagnostics, *_ = analyze(
        {"keys": KEYS, "widgets": KEYBOARD},
        f"<Keyboard>:\n    on_press: root.press({key})\n",
    )
    assert not diagnostics


@pytest.mark.parametrize("key", ["42", "False", "[]", "{}"])
def test_keyboard_union_rejects_known_invalid_argument_types(key):
    diagnostics, *_ = analyze(
        {"keys": KEYS, "widgets": KEYBOARD},
        f"<Keyboard>:\n    on_press: root.press({key})\n",
    )
    assert [item.code for item in diagnostics] == ["kv-argument-type"]


@pytest.mark.parametrize("source", [
    "FnKeycode = str\n",
    "FnKeycode: str = 'left'\n",
    "class TypeAlias: pass\nFnKeycode: TypeAlias = str\n",
    "from unrelated import TypeAlias\nFnKeycode: TypeAlias = str\n",
    "import unrelated as t\nFnKeycode: t.TypeAlias = str\n",
    "from .typing import TypeAlias\nFnKeycode: TypeAlias = str\n",
    "FnKeycode: TypeAlias = str\n",
    "FnKeycode: typing.TypeAlias = str\n",
    "from typing import TypeAlias\nTypeAlias = object()\n"
    "FnKeycode: TypeAlias = str\n",
    "import typing as t\nclass t: pass\nFnKeycode: t.TypeAlias = str\n",
    "from typing import TypeAlias\nfrom unrelated import TypeAlias\n"
    "FnKeycode: TypeAlias = str\n",
])
def test_ordinary_variables_and_unrelated_markers_are_not_aliases(source):
    index = build_index({"keys": source})
    symbol = index.resolve_symbol("FnKeycode", from_module="keys")
    assert symbol is not None
    assert not symbol.is_type_alias


def test_typing_extensions_fallback_imports_recognize_same_marker():
    source = '''try:
    from typing import TypeAlias
except ImportError:
    from typing_extensions import TypeAlias
FnKeycode: TypeAlias = str | None
'''
    diagnostics, *_ = analyze(
        {"keys": source + "class FnKey: pass\n", "widgets": KEYBOARD},
        '<Keyboard>:\n    on_press: root.press("left")\n',
    )
    assert not diagnostics


@pytest.mark.parametrize("declarations", [
    "if flag:\n    FnKeycode: TypeAlias = str\n",
    "FnKeycode: TypeAlias = str\nFnKeycode: TypeAlias = int\n",
    "FnKeycode: TypeAlias = str\ntype FnKeycode = int\n",
    "type FnKeycode = str\nFnKeycode: TypeAlias = int\n",
    "FnKeycode: TypeAlias = str\nFnKeycode = 42\n",
    "FnKeycode = 42\nFnKeycode: TypeAlias = str\n",
    "FnKeycode: TypeAlias = FnKeycode\n",
    "FnKeycode: TypeAlias = Other\nOther: TypeAlias = FnKeycode\n",
    "FnKeycode: TypeAlias = Missing\n",
    "FnKeycode: TypeAlias = dynamic_factory()\n",
    "FnKeycode: TypeAlias\n",
])
def test_unsupported_or_ambiguous_alias_targets_are_conservative(declarations):
    source = "from typing import TypeAlias\n" + declarations
    diagnostics, index, _, config = analyze(
        {"keys": source + "class FnKey: pass\n", "widgets": KEYBOARD},
        '<Keyboard>:\n    on_press: root.press("left")\n',
    )
    assert not diagnostics
    result = PythonTypeResolver(index, config).resolve_annotation(
        "FnKeycode", from_module="keys",
    )
    assert result.is_unknown


def test_literal_alias_chain_across_modules_still_rejects_typos():
    modules = {
        "keys": '''from typing import TypeAlias, Literal
Direction: TypeAlias = Literal["left", "right"]
''',
        "public": "from keys import Direction as Exported\n",
        "widgets": '''from typing import TypeAlias
from public import Exported
Key: TypeAlias = Exported
class Keyboard:
    def press(self, key: Key) -> None: ...
''',
    }
    diagnostics, *_ = analyze(
        modules, '<Keyboard>:\n    on_press: root.press("rigth")\n',
    )
    assert [item.code for item in diagnostics] == ["kv-argument-type"]
    diagnostics, *_ = analyze(
        modules, '<Keyboard>:\n    on_press: root.press("right")\n',
    )
    assert not diagnostics


def test_legacy_alias_references_pep695_alias_and_preserves_broad_union():
    modules = {
        "keys": '''from typing import TypeAlias, Literal
type Direction = Literal["left", "right"]
FnKeycode: TypeAlias = Direction | str | None
class FnKey: pass
''',
        "widgets": KEYBOARD,
    }
    diagnostics, *_ = analyze(
        modules, '<Keyboard>:\n    on_press: root.press("backspace")\n',
    )
    assert not diagnostics


def test_forward_annotated_legacy_alias_target():
    modules = {
        "keys": '''from typing import TypeAlias
FnKeycode: TypeAlias = "str | None"
class FnKey: pass
''',
        "widgets": KEYBOARD,
    }
    diagnostics, *_ = analyze(
        modules, '<Keyboard>:\n    on_press: root.press("backspace")\n',
    )
    assert not diagnostics
