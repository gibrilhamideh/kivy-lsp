# src/kivy_lsp/kv/index.py

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from kivy_lsp.model.span import Span


@dataclass(frozen=True, slots=True)
class KvIdSymbol:
    """A Kivy id and the widget instance stored under that name."""

    name: str
    widget_class: str
    uri: str
    span: Span

    def __post_init__(self) -> None:
        if not self.name.isidentifier():
            raise ValueError(
                "KV id name must be a valid identifier",
            )

        if not self.widget_class:
            raise ValueError(
                "KV id widget class cannot be empty",
            )

        if not self.uri:
            raise ValueError(
                "KV id URI cannot be empty",
            )


@dataclass(frozen=True, slots=True)
class KvClassSymbol:
    """A widget class referenced or defined by a KV rule."""

    name: str
    uri: str
    span: Span
    bases: tuple[str, ...] = ()
    is_dynamic: bool = False
    ids: tuple[KvIdSymbol, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError(
                "KV class name cannot be empty",
            )

        if not self.uri:
            raise ValueError(
                "KV class URI cannot be empty",
            )

        if self.is_dynamic and not self.bases:
            raise ValueError(
                "A dynamic KV class must have a base class",
            )

        for id_symbol in self.ids:
            if id_symbol.uri != self.uri:
                raise ValueError(
                    "KV id URI does not match its rule URI",
                )


class KvIndex:
    """Project-wide index of classes discovered in KV files."""

    def __init__(self) -> None:
        self._by_uri: dict[
            str,
            tuple[KvClassSymbol, ...],
        ] = {}

        self._by_name: dict[
            str,
            list[KvClassSymbol],
        ] = {}

    def replace(
        self,
        uri: str,
        symbols: Iterable[KvClassSymbol],
    ) -> None:
        """Replace every indexed symbol belonging to one document."""

        new_symbols = tuple(symbols)

        for symbol in new_symbols:
            if symbol.uri != uri:
                raise ValueError(
                    "KV symbol URI does not match document URI",
                )

        self.remove(uri)

        if not new_symbols:
            return

        self._by_uri[uri] = new_symbols

        for symbol in new_symbols:
            bucket = self._by_name.setdefault(
                symbol.name,
                [],
            )
            bucket.append(symbol)

    def remove(
        self,
        uri: str,
    ) -> None:
        """Remove every indexed symbol belonging to one document."""

        previous = self._by_uri.pop(
            uri,
            (),
        )

        for symbol in previous:
            bucket = self._by_name.get(
                symbol.name,
            )

            if bucket is None:
                continue

            bucket.remove(symbol)

            if not bucket:
                del self._by_name[symbol.name]

    def find(
        self,
        name: str,
    ) -> tuple[KvClassSymbol, ...]:
        """Return all KV class symbols with the requested name."""

        return tuple(
            self._by_name.get(
                name,
                (),
            ),
        )

    def symbols_for_uri(
        self,
        uri: str,
    ) -> tuple[KvClassSymbol, ...]:
        """Return every KV class symbol found in one document."""

        return self._by_uri.get(
            uri,
            (),
        )

    def ids_for_class(
        self,
        name: str,
    ) -> tuple[KvIdSymbol, ...]:
        """Return merged ids declared by every rule for one class."""
        ids: dict[str, KvIdSymbol] = {}

        for symbol in self.find(name):
            for id_symbol in symbol.ids:
                ids.setdefault(
                    id_symbol.name,
                    id_symbol,
                )

        return tuple(
            sorted(
                ids.values(),
                key=lambda item: (
                    item.name.casefold(),
                    item.uri,
                    item.span.start,
                ),
            )
        )

    def id_definitions_for_class(
        self,
        class_name: str,
        id_name: str,
    ) -> tuple[KvIdSymbol, ...]:
        """Return every declaration of one id on a KV class."""
        result = [
            id_symbol
            for symbol in self.find(class_name)
            for id_symbol in symbol.ids
            if id_symbol.name == id_name
        ]

        return tuple(
            sorted(
                result,
                key=lambda item: (
                    item.uri,
                    item.span.start,
                ),
            )
        )

    def names(self) -> tuple[str, ...]:
        """Return unique class names in deterministic order."""

        return tuple(
            sorted(
                self._by_name,
                key=str.casefold,
            ),
        )

    def symbols(self) -> tuple[KvClassSymbol, ...]:
        """Return every indexed symbol in deterministic order."""

        result = [
            symbol
            for symbols in self._by_uri.values()
            for symbol in symbols
        ]

        return tuple(
            sorted(
                result,
                key=lambda symbol: (
                    symbol.name.casefold(),
                    symbol.uri,
                    symbol.span.start,
                ),
            ),
        )

    def contains(
        self,
        name: str,
    ) -> bool:
        """Return whether at least one KV rule uses this class name."""

        return name in self._by_name

    def clear(self) -> None:
        """Remove every class from the index."""

        self._by_uri.clear()
        self._by_name.clear()

    def __len__(self) -> int:
        return sum(
            len(symbols)
            for symbols in self._by_uri.values()
        )

