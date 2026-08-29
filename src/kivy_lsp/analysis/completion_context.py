# src/kivy_lsp/analysis/completion_context.py

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import StrEnum

from kivy_lsp.kv.context import context_at
from kivy_lsp.kv.parser import ParseResult
from kivy_lsp.model.span import Span
from kivy_lsp.workspace.document import TextDocument


class KvCompletionTargetKind(StrEnum):
    """The kind of completion requested at the cursor."""

    NAME = "name"
    MEMBER = "member"
    RULE = "rule"
    WIDGET = "widget"
    PROPERTY = "property"
    STRUCTURE = "structure"


@dataclass(frozen=True, slots=True)
class KvCompletionTarget:
    """The source portion that should receive completions."""

    kind: KvCompletionTargetKind
    prefix: str
    replacement_span: Span
    expression_span: Span
    receiver: str | None = None
    receiver_span: Span | None = None

    @property
    def is_member(self) -> bool:
        return self.kind is KvCompletionTargetKind.MEMBER

    @property
    def is_expression(self) -> bool:
        return self.kind in {
            KvCompletionTargetKind.NAME,
            KvCompletionTargetKind.MEMBER,
        }

    @property
    def is_widget(self) -> bool:
        return self.kind in {
            KvCompletionTargetKind.RULE,
            KvCompletionTargetKind.WIDGET,
        }

    @property
    def is_property(self) -> bool:
        return self.kind is KvCompletionTargetKind.PROPERTY

    @property
    def is_structure(self) -> bool:
        return self.kind is KvCompletionTargetKind.STRUCTURE


def completion_target_at(
    document: TextDocument,
    parse_result: ParseResult,
    offset: int,
) -> KvCompletionTarget | None:
    """Extract a completion target at a document offset."""

    if offset < 0 or offset > len(document.text):
        raise ValueError(
            "Completion offset is outside the document."
        )

    line_start = _line_start(
        document.text,
        offset,
    )
    line_prefix = document.text[line_start:offset]

    rule_target = _rule_target(
        line_prefix,
        line_start,
    )

    if rule_target is not None:
        return rule_target

    context = context_at(
        parse_result,
        offset,
    )
    expression = context.expression

    if (
        expression is not None
        and expression.span.start <= offset
        and offset <= expression.span.end
    ):
        text = document.text[
            expression.span.start:offset
        ]

        return extract_completion_target(
            text,
            start_offset=expression.span.start,
        )

    value_target = _inline_value_target(
        line_prefix,
        line_start,
    )

    if value_target is not None:
        return value_target

    return _structure_target(
        line_prefix,
        line_start,
    )


def extract_completion_target(
    text: str,
    *,
    start_offset: int = 0,
) -> KvCompletionTarget | None:
    """Extract a completion target from an expression prefix."""

    prefix_start = _identifier_start(text)
    prefix = text[prefix_start:]
    replacement_span = Span(
        start=start_offset + prefix_start,
        end=start_offset + len(text),
    )
    expression_span = Span(
        start=start_offset,
        end=start_offset + len(text),
    )

    dot_offset = prefix_start - 1

    if dot_offset < 0 or text[dot_offset] != ".":
        return KvCompletionTarget(
            kind=KvCompletionTargetKind.NAME,
            prefix=prefix,
            replacement_span=replacement_span,
            expression_span=expression_span,
        )

    receiver = _extract_receiver(
        text,
        dot_offset,
    )

    if receiver is None:
        return None

    receiver_text, receiver_start, receiver_end = receiver

    return KvCompletionTarget(
        kind=KvCompletionTargetKind.MEMBER,
        prefix=prefix,
        replacement_span=replacement_span,
        expression_span=expression_span,
        receiver=receiver_text,
        receiver_span=Span(
            start=start_offset + receiver_start,
            end=start_offset + receiver_end,
        ),
    )


def _rule_target(
    line_prefix: str,
    line_start: int,
) -> KvCompletionTarget | None:
    open_offset = line_prefix.rfind("<")

    if open_offset < 0:
        return None

    if line_prefix[:open_offset].strip():
        return None

    selector_text = line_prefix[open_offset + 1:]

    if ">" in selector_text:
        return None

    prefix_start = _identifier_start(selector_text)
    prefix = selector_text[prefix_start:]
    absolute_start = (
        line_start
        + open_offset
        + 1
        + prefix_start
    )

    return KvCompletionTarget(
        kind=KvCompletionTargetKind.RULE,
        prefix=prefix,
        replacement_span=Span(
            start=absolute_start,
            end=line_start + len(line_prefix),
        ),
        expression_span=Span(
            start=line_start + open_offset + 1,
            end=line_start + len(line_prefix),
        ),
    )


def _inline_value_target(
    line_prefix: str,
    line_start: int,
) -> KvCompletionTarget | None:
    colon_offset = line_prefix.find(":")

    if colon_offset < 0:
        return None

    property_name = line_prefix[:colon_offset].strip()

    if not property_name.isidentifier():
        return None

    if property_name[0].isupper():
        return None

    value_start = colon_offset + 1

    while (
        value_start < len(line_prefix)
        and line_prefix[value_start].isspace()
    ):
        value_start += 1

    value_text = line_prefix[value_start:]

    return extract_completion_target(
        value_text,
        start_offset=line_start + value_start,
    )


def _structure_target(
    line_prefix: str,
    line_start: int,
) -> KvCompletionTarget | None:
    content = line_prefix.lstrip()
    indentation = len(line_prefix) - len(content)

    if content.startswith("#"):
        return None

    if content and not content.isidentifier():
        return None

    if not content:
        kind = KvCompletionTargetKind.STRUCTURE
    elif content[0].isupper():
        kind = KvCompletionTargetKind.WIDGET
    else:
        kind = KvCompletionTargetKind.PROPERTY

    start = line_start + indentation

    return KvCompletionTarget(
        kind=kind,
        prefix=content,
        replacement_span=Span(
            start=start,
            end=line_start + len(line_prefix),
        ),
        expression_span=Span(
            start=start,
            end=line_start + len(line_prefix),
        ),
    )


def _line_start(
    text: str,
    offset: int,
) -> int:
    newline = text.rfind(
        "\n",
        0,
        offset,
    )

    if newline < 0:
        return 0

    return newline + 1


def _identifier_start(text: str) -> int:
    offset = len(text)

    while offset > 0:
        character = text[offset - 1]

        if not _is_identifier_character(character):
            break

        offset -= 1

    return offset


def _extract_receiver(
    text: str,
    receiver_end: int,
) -> tuple[str, int, int] | None:
    segment = text[:receiver_end]

    for start in range(len(segment)):
        if not _can_start_expression(
            segment,
            start,
        ):
            continue

        raw_candidate = segment[start:]
        left_trimmed = raw_candidate.lstrip()
        left_trim = len(raw_candidate) - len(left_trimmed)
        candidate = left_trimmed.rstrip()

        if not candidate:
            continue

        try:
            parsed = ast.parse(
                candidate,
                mode="eval",
            )
        except SyntaxError:
            continue

        if not _is_supported_receiver(
            parsed.body,
            candidate,
        ):
            continue

        candidate_start = start + left_trim
        candidate_end = candidate_start + len(candidate)

        return (
            candidate,
            candidate_start,
            candidate_end,
        )

    return None


def _can_start_expression(
    text: str,
    offset: int,
) -> bool:
    character = text[offset]

    if not (
        character == "_"
        or character.isalpha()
        or character == "("
    ):
        return False

    if offset == 0:
        return True

    previous = text[offset - 1]

    if _is_identifier_character(character):
        return not _is_identifier_character(previous)

    return True


def _is_identifier_character(character: str) -> bool:
    return character == "_" or character.isalnum()


def _is_supported_receiver(
    node: ast.expr,
    source: str,
) -> bool:
    if isinstance(node, ast.Name):
        return True

    if isinstance(node, ast.Attribute):
        return _is_supported_receiver(
            node.value,
            source,
        )

    if isinstance(node, ast.Subscript):
        return _is_supported_receiver(
            node.value,
            source,
        )

    if isinstance(node, ast.Call):
        return _is_supported_receiver(
            node.func,
            source,
        )

    if isinstance(node, ast.IfExp):
        return (
            source.startswith("(")
            and source.endswith(")")
        )

    return False
