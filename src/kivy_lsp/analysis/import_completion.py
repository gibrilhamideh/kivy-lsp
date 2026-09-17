"""Completion of the dotted target in a KV import directive."""

from __future__ import annotations

import keyword
import re

from kivy_lsp.analysis.completion import (
    KvCompletionItem,
    KvCompletionKind,
    KvCompletionResult,
)
from kivy_lsp.analysis.completion_context import (
    KvCompletionTarget,
    KvCompletionTargetKind,
)
from kivy_lsp.kv.context import context_at
from kivy_lsp.kv.parser import ParseResult
from kivy_lsp.model.span import Span
from kivy_lsp.model.symbol import SymbolKind
from kivy_lsp.python.import_completion import (
    ImportCandidate,
    PythonImportCompleter,
)
from kivy_lsp.workspace.document import TextDocument

_IMPORT_PREFIX = re.compile(r"#:[ \t]*import[ \t]+(\S+)[ \t]+([^\s]*)")


class KvImportCompletionEngine:
    def __init__(self, candidates: PythonImportCompleter) -> None:
        self._candidates = candidates

    def complete(
        self,
        document: TextDocument,
        parsed: ParseResult,
        offset: int,
    ) -> KvCompletionResult | None:
        target = import_target_at(document, parsed, offset)
        if target is None:
            return None
        candidates = self._candidates.candidates(
            target.receiver or "", target.prefix
        )
        return KvCompletionResult(
            target=target,
            items=tuple(_completion_item(item) for item in candidates),
            is_incomplete=True,
        )


def import_target_at(
    document: TextDocument,
    parsed: ParseResult,
    offset: int,
) -> KvCompletionTarget | None:
    """Recognize a real directive and replace only the current path part."""
    directive = context_at(parsed, offset).directive
    if directive is None or directive.name != "import":
        return None
    start = directive.token.span.start
    match = _IMPORT_PREFIX.fullmatch(document.text[start:offset])
    if match is None:
        return None
    alias, path = match.groups()
    if not alias.isidentifier() or keyword.iskeyword(alias):
        return None
    parent, separator, prefix = path.rpartition(".")
    if separator and not all(_identifier(part) for part in parent.split(".")):
        return None
    if prefix and not _identifier(prefix):
        return None

    end = offset
    while end < directive.span.end:
        character = document.text[end]
        if not ("_" + character).isidentifier():
            break
        end += 1
    return KvCompletionTarget(
        kind=KvCompletionTargetKind.IMPORT,
        prefix=prefix,
        replacement_span=Span(offset - len(prefix), end),
        expression_span=Span(start + match.start(2), end),
        receiver=parent,
    )


def _identifier(text: str) -> bool:
    return text.isidentifier() and not keyword.iskeyword(text)


def _completion_item(candidate: ImportCandidate) -> KvCompletionItem:
    symbol = candidate.symbol
    kind = KvCompletionKind.MODULE
    if symbol is not None:
        kind = (
            KvCompletionKind.VARIABLE
            if symbol.kind is SymbolKind.PARAMETER
            else KvCompletionKind(symbol.kind.value)
        )
    return KvCompletionItem(
        label=candidate.name,
        kind=kind,
        insert_text=candidate.name,
        sort_text=candidate.name.casefold(),
        detail=candidate.qualified_name,
        documentation=symbol.documentation if symbol else None,
        symbol=symbol,
    )
