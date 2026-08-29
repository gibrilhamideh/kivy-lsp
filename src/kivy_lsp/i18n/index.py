from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from kivy_lsp.config import I18nConfig
from kivy_lsp.model.span import Span

_PLACEHOLDER_PATTERN = re.compile(
    r"(?<!\{)\{"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:![ars])?"
    r"(?::[^{}]*)?"
    r"\}(?!\})"
)


@dataclass(frozen=True, slots=True)
class TranslationPlaceholder:
    """One named format placeholder in a translation value."""

    name: str
    span: Span


@dataclass(frozen=True, slots=True)
class TranslationEntry:
    """One flattened translation key and its source location."""

    key: str
    value: str
    uri: str
    key_span: Span
    value_span: Span
    placeholders: tuple[TranslationPlaceholder, ...] = ()

    @property
    def placeholder_names(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                placeholder.name
                for placeholder in self.placeholders
            )
        )

    def placeholder_named(
        self,
        name: str,
    ) -> TranslationPlaceholder | None:
        for placeholder in self.placeholders:
            if placeholder.name == name:
                return placeholder

        return None


@dataclass(frozen=True, slots=True)
class TranslationCatalogProblem:
    """A problem found while loading the translation catalog."""

    message: str
    span: Span


class TranslationIndex:
    """Cached index of one configured JSON translation catalog."""

    def __init__(self, config: I18nConfig | None) -> None:
        self._config = config
        self._entries: dict[str, TranslationEntry] = {}
        self._ordered_entries: tuple[TranslationEntry, ...] = ()
        self._problems: tuple[TranslationCatalogProblem, ...] = ()
        self._fingerprint: tuple[object, ...] | None = None
        self._source = ""
        self._revision = 0

    @property
    def configured(self) -> bool:
        return self._config is not None

    @property
    def source_path(self) -> Path | None:
        if self._config is None:
            return None

        return self._config.source

    @property
    def source_uri(self) -> str | None:
        path = self.source_path

        if path is None:
            return None

        return path.as_uri()

    @property
    def source(self) -> str:
        self.refresh()
        return self._source

    @property
    def revision(self) -> int:
        self.refresh()
        return self._revision

    @property
    def problems(self) -> tuple[TranslationCatalogProblem, ...]:
        self.refresh()
        return self._problems

    def refresh(self, *, force: bool = False) -> bool:
        """Reload the catalog when its filesystem identity changes."""
        path = self.source_path

        if path is None:
            return False

        fingerprint = _fingerprint(path)

        if not force and fingerprint == self._fingerprint:
            return False

        self._fingerprint = fingerprint
        self._entries = {}
        self._ordered_entries = ()
        self._source = ""

        try:
            source = path.read_text(encoding="utf-8")
        except OSError as error:
            self._problems = (
                TranslationCatalogProblem(
                    message=(
                        "Could not read translation catalog: "
                        f"{error}."
                    ),
                    span=Span.empty(0),
                ),
            )
            self._revision += 1
            return True

        self._source = source

        try:
            decoded = json.loads(source)
        except json.JSONDecodeError as error:
            self._problems = (
                TranslationCatalogProblem(
                    message=(
                        "Invalid translation JSON: "
                        f"{error.msg}."
                    ),
                    span=Span.empty(error.pos),
                ),
            )
            self._revision += 1
            return True

        if not isinstance(decoded, dict):
            self._problems = (
                TranslationCatalogProblem(
                    message=(
                        "The translation catalog root must be an "
                        "object."
                    ),
                    span=Span(
                        start=0,
                        end=len(source),
                    ),
                ),
            )
            self._revision += 1
            return True

        parser = _CatalogParser(
            source,
            path.as_uri(),
        )
        entries, problems = parser.parse()
        self._entries = {
            entry.key: entry
            for entry in entries
        }
        self._ordered_entries = tuple(
            sorted(
                self._entries.values(),
                key=lambda entry: entry.key.casefold(),
            )
        )
        self._problems = problems
        self._revision += 1
        return True

    def entry(self, key: str) -> TranslationEntry | None:
        self.refresh()
        return self._entries.get(key)

    def entries(self) -> tuple[TranslationEntry, ...]:
        self.refresh()
        return self._ordered_entries

    def complete(
        self,
        prefix: str,
    ) -> tuple[TranslationEntry, ...]:
        normalized = prefix.casefold()

        return tuple(
            entry
            for entry in self.entries()
            if entry.key.casefold().startswith(normalized)
        )


@dataclass(frozen=True, slots=True)
class _JsonString:
    value: str
    span: Span
    content_span: Span


class _CatalogParser:
    """Locate translation entries after the standard JSON validation."""

    def __init__(self, source: str, uri: str) -> None:
        self._source = source
        self._uri = uri
        self._offset = 0
        self._entries: list[TranslationEntry] = []
        self._problems: list[TranslationCatalogProblem] = []
        self._keys: set[str] = set()

    def parse(
        self,
    ) -> tuple[
        tuple[TranslationEntry, ...],
        tuple[TranslationCatalogProblem, ...],
    ]:
        self._skip_whitespace()
        self._parse_object(())

        return (
            tuple(self._entries),
            tuple(self._problems),
        )

    def _parse_object(self, path: tuple[str, ...]) -> None:
        self._consume("{")
        self._skip_whitespace()

        if self._peek() == "}":
            self._offset += 1
            return

        while self._offset < len(self._source):
            key = self._parse_string()
            self._skip_whitespace()
            self._consume(":")
            self._skip_whitespace()
            entry_path = (*path, key.value)
            character = self._peek()

            if character == "{":
                self._parse_object(entry_path)
            elif character == '"':
                value = self._parse_string()
                self._add_entry(entry_path, key, value)
            else:
                value_span = self._skip_value()
                self._problems.append(
                    TranslationCatalogProblem(
                        message=(
                            "Translation values must be strings or "
                            "nested objects."
                        ),
                        span=value_span,
                    )
                )

            self._skip_whitespace()

            if self._peek() == "}":
                self._offset += 1
                return

            self._consume(",")
            self._skip_whitespace()

    def _add_entry(
        self,
        path: tuple[str, ...],
        key_token: _JsonString,
        value_token: _JsonString,
    ) -> None:
        flattened = ".".join(path)

        if not flattened:
            self._problems.append(
                TranslationCatalogProblem(
                    message="Translation keys cannot be empty.",
                    span=key_token.content_span,
                )
            )
            return

        if flattened in self._keys:
            self._problems.append(
                TranslationCatalogProblem(
                    message=(
                        "Duplicate flattened translation key "
                        f"{flattened!r}."
                    ),
                    span=key_token.content_span,
                )
            )
            return

        self._keys.add(flattened)
        placeholders = tuple(
            TranslationPlaceholder(
                name=match.group("name"),
                span=Span(
                    start=(
                        value_token.content_span.start
                        + match.start()
                    ),
                    end=(
                        value_token.content_span.start
                        + match.end()
                    ),
                ),
            )
            for match in _PLACEHOLDER_PATTERN.finditer(
                self._source[
                    value_token.content_span.start:
                    value_token.content_span.end
                ]
            )
        )
        self._entries.append(
            TranslationEntry(
                key=flattened,
                value=value_token.value,
                uri=self._uri,
                key_span=key_token.content_span,
                value_span=value_token.content_span,
                placeholders=placeholders,
            )
        )

    def _parse_string(self) -> _JsonString:
        start = self._offset
        self._consume('"')
        escaped = False

        while self._offset < len(self._source):
            character = self._source[self._offset]

            if escaped:
                escaped = False
                self._offset += 1
                continue

            if character == "\\":
                escaped = True
                self._offset += 1
                continue

            if character == '"':
                self._offset += 1
                break

            self._offset += 1

        end = self._offset
        raw = self._source[start:end]

        return _JsonString(
            value=json.loads(raw),
            span=Span(start=start, end=end),
            content_span=Span(
                start=start + 1,
                end=end - 1,
            ),
        )

    def _skip_value(self) -> Span:
        start = self._offset
        _, length = json.JSONDecoder().raw_decode(
            self._source[start:],
        )
        self._offset = start + length
        return Span(start=start, end=self._offset)

    def _skip_whitespace(self) -> None:
        while (
            self._offset < len(self._source)
            and self._source[self._offset].isspace()
        ):
            self._offset += 1

    def _consume(self, character: str) -> None:
        if self._peek() != character:
            raise ValueError(
                "Validated JSON did not match the catalog parser."
            )

        self._offset += 1

    def _peek(self) -> str | None:
        if self._offset >= len(self._source):
            return None

        return self._source[self._offset]


def _fingerprint(path: Path) -> tuple[object, ...]:
    try:
        stat = path.stat()
    except OSError as error:
        return (
            "missing",
            type(error).__name__,
            str(error),
        )

    return (
        "file",
        stat.st_mtime_ns,
        stat.st_size,
    )

