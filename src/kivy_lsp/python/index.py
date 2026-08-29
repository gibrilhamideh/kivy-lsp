# src/kivy_lsp/python/index.py

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from kivy_lsp.model.symbol import ClassSymbol, Symbol
from kivy_lsp.python.module import (
    FactoryRegistration,
    ImportBinding,
    PythonModule,
)


class PythonIndex:
    """A searchable collection of statically indexed Python modules."""

    def __init__(self) -> None:
        self._modules: dict[str, PythonModule] = {}
        self._classes: dict[str, ClassSymbol] = {}
        self._classes_by_name: dict[
            str,
            list[ClassSymbol],
        ] = {}
        self._symbols: dict[str, Symbol] = {}

        self._class_modules: dict[str, str] = {}
        self._symbol_modules: dict[str, str] = {}
        self._uri_modules: dict[str, str] = {}

        self._factory_registrations: dict[
            str,
            list[FactoryRegistration],
        ] = {}
        self._factory_registration_modules: dict[
            FactoryRegistration,
            str,
        ] = {}

        self._widget_classes: tuple[ClassSymbol, ...] = ()
        self._widget_classes_revision = -1
        self._members_cache: dict[
            tuple[str, bool],
            tuple[Symbol, ...],
        ] = {}
        self._resolution_candidates_cache: dict[
            tuple[str, str | None],
            tuple[str, ...],
        ] = {}
        self._revision = 0

    @property
    def revision(self) -> int:
        """Return a number that changes whenever the index changes."""

        return self._revision

    @property
    def modules(self) -> tuple[PythonModule, ...]:
        return tuple(self._modules.values())

    @property
    def classes(self) -> tuple[ClassSymbol, ...]:
        return tuple(self._classes.values())

    @property
    def symbols(self) -> tuple[Symbol, ...]:
        return tuple(self._symbols.values())

    @property
    def factory_registrations(
        self,
    ) -> tuple[FactoryRegistration, ...]:
        return tuple(
            registration
            for module in self._modules.values()
            for registration in module.factory_registrations
        )

    def clear(self) -> None:
        if not self._modules:
            return

        self._modules.clear()
        self._classes.clear()
        self._classes_by_name.clear()
        self._symbols.clear()
        self._class_modules.clear()
        self._symbol_modules.clear()
        self._uri_modules.clear()
        self._factory_registrations.clear()
        self._factory_registration_modules.clear()
        self._members_cache.clear()
        self._resolution_candidates_cache.clear()
        self._revision += 1

    def replace(self, module: PythonModule) -> None:
        """Insert a module or replace its previous indexed version."""

        self._drop_module(module.name)
        self._modules[module.name] = module
        self._uri_modules[module.uri] = module.name
        self._index_module(module)
        self._members_cache.clear()
        self._resolution_candidates_cache.clear()
        self._revision += 1

    def remove(self, module_name: str) -> PythonModule | None:
        """Remove a module and every symbol owned by it."""

        module = self._drop_module(module_name)

        if module is not None:
            self._members_cache.clear()
            self._resolution_candidates_cache.clear()
            self._revision += 1

        return module

    def module_named(self, name: str) -> PythonModule | None:
        return self._modules.get(name)

    def class_named(
        self,
        qualified_name: str,
    ) -> ClassSymbol | None:
        return self._classes.get(qualified_name)

    def classes_named(
        self,
        name: str,
    ) -> tuple[ClassSymbol, ...]:
        """Return every class with the given unqualified name."""

        return tuple(self._classes_by_name.get(name, ()))

    def symbol_named(
        self,
        qualified_name: str,
    ) -> Symbol | None:
        return self._symbols.get(qualified_name)

    def factory_registrations_named(
        self,
        name: str,
    ) -> tuple[FactoryRegistration, ...]:
        """Return all Factory registrations with a given name."""

        return tuple(
            self._factory_registrations.get(
                name,
                (),
            ),
        )

    def module_for_class(
        self,
        class_symbol: ClassSymbol,
    ) -> PythonModule | None:
        qualified_name = class_symbol.symbol.qualified_name
        module_name = self._class_modules.get(qualified_name)

        if module_name is None:
            return None

        return self._modules.get(module_name)

    def module_name_for_symbol(
        self,
        symbol: Symbol,
    ) -> str | None:
        """Return the indexed module that owns a symbol."""
        module_name = self._symbol_modules.get(
            symbol.qualified_name,
        )

        if module_name is not None:
            return module_name

        return self._uri_modules.get(symbol.uri)

    def module_for_factory_registration(
        self,
        registration: FactoryRegistration,
    ) -> PythonModule | None:
        module_name = self._factory_registration_modules.get(
            registration,
        )

        if module_name is None:
            return None

        return self._modules.get(module_name)

    def resolve_class(
        self,
        reference: str,
        *,
        from_module: str | None = None,
    ) -> ClassSymbol | None:
        """Resolve a class reference from an optional source module."""

        for candidate in self._resolution_candidates(
            reference,
            from_module,
        ):
            class_symbol = self._classes.get(candidate)

            if class_symbol is not None:
                return class_symbol

        return None

    def resolve_symbol(
        self,
        reference: str,
        *,
        from_module: str | None = None,
    ) -> Symbol | None:
        """Resolve a variable, function, class, or other symbol."""

        for candidate in self._resolution_candidates(
            reference,
            from_module,
        ):
            symbol = self._symbols.get(candidate)

            if symbol is not None:
                return symbol

        return None

    def resolve_factory_class(
        self,
        registration: FactoryRegistration,
    ) -> ClassSymbol | None:
        """Resolve the Python class behind a Factory registration."""

        module = self.module_for_factory_registration(
            registration,
        )
        source_module = (
            module.name
            if module is not None
            else None
        )

        if registration.class_reference is not None:
            class_symbol = self.resolve_class(
                registration.class_reference,
                from_module=source_module,
            )

            if class_symbol is not None:
                return class_symbol

        if registration.module_name is not None:
            class_symbol = self.resolve_class(
                registration.name,
                from_module=registration.module_name,
            )

            if class_symbol is not None:
                return class_symbol

        for base_reference in registration.baseclasses:
            class_symbol = self.resolve_class(
                base_reference,
                from_module=source_module,
            )

            if class_symbol is not None:
                return class_symbol

        return None

    def widget_classes(self) -> tuple[ClassSymbol, ...]:
        """Return every indexed class derived from Kivy Widget."""

        if self._widget_classes_revision == self._revision:
            return self._widget_classes

        memo: dict[str, bool] = {}
        widget_classes = tuple(
            class_symbol
            for class_symbol in self._classes.values()
            if self._is_widget_class(
                class_symbol,
                memo,
                set(),
            )
        )

        self._widget_classes = tuple(
            sorted(
                widget_classes,
                key=lambda class_symbol: (
                    class_symbol.symbol.name.casefold(),
                    class_symbol.symbol.qualified_name,
                ),
            ),
        )
        self._widget_classes_revision = self._revision

        return self._widget_classes

    def members_of(
        self,
        class_reference: ClassSymbol | str,
        *,
        from_module: str | None = None,
        include_inherited: bool = True,
    ) -> tuple[Symbol, ...]:
        """Return class members with child definitions taking priority."""

        if isinstance(class_reference, str):
            class_symbol = self.resolve_class(
                class_reference,
                from_module=from_module,
            )
        else:
            class_symbol = class_reference

        if class_symbol is None:
            return ()

        cache_key = (
            class_symbol.qualified_name,
            include_inherited,
        )
        cached = self._members_cache.get(cache_key)

        if cached is not None:
            return cached

        members: dict[str, Symbol] = {}
        visited: set[str] = set()

        self._collect_members(
            class_symbol,
            members,
            visited,
            include_inherited=include_inherited,
        )

        result = tuple(members.values())
        self._members_cache[cache_key] = result
        return result

    def member_named(
        self,
        class_reference: ClassSymbol | str,
        member_name: str,
        *,
        from_module: str | None = None,
        include_inherited: bool = True,
    ) -> Symbol | None:
        for member in self.members_of(
            class_reference,
            from_module=from_module,
            include_inherited=include_inherited,
        ):
            if member.name == member_name:
                return member

        return None

    def _index_module(self, module: PythonModule) -> None:
        for symbol in module.symbol.symbols:
            self._add_symbol(symbol, module.name)

        for class_symbol in module.symbol.classes:
            symbol = class_symbol.symbol
            qualified_name = symbol.qualified_name

            self._classes[qualified_name] = class_symbol
            self._class_modules[qualified_name] = module.name
            bucket = self._classes_by_name.setdefault(
                symbol.name,
                [],
            )
            bucket.append(class_symbol)
            self._add_symbol(symbol, module.name)

        for registration in module.factory_registrations:
            registrations = self._factory_registrations.setdefault(
                registration.name,
                [],
            )
            registrations.append(registration)
            self._factory_registration_modules[
                registration
            ] = module.name

    def _add_symbol(
        self,
        symbol: Symbol,
        module_name: str,
    ) -> None:
        qualified_name = symbol.qualified_name
        self._symbols[qualified_name] = symbol
        self._symbol_modules[qualified_name] = module_name

    def _drop_module(
        self,
        module_name: str,
    ) -> PythonModule | None:
        module = self._modules.pop(module_name, None)

        if module is None:
            return None

        if self._uri_modules.get(module.uri) == module_name:
            self._uri_modules.pop(module.uri, None)

        class_names = tuple(
            qualified_name
            for qualified_name, owner in self._class_modules.items()
            if owner == module_name
        )

        for qualified_name in class_names:
            class_symbol = self._classes.pop(
                qualified_name,
                None,
            )
            self._class_modules.pop(qualified_name, None)

            if class_symbol is None:
                continue

            bucket = self._classes_by_name.get(
                class_symbol.name,
            )

            if bucket is None:
                continue

            bucket.remove(class_symbol)

            if not bucket:
                del self._classes_by_name[class_symbol.name]

        symbol_names = tuple(
            qualified_name
            for qualified_name, owner in self._symbol_modules.items()
            if owner == module_name
        )

        for qualified_name in symbol_names:
            self._symbols.pop(qualified_name, None)
            self._symbol_modules.pop(qualified_name, None)

        for registration in module.factory_registrations:
            registrations = self._factory_registrations.get(
                registration.name,
            )

            if registrations is not None:
                registrations.remove(registration)

                if not registrations:
                    del self._factory_registrations[
                        registration.name
                    ]

            self._factory_registration_modules.pop(
                registration,
                None,
            )

        return module

    def _resolution_candidates(
        self,
        reference: str,
        from_module: str | None,
    ) -> tuple[str, ...]:
        reference = _normalize_reference(reference)

        if not reference:
            return ()

        cache_key = (
            reference,
            from_module,
        )
        cached = self._resolution_candidates_cache.get(
            cache_key,
        )

        if cached is not None:
            return cached

        candidates: list[str] = []
        _append_unique(candidates, reference)

        if from_module is not None:
            _append_unique(
                candidates,
                _qualified_name(from_module, reference),
            )

            current_module = self._modules.get(from_module)

            if current_module is not None:
                for target in self._import_targets(
                    reference,
                    current_module,
                ):
                    _append_unique(candidates, target)

        candidate_index = 0

        while candidate_index < len(candidates):
            candidate = candidates[candidate_index]
            candidate_index += 1

            module_and_reference = self._module_reference(
                candidate,
            )

            if module_and_reference is None:
                continue

            current_module, local_reference = (
                module_and_reference
            )

            for target in self._import_targets(
                local_reference,
                current_module,
            ):
                _append_unique(candidates, target)

        result = tuple(candidates)
        self._resolution_candidates_cache[cache_key] = result
        return result

    def _module_reference(
        self,
        reference: str,
    ) -> tuple[PythonModule, str] | None:
        parts = reference.split(".")

        for index in range(len(parts) - 1, 0, -1):
            module_name = ".".join(parts[:index])
            module = self._modules.get(module_name)

            if module is None:
                continue

            local_reference = ".".join(parts[index:])
            return module, local_reference

        return None

    def _import_targets(
        self,
        reference: str,
        current_module: PythonModule,
    ) -> tuple[str, ...]:
        local_name, separator, remainder = reference.partition(".")
        targets: list[str] = []

        for binding in current_module.imports:
            if binding.local_name not in {local_name, "*"}:
                continue

            import_module = self._absolute_import_module(
                binding,
                current_module,
            )

            if import_module is None:
                continue

            if binding.local_name == "*":
                target = _qualified_name(
                    import_module,
                    local_name,
                )
            elif binding.target_name is not None:
                target = _qualified_name(
                    import_module,
                    binding.target_name,
                )
            else:
                imported_root = import_module.partition(".")[0]
                target = (
                    local_name
                    if binding.local_name == imported_root
                    else import_module
                )

            if separator:
                target = _qualified_name(target, remainder)

            _append_unique(targets, target)

        return tuple(targets)

    def _absolute_import_module(
        self,
        binding: ImportBinding,
        current_module: PythonModule,
    ) -> str | None:
        if binding.relative_level == 0:
            return binding.target_module

        module_parts = current_module.name.split(".")

        if not _is_package_module(current_module):
            module_parts = module_parts[:-1]

        parent_count = binding.relative_level - 1

        if parent_count > len(module_parts):
            return None

        if parent_count:
            module_parts = module_parts[:-parent_count]

        if binding.target_module:
            module_parts.extend(binding.target_module.split("."))

        return ".".join(module_parts)

    def _collect_members(
        self,
        class_symbol: ClassSymbol,
        members: dict[str, Symbol],
        visited: set[str],
        *,
        include_inherited: bool,
    ) -> None:
        qualified_name = class_symbol.symbol.qualified_name

        if qualified_name in visited:
            return

        visited.add(qualified_name)

        for member in class_symbol.members:
            members.setdefault(member.name, member)

        if not include_inherited:
            return

        module_name = self._class_modules.get(qualified_name)

        for base_reference in class_symbol.bases:
            base_class = self.resolve_class(
                base_reference,
                from_module=module_name,
            )

            if base_class is None:
                continue

            self._collect_members(
                base_class,
                members,
                visited,
                include_inherited=True,
            )

    def _is_widget_class(
        self,
        class_symbol: ClassSymbol,
        memo: dict[str, bool],
        visiting: set[str],
    ) -> bool:
        symbol = class_symbol.symbol
        qualified_name = symbol.qualified_name

        cached = memo.get(qualified_name)

        if cached is not None:
            return cached

        if qualified_name in visiting:
            return False

        if _is_widget_reference(qualified_name):
            memo[qualified_name] = True
            return True

        visiting.add(qualified_name)
        module_name = self._class_modules.get(qualified_name)

        for base_reference in class_symbol.bases:
            base_class = self.resolve_class(
                base_reference,
                from_module=module_name,
            )

            if base_class is not None:
                if self._is_widget_class(
                    base_class,
                    memo,
                    visiting,
                ):
                    visiting.remove(qualified_name)
                    memo[qualified_name] = True
                    return True

                continue

            if _is_widget_reference(base_reference):
                visiting.remove(qualified_name)
                memo[qualified_name] = True
                return True

        visiting.remove(qualified_name)
        memo[qualified_name] = False
        return False


def _normalize_reference(reference: str) -> str:
    reference = "".join(reference.split())
    generic_start = reference.find("[")

    if generic_start != -1:
        reference = reference[:generic_start]

    return reference


def _qualified_name(prefix: str, name: str) -> str:
    if not prefix:
        return name

    if not name:
        return prefix

    return f"{prefix}.{name}"


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _is_package_module(module: PythonModule) -> bool:
    parsed = urlparse(module.uri)

    path = unquote(parsed.path) if parsed.scheme else module.uri

    filename = PurePosixPath(
        path.replace("\\", "/"),
    ).name

    return filename in {
        "__init__.py",
        "__init__.pyi",
    }


def _is_widget_reference(reference: str) -> bool:
    reference = _normalize_reference(reference)

    if not reference:
        return False

    return reference.rsplit(".", 1)[-1] == "Widget"
