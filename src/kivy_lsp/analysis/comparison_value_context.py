"""Cursor contexts for finite values on either side of a comparison."""

from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass

from kivy_lsp.kv.expression_source import EmbeddedPythonSource
from kivy_lsp.model.span import Span
from kivy_lsp.workspace.document import TextDocument


@dataclass(frozen=True, slots=True)
class KvValueToken:
    """A whole value token, including text after the cursor."""

    prefix: str
    replacement_span: Span
    quote: str | None = None


@dataclass(frozen=True, slots=True)
class KvComparisonValueContext:
    """A value being compared with one or more neighboring operands."""

    token: KvValueToken
    operands: tuple[str, ...]


def comparison_value_context_at(
    document: TextDocument,
    expression_span: Span,
    offset: int,
) -> KvComparisonValueContext | None:
    """Recognize direct ==/!= operands, including incomplete expressions."""
    original = document.text[expression_span.start:expression_span.end]
    if "==" not in original and "!=" not in original:
        return None
    token = value_token_at(document.text, expression_span, offset)
    if token is None:
        return None

    start = token.replacement_span.start
    end = token.replacement_span.end
    marker = "_kivy_lsp_completion_value"
    while marker in original:
        marker += "_"
    replacement = (
        document.text[expression_span.start:start]
        + marker
        + document.text[end:expression_span.end]
    )
    source = EmbeddedPythonSource.from_source(
        replacement, Span(0, len(replacement)),
    ).text
    try:
        tree = ast.parse(_close_delimiters(source), mode="exec")
    except (SyntaxError, ValueError):
        return None

    operands: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        values = (node.left, *node.comparators)
        for left, operator, right in zip(values, node.ops, values[1:]):
            if not isinstance(operator, (ast.Eq, ast.NotEq)):
                continue
            for active, other in ((left, right), (right, left)):
                if isinstance(active, ast.Name) and active.id == marker:
                    operands.append(ast.unparse(other))

    if not operands:
        return None
    return KvComparisonValueContext(token, tuple(operands))


def value_token_at(
    source: str,
    expression_span: Span,
    offset: int,
) -> KvValueToken | None:
    """Locate a plain string/name token without interpreting its value."""
    if not expression_span.start <= offset <= expression_span.end:
        return None

    current = expression_span.start
    while current < expression_span.end:
        character = source[current]
        if character == "#":
            end = source.find("\n", current, expression_span.end)
            end = expression_span.end if end < 0 else end
            if current <= offset <= end:
                return None
            current = end + 1
            continue
        if character not in {"'", '"'}:
            current += 1
            continue

        triple = source.startswith(character * 3, current)
        quote = character * (3 if triple else 1)
        content_start = current + len(quote)
        content_end, end = _string_end(
            source, content_start, expression_span.end, quote,
        )
        if current <= offset <= content_end:
            prefix_start = current
            while (
                prefix_start > expression_span.start
                and source[prefix_start - 1].isalpha()
            ):
                prefix_start -= 1
            literal_prefix = source[prefix_start:current].lower()
            if triple or literal_prefix not in {"", "u", "r"}:
                return None
            if offset < content_start:
                return None
            raw_prefix = source[content_start:offset]
            try:
                prefix = ast.literal_eval(
                    literal_prefix + character + raw_prefix + character
                )
            except (SyntaxError, ValueError):
                return None
            if not isinstance(prefix, str):
                return None
            return KvValueToken(
                prefix, Span(prefix_start, end), character,
            )
        current = end

    start = offset
    while start > expression_span.start and _name_character(source[start - 1]):
        start -= 1
    end = offset
    while end < expression_span.end and _name_character(source[end]):
        end += 1
    if start > expression_span.start and source[start - 1] == ".":
        return None
    prefix = source[start:offset]
    if prefix and not prefix.isidentifier() and not prefix.isdecimal():
        return None
    return KvValueToken(prefix, Span(start, end))


def _string_end(
    source: str,
    start: int,
    limit: int,
    quote: str,
) -> tuple[int, int]:
    current = start
    while current < limit:
        if source[current] == "\\":
            current += 2
            continue
        if source.startswith(quote, current):
            return current, current + len(quote)
        if len(quote) == 1 and source[current] in "\r\n":
            break
        current += 1
    return min(current, limit), min(current, limit)


def _name_character(character: str) -> bool:
    return character == "_" or character.isalnum()


def _close_delimiters(source: str) -> str:
    """Close pending containers/calls while preserving Python structure."""
    closers = {"(": ")", "[": "]", "{": "}"}
    pending: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.OP:
                continue
            if token.string in closers:
                pending.append(closers[token.string])
            elif token.string in closers.values():
                if not pending or pending.pop() != token.string:
                    return source
    except (tokenize.TokenError, IndentationError):
        pass
    return source + "".join(reversed(pending))
