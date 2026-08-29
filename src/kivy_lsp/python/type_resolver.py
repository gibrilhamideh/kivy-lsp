# src/kivy_lsp/python/type_resolver.py

from __future__ import annotations

from dataclasses import dataclass

from kivy_lsp.config import ServerConfig
from kivy_lsp.model.symbol import (
    ClassSymbol,
    Symbol,
    SymbolKind,
)
from kivy_lsp.model.value_type import (
    UNKNOWN_TYPE,
    ValueType,
    ValueTypeKind,
    literal_type,
    value_type_from_annotation,
)
from kivy_lsp.python.index import PythonIndex

type ResolvedTypeCacheKey = tuple[
    str,
    str | None,
    str | None,
]


@dataclass(frozen=True, slots=True)
class ResolvedPythonType:
    """A parsed value type connected to the Python symbol index."""

    value_type: ValueType
    source_module: str | None
    class_symbol: ClassSymbol | None = None
    arguments: tuple[ResolvedPythonType, ...] = ()

    @property
    def name(self) -> str | None:
        if self.class_symbol is not None:
            return self.class_symbol.qualified_name

        return self.value_type.name

    @property
    def is_unknown(self) -> bool:
        return self.value_type.kind is ValueTypeKind.UNKNOWN

    @property
    def is_any(self) -> bool:
        return self.value_type.kind is ValueTypeKind.ANY

    @property
    def is_none(self) -> bool:
        return self.value_type.kind is ValueTypeKind.NONE

    @property
    def is_union(self) -> bool:
        return self.value_type.kind is ValueTypeKind.UNION


class PythonTypeResolver:
    """Resolve Python annotations, members, and generic projections."""

    def __init__(
        self,
        python_index: PythonIndex,
        config: ServerConfig,
    ) -> None:
        self._python_index = python_index
        self._config = config
        self._cache_revision = python_index.revision
        self._annotation_cache: dict[
            tuple[str | None, str | None],
            ResolvedPythonType,
        ] = {}
        self._members_cache: dict[
            ResolvedTypeCacheKey,
            tuple[Symbol, ...],
        ] = {}
        self._member_type_cache: dict[
            tuple[ResolvedTypeCacheKey, str],
            ResolvedPythonType | None,
        ] = {}
        self._member_return_type_cache: dict[
            tuple[ResolvedTypeCacheKey, str],
            ResolvedPythonType | None,
        ] = {}

    def resolve_annotation(
        self,
        annotation: str | None,
        *,
        from_module: str | None = None,
    ) -> ResolvedPythonType:
        """Parse and resolve an annotation from a source module."""
        self._ensure_current_revision()
        key = (
            annotation,
            from_module,
        )
        cached = self._annotation_cache.get(key)

        if cached is not None:
            return cached

        if annotation is None:
            value_type = UNKNOWN_TYPE
        else:
            value_type = value_type_from_annotation(annotation)

        resolved = self._resolve_value_type(
            value_type,
            from_module,
        )
        self._annotation_cache[key] = resolved
        return resolved

    def type_of_symbol(
        self,
        symbol: Symbol,
    ) -> ResolvedPythonType:
        """Resolve the value type represented by a symbol."""
        source_module = self.module_name_for_symbol(symbol)

        if symbol.kind is SymbolKind.CLASS:
            return self.resolve_annotation(
                symbol.qualified_name,
                from_module=source_module,
            )

        return self.resolve_annotation(
            symbol.annotation,
            from_module=source_module,
        )

    def return_type_of_symbol(
        self,
        symbol: Symbol,
    ) -> ResolvedPythonType:
        """Resolve the return type of a function or method."""
        source_module = self.module_name_for_symbol(symbol)

        return self.resolve_annotation(
            symbol.return_annotation,
            from_module=source_module,
        )

    def members_of(
        self,
        resolved_type: ResolvedPythonType,
    ) -> tuple[Symbol, ...]:
        """Return safe members exposed by a resolved value type."""
        self._ensure_current_revision()
        key = _resolved_type_cache_key(resolved_type)
        cached = self._members_cache.get(key)

        if cached is not None:
            return cached

        members = self._members_of(
            resolved_type,
            set(),
        )
        self._members_cache[key] = members
        return members

    def member_named(
        self,
        resolved_type: ResolvedPythonType,
        name: str,
    ) -> Symbol | None:
        """Return a named member exposed by a resolved value type."""
        for member in self.members_of(resolved_type):
            if member.name == name:
                return member

        return None

    def member_definitions(
        self,
        resolved_type: ResolvedPythonType,
        name: str,
    ) -> tuple[Symbol, ...]:
        """Return every declaration represented by a member access."""
        if resolved_type.is_union:
            definitions: list[Symbol] = []

            for branch in resolved_type.arguments:
                if branch.is_none:
                    continue

                definitions.extend(
                    self.member_definitions(branch, name)
                )

            return _deduplicate_symbols(definitions)

        member = self.member_named(resolved_type, name)

        if member is None:
            return ()

        return (member,)

    def member_type(
        self,
        resolved_type: ResolvedPythonType,
        name: str,
    ) -> ResolvedPythonType | None:
        """Resolve the effective value type of a named member."""
        self._ensure_current_revision()
        key = (
            _resolved_type_cache_key(resolved_type),
            name,
        )

        if key in self._member_type_cache:
            return self._member_type_cache[key]

        member = self.member_named(
            resolved_type,
            name,
        )

        if member is None:
            self._member_type_cache[key] = None
            return None

        member_type = self.type_of_symbol(member)
        owner_type = self._owner_type_for_member(
            resolved_type,
            member,
            set(),
        )

        if owner_type is not None:
            member_type = self._substitute_type_parameters(
                member_type,
                self._type_parameter_bindings(
                    owner_type,
                ),
            )

        descriptor_type = self._descriptor_value_type(
            member_type,
        )

        if descriptor_type is not None:
            self._member_type_cache[key] = descriptor_type
            return descriptor_type

        self._member_type_cache[key] = member_type
        return member_type

    def member_return_type(
        self,
        resolved_type: ResolvedPythonType,
        name: str,
    ) -> ResolvedPythonType | None:
        """Resolve the return type of a named method."""
        self._ensure_current_revision()
        key = (
            _resolved_type_cache_key(resolved_type),
            name,
        )

        if key in self._member_return_type_cache:
            return self._member_return_type_cache[key]

        member = self.member_named(
            resolved_type,
            name,
        )

        if member is None:
            self._member_return_type_cache[key] = None
            return None

        return_type = self.return_type_of_symbol(member)
        owner_type = self._owner_type_for_member(
            resolved_type,
            member,
            set(),
        )

        if owner_type is None:
            self._member_return_type_cache[key] = return_type
            return return_type

        resolved_return_type = self._substitute_type_parameters(
            return_type,
            self._type_parameter_bindings(owner_type),
        )
        self._member_return_type_cache[key] = resolved_return_type
        return resolved_return_type

    def _ensure_current_revision(self) -> None:
        revision = self._python_index.revision

        if revision == self._cache_revision:
            return

        self._cache_revision = revision
        self._annotation_cache.clear()
        self._members_cache.clear()
        self._member_type_cache.clear()
        self._member_return_type_cache.clear()

    def member_projection(
        self,
        resolved_type: ResolvedPythonType,
    ) -> ResolvedPythonType | None:
        """Return the type whose members a generic wrapper exposes."""
        argument_index = self._projection_index(
            resolved_type,
            member=True,
        )

        if argument_index is None:
            return None

        if argument_index >= len(resolved_type.arguments):
            return None

        return resolved_type.arguments[argument_index]

    def subscript_result(
        self,
        resolved_type: ResolvedPythonType,
    ) -> ResolvedPythonType | None:
        """Return the type produced by indexing a value."""
        if resolved_type.is_union:
            return self._union_subscript_result(
                resolved_type,
            )

        argument_index = self._projection_index(
            resolved_type,
            member=False,
        )

        if argument_index is not None:
            if argument_index >= len(resolved_type.arguments):
                return None

            return resolved_type.arguments[argument_index]

        kind = resolved_type.value_type.kind

        if kind in {
            ValueTypeKind.LIST,
            ValueTypeKind.SEQUENCE,
            ValueTypeKind.SET,
        }:
            return self._argument_at(
                resolved_type,
                0,
            )

        if kind is ValueTypeKind.DICT:
            return self._argument_at(
                resolved_type,
                1,
            )

        if kind is ValueTypeKind.TUPLE:
            return self._tuple_item_type(
                resolved_type,
            )

        return None

    def module_name_for_symbol(
        self,
        symbol: Symbol,
    ) -> str | None:
        """Return the module that owns a source symbol."""
        return self._python_index.module_name_for_symbol(symbol)

    def _resolve_value_type(
        self,
        value_type: ValueType,
        from_module: str | None,
    ) -> ResolvedPythonType:
        arguments = tuple(
            self._resolve_value_type(
                argument,
                from_module,
            )
            for argument in value_type.arguments
        )
        class_symbol = None
        source_module = from_module

        if (
            value_type.kind is ValueTypeKind.OBJECT
            and value_type.name is not None
        ):
            class_symbol = self._python_index.resolve_class(
                value_type.name,
                from_module=from_module,
            )

            if class_symbol is not None:
                class_module = self._python_index.module_for_class(
                    class_symbol,
                )

                if class_module is not None:
                    source_module = class_module.name
            else:
                literal_alias = self._literal_alias_type(
                    value_type.name,
                    from_module,
                )

                if literal_alias is not None:
                    return literal_alias

        return ResolvedPythonType(
            value_type=value_type,
            source_module=source_module,
            class_symbol=class_symbol,
            arguments=arguments,
        )

    def _literal_alias_type(
        self,
        reference: str,
        from_module: str | None,
    ) -> ResolvedPythonType | None:
        symbol = self._python_index.resolve_symbol(
            reference,
            from_module=from_module,
        )

        if symbol is None or not symbol.literal_values:
            return None

        source_module = self.module_name_for_symbol(
            symbol,
        )

        return self._resolve_value_type(
            literal_type(
                *symbol.literal_values,
            ),
            source_module,
        )

    def _members_of(
        self,
        resolved_type: ResolvedPythonType,
        visited: set[tuple[str, str | None]],
    ) -> tuple[Symbol, ...]:
        if resolved_type.is_unknown or resolved_type.is_any:
            return ()

        if resolved_type.is_none:
            return ()

        if resolved_type.is_union:
            return self._union_members(
                resolved_type,
                visited,
            )

        key = (
            resolved_type.value_type.display,
            resolved_type.source_module,
        )

        if key in visited:
            return ()

        visited.add(key)

        try:
            direct_members = self._direct_members(
                resolved_type,
            )
            projection = self.member_projection(
                resolved_type,
            )

            if projection is None:
                return direct_members

            projected_members = self._members_of(
                projection,
                visited,
            )

            return _merge_members(
                projected_members,
                direct_members,
            )
        finally:
            visited.remove(key)

    def _direct_members(
        self,
        resolved_type: ResolvedPythonType,
    ) -> tuple[Symbol, ...]:
        class_symbol = resolved_type.class_symbol

        if class_symbol is None:
            return ()

        return self._python_index.members_of(
            class_symbol,
        )

    def _owner_type_for_member(
        self,
        resolved_type: ResolvedPythonType,
        member: Symbol,
        visited: set[tuple[str, str | None]],
    ) -> ResolvedPythonType | None:
        key = (
            resolved_type.value_type.display,
            resolved_type.source_module,
        )

        if key in visited:
            return None

        visited.add(key)

        try:
            class_symbol = resolved_type.class_symbol
            member_owner = member.qualified_name.rsplit(
                ".",
                maxsplit=1,
            )[0]

            if (
                class_symbol is not None
                and class_symbol.qualified_name == member_owner
            ):
                return resolved_type

            projection = self.member_projection(resolved_type)

            if projection is not None:
                owner_type = self._owner_type_for_member(
                    projection,
                    member,
                    visited,
                )

                if owner_type is not None:
                    return owner_type

            for base_type in self._base_types(resolved_type):
                owner_type = self._owner_type_for_member(
                    base_type,
                    member,
                    visited,
                )

                if owner_type is not None:
                    return owner_type

            return None
        finally:
            visited.remove(key)

    def _base_types(
        self,
        resolved_type: ResolvedPythonType,
    ) -> tuple[ResolvedPythonType, ...]:
        class_symbol = resolved_type.class_symbol

        if class_symbol is None:
            return ()

        class_module = self._python_index.module_for_class(
            class_symbol,
        )
        module_name = (
            class_module.name
            if class_module is not None
            else resolved_type.source_module
        )
        bindings = self._type_parameter_bindings(
            resolved_type,
        )
        base_types: list[ResolvedPythonType] = []

        for base_reference in class_symbol.bases:
            base_type = self.resolve_annotation(
                base_reference,
                from_module=module_name,
            )
            base_type = self._substitute_type_parameters(
                base_type,
                bindings,
            )

            if base_type.class_symbol is not None:
                base_types.append(base_type)

        return tuple(base_types)

    def _type_parameter_bindings(
        self,
        resolved_type: ResolvedPythonType,
    ) -> dict[str, ResolvedPythonType]:
        class_symbol = resolved_type.class_symbol

        if class_symbol is None or not resolved_type.arguments:
            return {}

        parameter_names: list[str] = []

        for base_reference in class_symbol.bases:
            base_type = value_type_from_annotation(
                base_reference,
            )

            if (
                base_type.kind is not ValueTypeKind.OBJECT
                or base_type.name is None
                or base_type.name.rsplit(".", 1)[-1] != "Generic"
            ):
                continue

            for argument in base_type.arguments:
                if (
                    argument.kind is ValueTypeKind.OBJECT
                    and argument.name is not None
                    and not argument.arguments
                    and argument.name not in parameter_names
                ):
                    parameter_names.append(argument.name)

        return dict(
            zip(
                parameter_names,
                resolved_type.arguments,
                strict=False,
            )
        )

    def _substitute_type_parameters(
        self,
        resolved_type: ResolvedPythonType,
        bindings: dict[str, ResolvedPythonType],
    ) -> ResolvedPythonType:
        value_type = resolved_type.value_type

        if (
            value_type.kind is ValueTypeKind.OBJECT
            and value_type.name is not None
            and not value_type.arguments
        ):
            replacement = bindings.get(value_type.name)

            if replacement is not None:
                return replacement

        arguments = tuple(
            self._substitute_type_parameters(
                argument,
                bindings,
            )
            for argument in resolved_type.arguments
        )

        if arguments == resolved_type.arguments:
            return resolved_type

        substituted_value_type = ValueType(
            kind=value_type.kind,
            name=value_type.name,
            arguments=tuple(
                argument.value_type
                for argument in arguments
            ),
            literals=value_type.literals,
        )

        return ResolvedPythonType(
            value_type=substituted_value_type,
            source_module=resolved_type.source_module,
            class_symbol=resolved_type.class_symbol,
            arguments=arguments,
        )

    def _union_members(
        self,
        resolved_type: ResolvedPythonType,
        visited: set[tuple[str, str | None]],
    ) -> tuple[Symbol, ...]:
        branches = tuple(
            argument
            for argument in resolved_type.arguments
            if not argument.is_none
        )

        if not branches:
            return ()

        if len(branches) == 1:
            return self._members_of(
                branches[0],
                visited,
            )

        branch_members = [
            self._members_of(
                branch,
                visited,
            )
            for branch in branches
        ]

        if not branch_members:
            return ()

        first_members = branch_members[0]
        common_names = {
            member.name
            for member in first_members
        }

        for members in branch_members[1:]:
            common_names.intersection_update(
                member.name
                for member in members
            )

        return tuple(
            member
            for member in first_members
            if member.name in common_names
        )

    def _union_subscript_result(
        self,
        resolved_type: ResolvedPythonType,
    ) -> ResolvedPythonType | None:
        branches = tuple(
            branch
            for branch in resolved_type.arguments
            if not branch.is_none
        )

        if len(branches) != 1:
            return None

        return self.subscript_result(branches[0])

    def _projection_index(
        self,
        resolved_type: ResolvedPythonType,
        *,
        member: bool,
    ) -> int | None:
        names: list[str] = []

        if resolved_type.class_symbol is not None:
            names.append(
                resolved_type.class_symbol.qualified_name
            )

        if resolved_type.value_type.name is not None:
            names.append(
                resolved_type.value_type.name
            )

        for type_name in names:
            if member:
                result = self._config.member_projection_for(
                    type_name,
                )
            else:
                result = self._config.subscript_projection_for(
                    type_name,
                )

            if result is not None:
                return result

        return None

    @staticmethod
    def _argument_at(
        resolved_type: ResolvedPythonType,
        index: int,
    ) -> ResolvedPythonType | None:
        if index >= len(resolved_type.arguments):
            return None

        return resolved_type.arguments[index]

    @staticmethod
    def _tuple_item_type(
        resolved_type: ResolvedPythonType,
    ) -> ResolvedPythonType | None:
        arguments = resolved_type.arguments

        if not arguments:
            return None

        first = arguments[0]

        if all(
            argument.value_type == first.value_type
            for argument in arguments
        ):
            return first

        return None

    def _descriptor_value_type(
        self,
        resolved_type: ResolvedPythonType,
    ) -> ResolvedPythonType | None:
        """
        Resolve the value returned by a Python descriptor.
        """
        result = self.member_return_type(
            resolved_type,
            "__get__",
        )

        if result is None or result.is_unknown:
            return None

        return result


def _resolved_type_cache_key(
    resolved_type: ResolvedPythonType,
) -> ResolvedTypeCacheKey:
    class_name = (
        resolved_type.class_symbol.qualified_name
        if resolved_type.class_symbol is not None
        else None
    )

    return (
        resolved_type.value_type.display,
        resolved_type.source_module,
        class_name,
    )


def _merge_members(
    primary: tuple[Symbol, ...],
    secondary: tuple[Symbol, ...],
) -> tuple[Symbol, ...]:
    members: dict[str, Symbol] = {}

    for member in primary:
        members.setdefault(
            member.name,
            member,
        )

    for member in secondary:
        members.setdefault(
            member.name,
            member,
        )

    return tuple(members.values())


def _deduplicate_symbols(
    symbols: list[Symbol],
) -> tuple[Symbol, ...]:
    unique: dict[tuple[str, str, int], Symbol] = {}

    for symbol in symbols:
        key = (
            symbol.qualified_name,
            symbol.uri,
            symbol.selection_span.start,
        )
        unique.setdefault(key, symbol)

    return tuple(unique.values())

