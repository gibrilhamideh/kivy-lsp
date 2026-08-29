# src/kivy_lsp/analysis/semantic_tokens.py

from __future__ import annotations

import ast
import io
import keyword
import re
import token
import tokenize

from kivy_lsp.analysis.expression import (
    KvExpressionResolution,
    KvExpressionResolver,
    KvResolutionKind,
)
from kivy_lsp.analysis.scope import (
    KvScope,
    KvSemanticModel,
    KvValue,
    KvValueKind,
)
from kivy_lsp.config import ServerConfig
from kivy_lsp.kv.nodes import (
    BodyNode,
    DocumentNode,
    ExpressionNode,
    PropertyNode,
    RuleNode,
    WidgetNode,
)
from kivy_lsp.kv.parser import ParseResult
from kivy_lsp.model.semantic_token import (
    SemanticToken,
    SemanticTokenKind,
)
from kivy_lsp.model.span import Span
from kivy_lsp.model.symbol import SymbolKind
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.workspace.document import TextDocument

_CANVAS_NAMES = frozenset(
    {
        "canvas",
        "canvas.before",
        "canvas.after",
    }
)


_DIRECTIVE_PATTERN = re.compile(
    r"^[ \t]*#:[ \t]*(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
)

_RULE_PATTERN = re.compile(
    r"^[ \t]*<(?P<selectors>[^>]+)>[ \t]*:",
)

_ENTRY_PATTERN = re.compile(
    r"^[ \t]*(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*:",
)

_NAME_PATTERN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*",
)

_IGNORED_TOKEN_TYPES = {
    token.ENDMARKER,
    token.INDENT,
    token.DEDENT,
    token.NEWLINE,
    tokenize.NL,
    tokenize.ENCODING,
}


def semantic_tokens_for(
    document: TextDocument,
    parse_result: ParseResult,
) -> tuple[SemanticToken, ...]:
    """
    Produce syntax-aware semantic tokens.

    This entry point remains available for callers that do not yet have a
    semantic model or initialized Python index.
    """
    collector = _SemanticTokenCollector(
        document,
        parse_result,
    )
    return collector.collect()


class KvSemanticTokenAnalyzer:
    """Produce semantic tokens using KV and Python type information."""

    def __init__(
        self,
        python_index: PythonIndex,
        config: ServerConfig | None = None,
    ) -> None:
        self._resolver = KvExpressionResolver(
            python_index,
            config,
        )

    def analyze(
        self,
        document: TextDocument,
        parse_result: ParseResult,
        semantic_model: KvSemanticModel,
    ) -> tuple[SemanticToken, ...]:
        collector = _SemanticTokenCollector(
            document,
            parse_result,
            semantic_model=semantic_model,
            resolver=self._resolver,
        )
        return collector.collect()


class _SemanticTokenCollector:
    def __init__(
        self,
        document: TextDocument,
        parse_result: ParseResult,
        *,
        semantic_model: KvSemanticModel | None = None,
        resolver: KvExpressionResolver | None = None,
    ) -> None:
        self._document = document
        self._source = document.text
        self._parse_result = parse_result
        self._semantic_model = semantic_model
        self._resolver = resolver
        self._tokens: dict[tuple[int, int], SemanticToken] = {}

    def collect(self) -> tuple[SemanticToken, ...]:
        self._collect_syntax_tokens()
        self._visit_document(
            self._parse_result.document,
        )

        return tuple(
            sorted(
                self._tokens.values(),
                key=lambda semantic_token: (
                    semantic_token.span.start,
                    semantic_token.span.end,
                ),
            ),
        )

    def _collect_syntax_tokens(self) -> None:
        offset = 0
        expression_depth = 0
        continued_expression = False

        for raw_line in self._source.splitlines(keepends=True):
            line = raw_line.rstrip("\r\n")

            if expression_depth > 0 or continued_expression:
                python_tokens = self._add_expression_tokens(
                    line,
                    offset,
                )
                expression_depth = self._bracket_depth(
                    python_tokens,
                    expression_depth,
                )
                continued_expression = self._has_line_continuation(
                    line,
                )
                offset += len(raw_line)
                continue

            if self._visit_directive_line(line, offset):
                offset += len(raw_line)
                continue

            if self._visit_rule_line(line, offset):
                offset += len(raw_line)
                continue

            entry = _ENTRY_PATTERN.match(line)

            if entry is not None:
                name = entry.group("name")
                name_start = offset + entry.start("name")
                name_end = offset + entry.end("name")
                kind = self._entry_kind(name)

                self._add(
                    Span(name_start, name_end),
                    kind,
                )

                if kind is not SemanticTokenKind.CLASS:
                    expression = line[entry.end():]
                    expression_offset = offset + entry.end()

                    python_tokens = self._add_expression_tokens(
                        expression,
                        expression_offset,
                    )
                    expression_depth = self._bracket_depth(
                        python_tokens,
                        0,
                    )
                    continued_expression = (
                        self._has_line_continuation(expression)
                    )

                offset += len(raw_line)
                continue

            python_tokens = self._add_expression_tokens(
                line,
                offset,
            )
            expression_depth = self._bracket_depth(
                python_tokens,
                0,
            )
            continued_expression = self._has_line_continuation(
                line,
            )

            offset += len(raw_line)

    def _visit_document(
        self,
        document: DocumentNode,
    ) -> None:
        for item in document.items:
            if isinstance(item, RuleNode):
                self._visit_rule(item)
                continue

            if isinstance(item, WidgetNode):
                self._visit_widget(item)

    def _visit_rule(
        self,
        rule: RuleNode,
    ) -> None:
        for selector in rule.selectors:
            self._add(
                selector.name.span,
                SemanticTokenKind.CLASS,
            )

            for base_name in selector.base_names:
                self._add(
                    base_name.span,
                    SemanticTokenKind.CLASS,
                )

        self._visit_body(
            rule.body,
            current_widget=None,
        )

    def _visit_widget(
        self,
        widget: WidgetNode,
    ) -> None:
        self._add(
            widget.name.span,
            SemanticTokenKind.CLASS,
        )
        self._visit_body(
            widget.body,
            current_widget=widget,
        )

    def _visit_body(
        self,
        body: tuple[BodyNode, ...],
        *,
        current_widget: WidgetNode | None,
    ) -> None:
        for item in body:
            if isinstance(item, WidgetNode):
                self._visit_widget(item)
                continue

            self._visit_property(
                item,
                current_widget=current_widget,
            )

    def _visit_property(
        self,
        property_node: PropertyNode,
        *,
        current_widget: WidgetNode | None,
        inside_canvas: bool = False,
    ) -> None:
        kind = self._entry_kind(
            property_node.name,
        )

        for name_token in property_node.name_tokens:
            if _NAME_PATTERN.fullmatch(name_token.text) is None:
                continue

            self._add(
                name_token.span,
                kind,
            )

        if property_node.value is not None:
            self._add_expression_semantics(
                property_node.value,
                current_widget=current_widget,
            )

        if property_node.body:
            if (
                inside_canvas
                or property_node.name in _CANVAS_NAMES
            ):
                self._visit_canvas_body(
                    property_node.body,
                    current_widget=current_widget,
                )
            else:
                self._visit_body(
                    property_node.body,
                    current_widget=current_widget,
                )

    def _visit_canvas_body(
        self,
        body: tuple[BodyNode, ...],
        *,
        current_widget: WidgetNode | None,
    ) -> None:
        for item in body:
            if isinstance(item, WidgetNode):
                self._add(
                    item.name.span,
                    SemanticTokenKind.CLASS,
                )
                self._visit_canvas_body(
                    item.body,
                    current_widget=current_widget,
                )
                continue

            self._visit_property(
                item,
                current_widget=current_widget,
                inside_canvas=True,
            )

    def _add_expression_semantics(
        self,
        expression: ExpressionNode,
        *,
        current_widget: WidgetNode | None,
    ) -> None:
        semantic_model = self._semantic_model
        resolver = self._resolver

        if semantic_model is None or resolver is None:
            return

        source = expression.text

        if not source.strip():
            return

        scope = semantic_model.scope_at(
            expression.span.start,
        )

        if scope is None:
            return

        self_value = resolver.self_value(
            self._document,
            scope,
            current_widget,
        )

        try:
            tree = ast.parse(
                source,
                mode="eval",
            )
        except SyntaxError:
            return

        call_targets = {
            call_span
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            if (
                call_span := _call_target_span(
                    source,
                    node.func,
                )
            )
            is not None
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                relative_span = _node_span(
                    source,
                    node,
                )

                if relative_span is None:
                    continue

                fallback = self._name_fallback_kind(
                    node.id,
                    relative_span,
                    call_targets,
                )
                kind = self._resolved_node_kind(
                    source,
                    relative_span,
                    scope,
                    self_value,
                    fallback,
                )
                self._add_relative(
                    expression.span.start,
                    relative_span,
                    kind,
                )
                continue

            if not isinstance(node, ast.Attribute):
                continue

            relative_span = _attribute_span(
                source,
                node,
            )

            if relative_span is None:
                continue

            fallback = self._attribute_fallback_kind(
                node,
                relative_span,
                call_targets,
            )

            if _is_id_lookup(node):
                kind = fallback
            else:
                kind = self._resolved_node_kind(
                    source,
                    _node_span(source, node),
                    scope,
                    self_value,
                    fallback,
                )

            self._add_relative(
                expression.span.start,
                relative_span,
                kind,
            )

    def _resolved_node_kind(
        self,
        source: str,
        relative_span: Span | None,
        scope: KvScope,
        self_value: KvValue,
        fallback: SemanticTokenKind,
    ) -> SemanticTokenKind:
        if relative_span is None:
            return fallback

        expression = source[
            relative_span.start:relative_span.end
        ]
        resolver = self._resolver

        if resolver is None:
            return fallback

        result = resolver.resolve(
            expression,
            scope,
            self_value=self_value,
        )

        return _resolution_token_kind(
            result,
            fallback,
        )

    def _name_fallback_kind(
        self,
        name: str,
        span: Span,
        call_targets: set[Span],
    ) -> SemanticTokenKind:
        if span in call_targets:
            if name[0].isupper():
                return SemanticTokenKind.CLASS

            return SemanticTokenKind.FUNCTION

        if name[0].isupper():
            return SemanticTokenKind.CLASS

        return SemanticTokenKind.VARIABLE

    def _attribute_fallback_kind(
        self,
        node: ast.Attribute,
        span: Span,
        call_targets: set[Span],
    ) -> SemanticTokenKind:
        if _is_ids_owner(node.value):
            if node.attr == "get":
                return SemanticTokenKind.METHOD

            return SemanticTokenKind.VARIABLE

        if span in call_targets:
            return SemanticTokenKind.METHOD

        return SemanticTokenKind.PROPERTY

    def _visit_directive_line(
        self,
        line: str,
        line_offset: int,
    ) -> bool:
        match = _DIRECTIVE_PATTERN.match(line)

        if match is None:
            return False

        self._add(
            Span(
                line_offset + match.start("name"),
                line_offset + match.end("name"),
            ),
            SemanticTokenKind.KEYWORD,
        )
        return True

    def _visit_rule_line(
        self,
        line: str,
        line_offset: int,
    ) -> bool:
        match = _RULE_PATTERN.match(line)

        if match is None:
            return False

        selectors = match.group("selectors")
        selectors_offset = (
            line_offset + match.start("selectors")
        )

        for name_match in _NAME_PATTERN.finditer(selectors):
            self._add(
                Span(
                    selectors_offset + name_match.start(),
                    selectors_offset + name_match.end(),
                ),
                SemanticTokenKind.CLASS,
            )

        return True

    def _add_expression_tokens(
        self,
        expression: str,
        expression_offset: int,
    ) -> tuple[tokenize.TokenInfo, ...]:
        python_tokens = self._python_tokens(expression)
        meaningful = tuple(
            python_token
            for python_token in python_tokens
            if python_token.type not in _IGNORED_TOKEN_TYPES
        )

        for index, python_token in enumerate(meaningful):
            if python_token.type != token.NAME:
                continue

            previous = (
                meaningful[index - 1]
                if index > 0
                else None
            )
            following = (
                meaningful[index + 1]
                if index + 1 < len(meaningful)
                else None
            )

            kind = self._expression_name_kind(
                python_token.string,
                previous,
                following,
            )

            start = expression_offset + python_token.start[1]
            end = expression_offset + python_token.end[1]

            self._add(
                Span(start, end),
                kind,
            )

        return python_tokens

    def _expression_name_kind(
        self,
        name: str,
        previous: tokenize.TokenInfo | None,
        following: tokenize.TokenInfo | None,
    ) -> SemanticTokenKind:
        if keyword.iskeyword(name):
            return SemanticTokenKind.KEYWORD

        if name in {"True", "False", "None"}:
            return SemanticTokenKind.KEYWORD

        follows_dot = (
            previous is not None
            and previous.type == token.OP
            and previous.string == "."
        )

        followed_by_call = (
            following is not None
            and following.type == token.OP
            and following.string == "("
        )

        if follows_dot and followed_by_call:
            return SemanticTokenKind.METHOD

        if follows_dot:
            return SemanticTokenKind.PROPERTY

        if followed_by_call and name[0].isupper():
            return SemanticTokenKind.CLASS

        if followed_by_call:
            return SemanticTokenKind.FUNCTION

        if name[0].isupper():
            return SemanticTokenKind.CLASS

        return SemanticTokenKind.VARIABLE

    def _entry_kind(
        self,
        name: str,
    ) -> SemanticTokenKind:
        if name[0].isupper():
            return SemanticTokenKind.CLASS

        if name in {
            "id",
            "canvas",
            "canvas.before",
            "canvas.after",
        }:
            return SemanticTokenKind.KEYWORD

        if name.startswith("on_"):
            return SemanticTokenKind.EVENT

        return SemanticTokenKind.PROPERTY

    def _python_tokens(
        self,
        expression: str,
    ) -> tuple[tokenize.TokenInfo, ...]:
        result: list[tokenize.TokenInfo] = []
        generator = tokenize.generate_tokens(
            io.StringIO(expression).readline,
        )

        try:
            while True:
                result.append(next(generator))
        except StopIteration:
            pass
        except (IndentationError, tokenize.TokenError):
            pass

        return tuple(result)

    def _bracket_depth(
        self,
        python_tokens: tuple[tokenize.TokenInfo, ...],
        initial_depth: int,
    ) -> int:
        depth = initial_depth

        for python_token in python_tokens:
            if python_token.type != token.OP:
                continue

            if python_token.string in {"(", "[", "{"}:
                depth += 1
            elif python_token.string in {")", "]", "}"}:
                depth = max(0, depth - 1)

        return depth

    def _has_line_continuation(
        self,
        expression: str,
    ) -> bool:
        return expression.rstrip().endswith("\\")

    def _add_relative(
        self,
        expression_start: int,
        span: Span,
        kind: SemanticTokenKind,
    ) -> None:
        self._add(
            Span(
                expression_start + span.start,
                expression_start + span.end,
            ),
            kind,
        )

    def _add(
        self,
        span: Span,
        kind: SemanticTokenKind,
    ) -> None:
        if span.start >= span.end:
            return

        self._tokens[(span.start, span.end)] = SemanticToken(
            span=span,
            kind=kind,
        )


def _resolution_token_kind(
    resolution: KvExpressionResolution,
    fallback: SemanticTokenKind,
) -> SemanticTokenKind:
    if resolution.kind is not KvResolutionKind.VALUE:
        return fallback

    value = resolution.value

    if value is None:
        return fallback

    symbol = value.symbol

    if symbol is not None:
        return _symbol_token_kind(
            symbol.kind,
            fallback,
        )

    if value.kind is KvValueKind.CLASS:
        return SemanticTokenKind.CLASS

    if value.kind is KvValueKind.FUNCTION:
        return SemanticTokenKind.FUNCTION

    return fallback


def _symbol_token_kind(
    kind: SymbolKind,
    fallback: SemanticTokenKind,
) -> SemanticTokenKind:
    kinds = {
        SymbolKind.CLASS: SemanticTokenKind.CLASS,
        SymbolKind.METHOD: SemanticTokenKind.METHOD,
        SymbolKind.FUNCTION: SemanticTokenKind.FUNCTION,
        SymbolKind.PROPERTY: SemanticTokenKind.PROPERTY,
        SymbolKind.EVENT: SemanticTokenKind.EVENT,
    }

    return kinds.get(
        kind,
        fallback,
    )


def _call_target_span(
    source: str,
    node: ast.expr,
) -> Span | None:
    if isinstance(node, ast.Attribute):
        return _attribute_span(
            source,
            node,
        )

    return _node_span(
        source,
        node,
    )


def _is_id_lookup(node: ast.Attribute) -> bool:
    return (
        _is_ids_owner(node.value)
        and node.attr != "get"
    )


def _is_ids_owner(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "ids"
    )


def _node_span(
    source: str,
    node: ast.AST,
) -> Span | None:
    start_line = getattr(
        node,
        "lineno",
        None,
    )
    start_column = getattr(
        node,
        "col_offset",
        None,
    )
    end_line = getattr(
        node,
        "end_lineno",
        None,
    )
    end_column = getattr(
        node,
        "end_col_offset",
        None,
    )

    if not isinstance(start_line, int):
        return None

    if not isinstance(start_column, int):
        return None

    if not isinstance(end_line, int):
        return None

    if not isinstance(end_column, int):
        return None

    return Span(
        start=_source_offset(
            source,
            start_line,
            start_column,
        ),
        end=_source_offset(
            source,
            end_line,
            end_column,
        ),
    )


def _attribute_span(
    source: str,
    node: ast.Attribute,
) -> Span | None:
    node_span = _node_span(
        source,
        node,
    )

    if node_span is None:
        return None

    return Span(
        start=max(
            node_span.start,
            node_span.end - len(node.attr),
        ),
        end=node_span.end,
    )


def _source_offset(
    source: str,
    line: int,
    byte_column: int,
) -> int:
    lines = source.splitlines(keepends=True)
    line_index = max(
        0,
        line - 1,
    )

    if line_index >= len(lines):
        return len(source)

    line_start = sum(
        len(current)
        for current in lines[:line_index]
    )
    current_line = lines[line_index]
    encoded_line = current_line.encode("utf-8")
    encoded_prefix = encoded_line[:byte_column]
    character_prefix = encoded_prefix.decode(
        "utf-8",
        errors="ignore",
    )

    return line_start + len(character_prefix)
