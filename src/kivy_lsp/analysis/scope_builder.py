# src/kivy_lsp/analysis/scope_builder.py

from __future__ import annotations

import ast
import re

from kivy_lsp.analysis.scope import (
    KvBinding,
    KvBindingKind,
    KvScope,
    KvScopeOwner,
    KvSemanticModel,
    KvValue,
    KvWidgetValue,
)
from kivy_lsp.config import GlobalImport, ServerConfig
from kivy_lsp.kv.nodes import (
    BodyNode,
    PropertyNode,
    RuleNode,
    WidgetNode,
)
from kivy_lsp.kv.parser import ParseResult
from kivy_lsp.model.diagnostic import (
    Diagnostic,
    DiagnosticSeverity,
)
from kivy_lsp.model.span import Span
from kivy_lsp.model.symbol import (
    ClassSymbol,
    Symbol,
    SymbolKind,
    SymbolLocation,
)
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.workspace.document import TextDocument

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_IMPORT_DIRECTIVE_PATTERN = re.compile(
    r"^\s*#:\s*import\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+"
    r"(?P<target>[A-Za-z_][A-Za-z0-9_.]*)\s*$",
    re.MULTILINE,
)


def build_kv_semantic_model(
    document: TextDocument,
    parse_result: ParseResult,
    python_index: PythonIndex,
    config: ServerConfig,
) -> KvSemanticModel:
    """Build semantic scopes for one parsed KV document."""
    diagnostics: list[Diagnostic] = []
    scopes: list[KvScope] = []

    app_binding = _build_app_binding(
        python_index,
        config,
    )
    global_bindings = _build_global_bindings(
        document,
        python_index,
        config,
    )

    for item in parse_result.document.items:
        if isinstance(item, RuleNode):
            root_value = _rule_root_value(
                document,
                item,
                python_index,
            )
            scope = _build_scope(
                document=document,
                owner=item,
                body=item.body,
                root_value=root_value,
                app_binding=app_binding,
                global_bindings=global_bindings,
                python_index=python_index,
                diagnostics=diagnostics,
            )
            scopes.append(scope)
            continue

        if isinstance(item, WidgetNode):
            root_value = _widget_value(
                document,
                item,
                python_index,
            )
            scope = _build_scope(
                document=document,
                owner=item,
                body=item.body,
                root_value=root_value,
                app_binding=app_binding,
                global_bindings=global_bindings,
                python_index=python_index,
                diagnostics=diagnostics,
            )
            scopes.append(scope)

    return KvSemanticModel(
        uri=document.uri,
        scopes=tuple(scopes),
        diagnostics=tuple(diagnostics),
    )


def _build_scope(
    *,
    document: TextDocument,
    owner: KvScopeOwner,
    body: tuple[BodyNode, ...],
    root_value: KvValue,
    app_binding: KvBinding,
    global_bindings: tuple[KvBinding, ...],
    python_index: PythonIndex,
    diagnostics: list[Diagnostic],
) -> KvScope:
    root_value = _with_local_members(
        document,
        body,
        root_value,
        python_index,
    )
    root_binding = KvBinding(
        name="root",
        kind=KvBindingKind.ROOT,
        value=root_value,
        declaration_span=owner.span,
    )
    id_bindings, widget_values = _collect_body_semantics(
        document=document,
        body=body,
        current_value=root_value,
        current_widget=(
            owner
            if isinstance(owner, WidgetNode)
            else None
        ),
        python_index=python_index,
        diagnostics=diagnostics,
    )

    if isinstance(owner, WidgetNode):
        widget_values = (
            KvWidgetValue(
                widget=owner,
                value=root_value,
            ),
            *widget_values,
        )

    return KvScope(
        uri=document.uri,
        owner=owner,
        span=owner.span,
        root_binding=root_binding,
        app_binding=app_binding,
        id_bindings=id_bindings,
        global_bindings=global_bindings,
        widget_values=widget_values,
    )


def _collect_body_semantics(
    *,
    document: TextDocument,
    body: tuple[BodyNode, ...],
    current_value: KvValue,
    current_widget: WidgetNode | None,
    python_index: PythonIndex,
    diagnostics: list[Diagnostic],
) -> tuple[
    tuple[KvBinding, ...],
    tuple[KvWidgetValue, ...],
]:
    bindings: list[KvBinding] = []
    widget_values: list[KvWidgetValue] = []
    names: set[str] = set()

    _visit_body(
        document=document,
        body=body,
        current_value=current_value,
        current_widget=current_widget,
        python_index=python_index,
        bindings=bindings,
        widget_values=widget_values,
        names=names,
        diagnostics=diagnostics,
    )

    return (
        tuple(bindings),
        tuple(widget_values),
    )


def _visit_body(
    *,
    document: TextDocument,
    body: tuple[BodyNode, ...],
    current_value: KvValue,
    current_widget: WidgetNode | None,
    python_index: PythonIndex,
    bindings: list[KvBinding],
    widget_values: list[KvWidgetValue],
    names: set[str],
    diagnostics: list[Diagnostic],
) -> None:
    for item in body:
        if isinstance(item, WidgetNode):
            widget_value = _widget_value(
                document,
                item,
                python_index,
            )
            widget_value = _with_local_members(
                document,
                item.body,
                widget_value,
                python_index,
            )
            widget_values.append(
                KvWidgetValue(
                    widget=item,
                    value=widget_value,
                )
            )
            _visit_body(
                document=document,
                body=item.body,
                current_value=widget_value,
                current_widget=item,
                python_index=python_index,
                bindings=bindings,
                widget_values=widget_values,
                names=names,
                diagnostics=diagnostics,
            )
            continue

        if _property_name(document, item) == "id":
            _add_id_binding(
                document=document,
                node=item,
                current_value=current_value,
                current_widget=current_widget,
                bindings=bindings,
                names=names,
                diagnostics=diagnostics,
            )

        if item.body:
            _visit_body(
                document=document,
                body=item.body,
                current_value=current_value,
                current_widget=current_widget,
                python_index=python_index,
                bindings=bindings,
                widget_values=widget_values,
                names=names,
                diagnostics=diagnostics,
            )


def _with_local_members(
    document: TextDocument,
    body: tuple[BodyNode, ...],
    value: KvValue,
    python_index: PythonIndex,
) -> KvValue:
    members: list[Symbol] = []
    names: set[str] = set()

    for item in body:
        if not isinstance(item, PropertyNode):
            continue

        name = item.name

        if not _is_local_property(item, name):
            continue

        if name in names:
            continue

        if _python_member_exists(
            value,
            name,
            python_index,
        ):
            continue

        names.add(name)
        members.append(
            _local_property_symbol(
                document,
                item,
                value,
            )
        )

    if not members:
        return value

    return value.with_local_members(tuple(members))


def _is_local_property(
    node: PropertyNode,
    name: str,
) -> bool:
    if node.value is None:
        return False

    if node.clear_previous is not None:
        return False

    if name == "id" or node.is_event_handler:
        return False

    return _IDENTIFIER_PATTERN.fullmatch(name) is not None


def _python_member_exists(
    value: KvValue,
    name: str,
    python_index: PythonIndex,
) -> bool:
    class_symbol = value.class_symbol

    if class_symbol is None:
        return False

    return (
        python_index.member_named(
            class_symbol,
            name,
        )
        is not None
    )


def _local_property_symbol(
    document: TextDocument,
    node: PropertyNode,
    owner: KvValue,
) -> Symbol:
    selection_span = Span(
        start=node.name_tokens[0].span.start,
        end=node.name_tokens[-1].span.end,
    )
    owner_name = owner.type_name or "kv"
    annotation = (
        _expression_annotation(node.value.text)
        if node.value is not None
        else None
    )

    return Symbol(
        name=node.name,
        qualified_name=f"{owner_name}.{node.name}",
        kind=SymbolKind.PROPERTY,
        location=SymbolLocation(
            uri=document.uri,
            span=node.span,
            selection_span=selection_span,
        ),
        annotation=annotation,
        documentation="KV-created instance property",
    )


def _expression_annotation(source: str) -> str | None:
    try:
        expression = ast.parse(
            source,
            mode="eval",
        ).body
    except SyntaxError:
        return None

    return _node_annotation(expression)


def _node_annotation(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant):
        value = node.value

        if value is None:
            return "None"

        if isinstance(value, bool):
            return "bool"

        if isinstance(value, int):
            return "int"

        if isinstance(value, float):
            return "float"

        if isinstance(value, str):
            return "str"

        return None

    if isinstance(node, ast.JoinedStr):
        return "str"

    if isinstance(node, ast.List):
        return "list[Any]"

    if isinstance(node, ast.Tuple):
        return "tuple[Any, ...]"

    if isinstance(node, ast.Dict):
        return "dict[Any, Any]"

    if isinstance(node, ast.Set):
        return "set[Any]"

    if isinstance(node, ast.Lambda):
        return "Callable[..., Any]"

    if isinstance(node, ast.IfExp):
        first = _node_annotation(node.body)
        second = _node_annotation(node.orelse)

        if first is None or second is None:
            return None

        if first == second:
            return first

        return f"{first} | {second}"

    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.operand, ast.Constant)
    ):
        value = node.operand.value

        if isinstance(value, int) and not isinstance(value, bool):
            return "int"

        if isinstance(value, float):
            return "float"

    return None


def _add_id_binding(
    *,
    document: TextDocument,
    node: PropertyNode,
    current_value: KvValue,
    current_widget: WidgetNode | None,
    bindings: list[KvBinding],
    names: set[str],
    diagnostics: list[Diagnostic],
) -> None:
    if node.value is None:
        diagnostics.append(
            Diagnostic(
                message="A Kivy id requires a name.",
                span=node.span,
                severity=DiagnosticSeverity.ERROR,
                code="kv-id-missing",
            )
        )
        return

    declaration_span = node.value.span
    name = _source_text(
        document,
        declaration_span,
    ).strip()

    if _IDENTIFIER_PATTERN.fullmatch(name) is None:
        diagnostics.append(
            Diagnostic(
                message="A Kivy id must be a valid identifier.",
                span=declaration_span,
                severity=DiagnosticSeverity.ERROR,
                code="kv-id-invalid",
            )
        )
        return

    if name in {"root", "self", "app"}:
        diagnostics.append(
            Diagnostic(
                message=f'"{name}" is reserved in Kivy expressions.',
                span=declaration_span,
                severity=DiagnosticSeverity.ERROR,
                code="kv-id-reserved",
            )
        )
        return

    if name in names:
        diagnostics.append(
            Diagnostic(
                message=f'Duplicate Kivy id "{name}".',
                span=declaration_span,
                severity=DiagnosticSeverity.ERROR,
                code="kv-id-duplicate",
            )
        )
        return

    names.add(name)
    bindings.append(
        KvBinding(
            name=name,
            kind=KvBindingKind.ID,
            value=current_value,
            declaration_span=declaration_span,
            widget=current_widget,
        )
    )


def _build_app_binding(
    python_index: PythonIndex,
    config: ServerConfig,
) -> KvBinding:
    app_class_name = config.app_class

    if app_class_name is None:
        value = KvValue.unknown("kivy.app.App")
    else:
        class_symbol = python_index.resolve_class(app_class_name)
        value = KvValue.instance(
            app_class_name,
            class_symbol,
        )

    return KvBinding(
        name="app",
        kind=KvBindingKind.APP,
        value=value,
    )


def _build_global_bindings(
    document: TextDocument,
    python_index: PythonIndex,
    config: ServerConfig,
) -> tuple[KvBinding, ...]:
    bindings: list[KvBinding] = []
    names: set[str] = set()

    for imported_name, target in _directive_imports(document):
        binding = _global_binding(
            imported_name,
            target,
            python_index,
        )
        bindings.append(binding)
        names.add(imported_name)

    for name, target in config.globals.items():
        if name in names:
            continue

        bindings.append(
            _global_binding(
                name,
                target,
                python_index,
            )
        )
        names.add(name)

    for imported in config.global_imports:
        if imported.name in names:
            continue

        bindings.append(
            _configured_global_binding(
                imported,
                python_index,
            )
        )
        names.add(imported.name)

    return tuple(bindings)


def _configured_global_binding(
    imported: GlobalImport,
    python_index: PythonIndex,
) -> KvBinding:
    return _global_binding(
        imported.name,
        imported.target,
        python_index,
    )


def _global_binding(
    name: str,
    target: str,
    python_index: PythonIndex,
) -> KvBinding:
    class_symbol = python_index.resolve_class(target)

    if class_symbol is not None:
        value = KvValue.class_value(class_symbol)
    else:
        symbol = python_index.resolve_symbol(target)

        if symbol is not None:
            value = _symbol_value(
                symbol,
                python_index,
            )
        elif python_index.module_named(target) is not None:
            value = KvValue.module(target)
        else:
            value = KvValue.unknown(target)

    return KvBinding(
        name=name,
        kind=KvBindingKind.GLOBAL,
        value=value,
    )


def _symbol_value(
    symbol: Symbol,
    python_index: PythonIndex,
) -> KvValue:
    class_symbol = _resolve_symbol_annotation(
        symbol,
        python_index,
    )

    return KvValue.from_symbol(
        symbol,
        class_symbol=class_symbol,
    )


def _resolve_symbol_annotation(
    symbol: Symbol,
    python_index: PythonIndex,
) -> ClassSymbol | None:
    annotation = symbol.annotation

    if annotation is None:
        return None

    module_name, _, _ = symbol.qualified_name.rpartition(".")

    for reference in _annotation_references(annotation):
        class_symbol = python_index.resolve_class(
            reference,
            from_module=module_name,
        )

        if class_symbol is not None:
            return class_symbol

    return None


def _annotation_references(annotation: str) -> tuple[str, ...]:
    annotation = annotation.strip().strip("'\"")

    if annotation.startswith("Optional[") and annotation.endswith("]"):
        annotation = annotation[9:-1]

    references: list[str] = []

    for part in annotation.split("|"):
        reference = part.strip()

        if reference in {"", "None", "NoneType"}:
            continue

        references.append(reference)

    return tuple(references)


def _rule_root_value(
    document: TextDocument,
    rule: RuleNode,
    python_index: PythonIndex,
) -> KvValue:
    class_name, base_name = _rule_class_names(
        document,
        rule,
    )
    lookup_name = base_name or class_name

    if lookup_name is None:
        return KvValue.unknown()

    class_symbol = _resolve_widget_class(
        lookup_name,
        python_index,
    )

    return KvValue.instance(
        class_name or lookup_name,
        class_symbol,
    )


def _widget_value(
    document: TextDocument,
    widget: WidgetNode,
    python_index: PythonIndex,
) -> KvValue:
    class_name = _widget_name(
        document,
        widget,
    )

    if class_name is None:
        return KvValue.unknown()

    class_symbol = _resolve_widget_class(
        class_name,
        python_index,
    )

    return KvValue.instance(
        class_name,
        class_symbol,
    )


def _resolve_widget_class(
    name: str,
    python_index: PythonIndex,
) -> ClassSymbol | None:
    class_symbol = python_index.resolve_class(name)

    if class_symbol is not None:
        return class_symbol

    matches = python_index.classes_named(name)

    if len(matches) == 1:
        return matches[0]

    return None


def _rule_class_names(
    document: TextDocument,
    rule: RuleNode,
) -> tuple[str | None, str | None]:
    header = _header_text(
        document,
        rule.span,
    )
    opening = header.find("<")
    closing = header.find(">", opening + 1)

    if opening == -1 or closing == -1:
        return None, None

    selector = header[opening + 1:closing]
    selector = selector.split(",", maxsplit=1)[0].strip()
    selector = selector.removeprefix("-").strip()

    if "@" not in selector:
        return selector or None, None

    class_name, base_name = selector.split("@", maxsplit=1)

    return (
        class_name.strip() or None,
        base_name.strip() or None,
    )


def _widget_name(
    document: TextDocument,
    widget: WidgetNode,
) -> str | None:
    header = _header_text(
        document,
        widget.span,
    )
    name, separator, _ = header.partition(":")

    if not separator:
        return None

    name = name.strip()

    if _IDENTIFIER_PATTERN.fullmatch(name) is None:
        return None

    return name


def _property_name(
    document: TextDocument,
    node: PropertyNode,
) -> str | None:
    header = _header_text(
        document,
        node.span,
    )
    name, separator, _ = header.partition(":")

    if not separator:
        return None

    name = name.strip().removeprefix("-").strip()

    if _IDENTIFIER_PATTERN.fullmatch(name) is None:
        return None

    return name


def _directive_imports(
    document: TextDocument,
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            match.group("name"),
            match.group("target"),
        )
        for match in _IMPORT_DIRECTIVE_PATTERN.finditer(document.text)
    )


def _header_text(
    document: TextDocument,
    span: Span,
) -> str:
    line_end = document.text.find(
        "\n",
        span.start,
        span.end,
    )

    if line_end == -1:
        line_end = span.end

    return document.text[span.start:line_end].strip()


def _source_text(
    document: TextDocument,
    span: Span,
) -> str:
    return document.text[span.start:span.end]
