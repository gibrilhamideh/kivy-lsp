# src/kivy_lsp/config.py


from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from kivy_lsp.formatting.options import FormatOptions

CONFIG_FILENAME = "kivy-lsp.toml"

_DEFAULT_EXCLUDES = (
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "venv",
)


class ConfigError(ValueError):
    """An invalid kivy-lsp project configuration."""


@dataclass(frozen=True, slots=True)
class DiagnosticOptions:
    """Control warnings about values that cannot be proven compatible."""

    strict: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.strict, bool):
            raise ValueError("strict must be a boolean.")


@dataclass(frozen=True, slots=True)
class GlobalImport:
    """One configured name imported into every KV scope."""

    name: str
    target: str

    def __post_init__(self) -> None:
        if not self.name.isidentifier():
            raise ValueError(f"Invalid global import name: {self.name!r}.")

        if not self.target:
            raise ValueError(
                f"Global import {self.name!r} has an empty target."
            )


def _empty_string_map() -> dict[str, str]:
    return {}


def _empty_projection_map() -> dict[str, int]:
    return {}


def _empty_global_imports() -> tuple[GlobalImport, ...]:
    return ()


@dataclass(frozen=True, slots=True)
class LibraryExports:
    """Library-owned KV bindings and generic projection information."""

    globals: dict[str, str] = field(default_factory=_empty_string_map)
    global_imports: tuple[GlobalImport, ...] = field(
        default_factory=_empty_global_imports,
    )
    member_projections: dict[str, int] = field(
        default_factory=_empty_projection_map,
    )
    subscript_projections: dict[str, int] = field(
        default_factory=_empty_projection_map,
    )


@dataclass(frozen=True, slots=True)
class I18nConfig:
    """Configuration for one canonical translation catalog."""

    source: Path
    properties: tuple[str, ...] = (
        "i18n_key",
        "hint_i18n_key",
    )

    def __post_init__(self) -> None:
        if not self.source.is_absolute():
            raise ValueError("Translation source path must be absolute.")

        if not self.properties:
            raise ValueError("Translation property names cannot be empty.")

        for name in self.properties:
            if not name.isidentifier():
                raise ValueError(
                    f"Invalid translation property name: {name!r}."
                )


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Resolved configuration for one Kivy project."""

    project_root: Path
    source_roots: tuple[Path, ...]
    kv_paths: tuple[Path, ...]
    app_class: str | None = None
    globals: dict[str, str] = field(
        default_factory=_empty_string_map,
    )
    member_projections: dict[str, int] = field(
        default_factory=_empty_projection_map,
    )
    subscript_projections: dict[str, int] = field(
        default_factory=_empty_projection_map,
    )
    global_imports: tuple[GlobalImport, ...] = field(
        default_factory=_empty_global_imports,
    )
    i18n: I18nConfig | None = None
    excludes: tuple[str, ...] = _DEFAULT_EXCLUDES
    format: FormatOptions = field(default_factory=FormatOptions)
    python_environment: Path | None = None
    python_interpreter: Path | None = None
    diagnostics: DiagnosticOptions = field(default_factory=DiagnosticOptions)

    def __post_init__(self) -> None:
        if not self.project_root.is_absolute():
            raise ValueError("Project root must be absolute.")

        for path in (self.python_environment, self.python_interpreter):
            if path is not None and not path.is_absolute():
                raise ValueError("Python environment paths must be absolute.")

        if self.python_environment and self.python_interpreter:
            raise ValueError("Select an environment or interpreter, not both.")

        for source_root in self.source_roots:
            if not source_root.is_absolute():
                raise ValueError("Source roots must be absolute.")

        for kv_path in self.kv_paths:
            if not kv_path.is_absolute():
                raise ValueError("KV paths must be absolute.")

        for name, target in self.globals.items():
            if not name.isidentifier():
                raise ValueError(f"Invalid KV global name: {name!r}.")

            if not target:
                raise ValueError(f"KV global {name!r} has an empty target.")

        global_import_names: set[str] = set()

        for imported in self.global_imports:
            if imported.name in global_import_names:
                raise ValueError(
                    f"Duplicate global import name: {imported.name!r}."
                )

            global_import_names.add(imported.name)

        for pattern in self.excludes:
            if not pattern.strip():
                raise ValueError("Exclude patterns cannot be empty.")

        self._validate_projections(
            self.member_projections,
            "member",
        )
        self._validate_projections(
            self.subscript_projections,
            "subscript",
        )

    @staticmethod
    def _validate_projections(
        projections: dict[str, int],
        projection_kind: str,
    ) -> None:
        for type_name, argument_index in projections.items():
            if not type_name:
                raise ValueError(
                    f"{projection_kind.title()} projection type "
                    "cannot be empty."
                )

            if argument_index < 0:
                raise ValueError(
                    f"{projection_kind.title()} projection index "
                    "cannot be negative."
                )

    def module_name_for(self, path: Path) -> str | None:
        """Return the Python module name represented by a source path."""
        resolved_path = path.resolve()

        if resolved_path.suffix not in {".py", ".pyi"}:
            return None

        source_roots = sorted(
            self.source_roots,
            key=lambda root: len(root.parts),
            reverse=True,
        )

        for source_root in source_roots:
            try:
                relative = resolved_path.relative_to(source_root)
            except ValueError:
                continue

            module_path = relative.with_suffix("")
            parts = list(module_path.parts)

            if parts and parts[-1] == "__init__":
                parts.pop()

            if not parts:
                return None

            if not all(part.isidentifier() for part in parts):
                return None

            return ".".join(parts)

        return None

    def member_projection_for(
        self,
        type_name: str,
    ) -> int | None:
        """Return the generic argument exposed as object members."""
        return _projection_for(
            self.member_projections,
            type_name,
        )

    def subscript_projection_for(
        self,
        type_name: str,
    ) -> int | None:
        """Return the generic argument produced by subscription."""
        return _projection_for(
            self.subscript_projections,
            type_name,
        )


def find_project_root(path: Path) -> Path:
    """Prefer dedicated configuration within the nearest Git boundary."""
    directory = path.resolve()
    if not directory.is_dir():
        directory = directory.parent
    fallback: Path | None = None
    for candidate in (directory, *directory.parents):
        if (candidate / CONFIG_FILENAME).is_file():
            return candidate
        git_boundary = (candidate / ".git").exists()
        if fallback is None and (
            (candidate / "pyproject.toml").is_file() or git_boundary
        ):
            fallback = candidate
        if git_boundary:
            break
    return fallback or directory


def load_config(project_root: Path) -> ServerConfig:
    """Load project settings exclusively from its kivy-lsp.toml file."""
    root = project_root.resolve()
    config_path = root / CONFIG_FILENAME
    default_source_roots = _default_source_roots(root)

    if not config_path.exists():
        return ServerConfig(
            project_root=root,
            source_roots=default_source_roots,
            kv_paths=default_source_roots,
        )

    config_table = _read_config_table(config_path)
    exports = _library_exports(config_table)
    source_roots = _path_list(
        root,
        config_table.get("source-roots"),
        default=default_source_roots,
        name="source-roots",
    )
    kv_paths = _path_list(
        root,
        config_table.get("kv-paths"),
        default=source_roots,
        name="kv-paths",
    )
    app_class = _optional_string(config_table.get("app-class"), "app-class")
    excludes = _string_list(
        config_table.get("excludes"),
        default=_DEFAULT_EXCLUDES,
        name="excludes",
    )
    format_table = _optional_table(config_table.get("format"), "format")
    try:
        format_options = FormatOptions(
            line_length=cast(int, format_table.get("line-length", 79)),
        )
    except ValueError as error:
        raise ConfigError(f"format: {error}") from error

    diagnostics_table = _optional_table(
        config_table.get("diagnostics"), "diagnostics",
    )
    try:
        diagnostics_options = DiagnosticOptions(
            strict=cast(bool, diagnostics_table.get("strict", False)),
        )
    except ValueError as error:
        raise ConfigError(f"diagnostics: {error}") from error

    environment = _optional_path(root, config_table, "python-environment")
    interpreter = _optional_path(root, config_table, "python-interpreter")
    if environment is not None and interpreter is not None:
        raise ConfigError(
            "Set only one of python-environment and python-interpreter."
        )

    return ServerConfig(
        project_root=root,
        source_roots=source_roots,
        kv_paths=kv_paths,
        app_class=app_class,
        globals=exports.globals,
        member_projections=exports.member_projections,
        subscript_projections=exports.subscript_projections,
        global_imports=exports.global_imports,
        i18n=_i18n_config(root, config_table.get("i18n")),
        excludes=excludes,
        format=format_options,
        python_environment=environment,
        python_interpreter=interpreter,
        diagnostics=diagnostics_options,
    )


def load_library_exports(path: Path) -> LibraryExports:
    """Read library-owned exports without applying project-only settings."""
    return _library_exports(_read_config_table(path))


def _read_config_table(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as config_file:
            raw_data: object = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError, UnicodeError) as error:
        raise ConfigError(f"Could not load {path}: {error}") from error
    table = _table(raw_data, str(path))
    if "tool" in table or "kivy-lsp" in table:
        raise ConfigError(
            f"{path}: put settings at the root of {CONFIG_FILENAME}; "
            "remove the [tool.kivy-lsp] wrapper and tool.kivy-lsp. "
            "prefixes from section names."
        )
    return table


def _library_exports(table: dict[str, object]) -> LibraryExports:
    def export_table(name: str) -> dict[str, object]:
        return _optional_table(table.get(name), name)

    return LibraryExports(
        globals=_string_map(export_table("globals"), "globals"),
        global_imports=_global_import_list(
            export_table("global-imports"), "global-imports",
        ),
        member_projections=_projection_map(
            export_table("member-projections"), "member-projections",
        ),
        subscript_projections=_projection_map(
            export_table("subscript-projections"), "subscript-projections",
        ),
    )


def _optional_path(
    root: Path, table: dict[str, object], key: str
) -> Path | None:
    value = _optional_string(table.get(key), key)
    if value is None:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    # Keep an interpreter's venv path even when the executable is a symlink.
    return path.absolute()


def _default_source_roots(
    project_root: Path,
) -> tuple[Path, ...]:
    source_root = project_root / "src"

    if source_root.is_dir():
        return (source_root.resolve(),)

    return (project_root,)


def _path_list(
    project_root: Path,
    value: object,
    *,
    default: tuple[Path, ...],
    name: str,
) -> tuple[Path, ...]:
    if value is None:
        return default

    if not isinstance(value, list):
        raise ConfigError(f"{name} must be an array of paths.")

    values = cast(list[object], value)
    paths: list[Path] = []

    for item in values:
        if not isinstance(item, str) or not item:
            raise ConfigError(f"{name} entries must be non-empty strings.")

        path = Path(item)

        if not path.is_absolute():
            path = project_root / path

        paths.append(path.resolve())

    if not paths:
        raise ConfigError(f"{name} cannot be empty.")

    return tuple(dict.fromkeys(paths))


def _string_list(
    value: object,
    *,
    default: tuple[str, ...],
    name: str,
) -> tuple[str, ...]:
    if value is None:
        return default

    if not isinstance(value, list):
        raise ConfigError(f"{name} must be an array of strings.")

    values = cast(list[object], value)
    result: list[str] = []

    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{name} entries must be non-empty strings.")

        result.append(item)

    return tuple(dict.fromkeys(result))


def _string_map(
    table: dict[str, object],
    name: str,
) -> dict[str, str]:
    result: dict[str, str] = {}

    for key, value in table.items():
        if not key.isidentifier():
            raise ConfigError(f"{name} contains an invalid name: {key!r}.")
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{name}.{key} must be a non-empty string.")

        result[key] = value

    return result


def _global_import_list(
    table: dict[str, object],
    name: str,
) -> tuple[GlobalImport, ...]:
    imports: list[GlobalImport] = []

    for import_name, target in table.items():
        if not import_name.isidentifier():
            raise ConfigError(
                f"{name} contains an invalid name: {import_name!r}."
            )

        if not isinstance(target, str) or not target.strip():
            raise ConfigError(
                f"{name}.{import_name} must be a non-empty string."
            )

        imports.append(
            GlobalImport(
                name=import_name,
                target=target,
            )
        )

    return tuple(imports)


def _projection_map(
    table: dict[str, object],
    name: str,
) -> dict[str, int]:
    result: dict[str, int] = {}

    for type_name, value in table.items():
        if not type_name.strip():
            raise ConfigError(f"{name} type names cannot be empty.")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(
                f"{name}.{type_name} must be a non-negative integer."
            )

        result[type_name] = value

    return result


def _projection_for(
    projections: dict[str, int],
    type_name: str,
) -> int | None:
    direct = projections.get(type_name)

    if direct is not None:
        return direct

    short_name = type_name.rsplit(".", maxsplit=1)[-1]

    return projections.get(short_name)


def _i18n_config(
    project_root: Path,
    value: object,
) -> I18nConfig | None:
    if value is None:
        return None

    table = _table(
        value,
        "i18n",
    )
    source_value = table.get("source")

    if not isinstance(source_value, str) or not source_value.strip():
        raise ConfigError(
            "i18n.source must be a non-empty path."
        )

    source = Path(source_value)

    if not source.is_absolute():
        source = project_root / source

    properties = _string_list(
        table.get("properties"),
        default=(
            "i18n_key",
            "hint_i18n_key",
        ),
        name="i18n.properties",
    )
    if not properties:
        raise ConfigError("i18n.properties cannot be empty.")

    for property_name in properties:
        if not property_name.isidentifier():
            raise ConfigError(
                "i18n.properties contains an "
                f"invalid name: {property_name!r}."
            )

    return I18nConfig(
        source=source.resolve(),
        properties=properties,
    )


def _optional_string(
    value: object,
    name: str,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name} must be a non-empty string.")

    return value


def _optional_table(
    value: object,
    name: str,
) -> dict[str, object]:
    if value is None:
        return {}

    return _table(value, name)


def _table(
    value: object,
    name: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a table.")

    raw_table = cast(dict[object, object], value)

    if not all(isinstance(key, str) for key in raw_table):
        raise ConfigError(f"{name} keys must be strings.")

    return cast(dict[str, object], raw_table)
