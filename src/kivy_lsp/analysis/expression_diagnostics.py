# src/kivy_lsp/analysis/expression_diagnostics.py

from __future__ import annotations

import ast
import builtins

from kivy_lsp.analysis.expression import (
    KvExpressionResolver,
    KvResolutionKind,
)
from kivy_lsp.analysis.scope import KvScope, KvValue
from kivy_lsp.analysis.type_compatibility import (
    KivyPropertyTypeChecker,
)
from kivy_lsp.analysis.type_narrowing import (
    KvNoneNarrowing,
    KvTypeNarrowings,
    branch_narrowings,
    merge_narrowings,
)
from kivy_lsp.analysis.value_inference import KvValueInferer
from kivy_lsp.model.diagnostic import (
    Diagnostic,
    DiagnosticSeverity,
)
from kivy_lsp.model.property import (
    KivyPropertyInfo,
    KivyPropertyKind,
)
from kivy_lsp.model.span import Span
from kivy_lsp.model.symbol import (
    ParameterKind,
    ParameterSymbol,
    Symbol,
    SymbolKind,
)
from kivy_lsp.model.value_type import ValueTypeKind

_PYTHON_BUILTINS = frozenset(dir(builtins))

_KV_BUILTINS = frozenset(
    {
        "Builder",
        "Cache",
        "Clock",
        "Factory",
        "Metrics",
        "app",
        "args",
        "cm",
        "ctx",
        "dp",
        "inch",
        "kwargs",
        "mm",
        "pt",
        "root",
        "self",
        "sp",
    }
)

_ID_NAMESPACE_MEMBERS = frozenset(
    {
        "copy",
        "get",
        "items",
        "keys",
        "values",
    }
)


class KvExpressionDiagnosticAnalyzer(ast.NodeVisitor):
    """Find invalid names and members in one KV expression."""

    def __init__(
        self,
        resolver: KvExpressionResolver,
    ) -> None:
        self._resolver = resolver
        self._value_inferer = KvValueInferer(resolver)
        self._type_checker = KivyPropertyTypeChecker()
        self._source = ""
        self._expression_span = Span(0, 0)
        self._scope: KvScope | None = None
        self._self_value: KvValue | None = None
        self._local_names: frozenset[str] = frozenset()
        self._narrowings: dict[str, KvNoneNarrowing] = {}
        self._diagnostics: list[Diagnostic] = []
        self._diagnostic_keys: set[tuple[str, int, int]] = set()

    def analyze(
        self,
        source: str,
        expression_span: Span,
        scope: KvScope,
        *,
        self_value: KvValue,
    ) -> tuple[Diagnostic, ...]:
        try:
            tree = ast.parse(
                source,
                mode="eval",
            )
        except SyntaxError as error:
            return (
                _syntax_diagnostic(
                    source,
                    expression_span,
                    error,
                ),
            )

        self._source = source
        self._expression_span = expression_span
        self._scope = scope
        self._self_value = self_value
        self._local_names = _expression_local_names(tree)
        self._narrowings = {}
        self._diagnostics = []
        self._diagnostic_keys = set()

        self.visit(tree.body)

        return tuple(self._diagnostics)

    def visit_Name(
        self,
        node: ast.Name,
    ) -> None:
        if not isinstance(node.ctx, ast.Load):
            return

        name = node.id

        if name in self._local_names:
            return

        if name in _PYTHON_BUILTINS:
            return

        if name in _KV_BUILTINS:
            return

        scope = self._scope

        if scope is None:
            return

        resolution = self._resolver.resolve(
            name,
            scope,
            self_value=self._self_value,
        )

        if resolution.kind is not KvResolutionKind.UNKNOWN:
            return

        self._add_diagnostic(
            message=f'Unknown KV name "{name}".',
            span=self._node_span(node),
            code="kv-unknown-name",
        )

    def visit_Attribute(
        self,
        node: ast.Attribute,
    ) -> None:
        self.visit(node.value)

        scope = self._scope

        if scope is None:
            return

        owner_source = ast.get_source_segment(
            self._source,
            node.value,
        )

        if owner_source is None:
            return

        owner = self._resolver.resolve(
            owner_source,
            scope,
            self_value=self._self_value,
        )

        if owner.kind is KvResolutionKind.UNKNOWN:
            return

        if owner.kind is KvResolutionKind.ID_NAMESPACE:
            if node.attr in _ID_NAMESPACE_MEMBERS:
                return

            if scope.id_named(node.attr) is not None:
                return

            self._add_diagnostic(
                message=f'Unknown KV id "{node.attr}".',
                span=self._attribute_span(node),
                code="kv-unknown-id",
            )
            return

        members = self._resolver.members_of(owner)

        if not members:
            return

        if any(member.name == node.attr for member in members):
            return

        owner_name = _resolution_name(owner)

        self._add_diagnostic(
            message=(
                f'"{owner_name}" has no member named '
                f'"{node.attr}".'
            ),
            span=self._attribute_span(node),
            code="kv-unknown-member",
        )

    def visit_Subscript(
        self,
        node: ast.Subscript,
    ) -> None:
        self.visit(node.value)
        self.visit(node.slice)

        scope = self._scope

        if scope is None:
            return

        owner_source = ast.get_source_segment(
            self._source,
            node.value,
        )

        if owner_source is None:
            return

        owner = self._resolver.resolve(
            owner_source,
            scope,
            self_value=self._self_value,
        )

        if owner.kind is not KvResolutionKind.ID_NAMESPACE:
            return

        id_name = _string_constant(node.slice)

        if id_name is None:
            return

        if scope.id_named(id_name) is not None:
            return

        self._add_diagnostic(
            message=f'Unknown KV id "{id_name}".',
            span=self._node_span(node.slice),
            code="kv-unknown-id",
        )

    def visit_Call(
        self,
        node: ast.Call,
    ) -> None:
        self.visit(node.func)

        for argument in node.args:
            self.visit(argument)

        for keyword in node.keywords:
            self.visit(keyword.value)

        scope = self._scope

        if scope is None:
            return

        self._validate_call_arguments(
            node,
            scope,
        )

        self._validate_ids_get(
            node,
            scope,
        )

    def visit_IfExp(
        self,
        node: ast.IfExp,
    ) -> None:
        self.visit(node.test)
        self._visit_with_narrowings(
            node.body,
            branch_narrowings(
                node.test,
                truthy=True,
            ),
        )
        self._visit_with_narrowings(
            node.orelse,
            branch_narrowings(
                node.test,
                truthy=False,
            ),
        )

    def visit_BoolOp(
        self,
        node: ast.BoolOp,
    ) -> None:
        active: dict[str, KvNoneNarrowing] = {}

        for value in node.values:
            self._visit_with_narrowings(
                value,
                active,
            )

            if isinstance(node.op, ast.And):
                branch = branch_narrowings(
                    value,
                    truthy=True,
                )
            else:
                branch = branch_narrowings(
                    value,
                    truthy=False,
                )

            active = merge_narrowings(
                active,
                branch,
            )

    def _visit_with_narrowings(
        self,
        node: ast.expr,
        narrowings: KvTypeNarrowings,
    ) -> None:
        previous = self._narrowings
        self._narrowings = merge_narrowings(
            previous,
            narrowings,
        )

        try:
            self.visit(node)
        finally:
            self._narrowings = previous

    def _validate_call_arguments(
        self,
        node: ast.Call,
        scope: KvScope,
    ) -> None:
        function_source = ast.get_source_segment(
            self._source,
            node.func,
        )

        if function_source is None:
            return

        resolution = self._resolver.resolve(
            function_source,
            scope,
            self_value=self._self_value,
        )

        if resolution.kind is not KvResolutionKind.VALUE:
            return

        value = resolution.value

        if value is None or value.symbol is None:
            return

        function = value.symbol

        if function.kind not in {
            SymbolKind.FUNCTION,
            SymbolKind.METHOD,
        }:
            return

        parameters = list(function.parameters)

        if (
            function.kind is SymbolKind.METHOD
            and parameters
            and parameters[0].name in {"self", "cls"}
        ):
            parameters.pop(0)

        self._bind_call_arguments(
            node,
            function,
            parameters,
            scope,
        )

    def _bind_call_arguments(
        self,
        node: ast.Call,
        function: Symbol,
        parameters: list[ParameterSymbol],
        scope: KvScope,
    ) -> None:
        positional_parameters = [
            parameter
            for parameter in parameters
            if parameter.kind in {
                ParameterKind.POSITIONAL_ONLY,
                ParameterKind.POSITIONAL_OR_KEYWORD,
            }
        ]
        keyword_parameters = {
            parameter.name: parameter
            for parameter in parameters
            if parameter.kind in {
                ParameterKind.POSITIONAL_OR_KEYWORD,
                ParameterKind.KEYWORD_ONLY,
            }
        }
        var_positional = next(
            (
                parameter
                for parameter in parameters
                if parameter.kind is ParameterKind.VAR_POSITIONAL
            ),
            None,
        )
        var_keyword = next(
            (
                parameter
                for parameter in parameters
                if parameter.kind is ParameterKind.VAR_KEYWORD
            ),
            None,
        )
        bound_names: set[str] = set()
        dynamic_positional = False

        for index, argument in enumerate(node.args):
            if isinstance(argument, ast.Starred):
                dynamic_positional = True
                continue

            if dynamic_positional:
                continue

            if index < len(positional_parameters):
                parameter = positional_parameters[index]
            elif var_positional is not None:
                parameter = var_positional
            else:
                self._add_diagnostic(
                    message=(
                        f"Too many positional arguments for "
                        f'"{function.name}".'
                    ),
                    span=self._node_span(argument),
                    code="kv-too-many-arguments",
                )
                continue

            if parameter.kind is not ParameterKind.VAR_POSITIONAL:
                bound_names.add(parameter.name)

            self._validate_argument_type(
                argument,
                function,
                parameter,
                scope,
            )

        dynamic_keywords = False

        for keyword in node.keywords:
            if keyword.arg is None:
                dynamic_keywords = True
                continue

            parameter = keyword_parameters.get(
                keyword.arg,
            )

            if parameter is None:
                parameter = var_keyword

            if parameter is None:
                self._add_diagnostic(
                    message=(
                        "Unexpected keyword argument "
                        f'"{keyword.arg}" for '
                        f'"{function.name}".'
                    ),
                    span=self._node_span(keyword.value),
                    code="kv-unexpected-keyword-argument",
                )
                continue

            if keyword.arg in bound_names:
                self._add_diagnostic(
                    message=(
                        f'Argument "{keyword.arg}" is supplied '
                        "more than once."
                    ),
                    span=self._node_span(keyword.value),
                    code="kv-duplicate-argument",
                )
                continue

            if parameter.kind is not ParameterKind.VAR_KEYWORD:
                bound_names.add(parameter.name)

            self._validate_argument_type(
                keyword.value,
                function,
                parameter,
                scope,
            )

        if dynamic_positional or dynamic_keywords:
            return

        for parameter in parameters:
            if parameter.kind in {
                ParameterKind.VAR_POSITIONAL,
                ParameterKind.VAR_KEYWORD,
            }:
                continue

            if parameter.default is not None:
                continue

            if parameter.name in bound_names:
                continue

            self._add_diagnostic(
                message=(
                    f'Missing required argument "{parameter.name}" '
                    f'for "{function.name}".'
                ),
                span=self._node_span(node.func),
                code="kv-missing-argument",
            )

    def _validate_argument_type(
        self,
        argument: ast.expr,
        function: Symbol,
        parameter: ParameterSymbol,
        scope: KvScope,
    ) -> None:
        expected = self._resolver.type_of_parameter(
            function,
            parameter,
        )

        if expected is None or expected.is_unknown:
            return

        if expected.is_any:
            return

        argument_source = ast.get_source_segment(
            self._source,
            argument,
        )

        if argument_source is None:
            return

        inferred = self._value_inferer.infer(
            argument_source,
            scope,
            self_value=self._self_value,
            narrowings=self._narrowings,
        )

        if not inferred.is_known:
            return

        if (
            expected.value_type.kind is ValueTypeKind.LITERAL
            and not inferred.literal_known
            and inferred.value_type.kind is ValueTypeKind.OBJECT
        ):
            return

        result = self._type_checker.check(
            KivyPropertyInfo(
                kind=KivyPropertyKind.UNKNOWN,
                accepted_type=expected.value_type,
            ),
            inferred,
        )

        if not result.is_incompatible:
            return

        self._add_diagnostic(
            message=(
                f'Argument "{parameter.name}" expects '
                f"{result.expected}, but received "
                f"{result.actual}."
            ),
            span=self._node_span(argument),
            code="kv-argument-type",
        )

    def _validate_ids_get(
        self,
        node: ast.Call,
        scope: KvScope,
    ) -> None:
        function = node.func

        if not isinstance(function, ast.Attribute):
            return

        if function.attr != "get":
            return

        owner_source = ast.get_source_segment(
            self._source,
            function.value,
        )

        if owner_source is None:
            return

        owner = self._resolver.resolve(
            owner_source,
            scope,
            self_value=self._self_value,
        )

        if owner.kind is not KvResolutionKind.ID_NAMESPACE:
            return

        if not node.args:
            return

        id_name = _string_constant(
            node.args[0],
        )

        if id_name is None:
            return

        if scope.id_named(id_name) is not None:
            return

        self._add_diagnostic(
            message=f'Unknown KV id "{id_name}".',
            span=self._node_span(node.args[0]),
            code="kv-unknown-id",
        )

    def _add_diagnostic(
        self,
        *,
        message: str,
        span: Span,
        code: str,
    ) -> None:
        key = (
            code,
            span.start,
            span.end,
        )

        if key in self._diagnostic_keys:
            return

        self._diagnostic_keys.add(key)
        self._diagnostics.append(
            Diagnostic(
                message=message,
                span=span,
                severity=DiagnosticSeverity.ERROR,
                code=code,
            )
        )

    def _node_span(
        self,
        node: ast.AST,
    ) -> Span:
        start = _node_start_offset(
            self._source,
            node,
        )
        end = _node_end_offset(
            self._source,
            node,
        )

        return Span(
            start=self._expression_span.start + start,
            end=self._expression_span.start + end,
        )

    def _attribute_span(
        self,
        node: ast.Attribute,
    ) -> Span:
        end = _node_end_offset(
            self._source,
            node,
        )
        start = max(
            0,
            end - len(node.attr),
        )

        return Span(
            start=self._expression_span.start + start,
            end=self._expression_span.start + end,
        )


def _expression_local_names(
    tree: ast.Expression,
) -> frozenset[str]:
    names: set[str] = set()

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
        ):
            names.add(node.id)

        if isinstance(node, ast.Lambda):
            names.update(
                argument.arg
                for argument in node.args.posonlyargs
            )
            names.update(
                argument.arg
                for argument in node.args.args
            )
            names.update(
                argument.arg
                for argument in node.args.kwonlyargs
            )

            if node.args.vararg is not None:
                names.add(node.args.vararg.arg)

            if node.args.kwarg is not None:
                names.add(node.args.kwarg.arg)

    return frozenset(names)


def _resolution_name(
    resolution: object,
) -> str:
    value = getattr(
        resolution,
        "value",
        None,
    )

    if value is None:
        return "value"

    type_name = getattr(
        value,
        "type_name",
        None,
    )

    if isinstance(type_name, str):
        return type_name

    module_name = getattr(
        value,
        "module_name",
        None,
    )

    if isinstance(module_name, str):
        return module_name

    return "value"


def _string_constant(
    node: ast.expr,
) -> str | None:
    if not isinstance(node, ast.Constant):
        return None

    if not isinstance(node.value, str):
        return None

    return node.value


def _node_start_offset(
    source: str,
    node: ast.AST,
) -> int:
    line = getattr(
        node,
        "lineno",
        1,
    )
    column = getattr(
        node,
        "col_offset",
        0,
    )

    return _source_offset(
        source,
        line,
        column,
    )


def _node_end_offset(
    source: str,
    node: ast.AST,
) -> int:
    line = getattr(
        node,
        "end_lineno",
        None,
    )
    column = getattr(
        node,
        "end_col_offset",
        None,
    )

    if not isinstance(line, int):
        return _node_start_offset(source, node)

    if not isinstance(column, int):
        return _node_start_offset(source, node)

    return _source_offset(
        source,
        line,
        column,
    )


def _source_offset(
    source: str,
    line: int,
    byte_column: int,
) -> int:
    lines = source.splitlines(keepends=True)
    line_index = max(
        0,
        line - 1,
    )

    if line_index >= len(lines):
        return len(source)

    line_start = sum(
        len(current)
        for current in lines[:line_index]
    )
    current_line = lines[line_index]
    encoded_line = current_line.encode("utf-8")
    encoded_prefix = encoded_line[:byte_column]
    character_prefix = encoded_prefix.decode(
        "utf-8",
        errors="ignore",
    )

    return line_start + len(character_prefix)

def _syntax_diagnostic(
    source: str,
    expression_span: Span,
    error: SyntaxError,
) -> Diagnostic:
    start_line = error.lineno or 1
    start_column = max(
        0,
        (error.offset or 1) - 1,
    )
    end_line = error.end_lineno or start_line
    end_column = max(
        start_column + 1,
        (error.end_offset or start_column + 2) - 1,
    )
    relative_start = _source_offset(
        source,
        start_line,
        start_column,
    )
    relative_end = _source_offset(
        source,
        end_line,
        end_column,
    )

    if relative_end <= relative_start:
        relative_end = min(
            len(source),
            relative_start + 1,
        )

    return Diagnostic(
        message=f"Invalid Python expression: {error.msg}",
        span=Span(
            start=expression_span.start + relative_start,
            end=expression_span.start + relative_end,
        ),
        severity=DiagnosticSeverity.ERROR,
        code="kv-invalid-expression",
    )

