from pathlib import Path

from kivy_lsp.analysis.diagnostics import KvDiagnosticAnalyzer
from kivy_lsp.analysis.expression import (
    KvExpressionResolver,
    KvResolutionKind,
)
from kivy_lsp.analysis.scope_builder import build_kv_semantic_model
from kivy_lsp.config import ServerConfig
from kivy_lsp.kv.context import context_at
from kivy_lsp.kv.nodes import RuleNode, WidgetNode
from kivy_lsp.kv.parser import parse
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.workspace.document import TextDocument


def _config(
    *,
    app_class: str | None = None,
) -> ServerConfig:
    root = Path.cwd()
    return ServerConfig(
        project_root=root,
        source_roots=(root,),
        kv_paths=(root,),
        app_class=app_class,
    )


def _index_module(
    index: PythonIndex,
    module_name: str,
    source: str,
) -> None:
    result = index_python_module(
        TextDocument(
            uri=f"file:///{module_name}.py",
            text=source,
        ),
        module_name,
    )
    assert result.module is not None
    index.replace(result.module)


def _widget_children(rule: RuleNode) -> tuple[WidgetNode, ...]:
    return tuple(
        item
        for item in rule.body
        if isinstance(item, WidgetNode)
    )


def test_kv_created_property_is_local_to_its_widget_node() -> None:
    index = PythonIndex()
    _index_module(
        index,
        "widgets",
        (
            "class RootView:\n"
            "    def edit(self, key: str, value: int) -> None:\n"
            "        pass\n"
            "\n"
            "class UINumericField:\n"
            "    value: int\n"
        ),
    )
    source = (
        "<RootView>:\n"
        "    UINumericField:\n"
        "        key: \"high_temperature\"\n"
        "        on_commit:\n"
        "            root.edit(self.key, self.value)\n"
        "    UINumericField:\n"
        "        on_commit:\n"
        "            root.edit(self.key, self.value)\n"
    )
    document = TextDocument(
        uri="file:///view.kv",
        text=source,
    )
    parse_result = parse(source)
    semantic_model = build_kv_semantic_model(
        document,
        parse_result,
        index,
        _config(),
    )
    rule = parse_result.document.items[0]
    assert isinstance(rule, RuleNode)
    first, second = _widget_children(rule)
    scope = semantic_model.scope_for_owner(rule)
    assert scope is not None

    first_value = scope.value_for_widget(first)
    second_value = scope.value_for_widget(second)
    assert first_value is not None
    assert second_value is not None
    assert first_value.local_member_named("key") is not None
    assert second_value.local_member_named("key") is None

    resolver = KvExpressionResolver(index, _config())
    resolution = resolver.resolve(
        "self.key",
        scope,
        self_value=first_value,
    )
    assert resolution.kind is KvResolutionKind.VALUE
    assert resolution.value is not None
    assert resolution.value.type_name == "str"

    diagnostics = KvDiagnosticAnalyzer(
        index,
        _config(),
    ).analyze(
        document,
        parse_result,
        semantic_model,
    )
    unknown_key_diagnostics = tuple(
        diagnostic
        for diagnostic in diagnostics
        if (
            diagnostic.code == "kv-unknown-member"
            and '"key"' in diagnostic.message
        )
    )
    assert len(unknown_key_diagnostics) == 1
    assert unknown_key_diagnostics[0].span.start > source.rfind(
        "UINumericField:"
    )


def test_canvas_expression_self_is_the_containing_widget() -> None:
    index = PythonIndex()
    _index_module(
        index,
        "graphics",
        (
            "class RootView:\n"
            "    pass\n"
            "\n"
            "class FnBoxLayout:\n"
            "    x: float\n"
            "    y: float\n"
            "    right: float\n"
            "\n"
            "class Line:\n"
            "    points = ListProperty([])\n"
            "    width = NumericProperty(1)\n"
        ),
    )
    source = (
        "<RootView>:\n"
        "    FnBoxLayout:\n"
        "        canvas.after:\n"
        "            Line:\n"
        "                points: self.x, self.y, self.right, self.y\n"
        "                width: 2\n"
    )
    document = TextDocument(
        uri="file:///canvas.kv",
        text=source,
    )
    parse_result = parse(source)
    semantic_model = build_kv_semantic_model(
        document,
        parse_result,
        index,
        _config(),
    )
    offset = source.index("self.right") + len("self.right")
    context = context_at(parse_result, offset)
    assert context.current_widget is not None
    assert context.current_widget.class_name == "FnBoxLayout"
    assert context.current_instruction is not None
    assert context.current_instruction.class_name == "Line"
    assert context.property_owner is context.current_instruction

    scope = semantic_model.scope_at(offset)
    assert scope is not None
    resolver = KvExpressionResolver(index, _config())
    self_value = resolver.self_value(
        document,
        scope,
        context.current_widget,
    )
    property_value = resolver.self_value(
        document,
        scope,
        context.property_owner,
    )
    assert self_value.type_name == "FnBoxLayout"
    assert property_value.type_name == "Line"

    diagnostics = KvDiagnosticAnalyzer(
        index,
        _config(),
    ).analyze(
        document,
        parse_result,
        semantic_model,
    )
    assert not any(
        diagnostic.code == "kv-unknown-member"
        for diagnostic in diagnostics
    )


def test_color_properties_are_compatible_across_expressions() -> None:
    index = PythonIndex()
    _index_module(
        index,
        "app",
        (
            "class ThemeColor:\n"
            "    error = ColorProperty([1, 0, 0, 1])\n"
            "    disabled = ColorProperty([0.5, 0.5, 0.5, 1])\n"
            "\n"
            "class Theme:\n"
            "    color: ThemeColor = ObjectProperty(None)\n"
            "\n"
            "class MainWindow:\n"
            "    theme: Theme = ObjectProperty(None)\n"
            "\n"
            "class UIText:\n"
            "    enabled = BooleanProperty(True)\n"
            "    text_color = ColorProperty(None, allownone=True)\n"
        ),
    )
    source = (
        "<UIText>:\n"
        "    text_color: app.theme.color.error\n"
        "<UIText>:\n"
        "    text_color:\n"
        "        None \\\n"
        "        if root.enabled \\\n"
        "        else app.theme.color.disabled\n"
    )
    document = TextDocument(
        uri="file:///colors.kv",
        text=source,
    )
    parse_result = parse(source)
    config = _config(app_class="app.MainWindow")
    semantic_model = build_kv_semantic_model(
        document,
        parse_result,
        index,
        config,
    )
    diagnostics = KvDiagnosticAnalyzer(
        index,
        config,
    ).analyze(
        document,
        parse_result,
        semantic_model,
    )
    assert not any(
        diagnostic.code in {
            "kv-incompatible-property-value",
            "kv-possibly-incompatible-property-value",
        }
        for diagnostic in diagnostics
    )
