from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from kivy_lsp.config import ServerConfig
from kivy_lsp.model.symbol import SymbolKind
from kivy_lsp.model.value_type import ValueTypeKind, object_type
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.python.type_resolver import (
    PythonTypeResolver,
    ResolvedPythonType,
)
from kivy_lsp.workspace.document import TextDocument


def add_module(index: PythonIndex, name: str, source: str):
    result = index_python_module(
        TextDocument(f"file:///project/{name}.py", dedent(source)), name
    )
    assert not result.diagnostics
    assert result.module is not None
    index.replace(result.module)
    return result.module


def resolver_for(index: PythonIndex) -> PythonTypeResolver:
    root = Path("/project")
    return PythonTypeResolver(index, ServerConfig(root, (root,), (root,)))


def test_diamond_members_follow_python_mro_and_refresh():
    source = """
        class A:
            value: int
        class B(A):
            pass
        class C(A):
            value: str
        class D(B, C):
            pass
    """
    index = PythonIndex()
    add_module(index, "models", source)
    member = index.member_named("models.D", "value")
    assert member is not None
    assert member.qualified_name == "models.C.value"
    assert index.members_of("models.D", include_inherited=False) == ()

    add_module(index, "models", source.replace("value: str", "value: float"))
    member = index.member_named("models.D", "value")
    assert member is not None
    assert member.annotation == "float"


def test_c3_handles_more_than_one_shared_ancestor():
    source = """
        class O:
            pass
        class A(O):
            value: int
        class B(O):
            value: str
        class X(A, B):
            pass
        class Y(B):
            value: bool
        class Z(X, Y):
            pass
    """
    runtime = {}
    exec(dedent(source), runtime)
    expected = next(
        cls.__name__
        for cls in runtime["Z"].__mro__
        if "value" in cls.__dict__.get("__annotations__", {})
    )
    index = PythonIndex()
    add_module(index, "models", source)
    member = index.member_named("models.Z", "value")
    assert member is not None
    assert member.qualified_name == f"models.{expected}.value"


@pytest.mark.parametrize(
    "source",
    [
        """
        class A(B):
            own: str
        class B(A):
            inherited: int
    """,
        """
        class X:
            inherited: int
        class Y:
            pass
        class B(X, Y):
            pass
        class C(Y, X):
            pass
        class A(B, C):
            own: str
    """,
    ],
)
def test_invalid_mro_keeps_local_members_without_inventing_order(source):
    index = PythonIndex()
    add_module(index, "models", source)
    assert [item.name for item in index.members_of("models.A")] == ["own"]


def test_synthetic_class_members_are_not_cached_by_name():
    index = PythonIndex()
    add_module(index, "models", "class A:\n    value: int\n")
    base = index.class_named("models.A")
    assert base is not None
    synthetic = replace(
        base,
        symbol=replace(base.symbol, qualified_name="kv.Dynamic"),
        bases=("models.A",),
        members=(),
    )
    assert index.member_named(synthetic, "value").annotation == "int"
    updated = replace(
        synthetic,
        members=(replace(base.members[0], annotation="str"),),
    )
    assert index.member_named(updated, "value").annotation == "str"
    resolver = resolver_for(index)
    owner = ResolvedPythonType(
        object_type("kv.Dynamic"), None, class_symbol=synthetic
    )
    value = resolver.member_type(owner, "value")
    assert value is not None and value.value_type.display == "int"
    value = resolver.member_type(replace(owner, class_symbol=updated), "value")
    assert value is not None and value.value_type.display == "str"


def test_c3_follows_nested_synthetic_bases_without_index_registration():
    index = PythonIndex()
    add_module(index, "models", "class A:\n    value: int\n")
    base = index.class_named("models.A")
    assert base is not None
    first = replace(
        base,
        symbol=replace(base.symbol, qualified_name="kv.B"),
        bases=("models.A",),
        resolved_bases=(base,),
        members=(),
    )
    second = replace(
        first,
        symbol=replace(base.symbol, qualified_name="kv.C"),
        members=(replace(base.members[0], annotation="str"),),
    )
    child = replace(
        first,
        symbol=replace(base.symbol, qualified_name="kv.D"),
        bases=("kv.B", "kv.C"),
        resolved_bases=(first, second),
    )
    member = index.member_named(child, "value")
    assert member is not None and member.annotation == "str"


def test_setter_and_deleter_preserve_getter_type_kind_and_location():
    index = PythonIndex()
    add_module(
        index,
        "models",
        '''
        class Model:
            @property
            def value(self) -> str:
                """A readable value."""
                return ""

            @value.setter
            def value(self, value: str) -> None:
                self._value: str = value

            @value.deleter
            def value(self) -> None:
                pass
    ''',
    )
    member = index.member_named("models.Model", "value")
    assert member is not None
    assert member.kind is SymbolKind.PROPERTY
    assert member.annotation == "str"
    assert member.documentation == "A readable value."
    assert index.member_named("models.Model", "_value") is not None


def test_replaced_getter_supplies_its_new_value_type():
    index = PythonIndex()
    add_module(
        index,
        "models",
        """
        class Model:
            @property
            def value(self) -> str:
                return ""

            @value.getter
            def value(self) -> int:
                return 1
    """,
    )
    member = index.member_named("models.Model", "value")
    assert member is not None
    assert member.kind is SymbolKind.PROPERTY
    assert member.annotation == "int"


def test_inherited_property_setter_preserves_inherited_getter():
    index = PythonIndex()
    add_module(
        index,
        "models",
        """
        class Base:
            @property
            def value(self) -> str:
                return ""
        class Child(Base):
            @Base.value.setter
            def value(self, value: str) -> None:
                pass
    """,
    )
    member = index.member_named("models.Child", "value")
    assert member is not None
    assert member.annotation == "str"
    assert member.kind is SymbolKind.PROPERTY


def test_type_checking_imports_resolve_without_leaking_local_imports():
    index = PythonIndex()
    add_module(index, "theme", "class Theme:\n    color: str\n")
    module = add_module(
        index,
        "app",
        """
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            from theme import Theme
        class App:
            theme: Theme
            def method(self):
                from hidden import Local
        def function():
            from hidden import OtherLocal
    """,
    )
    assert {binding.local_name for binding in module.imports} == {
        "TYPE_CHECKING",
        "Theme",
    }
    resolver = resolver_for(index)
    result = resolver.member_type(
        resolver.resolve_annotation("app.App"), "theme"
    )
    assert result is not None
    assert result.class_symbol is not None
    assert result.class_symbol.qualified_name == "theme.Theme"


def test_conditional_module_declarations_keep_module_scope():
    index = PythonIndex()
    add_module(
        index,
        "models",
        """
        if some_platform:
            class Theme:
                color: str
        try:
            from available import Dependency
        except ImportError:
            from fallback import Dependency
        with configuration:
            DEFAULTS = ("a", "b")
        def local():
            class Hidden:
                pass
    """,
    )
    assert index.class_named("models.Theme") is not None
    assert index.class_named("models.Hidden") is None
    assert index.symbol_named("models.DEFAULTS").literal_values == ("a", "b")
    module = index.module_named("models")
    assert [binding.target_module for binding in module.imports] == [
        "available",
        "fallback",
    ]


def test_union_members_and_returns_aggregate_all_branch_types():
    index = PythonIndex()
    add_module(
        index,
        "models",
        """
        class A:
            value: int
            only_a: str
            def read(self) -> bool:
                pass
        class B:
            value: str
            def read(self) -> float:
                pass
    """,
    )
    resolver = resolver_for(index)
    owner = resolver.resolve_annotation("models.A | models.B")
    value = resolver.member_type(owner, "value")
    result = resolver.member_return_type(owner, "read")
    assert value is not None and value.value_type.display == "int | str"
    assert result is not None and result.value_type.display == "bool | float"
    assert resolver.member_type(owner, "only_a") is None


@pytest.mark.parametrize(
    "annotation, expected",
    [
        ("Box[int] | None", "int"),
        ("Box[int] | Box[str]", "int | str"),
        ("Child[int] | None", "list[int]"),
    ],
)
def test_union_members_keep_generic_substitutions(annotation, expected):
    index = PythonIndex()
    add_module(
        index,
        "models",
        """
        from typing import Generic, TypeVar
        T = TypeVar("T")
        class Box(Generic[T]):
            value: T
            def read(self) -> T:
                pass
        class Child(Box[list[T]], Generic[T]):
            pass
    """,
    )
    resolver = resolver_for(index)
    owner = resolver.resolve_annotation(annotation, from_module="models")
    value = resolver.member_type(owner, "value")
    result = resolver.member_return_type(owner, "read")
    assert value is not None and value.value_type.display == expected
    assert result is not None and result.value_type.display == expected


def test_union_results_preserve_class_origins_across_modules():
    index = PythonIndex()
    for name, kind in (("first", "int"), ("second", "str")):
        add_module(
            index,
            name,
            f"""
            class Value:
                nested: {kind}
            class Model:
                value: Value
        """,
        )
    resolver = resolver_for(index)
    owner = resolver.resolve_annotation("first.Model | second.Model")
    value = resolver.member_type(owner, "value")
    assert value is not None and value.is_union
    origins = {
        branch.class_symbol.qualified_name for branch in value.arguments
    }
    assert origins == {"first.Value", "second.Value"}
    nested = resolver.member_type(value, "nested")
    assert nested is not None and nested.value_type.display == "int | str"


def test_union_identical_builtin_results_collapse_across_modules():
    index = PythonIndex()
    for name in ("first", "second"):
        add_module(index, name, "class Model:\n    value: int\n")
    resolver = resolver_for(index)
    owner = resolver.resolve_annotation("first.Model | second.Model")
    result = resolver.member_type(owner, "value")
    assert result is not None
    assert result.value_type.kind is ValueTypeKind.INT
