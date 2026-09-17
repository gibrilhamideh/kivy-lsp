"""Conservative, token-preserving layout of embedded Python expressions."""

from __future__ import annotations

import ast
import io
import keyword
import tokenize
from dataclasses import dataclass


class UnsupportedExpression(ValueError):
    """An expression cannot safely be rewritten by this formatter."""


@dataclass(frozen=True, slots=True)
class ExpressionLayout:
    compact: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Token:
    text: str
    spaced: bool = False


@dataclass(frozen=True, slots=True)
class _Group:
    opening: str
    children: tuple[_Element, ...]
    closing: str
    spaced: bool = False


type _Element = _Token | _Group

_IGNORED = {
    tokenize.ENCODING,
    tokenize.ENDMARKER,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.NEWLINE,
    tokenize.NL,
}
_BINARY = {
    "+", "-", "*", "/", "//", "%", "**", "@", "&", "|", "^",
    "<<", ">>", "==", "!=", "<", ">", "<=", ">=", "and", "or",
}
_UNSUPPORTED = (
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Yield,
    ast.YieldFrom,
)


def layout_expression(source: str, width: int) -> ExpressionLayout:
    """Lay out one complete expression without executing or unparsing it."""

    return _layout_python(source, width, "eval")


def layout_statement(source: str, width: int) -> ExpressionLayout:
    """Lay out one simple statement, retaining statement semantics."""

    return _layout_python(source, width, "exec")


def check_source_tokens(source: str) -> None:
    """Reject comments/multiline literals before splitting statement suites."""

    _elements(source)


def _layout_python(
    source: str, width: int, mode: str,
) -> ExpressionLayout:
    source = source.strip()
    try:
        tree = ast.parse(source, mode=mode)
    except (SyntaxError, ValueError) as error:
        raise UnsupportedExpression("incomplete Python expression") from error

    elements = _elements(source)
    compact = _compact(elements)
    if any(isinstance(node, _UNSUPPORTED) for node in ast.walk(tree)):
        if len(compact) > width:
            raise UnsupportedExpression(
                "wrapping lambdas, comprehensions, and yields is unsupported"
            )
        lines = [compact]
    else:
        if mode == "eval":
            elements = _remove_outer_group(elements, tree)
        compact = _compact(elements)
        lines = _layout(elements, width)

    rendered = " \\\n".join(lines)
    try:
        formatted_tree = ast.parse(rendered, mode=mode)
    except SyntaxError as error:
        raise UnsupportedExpression("no safe expression layout") from error
    if _identity(tree) != _identity(formatted_tree):
        raise UnsupportedExpression("layout would change expression meaning")
    return ExpressionLayout(compact, tuple(lines))


def _identity(tree: ast.AST) -> str:
    return ast.dump(tree, include_attributes=False)


def _elements(source: str) -> tuple[_Element, ...]:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError) as error:
        raise UnsupportedExpression("incomplete Python tokens") from error

    starts = [0]
    for line in source.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))

    def offset(position: tuple[int, int]) -> int:
        line, column = position
        return starts[min(line - 1, len(starts) - 1)] + column

    flat: list[_Token] = []
    previous_end = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token.type in _IGNORED:
            continue
        if token.type == tokenize.COMMENT:
            raise UnsupportedExpression(
                "comments retain their original layout"
            )
        start, end = offset(token.start), offset(token.end)
        if token.type == tokenize.FSTRING_START:
            depth = 1
            while index < len(tokens) and depth:
                item = tokens[index]
                index += 1
                if item.type == tokenize.FSTRING_START:
                    depth += 1
                elif item.type == tokenize.FSTRING_END:
                    depth -= 1
                end = offset(item.end)
        text = source[start:end]
        if "\n" in text or "\r" in text:
            raise UnsupportedExpression(
                "multiline literals retain their original layout"
            )
        flat.append(_Token(text, bool(source[previous_end:start])))
        previous_end = end

    stack: list[tuple[_Token, list[_Element]]] = []
    current: list[_Element] = []
    matching = {"(": ")", "[": "]", "{": "}"}
    for token in flat:
        if token.text in matching:
            stack.append((token, current))
            current = []
        elif token.text in matching.values():
            if not stack:
                raise UnsupportedExpression("unmatched expression delimiter")
            opener, parent = stack.pop()
            if matching[opener.text] != token.text:
                raise UnsupportedExpression("mismatched expression delimiter")
            parent.append(_Group(
                opener.text, tuple(current), token.text, opener.spaced,
            ))
            current = parent
        else:
            current.append(token)
    if stack:
        raise UnsupportedExpression("unclosed expression delimiter")
    return tuple(current)


def _compact(elements: tuple[_Element, ...]) -> str:
    result = ""
    for element in elements:
        if isinstance(element, _Token):
            value = element.text
        else:
            value = (
                element.opening + _compact(element.children) + element.closing
            )
        if result and element.spaced and value not in {",", ":", ";"}:
            result += " "
        result += value
    return result


def _remove_outer_group(
    elements: tuple[_Element, ...], tree: ast.AST,
) -> tuple[_Element, ...]:
    while len(elements) == 1 and isinstance(elements[0], _Group):
        group = elements[0]
        if group.opening != "(" or not group.children:
            break
        candidate = _compact(group.children)
        try:
            inner = ast.parse(candidate, mode="eval")
        except SyntaxError:
            break
        if _identity(inner) != _identity(tree):
            break
        # A tuple's parentheses express its container and remain explicit.
        if isinstance(inner.body, ast.Tuple):
            break
        elements = group.children
    return elements


def _layout(
    elements: tuple[_Element, ...],
    width: int,
    prefix: str = "",
    suffix: str = "",
    final: bool = True,
) -> list[str]:
    compact = prefix + _compact(elements) + suffix
    if len(compact) + (0 if final else 2) <= width:
        return [compact]

    # Conditional alternatives are the highest-priority line boundaries.
    for index, element in enumerate(elements[:-1]):
        if isinstance(element, _Token) and element.text == "else":
            left = _layout(
                elements[:index], width, prefix, " else", False,
            )
            right = _layout(elements[index + 1:], width, "", suffix, final)
            return left + right

    breaks = _operator_breaks(elements)
    if breaks:
        chunks: list[tuple[_Element, ...]] = []
        start = 0
        for index in breaks:
            chunks.append(elements[start:index])
            start = index
        chunks.append(elements[start:])
        lines: list[str] = []
        for index, chunk in enumerate(chunks):
            first = index == 0
            last = index == len(chunks) - 1
            lines.extend(_layout(
                chunk, width, prefix if first else "",
                suffix if last else "", final and last,
            ))
        return lines

    # Expand an enclosing call/container before splitting its interior.
    groups = [
        (index, element) for index, element in enumerate(elements)
        if isinstance(element, _Group) and element.children
    ]
    separator = next((
        index for index, element in enumerate(elements[:-1])
        if isinstance(element, _Token) and element.text in {":", "="}
    ), None)
    if separator is not None:
        first_group = groups[0][0] if groups else len(elements)
        before_group = prefix + _compact(elements[:first_group])
        if not groups or len(before_group) + 3 > width:
            return (
                _layout(elements[:separator + 1], width, prefix, "", False)
                + _layout(elements[separator + 1:], width, "", suffix, final)
            )
    if groups:
        index, element = max(
            groups, key=lambda item: len(_compact(item[1].children)),
        )
        before = _compact(elements[:index])
        after = _compact(elements[index + 1:])
        space_before = " " if before and element.spaced else ""
        space_after = ""
        if after and elements[index + 1].spaced:
            space_after = " "
        opening = prefix + before + space_before + element.opening
        chunks, had_comma, trailing = _comma_chunks(element.children)
        add_comma = (
            element.opening == "{"
            or element.opening == "[" and index == 0
            or element.opening == "(" and _is_call(elements, index)
            or had_comma
        )
        if before and len(opening) + 2 > width:
            lines = _layout(elements[:index], width, prefix, "", False)
            lines.append(element.opening)
        else:
            lines = [opening]
        for part, chunk in enumerate(chunks):
            comma = part < len(chunks) - 1 or trailing or add_comma
            lines.extend(_layout(
                chunk, width, "", "," if comma else "", False,
            ))
        if after:
            lines.extend(_layout(
                elements[index + 1:], width,
                element.closing + space_after, suffix, final,
            ))
        else:
            lines.append(element.closing + suffix)
        return lines

    # An oversized conditional value may force `if` onto a later line.
    for index, element in enumerate(elements):
        if index and isinstance(element, _Token) and element.text == "if":
            return (
                _layout(elements[:index], width, prefix, "", False)
                + _layout(elements[index:], width, "", suffix, final)
            )

    # An indivisible token or dotted chain is deliberately left intact.
    return [compact]


def _operator_breaks(elements: tuple[_Element, ...]) -> list[int]:
    indexes: list[int] = []
    for index, element in enumerate(elements):
        if not index or not isinstance(element, _Token):
            continue
        if element.text not in _BINARY:
            continue
        previous = elements[index - 1]
        if isinstance(previous, _Token) and (
            previous.text in _BINARY or previous.text in {
                "if", "else", "=", ":", ",", "not", "in", "is",
            }
        ):
            continue
        indexes.append(index)
    return indexes


def _comma_chunks(
    elements: tuple[_Element, ...],
) -> tuple[list[tuple[_Element, ...]], bool, bool]:
    chunks: list[tuple[_Element, ...]] = []
    start = 0
    for index, element in enumerate(elements):
        if isinstance(element, _Token) and element.text == ",":
            chunks.append(elements[start:index])
            start = index + 1
    had_comma = bool(chunks)
    trailing = start == len(elements)
    if not trailing:
        chunks.append(elements[start:])
    return chunks, had_comma, trailing


def _is_call(elements: tuple[_Element, ...], index: int) -> bool:
    if not index:
        return False
    previous = elements[index - 1]
    return isinstance(previous, _Group) or (
        previous.text.isidentifier() and not keyword.iskeyword(previous.text)
    )
