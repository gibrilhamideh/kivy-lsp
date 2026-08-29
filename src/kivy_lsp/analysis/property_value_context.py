# src/kivy_lsp/analysis/property_value_context.py

from __future__ import annotations

import re
from dataclasses import dataclass

from kivy_lsp.analysis.completion_context import (
    KvCompletionTarget,
    KvCompletionTargetKind,
)
from kivy_lsp.analysis.property_resolution import (
    KivyPropertyResolver,
)
from kivy_lsp.analysis.scope import KvValue
from kivy_lsp.model.property import KivyPropertyInfo
from kivy_lsp.model.span import Span
from kivy_lsp.workspace.document import TextDocument

_PROPERTY_HEADER_PATTERN = re.compile(
    r"^\s*([a-z_][A-Za-z0-9_]*)\s*:\s*$"
)

_INLINE_PROPERTY_PATTERN = re.compile(
    r"^\s*([a-z_][A-Za-z0-9_]*)\s*:"
)


@dataclass(frozen=True, slots=True)
class KvPropertyValueContext:
    """The property-value completion context at the cursor."""

    property_name: str
    property_info: KivyPropertyInfo
    prefix: str
    replacement_span: Span
    quote: str | None = None

    @property
    def is_quoted(self) -> bool:
        return self.quote is not None


def property_value_context_at(
    document: TextDocument,
    target: KvCompletionTarget,
    expression_start: int,
    widget_value: KvValue,
    property_resolver: KivyPropertyResolver,
) -> KvPropertyValueContext | None:
    """Resolve the property whose value contains the cursor."""
    if target.kind is not KvCompletionTargetKind.NAME:
        return None

    property_name = _property_name_at_target(
        document.text,
        target.replacement_span.start,
    )

    if property_name is None:
        property_name = _property_name_before_expression(
            document.text,
            expression_start,
        )

    if property_name is None:
        return None

    resolved = property_resolver.resolve(
        widget_value,
        property_name,
    )

    if resolved is None or resolved.info is None:
        return None

    quote = _quote_before_target(
        document.text,
        target.replacement_span,
    )
    replacement_span = _replacement_span(
        document.text,
        target.replacement_span,
        quote,
    )

    return KvPropertyValueContext(
        property_name=property_name,
        property_info=resolved.info,
        prefix=target.prefix,
        replacement_span=replacement_span,
        quote=quote,
    )


def _property_name_at_target(
    source: str,
    target_start: int,
) -> str | None:
    if target_start < 0 or target_start > len(source):
        return None

    line_start = source.rfind(
        "\n",
        0,
        target_start,
    ) + 1
    line_prefix = source[
        line_start:target_start
    ]
    match = _INLINE_PROPERTY_PATTERN.match(line_prefix)

    if match is None:
        return None

    return match.group(1)


def _property_name_before_expression(
    source: str,
    expression_start: int,
) -> str | None:
    if expression_start < 0 or expression_start > len(source):
        return None

    line_start = source.rfind(
        "\n",
        0,
        expression_start,
    ) + 1
    same_line_prefix = source[
        line_start:expression_start
    ]
    same_line_match = _PROPERTY_HEADER_PATTERN.fullmatch(
        same_line_prefix,
    )

    if same_line_match is not None:
        return same_line_match.group(1)

    expression_line_end = source.find(
        "\n",
        expression_start,
    )

    if expression_line_end == -1:
        expression_line_end = len(source)

    expression_line = source[
        line_start:expression_line_end
    ]
    expression_indent = _indent_width(expression_line)
    search_end = max(
        0,
        line_start - 1,
    )

    while search_end > 0:
        previous_start = source.rfind(
            "\n",
            0,
            search_end,
        ) + 1
        previous_line = source[
            previous_start:search_end
        ]
        stripped = previous_line.strip()

        if not stripped or stripped.startswith("#"):
            search_end = max(
                0,
                previous_start - 1,
            )
            continue

        previous_indent = _indent_width(previous_line)

        if previous_indent >= expression_indent:
            return None

        match = _PROPERTY_HEADER_PATTERN.fullmatch(
            previous_line,
        )

        if match is None:
            return None

        return match.group(1)

    return None


def _quote_before_target(
    source: str,
    span: Span,
) -> str | None:
    if span.start <= 0:
        return None

    character = source[span.start - 1]

    if character not in {"'", '"'}:
        return None

    return character


def _replacement_span(
    source: str,
    span: Span,
    quote: str | None,
) -> Span:
    if quote is None:
        return span

    start = span.start - 1
    end = span.end

    if end < len(source) and source[end] == quote:
        end += 1

    return Span(
        start=start,
        end=end,
    )


def _indent_width(line: str) -> int:
    width = 0

    for character in line:
        if character == " ":
            width += 1
            continue

        if character == "\t":
            width += 4
            continue

        break

    return width
