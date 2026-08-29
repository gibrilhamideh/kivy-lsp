# src/kivy_lsp/analysis/python_ids_completion.py

from __future__ import annotations

import re
import token
import tokenize
from io import StringIO

from kivy_lsp.analysis.completion import (
    KvCompletionItem,
    KvCompletionKind,
    KvCompletionResult,
)
from kivy_lsp.analysis.completion_context import (
    KvCompletionTarget,
    KvCompletionTargetKind,
)
from kivy_lsp.analysis.widget_resolution import resolve_widget_class
from kivy_lsp.kv.index import KvIdSymbol, KvIndex
from kivy_lsp.model.span import Span
from kivy_lsp.model.symbol import (
    Symbol,
    SymbolKind,
)
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.workspace.document import TextDocument

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"

_DOT_MEMBER_PATTERN = re.compile(
    rf"\bself\s*\.\s*ids\s*\.\s*"
    rf"(?P<id>{_IDENTIFIER})\s*\.\s*"
    rf"(?P<prefix>[A-Za-z_][A-Za-z0-9_]*|)$"
)

_SUBSCRIPT_MEMBER_PATTERN = re.compile(
    rf"\bself\s*\.\s*ids\s*\[\s*"
    rf"(?P<quote>['\"])(?P<id>{_IDENTIFIER})(?P=quote)"
    rf"\s*\]\s*\.\s*"
    rf"(?P<prefix>[A-Za-z_][A-Za-z0-9_]*|)$"
)

_DOT_ID_PATTERN = re.compile(
    r"\bself\s*\.\s*ids\s*\.\s*"
    r"(?P<prefix>[A-Za-z_][A-Za-z0-9_]*|)$"
)

_SUBSCRIPT_ID_PATTERN = re.compile(
    r"\bself\s*\.\s*ids\s*\[\s*"
    r"(?P<quote>['\"])(?P<prefix>[A-Za-z_][A-Za-z0-9_]*|)$"
)

_SUBSCRIPT_OPEN_PATTERN = re.compile(
    r"\bself\s*\.\s*ids\s*\[\s*$"
)

class PythonIdsCompletionEngine:
    """Complete Kivy ids inside methods of their Python root class."""

    def __init__(
        self,
        python_index: PythonIndex,
        kv_index: KvIndex,
    ) -> None:
        self._python_index = python_index
        self._kv_index = kv_index

    def complete(
        self,
        document: TextDocument,
        offset: int,
    ) -> KvCompletionResult | None:
        if offset < 0 or offset > len(document.text):
            raise ValueError(
                "Completion offset is outside the document.",
            )

        line_start = document.text.rfind("\n", 0, offset) + 1
        line_prefix = document.text[line_start:offset]
        class_name = enclosing_class_name(
            document.text,
            offset,
        )

        if class_name is None:
            return None

        ids = self._kv_index.ids_for_class(class_name)

        if not ids:
            return None

        member_match = _SUBSCRIPT_MEMBER_PATTERN.search(
            line_prefix,
        )

        if member_match is None:
            member_match = _DOT_MEMBER_PATTERN.search(
                line_prefix,
            )

        if member_match is not None:
            return self._complete_members(
                ids,
                member_match.group("id"),
                member_match.group("prefix"),
                line_start + member_match.start("prefix"),
                offset,
            )

        subscript_match = _SUBSCRIPT_ID_PATTERN.search(
            line_prefix,
        )

        if subscript_match is not None:
            prefix = subscript_match.group("prefix")
            quote = subscript_match.group("quote")
            insert_suffix = _subscript_insert_suffix(
                document.text,
                offset,
                quote,
            )

            return _id_result(
                ids,
                prefix,
                line_start + subscript_match.start("prefix"),
                offset,
                insert_prefix="",
                insert_suffix=insert_suffix,
            )

        open_match = _SUBSCRIPT_OPEN_PATTERN.search(
            line_prefix,
        )

        if open_match is not None:
            return _id_result(
                ids,
                "",
                offset,
                offset,
                insert_prefix='"',
                insert_suffix='"]',
            )

        dot_match = _DOT_ID_PATTERN.search(line_prefix)

        if dot_match is None:
            return None

        prefix = dot_match.group("prefix")

        return _id_result(
            ids,
            prefix,
            line_start + dot_match.start("prefix"),
            offset,
        )

    def _complete_members(
        self,
        ids: tuple[KvIdSymbol, ...],
        id_name: str,
        prefix: str,
        replacement_start: int,
        offset: int,
    ) -> KvCompletionResult | None:
        id_symbol = next(
            (
                item
                for item in ids
                if item.name == id_name
            ),
            None,
        )

        if id_symbol is None:
            return None

        class_symbol = resolve_widget_class(
            id_symbol.widget_class,
            self._python_index,
            self._kv_index,
        )

        if class_symbol is None:
            return None

        items = tuple(
            _member_completion(member)
            for member in self._python_index.members_of(class_symbol)
            if _member_matches(member.name, prefix)
        )
        target = _target(
            prefix,
            replacement_start,
            offset,
            kind=KvCompletionTargetKind.MEMBER,
        )

        return KvCompletionResult(
            target=target,
            items=_deduplicate_and_sort(items),
        )

def _id_result(
    ids: tuple[KvIdSymbol, ...],
    prefix: str,
    replacement_start: int,
    offset: int,
    *,
    insert_prefix: str = "",
    insert_suffix: str = "",
) -> KvCompletionResult:
    items = tuple(
        KvCompletionItem(
            label=id_symbol.name,
            kind=KvCompletionKind.ID,
            insert_text=(
                f"{insert_prefix}{id_symbol.name}{insert_suffix}"
            ),
            sort_text=f"00:{id_symbol.name.casefold()}",
            detail=f"Kivy id: {id_symbol.widget_class}",
        )
        for id_symbol in ids
        if id_symbol.name.casefold().startswith(prefix.casefold())
    )

    return KvCompletionResult(
        target=_target(
            prefix,
            replacement_start,
            offset,
            kind=KvCompletionTargetKind.NAME,
        ),
        items=items,
    )


def _target(
    prefix: str,
    replacement_start: int,
    offset: int,
    *,
    kind: KvCompletionTargetKind,
) -> KvCompletionTarget:
    span = Span(
        start=replacement_start,
        end=offset,
    )

    return KvCompletionTarget(
        kind=kind,
        prefix=prefix,
        replacement_span=span,
        expression_span=span,
    )


def _subscript_insert_suffix(
    source: str,
    offset: int,
    quote: str,
) -> str:
    remainder = source[offset:]
    closing_pattern = re.compile(
        rf"^{re.escape(quote)}\s*\]"
    )

    if closing_pattern.match(remainder) is not None:
        return ""

    return f"{quote}]"


def enclosing_class_name(
    source: str,
    offset: int,
) -> str | None:
    scope_classes: list[str | None] = []
    pending_suite_class: str | None = None
    class_name: str | None = None
    expects_class_name = False
    class_bracket_depth = 0
    reader = StringIO(source[:offset]).readline

    try:
        tokens = tokenize.generate_tokens(reader)

        for item in tokens:
            if (
                item.type == token.NEWLINE
                and not item.string
            ):
                break

            if item.type == token.INDENT:
                scope_classes.append(pending_suite_class)
                pending_suite_class = None
                continue

            if item.type == token.DEDENT:
                if scope_classes:
                    scope_classes.pop()

                continue

            if (
                item.type == token.NAME
                and item.string == "class"
                and class_name is None
            ):
                expects_class_name = True
                continue

            if (
                expects_class_name
                and item.type == token.NAME
            ):
                class_name = item.string
                expects_class_name = False
                continue

            if class_name is None:
                continue

            if item.type == token.OP:
                if item.string in {"(", "[", "{"}:
                    class_bracket_depth += 1
                    continue

                if item.string in {")", "]", "}"}:
                    class_bracket_depth = max(
                        0,
                        class_bracket_depth - 1,
                    )
                    continue

                if (
                    item.string == ":"
                    and class_bracket_depth == 0
                ):
                    pending_suite_class = class_name
                    class_name = None
                    continue

            if item.type == token.NEWLINE:
                class_name = None
                expects_class_name = False
                class_bracket_depth = 0
    except (IndentationError, SyntaxError, tokenize.TokenError):
        pass

    return next(
        (
            name
            for name in reversed(scope_classes)
            if name is not None
        ),
        None,
    )


def _member_matches(name: str, prefix: str) -> bool:
    if name.startswith("_") and not prefix.startswith("_"):
        return False

    return name.casefold().startswith(prefix.casefold())


def _member_completion(symbol: Symbol) -> KvCompletionItem:
    kind = _member_kind(symbol.kind)
    detail = (
        symbol.signature
        or symbol.annotation
        or symbol.qualified_name
    )

    return KvCompletionItem(
        label=symbol.name,
        kind=kind,
        insert_text=symbol.name,
        sort_text=f"00:{symbol.name.casefold()}",
        detail=detail,
        documentation=symbol.documentation,
        symbol=symbol,
    )


def _member_kind(kind: SymbolKind) -> KvCompletionKind:
    if kind is SymbolKind.METHOD:
        return KvCompletionKind.METHOD

    if kind is SymbolKind.FUNCTION:
        return KvCompletionKind.FUNCTION

    if kind is SymbolKind.PROPERTY:
        return KvCompletionKind.PROPERTY

    if kind is SymbolKind.EVENT:
        return KvCompletionKind.EVENT

    if kind is SymbolKind.CONSTANT:
        return KvCompletionKind.CONSTANT

    if kind is SymbolKind.CLASS:
        return KvCompletionKind.CLASS

    return KvCompletionKind.VARIABLE


def _deduplicate_and_sort(
    items: tuple[KvCompletionItem, ...],
) -> tuple[KvCompletionItem, ...]:
    unique: dict[str, KvCompletionItem] = {}

    for item in items:
        unique.setdefault(item.label, item)

    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.sort_text,
                item.label.casefold(),
            ),
        )
    )

