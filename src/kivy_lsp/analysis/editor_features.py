"""Editor-independent hover, signature, structure and quick-fix results."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass

from kivy_lsp.analysis.call_argument_context import signature_call_context_at
from kivy_lsp.analysis.expression import (
    KvExpressionResolution,
    KvExpressionResolver,
)
from kivy_lsp.analysis.property_resolution import KivyPropertyResolver
from kivy_lsp.analysis.scope import KvSemanticModel, KvValue
from kivy_lsp.analysis.widget_resolution import resolve_widget_class
from kivy_lsp.config import ServerConfig
from kivy_lsp.kv.context import context_at
from kivy_lsp.kv.expression_source import EmbeddedPythonSource
from kivy_lsp.kv.index import KvIndex
from kivy_lsp.kv.nodes import (
    DocumentNode,
    KvNode,
    PropertyNode,
    RuleNode,
    WidgetNode,
)
from kivy_lsp.kv.parser import ParseResult
from kivy_lsp.kv.tokens import TokenKind
from kivy_lsp.model.diagnostic import Diagnostic
from kivy_lsp.model.span import Span
from kivy_lsp.model.symbol import (
    ParameterKind,
    ParameterSymbol,
    Symbol,
    SymbolKind,
)
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.workspace.document import TextDocument


@dataclass(frozen=True, slots=True)
class HoverResult:
    span: Span
    markdown: str


@dataclass(frozen=True, slots=True)
class SignatureResult:
    label: str
    parameters: tuple[str, ...]
    active_parameter: int | None
    documentation: str | None


@dataclass(frozen=True, slots=True)
class QuickFix:
    title: str
    span: Span
    replacement: str
    diagnostic: Diagnostic


def walk_nodes(node: KvNode) -> Iterator[KvNode]:
    """Visit complete and recovered syntax nodes in source order."""
    yield node
    if isinstance(node, DocumentNode):
        children = node.items
    elif isinstance(node, (RuleNode, WidgetNode, PropertyNode)):
        children = node.body
    else:
        children = ()
    if isinstance(node, PropertyNode) and node.value is not None:
        yield node.value
    for child in children:
        yield from walk_nodes(child)


def selection_spans(result: ParseResult, offset: int) -> tuple[Span, ...]:
    """Return nested selections from a token to the enclosing document."""
    spans = {
        token.span
        for token in result.tokens
        if token.span.contains(offset)
        and not token.is_trivia
        and not token.is_structural
        and not token.is_missing
    }
    spans.update(
        node.span
        for node in walk_nodes(result.document)
        if node.span.contains_cursor(offset)
    )
    ordered = sorted(spans, key=lambda span: (span.length, span.start))
    nested: list[Span] = []
    for span in ordered:
        if not nested or span.encloses(nested[-1]):
            nested.append(span)
    return tuple(nested)


def folding_spans(result: ParseResult) -> tuple[Span, ...]:
    """Return candidate rule, widget, canvas and expression fold spans."""
    return tuple(
        sorted(
            {
                node.span
                for node in walk_nodes(result.document)
                if isinstance(node, (RuleNode, WidgetNode, PropertyNode))
            }
        )
    )


class KvEditorFeatures:
    """Resolve editor details through the same semantic model as completion."""

    def __init__(
        self,
        python_index: PythonIndex,
        kv_index: KvIndex,
        config: ServerConfig,
    ) -> None:
        self.python_index = python_index
        self.kv_index = kv_index
        self.resolver = KvExpressionResolver(python_index, config, kv_index)
        self.properties = KivyPropertyResolver(python_index)

    def hover(
        self,
        document: TextDocument,
        result: ParseResult,
        model: KvSemanticModel,
        offset: int,
    ) -> HoverResult | None:
        token = next(
            (
                token
                for token in result.tokens
                if token.kind is TokenKind.IDENTIFIER
                and token.span.contains(offset)
            ),
            None,
        )
        if token is None:
            return None
        context = context_at(result, offset)
        selector = context.selector
        class_token = False
        if selector is not None and not selector.is_class_selector:
            class_token = (
                token == selector.name or token in selector.base_names
            )
        for node in walk_nodes(result.document):
            if isinstance(node, WidgetNode) and node.name == token:
                class_token = True
                break
        if class_token:
            dynamic = [
                item
                for item in self.kv_index.find(token.text)
                if item.is_dynamic
            ]
            if len(dynamic) == 1:
                bases = ", ".join(dynamic[0].bases)
                return HoverResult(
                    token.span, f"```kv\n<{token.text}@{bases}>\n```"
                )
            if len(dynamic) > 1:
                return None
            symbol = resolve_widget_class(
                token.text,
                self.python_index,
                self.kv_index,
            )
            if symbol is not None:
                return HoverResult(token.span, _symbol_markdown(symbol.symbol))
        scope = model.scope_at(offset)
        if scope is None:
            return None
        self_value = self.resolver.self_value(
            document,
            scope,
            context.property_owner,
        )
        property_node = context.property_node
        if property_node is not None and token in property_node.name_tokens:
            resolution = KvExpressionResolution.resolved(self_value)
            members = self.resolver.member_definitions(
                resolution,
                property_node.name,
            )
            if members:
                return HoverResult(token.span, _symbol_markdown(members[0]))
        expression = context.expression
        if expression is None:
            return None
        if property_node is not None and property_node.name == "id":
            binding = scope.id_named(token.text)
            if binding is not None:
                return HoverResult(
                    token.span,
                    _value_markdown(
                        token.text,
                        binding.value,
                    ),
                )
        embedded = EmbeddedPythonSource.from_source(
            document.text,
            expression.span,
            statement_block=bool(
                property_node and property_node.is_event_handler
            ),
        )
        source = token.text
        try:
            tree = ast.parse(embedded.text, mode=embedded.mode)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Name, ast.Attribute)):
                    continue
                span = embedded.node_span(node)
                name = node.id if isinstance(node, ast.Name) else node.attr
                if span.end == token.span.end and name == token.text:
                    source = ast.get_source_segment(embedded.text, node)
                    break
        else:
            tokens = [
                item
                for item in expression.tokens
                if not item.is_trivia and not item.is_structural
            ]
            index = tokens.index(token)
            start = index
            while start >= 2 and tokens[start - 1].kind is TokenKind.DOT:
                if tokens[start - 2].kind is not TokenKind.IDENTIFIER:
                    break
                start -= 2
            source = document.text[tokens[start].span.start : token.span.end]
        if source is None:
            return None
        resolution = self.resolver.resolve(
            source, scope, self_value=self_value
        )
        if resolution.value is None:
            return None
        return HoverResult(
            token.span,
            _value_markdown(
                token.text,
                resolution.value,
            ),
        )

    def signature_help(
        self,
        document: TextDocument,
        result: ParseResult,
        model: KvSemanticModel,
        offset: int,
    ) -> SignatureResult | None:
        context = context_at(result, offset)
        expression = context.expression
        scope = model.scope_at(offset)
        if expression is None or scope is None:
            return None
        call = signature_call_context_at(
            document, expression.span.start, offset
        )
        if call is None:
            return None
        self_value = self.resolver.self_value(
            document,
            scope,
            context.property_owner,
        )
        value = self.resolver.resolve(
            call.callee,
            scope,
            self_value=self_value,
        ).value
        if value is None or value.symbol is None:
            return None
        function = value.symbol
        parameters = function.parameters
        if (
            function.kind is SymbolKind.METHOD
            and parameters
            and parameters[0].name in {"self", "cls"}
        ):
            parameters = parameters[1:]
        labels = tuple(_parameter_label(item) for item in parameters)
        parts: list[str] = []
        has_star = False
        for index, parameter in enumerate(parameters):
            if parameter.kind is ParameterKind.KEYWORD_ONLY and not has_star:
                parts.append("*")
                has_star = True
            parts.append(labels[index])
            if parameter.kind is ParameterKind.VAR_POSITIONAL:
                has_star = True
            if parameter.kind is ParameterKind.POSITIONAL_ONLY and (
                index + 1 == len(parameters)
                or parameters[index + 1].kind
                is not ParameterKind.POSITIONAL_ONLY
            ):
                parts.append("/")
        label = f"{function.name}({', '.join(parts)})"
        if function.return_annotation:
            label += f" -> {function.return_annotation}"
        active = None
        if call.keyword_name is not None:
            active = next(
                (
                    index
                    for index, parameter in enumerate(parameters)
                    if parameter.name == call.keyword_name
                ),
                None,
            )
            if active is None:
                active = next(
                    (
                        index
                        for index, parameter in enumerate(parameters)
                        if parameter.kind is ParameterKind.VAR_KEYWORD
                    ),
                    None,
                )
        else:
            positional = [
                index
                for index, parameter in enumerate(parameters)
                if parameter.kind
                in {
                    ParameterKind.POSITIONAL_ONLY,
                    ParameterKind.POSITIONAL_OR_KEYWORD,
                }
            ]
            if call.argument_index < len(positional):
                active = positional[call.argument_index]
            else:
                active = next(
                    (
                        index
                        for index, parameter in enumerate(parameters)
                        if parameter.kind is ParameterKind.VAR_POSITIONAL
                    ),
                    None,
                )
        return SignatureResult(label, labels, active, function.documentation)

    def quick_fixes(
        self,
        document: TextDocument,
        result: ParseResult,
        model: KvSemanticModel,
        diagnostics: tuple[Diagnostic, ...],
    ) -> tuple[QuickFix, ...]:
        fixes: list[QuickFix] = []
        for diagnostic in diagnostics:
            if diagnostic.code != "kv-incompatible-property-value":
                continue
            context = context_at(result, diagnostic.span.start)
            prop = context.property_node
            scope = model.scope_at(diagnostic.span.start)
            if prop is None or prop.value is None or scope is None:
                continue
            source = EmbeddedPythonSource.from_source(
                document.text,
                prop.value.span,
            )
            try:
                literal = ast.parse(source.text, mode="eval").body
            except SyntaxError:
                continue
            if not isinstance(literal, ast.Constant):
                continue
            owner = self.resolver.self_value(
                document,
                scope,
                context.property_owner,
            )
            resolved = self.properties.resolve(owner, prop.name)
            if resolved is None or resolved.info is None:
                continue
            for option in resolved.info.options:
                fixes.append(
                    QuickFix(
                        f"Set {prop.name} to {option!r}",
                        source.node_span(literal),
                        repr(option),
                        diagnostic,
                    )
                )
        return tuple(fixes)


def _parameter_label(parameter: ParameterSymbol) -> str:
    prefix = ""
    if parameter.kind is ParameterKind.VAR_POSITIONAL:
        prefix = "*"
    elif parameter.kind is ParameterKind.VAR_KEYWORD:
        prefix = "**"
    text = prefix + parameter.name
    if parameter.annotation:
        text += f": {parameter.annotation}"
    if parameter.default is not None:
        text += f" = {parameter.default}"
    return text


def _value_markdown(name: str, value: KvValue) -> str:
    if value.symbol is not None:
        return _symbol_markdown(value.symbol)
    text = f"```python\n{name}: {value.type_name or 'Unknown'}\n```"
    if value.class_symbol and value.class_symbol.documentation:
        text += "\n\n" + value.class_symbol.documentation
    return text


def _symbol_markdown(symbol: Symbol) -> str:
    info = symbol.property_info
    annotation = symbol.annotation or (info.expected_display if info else None)
    label = symbol.signature or symbol.name
    if symbol.signature is None and annotation:
        label += f": {annotation}"
    lines = [f"```python\n{label}\n```"]
    if symbol.documentation:
        lines.append(symbol.documentation)
    if info is not None:
        if info.options:
            lines.append(
                "Options: "
                + ", ".join(f"`{value!r}`" for value in info.options)
            )
        if info.minimum is not None:
            lines.append(f"Minimum: `{info.minimum}`")
        if info.maximum is not None:
            lines.append(f"Maximum: `{info.maximum}`")
    return "\n\n".join(lines)
