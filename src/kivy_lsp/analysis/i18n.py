from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass

from kivy_lsp.analysis.completion import (
    KvCompletionItem,
    KvCompletionKind,
    KvCompletionResult,
)
from kivy_lsp.analysis.completion_context import (
    KvCompletionTarget,
    KvCompletionTargetKind,
)
from kivy_lsp.config import I18nConfig
from kivy_lsp.i18n.index import (
    TranslationEntry,
    TranslationIndex,
    TranslationPlaceholder,
)
from kivy_lsp.kv.context import context_at
from kivy_lsp.kv.nodes import (
    BodyNode,
    DocumentNode,
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
from kivy_lsp.workspace.document import TextDocument

_PARAMETER_KEY_PATTERN = re.compile(
    r"(?P<quote>['\"])(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P=quote)\s*:"
)


@dataclass(frozen=True, slots=True)
class TranslationKeyTarget:
    """A literal KV translation key at the cursor."""

    key: str
    span: Span
    entry: TranslationEntry | None


@dataclass(frozen=True, slots=True)
class TranslationParameterTarget:
    """An i18n parameter key and its translation placeholder."""

    name: str
    span: Span
    entry: TranslationEntry
    placeholder: TranslationPlaceholder | None


@dataclass(frozen=True, slots=True)
class _QuotedContext:
    prefix: str
    quote: str
    span: Span


@dataclass(frozen=True, slots=True)
class _ParameterDictionary:
    names: tuple[str, ...]
    duplicate_spans: tuple[Span, ...]
    key_spans: dict[str, Span]
    invalid_key_spans: tuple[Span, ...]
    has_dynamic_keys: bool = False


class TranslationCompletionEngine:
    """Complete translation keys and their named parameters."""

    def __init__(
        self,
        index: TranslationIndex,
        config: I18nConfig,
    ) -> None:
        self._index = index
        self._config = config

    def complete(
        self,
        document: TextDocument,
        parse_result: ParseResult,
        offset: int,
    ) -> KvCompletionResult | None:
        property_node, body = _property_and_body_at(
            parse_result.document,
            offset,
        )

        if property_node is None:
            return None

        if property_node.name in self._config.properties:
            return self._complete_translation_key(
                document,
                property_node,
                offset,
            )

        if property_node.name != "i18n_params" or body is None:
            return None

        return self._complete_parameter(
            document,
            property_node,
            body,
            offset,
        )

    def _complete_translation_key(
        self,
        document: TextDocument,
        property_node: PropertyNode,
        offset: int,
    ) -> KvCompletionResult | None:
        quoted = _quoted_context_at(
            document.text,
            property_node.colon.span.end,
            property_node.span.end,
            offset,
        )

        if quoted is None:
            return None

        items = tuple(
            KvCompletionItem(
                label=entry.key,
                kind=KvCompletionKind.CONSTANT,
                insert_text=_quoted_text(
                    entry.key,
                    quoted.quote,
                ),
                sort_text=f"00:{entry.key.casefold()}",
                detail="Translation key",
                documentation=entry.value,
            )
            for entry in self._index.complete(quoted.prefix)
        )

        return _completion_result(
            quoted,
            items,
        )

    def _complete_parameter(
        self,
        document: TextDocument,
        property_node: PropertyNode,
        body: tuple[BodyNode, ...],
        offset: int,
    ) -> KvCompletionResult | None:
        entry = _entry_for_body(
            document,
            body,
            self._config,
            self._index,
        )

        if entry is None:
            return None

        quoted = _parameter_context_at(
            document.text,
            property_node,
            offset,
        )

        if quoted is None:
            return None

        existing = _parameter_names(
            document,
            property_node,
        )
        items = tuple(
            KvCompletionItem(
                label=name,
                kind=KvCompletionKind.CONSTANT,
                insert_text=_quoted_text(
                    name,
                    quoted.quote,
                ),
                sort_text=f"00:{name.casefold()}",
                detail="Translation parameter",
                documentation=entry.value,
            )
            for name in entry.placeholder_names
            if name.casefold().startswith(
                quoted.prefix.casefold()
            )
            if name not in existing or name == quoted.prefix
        )

        return _completion_result(
            quoted,
            items,
        )


class TranslationDiagnosticAnalyzer:
    """Validate translation keys and literal i18n parameters."""

    def __init__(
        self,
        index: TranslationIndex,
        config: I18nConfig,
    ) -> None:
        self._index = index
        self._config = config

    def analyze(
        self,
        document: TextDocument,
        parse_result: ParseResult,
    ) -> tuple[Diagnostic, ...]:
        diagnostics: list[Diagnostic] = []
        catalog_problems = self._index.problems
        entries = {
            entry.key: entry
            for entry in self._index.entries()
        }
        first_key = next(
            _translation_properties(
                parse_result.document,
                self._config,
            ),
            None,
        )

        if catalog_problems and first_key is not None:
            diagnostics.append(
                Diagnostic(
                    message=catalog_problems[0].message,
                    span=_property_selection_span(first_key),
                    severity=DiagnosticSeverity.ERROR,
                    code="i18n-catalog-error",
                )
            )
            return tuple(diagnostics)

        for body in _owner_bodies(parse_result.document):
            self._analyze_body(
                document,
                body,
                entries,
                diagnostics,
            )

        return _deduplicate_diagnostics(diagnostics)

    def _analyze_body(
        self,
        document: TextDocument,
        body: tuple[BodyNode, ...],
        entries: dict[str, TranslationEntry],
        diagnostics: list[Diagnostic],
    ) -> None:
        for key_property in _key_properties(
            body,
            self._config,
        ):
            literal = _literal_string(
                document,
                key_property,
            )

            if literal is None:
                continue

            key, key_span = literal
            entry = entries.get(key)

            if entry is None:
                diagnostics.append(
                    Diagnostic(
                        message=(
                            "Unknown translation key "
                            f"{key!r}."
                        ),
                        span=key_span,
                        severity=DiagnosticSeverity.ERROR,
                        code="i18n-unknown-key",
                    )
                )
                continue

            params = _property_named(body, "i18n_params")
            self._analyze_parameters(
                document,
                key_span,
                entry,
                params,
                diagnostics,
            )

    def _analyze_parameters(
        self,
        document: TextDocument,
        key_span: Span,
        entry: TranslationEntry,
        params: PropertyNode | None,
        diagnostics: list[Diagnostic],
    ) -> None:
        required = set(entry.placeholder_names)

        if params is None:
            if required:
                diagnostics.append(
                    Diagnostic(
                        message=_missing_parameters_message(required),
                        span=key_span,
                        severity=DiagnosticSeverity.ERROR,
                        code="i18n-missing-params",
                    )
                )

            return

        parsed = _literal_parameter_dict(
            document,
            params,
        )

        if parsed is None:
            if _is_definitely_non_dict(document, params):
                value = params.value

                if value is None:
                    return

                diagnostics.append(
                    Diagnostic(
                        message=(
                            "i18n_params must be a dictionary when "
                            "validated statically."
                        ),
                        span=value.span,
                        severity=DiagnosticSeverity.ERROR,
                        code="i18n-invalid-params",
                    )
                )

            return

        names = parsed.names

        for duplicate_span in parsed.duplicate_spans:
            diagnostics.append(
                Diagnostic(
                    message="Duplicate translation parameter.",
                    span=duplicate_span,
                    severity=DiagnosticSeverity.ERROR,
                    code="i18n-duplicate-param",
                )
            )

        for invalid_span in parsed.invalid_key_spans:
            diagnostics.append(
                Diagnostic(
                    message=(
                        "Translation parameter names must be "
                        "strings."
                    ),
                    span=invalid_span,
                    severity=DiagnosticSeverity.ERROR,
                    code="i18n-invalid-param-name",
                )
            )

        supplied = set(names)
        missing = required - supplied
        extras = supplied - required
        value = params.value

        if value is None:
            return

        if missing and not parsed.has_dynamic_keys:
            diagnostics.append(
                Diagnostic(
                    message=_missing_parameters_message(missing),
                    span=value.span,
                    severity=DiagnosticSeverity.ERROR,
                    code="i18n-missing-params",
                )
            )

        for extra in sorted(extras):
            diagnostics.append(
                Diagnostic(
                    message=(
                        "Unknown translation parameter "
                        f"{extra!r}."
                    ),
                    span=parsed.key_spans[extra],
                    severity=DiagnosticSeverity.ERROR,
                    code="i18n-unknown-param",
                )
            )


def translation_key_target_at(
    document: TextDocument,
    parse_result: ParseResult,
    offset: int,
    config: I18nConfig,
    index: TranslationIndex,
) -> TranslationKeyTarget | None:
    context = context_at(parse_result, offset)
    property_node = context.property_node

    if (
        property_node is None
        or property_node.name not in config.properties
    ):
        return None

    literal = _literal_string(document, property_node)

    if literal is None:
        return None

    key, span = literal

    if not span.contains_cursor(offset):
        return None

    return TranslationKeyTarget(
        key=key,
        span=span,
        entry=index.entry(key),
    )


def translation_parameter_target_at(
    document: TextDocument,
    parse_result: ParseResult,
    offset: int,
    config: I18nConfig,
    index: TranslationIndex,
) -> TranslationParameterTarget | None:
    property_node, body = _property_and_body_at(
        parse_result.document,
        offset,
    )

    if (
        property_node is None
        or property_node.name != "i18n_params"
        or body is None
    ):
        return None

    entry = _entry_for_body(
        document,
        body,
        config,
        index,
    )

    if entry is None or property_node.value is None:
        return None

    source = document.text[
        property_node.value.span.start:
        property_node.value.span.end
    ]

    for match in _PARAMETER_KEY_PATTERN.finditer(source):
        span = Span(
            start=(
                property_node.value.span.start
                + match.start("name")
            ),
            end=(
                property_node.value.span.start
                + match.end("name")
            ),
        )

        if not span.contains_cursor(offset):
            continue

        name = match.group("name")
        return TranslationParameterTarget(
            name=name,
            span=span,
            entry=entry,
            placeholder=entry.placeholder_named(name),
        )

    return None


def _completion_result(
    quoted: _QuotedContext,
    items: tuple[KvCompletionItem, ...],
) -> KvCompletionResult:
    target = KvCompletionTarget(
        kind=KvCompletionTargetKind.NAME,
        prefix=quoted.prefix,
        replacement_span=quoted.span,
        expression_span=quoted.span,
    )

    return KvCompletionResult(
        target=target,
        items=items,
    )


def _property_and_body_at(
    document: DocumentNode,
    offset: int,
) -> tuple[
    PropertyNode | None,
    tuple[BodyNode, ...] | None,
]:
    for body in _owner_bodies(document):
        for item in body:
            if not isinstance(item, PropertyNode):
                continue

            if item.span.contains_cursor(offset):
                return item, body

    return None, None


def _owner_bodies(
    document: DocumentNode,
) -> tuple[tuple[BodyNode, ...], ...]:
    bodies: list[tuple[BodyNode, ...]] = []

    def visit(body: tuple[BodyNode, ...]) -> None:
        bodies.append(body)

        for item in body:
            if isinstance(item, WidgetNode) or item.body:
                visit(item.body)

    for item in document.items:
        if isinstance(item, (RuleNode, WidgetNode)):
            visit(item.body)

    return tuple(bodies)


def _translation_properties(
    document: DocumentNode,
    config: I18nConfig,
) -> Iterator[PropertyNode]:
    for body in _owner_bodies(document):
        yield from _key_properties(body, config)


def _key_properties(
    body: tuple[BodyNode, ...],
    config: I18nConfig,
) -> tuple[PropertyNode, ...]:
    return tuple(
        item
        for item in body
        if (
            isinstance(item, PropertyNode)
            and item.name in config.properties
        )
    )


def _property_named(
    body: tuple[BodyNode, ...],
    name: str,
) -> PropertyNode | None:
    for item in body:
        if isinstance(item, PropertyNode) and item.name == name:
            return item

    return None


def _entry_for_body(
    document: TextDocument,
    body: tuple[BodyNode, ...],
    config: I18nConfig,
    index: TranslationIndex,
) -> TranslationEntry | None:
    key_property = next(
        iter(_key_properties(body, config)),
        None,
    )

    if key_property is None:
        return None

    literal = _literal_string(document, key_property)

    if literal is None:
        return None

    return index.entry(literal[0])


def _literal_string(
    document: TextDocument,
    property_node: PropertyNode,
) -> tuple[str, Span] | None:
    value = property_node.value

    if value is None:
        return None

    raw = document.text[value.span.start:value.span.end]
    stripped = raw.strip()

    try:
        parsed = ast.parse(stripped, mode="eval").body
    except SyntaxError:
        return None

    if (
        not isinstance(parsed, ast.Constant)
        or not isinstance(parsed.value, str)
    ):
        return None

    left_trim = len(raw) - len(raw.lstrip())
    opening = value.span.start + left_trim
    quote_length = 3 if stripped[:3] in {"'''", '\"\"\"'} else 1
    content_start = opening + quote_length
    content_end = value.span.end - (
        len(raw) - len(raw.rstrip())
    ) - quote_length

    if content_end < content_start:
        return None

    return (
        parsed.value,
        Span(
            start=content_start,
            end=content_end,
        ),
    )


def _quoted_context_at(
    source: str,
    search_start: int,
    search_end: int,
    offset: int,
) -> _QuotedContext | None:
    if offset < search_start or offset > len(source):
        return None

    limit = min(
        max(search_end, offset),
        len(source),
    )
    opening = search_start

    while opening < offset and source[opening].isspace():
        opening += 1

    if opening >= offset or source[opening] not in {"'", '"'}:
        return None

    quote = source[opening]

    if _unescaped_quote_before(source, opening + 1, offset, quote):
        return None

    closing = _closing_quote_after(
        source,
        offset,
        limit,
        quote,
    )
    end = closing + 1 if closing is not None else offset

    return _QuotedContext(
        prefix=source[opening + 1:offset],
        quote=quote,
        span=Span(
            start=opening,
            end=end,
        ),
    )


def _parameter_context_at(
    source: str,
    property_node: PropertyNode,
    offset: int,
) -> _QuotedContext | None:
    value = property_node.value

    if value is None or offset < value.span.start:
        return None

    prefix = source[value.span.start:offset]
    matches = tuple(
        re.finditer(
            r"(?:^|[\{,])\s*(?P<quote>['\"])"
            r"(?P<prefix>[A-Za-z_][A-Za-z0-9_]*)?$",
            prefix,
            re.DOTALL,
        )
    )

    if not matches:
        return None

    match = matches[-1]
    quote = match.group("quote")
    opening = value.span.start + match.start("quote")
    closing = _closing_quote_after(
        source,
        offset,
        value.span.end,
        quote,
    )
    end = closing + 1 if closing is not None else offset

    return _QuotedContext(
        prefix=match.group("prefix") or "",
        quote=quote,
        span=Span(start=opening, end=end),
    )


def _unescaped_quote_before(
    source: str,
    start: int,
    end: int,
    quote: str,
) -> bool:
    return _closing_quote_after(
        source,
        start,
        end,
        quote,
    ) is not None


def _closing_quote_after(
    source: str,
    start: int,
    end: int,
    quote: str,
) -> int | None:
    escaped = False

    for offset in range(start, min(end, len(source))):
        character = source[offset]

        if escaped:
            escaped = False
            continue

        if character == "\\":
            escaped = True
            continue

        if character == quote:
            return offset

    return None


def _quoted_text(value: str, quote: str) -> str:
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace(quote, f"\\{quote}")
    return f"{quote}{escaped}{quote}"


def _parameter_names(
    document: TextDocument,
    property_node: PropertyNode,
) -> set[str]:
    if property_node.value is None:
        return set()

    source = document.text[
        property_node.value.span.start:
        property_node.value.span.end
    ]
    return {
        match.group("name")
        for match in _PARAMETER_KEY_PATTERN.finditer(source)
    }


def _literal_parameter_dict(
    document: TextDocument,
    property_node: PropertyNode,
) -> _ParameterDictionary | None:
    value = property_node.value

    if value is None:
        return None

    source = document.text[value.span.start:value.span.end]

    try:
        parsed = ast.parse(source, mode="eval").body
    except SyntaxError:
        return None

    if not isinstance(parsed, ast.Dict):
        return None

    names: list[str] = []
    duplicates: list[Span] = []
    spans: dict[str, Span] = {}
    invalid_spans: list[Span] = []
    has_dynamic_keys = False

    for key_node in parsed.keys:
        if key_node is None:
            has_dynamic_keys = True
            continue

        if (
            not isinstance(key_node, ast.Constant)
            or not isinstance(key_node.value, str)
        ):
            invalid_spans.append(
                _ast_node_span(
                    source,
                    key_node,
                    value.span.start,
                )
            )
            continue

        name = key_node.value
        span = _ast_node_span(
            source,
            key_node,
            value.span.start,
            content_only=True,
        )

        if name in spans:
            duplicates.append(span)
        else:
            spans[name] = span

        names.append(name)

    return _ParameterDictionary(
        names=tuple(names),
        duplicate_spans=tuple(duplicates),
        key_spans=spans,
        invalid_key_spans=tuple(invalid_spans),
        has_dynamic_keys=has_dynamic_keys,
    )


def _is_definitely_non_dict(
    document: TextDocument,
    property_node: PropertyNode,
) -> bool:
    value = property_node.value

    if value is None:
        return False

    source = document.text[value.span.start:value.span.end]

    try:
        parsed = ast.parse(source, mode="eval").body
    except SyntaxError:
        return False

    return isinstance(
        parsed,
        (
            ast.Constant,
            ast.List,
            ast.Set,
            ast.Tuple,
        ),
    )


def _ast_node_span(
    source: str,
    node: ast.expr,
    base_offset: int,
    *,
    content_only: bool = False,
) -> Span:
    lines = source.splitlines(keepends=True)
    line_starts = [0]

    for line in lines[:-1]:
        line_starts.append(line_starts[-1] + len(line))

    start = line_starts[node.lineno - 1] + _byte_column_offset(
        lines[node.lineno - 1],
        node.col_offset,
    )
    end_line = node.end_lineno or node.lineno
    end_column = node.end_col_offset or node.col_offset
    end = line_starts[end_line - 1] + _byte_column_offset(
        lines[end_line - 1],
        end_column,
    )

    if content_only and end - start >= 2:
        start += 1
        end -= 1

    return Span(
        start=base_offset + start,
        end=base_offset + end,
    )


def _byte_column_offset(line: str, byte_column: int) -> int:
    encoded = line.encode("utf-8")[:byte_column]
    return len(encoded.decode("utf-8"))


def _property_selection_span(node: PropertyNode) -> Span:
    return Span(
        start=node.name_tokens[0].span.start,
        end=node.name_tokens[-1].span.end,
    )


def _missing_parameters_message(names: set[str]) -> str:
    formatted = ", ".join(
        repr(name)
        for name in sorted(names)
    )
    return f"Missing translation parameters: {formatted}."


def _deduplicate_diagnostics(
    diagnostics: list[Diagnostic],
) -> tuple[Diagnostic, ...]:
    unique: dict[tuple[str, int, int], Diagnostic] = {}

    for diagnostic in diagnostics:
        key = (
            diagnostic.code,
            diagnostic.span.start,
            diagnostic.span.end,
        )
        unique.setdefault(key, diagnostic)

    return tuple(
        sorted(
            unique.values(),
            key=lambda diagnostic: (
                diagnostic.span.start,
                diagnostic.span.end,
                diagnostic.code,
            ),
        )
    )

