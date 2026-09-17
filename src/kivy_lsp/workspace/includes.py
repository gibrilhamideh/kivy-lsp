"""Static include resolution; no directives or application code are run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kivy_lsp.kv.nodes import DirectiveNode
from kivy_lsp.kv.parser import ParseResult
from kivy_lsp.model.diagnostic import Diagnostic, DiagnosticSeverity
from kivy_lsp.model.span import Span


@dataclass(frozen=True, slots=True)
class IncludeReference:
    """An include directive and its statically resolved destination."""

    span: Span
    path: str
    target_uri: str | None
    diagnostic: Diagnostic | None = None


def resolve_includes(
    parsed: ParseResult,
    source_path: Path,
    project_root: Path,
    available_uris: set[str],
) -> tuple[IncludeReference, ...]:
    """Resolve absolute paths and unambiguous local/project-relative paths.

    Runtime resource search paths are deliberately not guessed. The optional
    ``force`` prefix affects Kivy loading, not the static include graph.
    """
    references: list[IncludeReference] = []
    for node in parsed.document.items:
        if not isinstance(node, DirectiveNode) or node.name != "include":
            continue
        argument = node.arguments.strip()
        if argument.startswith("force "):
            argument = argument[6:].strip()
        # Match Kivy 2.3.1's quote stripping (not Python string eval).
        if argument and argument[0] == argument[-1] and argument[0] in "\"'":
            quote_count = argument[:3].count(argument[0])
            if quote_count != 2:
                argument = argument[quote_count:-quote_count]
        if not argument:
            references.append(_unresolved(node, argument, "Empty path."))
            continue
        if not argument.endswith(".kv"):
            references.append(
                _unresolved(
                    node, argument, "Kivy include paths must end in .kv."
                )
            )
            continue
        path = Path(argument)
        candidates = (
            (path.resolve(),)
            if path.is_absolute()
            else tuple(
                dict.fromkeys(
                    (
                        (source_path.parent / path).resolve(),
                        (project_root / path).resolve(),
                    )
                )
            )
        )
        found = tuple(
            item
            for item in candidates
            if item.is_file() or item.as_uri() in available_uris
        )
        if len(found) != 1:
            reason = (
                "Multiple files match the local and project search roots."
                if found
                else "No file matches the local or project search roots."
            )
            references.append(_unresolved(node, argument, reason))
            continue
        references.append(
            IncludeReference(
                span=node.span,
                path=argument,
                target_uri=found[0].as_uri(),
            )
        )
    return tuple(references)


def _unresolved(
    node: DirectiveNode,
    argument: str,
    reason: str,
) -> IncludeReference:
    return IncludeReference(
        span=node.span,
        path=argument,
        target_uri=None,
        diagnostic=Diagnostic(
            message=f"Cannot resolve include {argument!r}. {reason}",
            span=node.span,
            severity=DiagnosticSeverity.WARNING,
            code="kv-include-unresolved",
        ),
    )
