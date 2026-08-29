# src/kivy_lsp/analysis/completion.py

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import StrEnum

from kivy_lsp.analysis.call_argument_context import (
    KvCallArgumentContext,
    call_argument_context_at,
)
from kivy_lsp.analysis.completion_context import (
    KvCompletionTarget,
    KvCompletionTargetKind,
    completion_target_at,
)
from kivy_lsp.analysis.expression import (
    KvExpressionResolver,
    KvResolutionKind,
)
from kivy_lsp.analysis.property_resolution import (
    KivyPropertyResolver,
)
from kivy_lsp.analysis.property_value_completion import (
    KvPropertyValueCompleter,
    KvPropertyValueSuggestion,
)
from kivy_lsp.analysis.property_value_context import (
    property_value_context_at,
)
from kivy_lsp.analysis.scope import (
    KvBinding,
    KvBindingKind,
    KvScope,
    KvSemanticModel,
    KvValue,
    KvValueKind,
)
from kivy_lsp.config import ServerConfig
from kivy_lsp.kv.context import context_at
from kivy_lsp.kv.index import KvClassSymbol, KvIndex
from kivy_lsp.kv.parser import ParseResult
from kivy_lsp.model.symbol import (
    ClassSymbol,
    ParameterKind,
    ParameterSymbol,
    Symbol,
    SymbolKind,
)
from kivy_lsp.model.value_type import (
    LiteralValue,
    ValueType,
    ValueTypeKind,
)
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.module import FactoryRegistration
from kivy_lsp.workspace.document import TextDocument

_BUILTIN_NAMES = (
    "False",
    "None",
    "True",
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "filter",
    "float",
    "int",
    "len",
    "list",
    "map",
    "max",
    "min",
    "range",
    "reversed",
    "round",
    "set",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
)

_KV_SPECIAL_NAMES = (
    "id",
    "canvas",
    "canvas.before",
    "canvas.after",
)


class KvCompletionKind(StrEnum):
    """An editor-neutral completion item category."""

    CLASS = "class"
    METHOD = "method"
    FUNCTION = "function"
    PROPERTY = "property"
    VARIABLE = "variable"
    CONSTANT = "constant"
    EVENT = "event"
    ID = "id"
    MODULE = "module"
    KEYWORD = "keyword"


@dataclass(frozen=True, slots=True)
class KvCompletionItem:
    """A completion suggestion independent of the LSP protocol."""

    label: str
    kind: KvCompletionKind
    insert_text: str
    sort_text: str
    detail: str | None = None
    documentation: str | None = None
    symbol: Symbol | None = None


@dataclass(frozen=True, slots=True)
class KvCompletionResult:
    """Completion suggestions and their source replacement target."""

    target: KvCompletionTarget
    items: tuple[KvCompletionItem, ...]
    is_incomplete: bool = False


class KvCompletionEngine:
    """Produce semantic completions for KV documents."""

    def __init__(
        self,
        python_index: PythonIndex,
        kv_index: KvIndex | None = None,
        config: ServerConfig | None = None,
    ) -> None:
        self._python_index = python_index
        self._kv_index = (
            kv_index
            if kv_index is not None
            else KvIndex()
        )
        self._resolver = KvExpressionResolver(
            python_index,
            config,
        )
        self._property_resolver = KivyPropertyResolver(
            python_index,
        )
        self._property_value_completer = (
            KvPropertyValueCompleter()
        )

    def complete(
        self,
        document: TextDocument,
        parse_result: ParseResult,
        semantic_model: KvSemanticModel,
        offset: int,
    ) -> KvCompletionResult | None:
        target = completion_target_at(
            document,
            parse_result,
            offset,
        )

        if target is None:
            return None

        if target.is_widget:
            return KvCompletionResult(
                target=target,
                items=self._complete_widgets(target),
            )

        scope = semantic_model.scope_at(offset)

        if scope is None:
            items = (
                self._complete_widgets(target)
                if target.is_structure
                else ()
            )

            return KvCompletionResult(
                target=target,
                items=items,
            )

        context = context_at(
            parse_result,
            offset,
        )
        self_value = self._resolver.self_value(
            document,
            scope,
            context.current_widget,
        )
        property_value = self._resolver.self_value(
            document,
            scope,
            context.property_owner,
        )
        expression_start = (
            context.expression.span.start
            if context.expression is not None
            else None
        )

        target, argument_items, argument_is_quoted = (
            self._complete_call_arguments(
                document,
                target,
                scope,
                self_value,
                expression_start,
            )
        )

        if argument_is_quoted:
            return KvCompletionResult(
                target=target,
                items=argument_items,
            )

        target, value_items, value_is_quoted = (
            self._complete_property_values(
                document,
                target,
                property_value,
                expression_start,
            )
        )

        if value_is_quoted:
            return KvCompletionResult(
                target=target,
                items=value_items,
            )

        if target.kind is KvCompletionTargetKind.NAME:
            items = self._complete_names(
                scope,
                self_value,
                target.prefix,
            )
        elif target.kind is KvCompletionTargetKind.MEMBER:
            items = self._complete_members(
                scope,
                self_value,
                target,
            )
        elif target.kind is KvCompletionTargetKind.PROPERTY:
            items = self._complete_properties(
                property_value,
                target.prefix,
            )
        elif target.kind is KvCompletionTargetKind.STRUCTURE:
            items = _deduplicate_and_sort(
                [
                    *self._complete_widgets(target),
                    *self._complete_properties(
                        property_value,
                        target.prefix,
                    ),
                ]
            )
        else:
            items = ()

        if value_items:
            items = _deduplicate_and_sort(
                [
                    *value_items,
                    *items,
                ]
            )

        if argument_items:
            items = _deduplicate_and_sort(
                [
                    *argument_items,
                    *items,
                ]
            )

        return KvCompletionResult(
            target=target,
            items=items,
        )

    def _complete_names(
        self,
        scope: KvScope,
        self_value: KvValue,
        prefix: str,
    ) -> tuple[KvCompletionItem, ...]:
        self_binding = KvBinding(
            name="self",
            kind=KvBindingKind.SELF,
            value=self_value,
        )
        items: list[KvCompletionItem] = []

        for binding in scope.visible_bindings(
            self_binding=self_binding,
        ):
            if not _matches_prefix(
                binding.name,
                prefix,
            ):
                continue

            items.append(
                _binding_completion(binding)
            )

        for name in _BUILTIN_NAMES:
            if not _matches_prefix(name, prefix):
                continue

            items.append(
                _builtin_completion(name)
            )

        return _deduplicate_and_sort(items)

    def _complete_members(
        self,
        scope: KvScope,
        self_value: KvValue,
        target: KvCompletionTarget,
    ) -> tuple[KvCompletionItem, ...]:
        receiver = target.receiver

        if receiver is None:
            return ()

        resolution = self._resolver.resolve(
            receiver,
            scope,
            self_value=self_value,
        )

        if resolution.kind is KvResolutionKind.ID_NAMESPACE:
            items = [
                _binding_completion(binding)
                for binding in scope.id_bindings
                if _matches_prefix(
                    binding.name,
                    target.prefix,
                )
            ]

            return _deduplicate_and_sort(items)

        members = self._resolver.members_of(resolution)
        items: list[KvCompletionItem] = []

        for member in members:
            if _is_hidden_member(
                member.name,
                target.prefix,
            ):
                continue

            if not _matches_prefix(
                member.name,
                target.prefix,
            ):
                continue

            items.append(
                _symbol_completion(member)
            )

        return _deduplicate_and_sort(items)

    def _complete_call_arguments(
        self,
        document: TextDocument,
        target: KvCompletionTarget,
        scope: KvScope,
        self_value: KvValue,
        expression_start: int | None,
    ) -> tuple[
        KvCompletionTarget,
        tuple[KvCompletionItem, ...],
        bool,
    ]:
        if expression_start is None:
            return target, (), False

        argument_context = call_argument_context_at(
            document,
            target,
            expression_start,
        )

        if argument_context is None:
            return target, (), False

        argument_target = replace(
            target,
            replacement_span=argument_context.replacement_span,
        )
        function = self._callable_symbol(
            argument_context,
            scope,
            self_value,
        )

        if function is None:
            return (
                argument_target,
                (),
                argument_context.is_quoted,
            )

        parameter = _call_parameter(
            function,
            argument_context,
        )

        if parameter is None:
            return (
                argument_target,
                (),
                argument_context.is_quoted,
            )

        parameter_type = self._resolver.type_of_parameter(
            function,
            parameter,
        )

        if parameter_type is None:
            return (
                argument_target,
                (),
                argument_context.is_quoted,
            )

        items = _call_argument_completions(
            parameter,
            parameter_type.value_type,
            argument_context,
        )

        return (
            argument_target,
            items,
            argument_context.is_quoted,
        )

    def _callable_symbol(
        self,
        context: KvCallArgumentContext,
        scope: KvScope,
        self_value: KvValue,
    ) -> Symbol | None:
        resolution = self._resolver.resolve(
            context.callee,
            scope,
            self_value=self_value,
        )

        if resolution.kind is not KvResolutionKind.VALUE:
            return None

        value = resolution.value

        if value is None or value.symbol is None:
            return None

        if value.symbol.kind not in {
            SymbolKind.FUNCTION,
            SymbolKind.METHOD,
        }:
            return None

        return value.symbol

    def _complete_widgets(
        self,
        target: KvCompletionTarget,
    ) -> tuple[KvCompletionItem, ...]:
        items: list[KvCompletionItem] = []

        for class_symbol in self._python_index.widget_classes():
            name = class_symbol.symbol.name

            if not _matches_prefix(name, target.prefix):
                continue

            items.append(
                _python_widget_completion(
                    class_symbol,
                    target.kind,
                )
            )

        for registration in (
            self._python_index.factory_registrations
        ):
            if not _matches_prefix(
                registration.name,
                target.prefix,
            ):
                continue

            items.append(
                self._factory_completion(
                    registration,
                    target.kind,
                )
            )

        for kv_symbol in self._kv_index.symbols():
            if not _matches_prefix(
                kv_symbol.name,
                target.prefix,
            ):
                continue

            items.append(
                _kv_widget_completion(
                    kv_symbol,
                    target.kind,
                )
            )

        return _deduplicate_and_sort(items)

    def _factory_completion(
        self,
        registration: FactoryRegistration,
        target_kind: KvCompletionTargetKind,
    ) -> KvCompletionItem:
        class_symbol = self._python_index.resolve_factory_class(
            registration,
        )

        if class_symbol is not None:
            symbol = class_symbol.symbol
            detail = (
                "Kivy Factory: "
                f"{symbol.qualified_name}"
            )
            documentation = symbol.documentation
        elif registration.baseclasses:
            symbol = None
            detail = (
                "Kivy Factory dynamic class: "
                + ", ".join(registration.baseclasses)
            )
            documentation = None
        elif registration.module_name is not None:
            symbol = None
            detail = (
                "Kivy Factory module: "
                f"{registration.module_name}"
            )
            documentation = None
        else:
            symbol = None
            detail = "Kivy Factory registration"
            documentation = None

        return KvCompletionItem(
            label=registration.name,
            kind=KvCompletionKind.CLASS,
            insert_text=_widget_insert_text(
                registration.name,
                target_kind,
            ),
            sort_text=(
                "01:"
                f"{registration.name.casefold()}"
            ),
            detail=detail,
            documentation=documentation,
            symbol=symbol,
        )

    def _complete_properties(
        self,
        self_value: KvValue,
        prefix: str,
    ) -> tuple[KvCompletionItem, ...]:
        items: list[KvCompletionItem] = []

        class_symbol = self._class_for_value(
            self_value,
        )

        if class_symbol is not None:
            for member in self._python_index.members_of(
                class_symbol,
            ):
                if member.kind not in {
                    SymbolKind.PROPERTY,
                    SymbolKind.EVENT,
                    SymbolKind.VARIABLE,
                }:
                    continue

                if (
                    member.kind is SymbolKind.VARIABLE
                    and member.annotation is None
                ):
                    continue

                if _is_hidden_member(
                    member.name,
                    prefix,
                ):
                    continue

                if not _matches_prefix(
                    member.name,
                    prefix,
                ):
                    continue

                items.append(
                    _property_completion(member)
                )

        for name in _KV_SPECIAL_NAMES:
            if not _matches_prefix(name, prefix):
                continue

            items.append(
                _special_property_completion(name)
            )

        return _deduplicate_and_sort(items)

    def _complete_property_values(
        self,
        document: TextDocument,
        target: KvCompletionTarget,
        self_value: KvValue,
        expression_start: int | None,
    ) -> tuple[
        KvCompletionTarget,
        tuple[KvCompletionItem, ...],
        bool,
    ]:
        if expression_start is None:
            return target, (), False

        value_context = property_value_context_at(
            document,
            target,
            expression_start,
            self_value,
            self._property_resolver,
        )

        if value_context is None:
            return target, (), False

        suggestions = self._property_value_completer.complete(
            value_context.property_info,
            value_context.prefix,
        )

        if value_context.is_quoted:
            suggestions = tuple(
                suggestion
                for suggestion in suggestions
                if _is_quoted_suggestion(suggestion)
            )

        value_target = replace(
            target,
            replacement_span=value_context.replacement_span,
        )
        items = tuple(
            _property_value_completion(suggestion)
            for suggestion in suggestions
        )

        return (
            value_target,
            items,
            value_context.is_quoted,
        )

    def _class_for_value(
        self,
        value: KvValue,
    ) -> ClassSymbol | None:
        if value.class_symbol is not None:
            return value.class_symbol

        symbol = value.symbol

        if (
            symbol is not None
            and symbol.kind is SymbolKind.CLASS
        ):
            class_symbol = self._python_index.class_named(
                symbol.qualified_name,
            )

            if class_symbol is not None:
                return class_symbol

        if value.type_name is None:
            return None

        return self._python_index.resolve_class(
            value.type_name,
            from_module=value.module_name,
        )

def _call_parameter(
    function: Symbol,
    context: KvCallArgumentContext,
) -> ParameterSymbol | None:
    parameters = _call_parameters(function)

    if context.keyword_name is not None:
        return next(
            (
                parameter
                for parameter in parameters
                if parameter.name == context.keyword_name
                and parameter.kind in {
                    ParameterKind.POSITIONAL_OR_KEYWORD,
                    ParameterKind.KEYWORD_ONLY,
                }
            ),
            None,
        )

    positional_parameters = tuple(
        parameter
        for parameter in parameters
        if parameter.kind in {
            ParameterKind.POSITIONAL_ONLY,
            ParameterKind.POSITIONAL_OR_KEYWORD,
        }
    )

    if context.argument_index < len(positional_parameters):
        return positional_parameters[context.argument_index]

    return next(
        (
            parameter
            for parameter in parameters
            if parameter.kind is ParameterKind.VAR_POSITIONAL
        ),
        None,
    )


def _call_parameters(
    function: Symbol,
) -> tuple[ParameterSymbol, ...]:
    parameters = function.parameters

    if function.kind is not SymbolKind.METHOD:
        return parameters

    if not parameters:
        return parameters

    if parameters[0].name not in {"self", "cls"}:
        return parameters

    return parameters[1:]


def _call_argument_completions(
    parameter: ParameterSymbol,
    value_type: ValueType,
    context: KvCallArgumentContext,
) -> tuple[KvCompletionItem, ...]:
    items: list[KvCompletionItem] = []

    for index, literal in enumerate(
        _completion_literals(value_type)
    ):
        source = _call_literal_source(
            literal,
            context.quote,
        )

        if not _matches_literal_prefix(
            source,
            context.prefix,
        ):
            continue

        kind = (
            KvCompletionKind.KEYWORD
            if literal is None or isinstance(literal, bool)
            else KvCompletionKind.CONSTANT
        )
        items.append(
            KvCompletionItem(
                label=source,
                kind=kind,
                insert_text=source,
                sort_text=(
                    f"00:{index:04d}:"
                    f"{source.casefold()}"
                ),
                detail=(
                    "Allowed value for parameter "
                    f"{parameter.name!r}"
                ),
            )
        )

    return _deduplicate_and_sort(items)


def _completion_literals(
    value_type: ValueType,
) -> tuple[LiteralValue, ...]:
    if value_type.kind is ValueTypeKind.LITERAL:
        return value_type.literals

    if value_type.kind is ValueTypeKind.BOOL:
        return (True, False)

    if value_type.kind is ValueTypeKind.NONE:
        return (None,)

    if value_type.kind is not ValueTypeKind.UNION:
        return ()

    literals: list[LiteralValue] = []
    literal_keys: set[tuple[type[object], str]] = set()

    for argument in value_type.arguments:
        for literal in _completion_literals(argument):
            key = (
                type(literal),
                repr(literal),
            )

            if key in literal_keys:
                continue

            literal_keys.add(key)
            literals.append(literal)

    return tuple(literals)


def _call_literal_source(
    literal: LiteralValue,
    quote: str | None,
) -> str:
    if literal is None:
        return "None"

    if isinstance(literal, bool):
        return str(literal)

    if not isinstance(literal, str):
        return repr(literal)

    if quote == "'":
        escaped = literal.replace(
            "\\",
            "\\\\",
        ).replace(
            "'",
            "\\'",
        )
        return f"'{escaped}'"

    return json.dumps(
        literal,
        ensure_ascii=False,
    )


def _matches_literal_prefix(
    source: str,
    prefix: str,
) -> bool:
    normalized_prefix = prefix.strip().casefold()

    if not normalized_prefix:
        return True

    normalized_prefix = normalized_prefix.lstrip("\"'")
    normalized_source = source.casefold().lstrip("\"'")

    return normalized_source.startswith(normalized_prefix)

def _python_widget_completion(
    class_symbol: ClassSymbol,
    target_kind: KvCompletionTargetKind,
) -> KvCompletionItem:
    symbol = class_symbol.symbol

    return KvCompletionItem(
        label=symbol.name,
        kind=KvCompletionKind.CLASS,
        insert_text=_widget_insert_text(
            symbol.name,
            target_kind,
        ),
        sort_text=f"00:{symbol.name.casefold()}",
        detail=symbol.qualified_name,
        documentation=symbol.documentation,
        symbol=symbol,
    )


def _kv_widget_completion(
    symbol: KvClassSymbol,
    target_kind: KvCompletionTargetKind,
) -> KvCompletionItem:
    if symbol.is_dynamic:
        detail = (
            "KV dynamic class: "
            + ", ".join(symbol.bases)
        )
    else:
        detail = "KV rule"

    return KvCompletionItem(
        label=symbol.name,
        kind=KvCompletionKind.CLASS,
        insert_text=_widget_insert_text(
            symbol.name,
            target_kind,
        ),
        sort_text=f"02:{symbol.name.casefold()}",
        detail=detail,
    )


def _property_completion(
    symbol: Symbol,
) -> KvCompletionItem:
    if symbol.kind is SymbolKind.EVENT:
        kind = KvCompletionKind.EVENT
    else:
        kind = KvCompletionKind.PROPERTY

    detail = (
        symbol.annotation
        or symbol.qualified_name
    )

    return KvCompletionItem(
        label=symbol.name,
        kind=kind,
        insert_text=f"{symbol.name}: ",
        sort_text=_symbol_sort_text(symbol),
        detail=detail,
        documentation=symbol.documentation,
        symbol=symbol,
    )


def _special_property_completion(
    name: str,
) -> KvCompletionItem:
    if name == "id":
        insert_text = "id: "
        detail = "Kivy widget identifier"
    else:
        insert_text = f"{name}:"
        detail = "Kivy canvas block"

    return KvCompletionItem(
        label=name,
        kind=KvCompletionKind.KEYWORD,
        insert_text=insert_text,
        sort_text=f"08:{name.casefold()}",
        detail=detail,
    )


def _property_value_completion(
    suggestion: KvPropertyValueSuggestion,
) -> KvCompletionItem:
    if suggestion.insert_text in {
        "False",
        "None",
        "True",
    }:
        kind = KvCompletionKind.KEYWORD
    else:
        kind = KvCompletionKind.CONSTANT

    return KvCompletionItem(
        label=suggestion.label,
        kind=kind,
        insert_text=suggestion.insert_text,
        sort_text=suggestion.sort_text,
        detail=suggestion.detail,
    )


def _is_quoted_suggestion(
    suggestion: KvPropertyValueSuggestion,
) -> bool:
    return suggestion.insert_text.startswith(
        (
            "'",
            '"',
        )
    )


def _widget_insert_text(
    name: str,
    target_kind: KvCompletionTargetKind,
) -> str:
    if target_kind is KvCompletionTargetKind.RULE:
        return name

    return f"{name}:"


def _binding_completion(
    binding: KvBinding,
) -> KvCompletionItem:
    value = binding.value
    symbol = value.symbol

    if binding.kind is KvBindingKind.ID:
        completion_kind = KvCompletionKind.ID
    elif value.kind is KvValueKind.CLASS:
        completion_kind = KvCompletionKind.CLASS
    elif value.kind is KvValueKind.MODULE:
        completion_kind = KvCompletionKind.MODULE
    elif value.kind is KvValueKind.FUNCTION:
        completion_kind = KvCompletionKind.FUNCTION
    else:
        completion_kind = KvCompletionKind.VARIABLE

    documentation = symbol.documentation if symbol is not None else None

    return KvCompletionItem(
        label=binding.name,
        kind=completion_kind,
        insert_text=binding.name,
        sort_text=_binding_sort_text(binding),
        detail=_binding_detail(binding),
        documentation=documentation,
        symbol=symbol,
    )


def _symbol_completion(
    symbol: Symbol,
) -> KvCompletionItem:
    kind = _completion_kind_for_symbol(symbol)
    detail = (
        symbol.signature
        or symbol.annotation
        or symbol.qualified_name
    )

    return KvCompletionItem(
        label=symbol.name,
        kind=kind,
        insert_text=symbol.name,
        sort_text=_symbol_sort_text(symbol),
        detail=detail,
        documentation=symbol.documentation,
        symbol=symbol,
    )


def _builtin_completion(name: str) -> KvCompletionItem:
    if name in {"False", "None", "True"}:
        kind = KvCompletionKind.KEYWORD
    else:
        kind = KvCompletionKind.FUNCTION

    return KvCompletionItem(
        label=name,
        kind=kind,
        insert_text=name,
        sort_text=f"90:{name.casefold()}",
        detail="Python builtin",
    )


def _binding_detail(
    binding: KvBinding,
) -> str:
    value = binding.value
    type_name = (
        value.type_name
        or value.module_name
        or "unknown"
    )

    return f"{binding.kind.value}: {type_name}"


def _binding_sort_text(
    binding: KvBinding,
) -> str:
    priorities = {
        KvBindingKind.ROOT: 0,
        KvBindingKind.SELF: 1,
        KvBindingKind.APP: 2,
        KvBindingKind.ID: 3,
        KvBindingKind.GLOBAL: 4,
        KvBindingKind.BUILTIN: 5,
    }
    priority = priorities[binding.kind]

    return f"{priority:02d}:{binding.name.casefold()}"


def _symbol_sort_text(symbol: Symbol) -> str:
    priorities = {
        SymbolKind.PROPERTY: 10,
        SymbolKind.EVENT: 11,
        SymbolKind.METHOD: 20,
        SymbolKind.FUNCTION: 21,
        SymbolKind.VARIABLE: 30,
        SymbolKind.CONSTANT: 31,
        SymbolKind.CLASS: 40,
        SymbolKind.MODULE: 41,
        SymbolKind.PARAMETER: 50,
        SymbolKind.ID: 51,
    }
    priority = priorities.get(
        symbol.kind,
        99,
    )

    return f"{priority:02d}:{symbol.name.casefold()}"


def _completion_kind_for_symbol(
    symbol: Symbol,
) -> KvCompletionKind:
    kinds = {
        SymbolKind.CLASS: KvCompletionKind.CLASS,
        SymbolKind.FUNCTION: KvCompletionKind.FUNCTION,
        SymbolKind.METHOD: KvCompletionKind.METHOD,
        SymbolKind.PROPERTY: KvCompletionKind.PROPERTY,
        SymbolKind.VARIABLE: KvCompletionKind.VARIABLE,
        SymbolKind.CONSTANT: KvCompletionKind.CONSTANT,
        SymbolKind.PARAMETER: KvCompletionKind.VARIABLE,
        SymbolKind.EVENT: KvCompletionKind.EVENT,
        SymbolKind.ID: KvCompletionKind.ID,
        SymbolKind.MODULE: KvCompletionKind.MODULE,
    }

    return kinds.get(
        symbol.kind,
        KvCompletionKind.VARIABLE,
    )


def _matches_prefix(
    name: str,
    prefix: str,
) -> bool:
    if not prefix:
        return True

    return name.casefold().startswith(
        prefix.casefold(),
    )


def _is_hidden_member(
    name: str,
    prefix: str,
) -> bool:
    return (
        name.startswith("_")
        and not prefix.startswith("_")
    )


def _deduplicate_and_sort(
    items: list[KvCompletionItem],
) -> tuple[KvCompletionItem, ...]:
    unique: dict[str, KvCompletionItem] = {}

    for item in items:
        unique.setdefault(
            item.label,
            item,
        )

    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.sort_text,
                item.label.casefold(),
            ),
        )
    )
