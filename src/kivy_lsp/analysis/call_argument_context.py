# src/kivy_lsp/analysis/call_argument_context.py

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from kivy_lsp.analysis.completion_context import (
    KvCompletionTarget,
    KvCompletionTargetKind,
)
from kivy_lsp.model.span import Span
from kivy_lsp.workspace.document import TextDocument

_KEYWORD_ARGUMENT_PATTERN = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*['\"]?$"
)

_MATCHING_DELIMITERS = {
    ")": "(",
    "]": "[",
    "}": "{",
}


@dataclass(frozen=True, slots=True)
class KvCallArgumentContext:
    """The active function-call argument at the cursor."""

    callee: str
    argument_index: int
    keyword_name: str | None
    prefix: str
    replacement_span: Span
    quote: str | None = None

    @property
    def is_quoted(self) -> bool:
        return self.quote is not None


@dataclass(slots=True)
class _CallFrame:
    callee: str
    argument_index: int
    argument_start: int


@dataclass(slots=True)
class _DelimiterFrame:
    opener: str
    call: _CallFrame | None = None


def call_argument_context_at(
    document: TextDocument,
    target: KvCompletionTarget,
    expression_start: int,
) -> KvCallArgumentContext | None:
    """Find the call argument containing a completion target."""
    if target.kind is not KvCompletionTargetKind.NAME:
        return None

    cursor = target.replacement_span.end

    if expression_start < 0 or expression_start > cursor:
        return None

    if cursor > len(document.text):
        return None

    expression_prefix = document.text[
        expression_start:cursor
    ]
    frames = _open_delimiters(expression_prefix)
    active_call = next(
        (
            frame.call
            for frame in reversed(frames)
            if frame.call is not None
        ),
        None,
    )

    if active_call is None:
        return None

    relative_target_start = (
        target.replacement_span.start
        - expression_start
    )

    if relative_target_start < active_call.argument_start:
        return None

    argument_prefix = expression_prefix[
        active_call.argument_start:relative_target_start
    ]
    keyword_name = _keyword_name(argument_prefix)
    quote = _quote_before_target(
        document.text,
        target.replacement_span,
    )
    replacement_span = _replacement_span(
        document.text,
        target.replacement_span,
        quote,
    )

    return KvCallArgumentContext(
        callee=active_call.callee,
        argument_index=active_call.argument_index,
        keyword_name=keyword_name,
        prefix=target.prefix,
        replacement_span=replacement_span,
        quote=quote,
    )


def _open_delimiters(
    source: str,
) -> list[_DelimiterFrame]:
    frames: list[_DelimiterFrame] = []
    quote: str | None = None
    triple = False
    escaped = False
    offset = 0

    while offset < len(source):
        character = source[offset]

        if quote is not None:
            if escaped:
                escaped = False
                offset += 1
                continue

            if character == "\\":
                escaped = True
                offset += 1
                continue

            if triple:
                if source.startswith(
                    quote * 3,
                    offset,
                ):
                    quote = None
                    triple = False
                    offset += 3
                    continue
            elif character == quote:
                quote = None
                offset += 1
                continue

            offset += 1
            continue

        if character in {"'", '"'}:
            quote = character
            triple = source.startswith(
                character * 3,
                offset,
            )
            offset += 3 if triple else 1
            continue

        if character == "#":
            newline = source.find(
                "\n",
                offset,
            )
            offset = (
                len(source)
                if newline < 0
                else newline + 1
            )
            continue

        if character in {"(", "[", "{"}:
            call = None

            if character == "(":
                callee = _callee_before(
                    source,
                    offset,
                )

                if callee is not None:
                    call = _CallFrame(
                        callee=callee,
                        argument_index=0,
                        argument_start=offset + 1,
                    )

            frames.append(
                _DelimiterFrame(
                    opener=character,
                    call=call,
                )
            )
            offset += 1
            continue

        if character == ",":
            if frames:
                frame = frames[-1]

                if frame.call is not None:
                    frame.call.argument_index += 1
                    frame.call.argument_start = offset + 1

            offset += 1
            continue

        if character in _MATCHING_DELIMITERS:
            expected = _MATCHING_DELIMITERS[character]

            if frames and frames[-1].opener == expected:
                frames.pop()

            offset += 1
            continue

        offset += 1

    return frames


def _callee_before(
    source: str,
    opening_offset: int,
) -> str | None:
    segment = source[:opening_offset]

    for start in range(len(segment)):
        if not _can_start_expression(
            segment,
            start,
        ):
            continue

        candidate = segment[start:].strip()

        if not candidate:
            continue

        try:
            parsed = ast.parse(
                candidate,
                mode="eval",
            )
        except SyntaxError:
            continue

        if _is_callable_expression(parsed.body):
            return candidate

    return None


def _can_start_expression(
    source: str,
    offset: int,
) -> bool:
    character = source[offset]

    if not (
        character == "_"
        or character.isalpha()
        or character == "("
    ):
        return False

    if offset == 0:
        return True

    previous = source[offset - 1]

    if character == "_" or character.isalnum():
        return not (
            previous == "_"
            or previous.isalnum()
        )

    return True


def _is_callable_expression(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return True

    if isinstance(node, ast.Attribute):
        return True

    if isinstance(node, ast.Subscript):
        return True

    return bool(isinstance(node, ast.Call))


def _keyword_name(
    argument_prefix: str,
) -> str | None:
    match = _KEYWORD_ARGUMENT_PATTERN.fullmatch(
        argument_prefix,
    )

    if match is None:
        return None

    return match.group(1)


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
