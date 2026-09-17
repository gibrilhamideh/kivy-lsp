"""One exclusion policy for Python and KV source discovery."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from fnmatch import fnmatchcase
from pathlib import Path


def is_excluded(
    path: Path,
    root: Path,
    patterns: Iterable[str],
) -> bool:
    """Match relative paths or any path component for simple patterns."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    names = [
        item.as_posix() for item in (relative, *relative.parents)
        if item != Path(".")
    ]
    for pattern in patterns:
        normalized = pattern.replace("\\", "/").strip("/")
        if "/" not in normalized and any(
            fnmatchcase(part, normalized) for part in relative.parts
        ):
            return True
        if any(
            fnmatchcase(text, normalized)
            or fnmatchcase(f"{text}/", normalized)
            for text in names
        ):
            return True
    return False


def discover_files(
    roots: Iterable[Path],
    suffixes: set[str],
    excludes: Iterable[str],
    *,
    on_error: Callable[[Path, str], None] | None = None,
) -> tuple[Path, ...]:
    """Walk roots deterministically, pruning excluded directories first."""
    paths: set[Path] = set()
    patterns = tuple(excludes)
    for root in roots:
        root = root.resolve()
        try:
            root.stat()
        except OSError as error:
            if on_error is not None:
                on_error(root, str(error))
            continue
        if root.is_file():
            if root.suffix in suffixes and not is_excluded(
                root, root.parent, patterns
            ):
                paths.add(root)
            continue
        if not root.is_dir():
            continue
        def report_error(error: OSError) -> None:
            if on_error is not None:
                on_error(Path(error.filename or root), str(error))

        for directory, directories, filenames in os.walk(
            root, onerror=report_error
        ):
            current = Path(directory)
            directories[:] = [
                name
                for name in sorted(directories)
                if not is_excluded(current / name, root, patterns)
            ]
            for filename in sorted(filenames):
                path = current / filename
                if path.suffix in suffixes and not is_excluded(
                    path, root, patterns
                ):
                    paths.add(path.resolve())
    return tuple(sorted(paths))
