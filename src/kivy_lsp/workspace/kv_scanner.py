# src/kivy_lsp/workspace/kv_scanner.py

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from kivy_lsp.kv.index import (
    KvClassSymbol,
    KvIdSymbol,
    KvIndex,
)
from kivy_lsp.kv.nodes import (
    BodyNode,
    PropertyNode,
    RuleNode,
    WidgetNode,
)
from kivy_lsp.kv.parser import parse
from kivy_lsp.model.span import Span

_IGNORED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}


@dataclass(frozen=True, slots=True)
class _IdCandidate:
    name: str
    span: Span
    widget_class: str | None


class KvScanner:
    """Discover classes and ids declared by KV rules."""

    def __init__(
        self,
        roots: Iterable[Path],
    ) -> None:
        self._roots = tuple(
            dict.fromkeys(
                root.resolve()
                for root in roots
            ),
        )

    def scan(self) -> KvIndex:
        """Build a new index from every discovered KV file."""
        index = KvIndex()

        for path in self.paths():
            uri = path.as_uri()
            index.replace(
                uri,
                self.scan_path(path),
            )

        return index

    def paths(self) -> tuple[Path, ...]:
        """Return every KV file beneath the configured roots."""
        paths: set[Path] = set()

        for root in self._roots:
            if root.is_file():
                if root.suffix == ".kv":
                    paths.add(root)

                continue

            if not root.is_dir():
                continue

            for path in root.rglob("*.kv"):
                resolved = path.resolve()

                if self._is_ignored(resolved):
                    continue

                paths.add(resolved)

        return tuple(
            sorted(
                paths,
                key=lambda path: path.as_posix(),
            ),
        )

    def scan_path(
        self,
        path: Path,
    ) -> tuple[KvClassSymbol, ...]:
        """Extract KV class symbols from one file on disk."""
        try:
            source = path.read_text(
                encoding="utf-8",
            )
        except (OSError, UnicodeError):
            return ()

        return self.scan_text(
            path.resolve().as_uri(),
            source,
        )

    def scan_text(
        self,
        uri: str,
        source: str,
    ) -> tuple[KvClassSymbol, ...]:
        """Extract KV classes and ids from in-memory source."""
        symbols: list[KvClassSymbol] = []
        parse_result = parse(source)

        for item in parse_result.document.items:
            if not isinstance(item, RuleNode):
                continue

            candidates = _collect_id_candidates(item.body)

            for selector in item.selectors:
                name = selector.name.text

                if not name.isidentifier():
                    continue

                bases = tuple(
                    base.text
                    for base in selector.base_names
                    if base.text.isidentifier()
                )
                is_dynamic = selector.is_dynamic

                if is_dynamic and not bases:
                    continue

                ids = tuple(
                    KvIdSymbol(
                        name=candidate.name,
                        widget_class=(
                            candidate.widget_class
                            or name
                        ),
                        uri=uri,
                        span=candidate.span,
                    )
                    for candidate in candidates
                )
                symbols.append(
                    KvClassSymbol(
                        name=name,
                        uri=uri,
                        span=selector.name.span,
                        bases=bases,
                        is_dynamic=is_dynamic,
                        ids=ids,
                    )
                )

        return tuple(symbols)

    def _is_ignored(
        self,
        path: Path,
    ) -> bool:
        return any(
            part in _IGNORED_DIRECTORIES
            for part in path.parts
        )


def _collect_id_candidates(
    body: tuple[BodyNode, ...],
) -> tuple[_IdCandidate, ...]:
    candidates: list[_IdCandidate] = []

    _visit_body_for_ids(
        body,
        current_widget_class=None,
        candidates=candidates,
    )

    return tuple(candidates)


def _visit_body_for_ids(
    body: tuple[BodyNode, ...],
    *,
    current_widget_class: str | None,
    candidates: list[_IdCandidate],
) -> None:
    for item in body:
        if isinstance(item, WidgetNode):
            _visit_body_for_ids(
                item.body,
                current_widget_class=item.class_name,
                candidates=candidates,
            )
            continue

        if item.name == "id":
            candidate = _id_candidate(
                item,
                current_widget_class,
            )

            if candidate is not None:
                candidates.append(candidate)

        if item.body:
            _visit_body_for_ids(
                item.body,
                current_widget_class=current_widget_class,
                candidates=candidates,
            )


def _id_candidate(
    node: PropertyNode,
    widget_class: str | None,
) -> _IdCandidate | None:
    if node.value is None:
        return None

    name = node.value.text.strip()

    if not name.isidentifier():
        return None

    if name in {"app", "root", "self"}:
        return None

    return _IdCandidate(
        name=name,
        span=node.value.span,
        widget_class=widget_class,
    )
