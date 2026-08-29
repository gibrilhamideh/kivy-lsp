# src/kivy_lsp/python/indexer.py

from __future__ import annotations

import ast
from dataclasses import dataclass

from kivy_lsp.model.diagnostic import (
    Diagnostic,
    DiagnosticSeverity,
)
from kivy_lsp.model.span import Span
from kivy_lsp.model.symbol import (
    ClassSymbol,
    ModuleSymbol,
    ParameterKind,
    ParameterSymbol,
    Symbol,
    SymbolKind,
    SymbolLocation,
)
from kivy_lsp.model.value_type import LiteralValue
from kivy_lsp.python.module import (
    FactoryRegistration,
    ImportBinding,
    PythonModule,
)
from kivy_lsp.python.property_indexer import KivyPropertyIndexer
from kivy_lsp.workspace.document import (
    PositionEncoding,
    TextDocument,
    TextPosition,
)

type FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True, slots=True)
class PythonIndexResult:
    """The result of statically indexing one Python document."""

    module: PythonModule | None
    diagnostics: tuple[Diagnostic, ...]


def index_python_module(
    document: TextDocument,
    module_name: str,
) -> PythonIndexResult:
    """Extract semantic symbols without executing Python code."""
    if not module_name:
        raise ValueError("module name cannot be empty")

    try:
        tree = ast.parse(
            source=document.text,
            filename=document.uri,
            type_comments=True,
        )
    except SyntaxError as error:
        diagnostic = Diagnostic(
            message=error.msg,
            span=_syntax_error_span(document, error),
            severity=DiagnosticSeverity.ERROR,
            code="python-syntax-error",
        )
        return PythonIndexResult(
            module=None,
            diagnostics=(diagnostic,),
        )

    indexer = _ModuleIndexer(
        document=document,
        module_name=module_name,
    )
    return PythonIndexResult(
        module=indexer.index(tree),
        diagnostics=(),
    )


class _ModuleIndexer:
    """Extract symbols, imports, and Factory registrations."""

    def __init__(
        self,
        document: TextDocument,
        module_name: str,
    ) -> None:
        self._document = document
        self._module_name = module_name
        self._property_indexer = KivyPropertyIndexer(())
        self._literal_sequences: dict[
            str,
            tuple[LiteralValue, ...],
        ] = {}

    def index(self, tree: ast.Module) -> PythonModule:
        imports = self._index_imports(tree)
        self._property_indexer = KivyPropertyIndexer(imports)
        self._literal_sequences = self._index_literal_sequences(
            tree,
        )

        classes: list[ClassSymbol] = []
        symbols: list[Symbol] = []

        for statement in tree.body:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                continue

            if isinstance(statement, ast.ClassDef):
                classes.append(self._index_class(statement))
            elif isinstance(
                statement,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                symbols.append(
                    self._function_symbol(
                        node=statement,
                        container=self._module_name,
                        kind=SymbolKind.FUNCTION,
                    )
                )
            elif isinstance(statement, ast.Assign):
                symbols.extend(
                    self._assignment_symbols(
                        statement=statement,
                        targets=statement.targets,
                        value=statement.value,
                        annotation=None,
                        container=self._module_name,
                    )
                )
            elif isinstance(statement, ast.AnnAssign):
                symbols.extend(
                    self._assignment_symbols(
                        statement=statement,
                        targets=(statement.target,),
                        value=statement.value,
                        annotation=statement.annotation,
                        container=self._module_name,
                    )
                )

        factory_names = self._factory_local_names(imports)
        factory_registrations = self._index_factory_registrations(
            tree,
            factory_names,
        )

        module_symbol = ModuleSymbol(
            name=self._module_name,
            uri=self._document.uri,
            classes=tuple(classes),
            symbols=tuple(symbols),
        )
        return PythonModule(
            symbol=module_symbol,
            imports=tuple(imports),
            factory_registrations=factory_registrations,
        )

    def _index_imports(
        self,
        tree: ast.Module,
    ) -> list[ImportBinding]:
        imports: list[ImportBinding] = []

        for statement in tree.body:
            if isinstance(statement, ast.Import):
                imports.extend(self._index_import(statement))
            elif isinstance(statement, ast.ImportFrom):
                imports.extend(self._index_import_from(statement))

        return imports

    def _index_import(
        self,
        node: ast.Import,
    ) -> list[ImportBinding]:
        bindings: list[ImportBinding] = []

        for alias in node.names:
            local_name = alias.asname or alias.name.split(".", 1)[0]
            bindings.append(
                ImportBinding(
                    local_name=local_name,
                    target_module=alias.name,
                    target_name=None,
                    relative_level=0,
                    location=self._location(
                        node=alias,
                        name=local_name,
                        prefer_last=alias.asname is not None,
                    ),
                )
            )

        return bindings

    def _index_import_from(
        self,
        node: ast.ImportFrom,
    ) -> list[ImportBinding]:
        bindings: list[ImportBinding] = []
        module_name = node.module or ""

        for alias in node.names:
            local_name = alias.asname or alias.name
            bindings.append(
                ImportBinding(
                    local_name=local_name,
                    target_module=module_name,
                    target_name=alias.name,
                    relative_level=node.level,
                    location=self._location(
                        node=alias,
                        name=local_name,
                        prefer_last=alias.asname is not None,
                    ),
                )
            )

        return bindings

    def _index_factory_registrations(
        self,
        tree: ast.Module,
        factory_names: set[str],
    ) -> tuple[FactoryRegistration, ...]:
        registrations: list[FactoryRegistration] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            if not self._is_factory_register_call(
                node,
                factory_names,
            ):
                continue

            registration = self._factory_registration(node)

            if registration is not None:
                registrations.append(registration)

        return tuple(
            sorted(
                registrations,
                key=lambda registration: (
                    registration.location.span.start
                ),
            ),
        )

    def _factory_registration(
        self,
        node: ast.Call,
    ) -> FactoryRegistration | None:
        name_node = self._call_argument(
            node,
            position=0,
            keyword_name="classname",
        )

        if name_node is None:
            return None

        name = self._string_value(name_node)

        if not name:
            return None

        class_node = self._call_argument(
            node,
            position=1,
            keyword_name="cls",
        )
        module_node = self._call_argument(
            node,
            position=2,
            keyword_name="module",
        )
        template_node = self._call_argument(
            node,
            position=3,
            keyword_name="is_template",
        )
        baseclasses_node = self._call_argument(
            node,
            position=4,
            keyword_name="baseclasses",
        )

        class_reference = None

        if class_node is not None:
            class_reference = self._qualified_expression(
                class_node,
            )

        module_name = self._string_value(
            module_node,
        )

        if not module_name:
            module_name = None

        return FactoryRegistration(
            name=name,
            location=self._location_from_name(
                node=node,
                selection_node=name_node,
                name=name,
                prefer_last=False,
            ),
            class_reference=class_reference,
            module_name=module_name,
            baseclasses=self._baseclass_names(
                baseclasses_node,
            ),
            is_template=(
                self._bool_value(template_node)
                or False
            ),
        )

    def _index_class(self, node: ast.ClassDef) -> ClassSymbol:
        qualified_name = f"{self._module_name}.{node.name}"
        members: dict[str, Symbol] = {}

        for statement in node.body:
            if isinstance(
                statement,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                member = self._class_function_symbol(
                    node=statement,
                    container=qualified_name,
                )
                members[member.name] = member

                for instance_member in self._instance_members(
                    function=statement,
                    container=qualified_name,
                ):
                    members.setdefault(
                        instance_member.name,
                        instance_member,
                    )

            elif isinstance(statement, ast.Assign):
                if self._is_events_assignment(statement.targets):
                    for event in self._event_symbols(
                        value=statement.value,
                        container=qualified_name,
                    ):
                        members[event.name] = event
                else:
                    for member in self._assignment_symbols(
                        statement=statement,
                        targets=statement.targets,
                        value=statement.value,
                        annotation=None,
                        container=qualified_name,
                    ):
                        members[member.name] = member

            elif isinstance(statement, ast.AnnAssign):
                for member in self._assignment_symbols(
                    statement=statement,
                    targets=(statement.target,),
                    value=statement.value,
                    annotation=statement.annotation,
                    container=qualified_name,
                ):
                    members[member.name] = member

        class_symbol = Symbol(
            name=node.name,
            qualified_name=qualified_name,
            kind=SymbolKind.CLASS,
            location=self._location(node=node, name=node.name),
            documentation=ast.get_docstring(node, clean=False),
        )
        return ClassSymbol(
            symbol=class_symbol,
            bases=tuple(
                ast.unparse(base)
                for base in node.bases
            ),
            members=tuple(members.values()),
        )

    def _class_function_symbol(
        self,
        node: FunctionNode,
        container: str,
    ) -> Symbol:
        if self._is_property_method(node):
            return Symbol(
                name=node.name,
                qualified_name=f"{container}.{node.name}",
                kind=SymbolKind.PROPERTY,
                location=self._location(node=node, name=node.name),
                annotation=self._annotation(node.returns),
                documentation=ast.get_docstring(node, clean=False),
            )

        return self._function_symbol(
            node=node,
            container=container,
            kind=SymbolKind.METHOD,
        )

    def _function_symbol(
        self,
        node: FunctionNode,
        container: str,
        kind: SymbolKind,
    ) -> Symbol:
        parameters = self._parameters(node.args)
        return_annotation = self._annotation(node.returns)

        return Symbol(
            name=node.name,
            qualified_name=f"{container}.{node.name}",
            kind=kind,
            location=self._location(node=node, name=node.name),
            signature=self._signature(
                name=node.name,
                parameters=parameters,
                return_annotation=return_annotation,
            ),
            documentation=ast.get_docstring(node, clean=False),
            parameters=parameters,
            return_annotation=return_annotation,
        )

    def _assignment_symbols(
        self,
        statement: ast.stmt,
        targets: list[ast.expr] | tuple[ast.expr, ...],
        value: ast.expr | None,
        annotation: ast.expr | None,
        container: str,
    ) -> list[Symbol]:
        symbols: list[Symbol] = []
        property_declaration = self._property_indexer.index(
            value,
            annotation,
        )

        for target in targets:
            for name_node in self._name_targets(target):
                name = name_node.id

                if property_declaration is not None:
                    kind = SymbolKind.PROPERTY
                    symbol_annotation = (
                        property_declaration.annotation
                    )
                elif name.isupper():
                    kind = SymbolKind.CONSTANT
                    symbol_annotation = (
                        self._annotation(annotation)
                        or self._infer_annotation(value)
                    )
                else:
                    kind = SymbolKind.VARIABLE
                    symbol_annotation = (
                        self._annotation(annotation)
                        or self._infer_annotation(value)
                    )

                symbols.append(
                    Symbol(
                        name=name,
                        qualified_name=f"{container}.{name}",
                        kind=kind,
                        location=self._location_from_selection(
                            node=statement,
                            selection=name_node,
                        ),
                        annotation=symbol_annotation,
                        property_info=(
                            property_declaration.info
                            if property_declaration is not None
                            else None
                        ),
                        literal_values=(
                            self._literal_sequences.get(name, ())
                            if container == self._module_name
                            else ()
                        ),
                    )
                )

        return symbols

    def _index_literal_sequences(
        self,
        tree: ast.Module,
    ) -> dict[str, tuple[LiteralValue, ...]]:
        assignments: dict[str, ast.expr] = {}

        for statement in tree.body:
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        assignments[target.id] = statement.value
            elif (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and statement.value is not None
            ):
                assignments[statement.target.id] = statement.value

        values: dict[str, tuple[LiteralValue, ...]] = {}
        unresolved = dict(assignments)

        while unresolved:
            resolved_names: list[str] = []

            for name, expression in unresolved.items():
                resolved = self._literal_sequence(
                    expression,
                    values,
                )

                if resolved is None:
                    continue

                values[name] = resolved
                resolved_names.append(name)

            if not resolved_names:
                break

            for name in resolved_names:
                del unresolved[name]

        return values

    def _literal_sequence(
        self,
        node: ast.expr,
        known: dict[str, tuple[LiteralValue, ...]],
    ) -> tuple[LiteralValue, ...] | None:
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            values: list[LiteralValue] = []

            for element in node.elts:
                is_literal, value = self._literal_value(element)

                if not is_literal:
                    return None

                if not _contains_literal(values, value):
                    values.append(value)

            return tuple(values)

        if isinstance(node, ast.Name):
            return known.get(node.id)

        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, (ast.Add, ast.BitOr))
        ):
            left = self._literal_sequence(
                node.left,
                known,
            )
            right = self._literal_sequence(
                node.right,
                known,
            )

            if left is None or right is None:
                return None

            values = list(left)

            for value in right:
                if not _contains_literal(values, value):
                    values.append(value)

            return tuple(values)

        if isinstance(node, ast.Subscript):
            name = self._qualified_expression(node.value)

            if (
                name is None
                or name.rsplit(".", 1)[-1] != "Literal"
            ):
                return None

            if isinstance(node.slice, ast.Tuple):
                elements = node.slice.elts
            else:
                elements = (node.slice,)

            values: list[LiteralValue] = []

            for element in elements:
                is_literal, value = self._literal_value(element)

                if not is_literal:
                    return None

                if not _contains_literal(values, value):
                    values.append(value)

            return tuple(values)

        return None

    @staticmethod
    def _literal_value(
        node: ast.expr,
    ) -> tuple[bool, LiteralValue]:
        if isinstance(node, ast.Constant):
            value = node.value

            if value is None:
                return True, None

            if isinstance(value, (str, int, float, bool)):
                return True, value

        if not isinstance(node, ast.UnaryOp):
            return False, None

        if not isinstance(node.operand, ast.Constant):
            return False, None

        value = node.operand.value

        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
        ):
            return False, None

        if isinstance(node.op, ast.USub):
            return True, -value

        if isinstance(node.op, ast.UAdd):
            return True, value

        return False, None

    def _instance_members(
        self,
        function: FunctionNode,
        container: str,
    ) -> list[Symbol]:
        members: list[Symbol] = []

        for node in self._walk_method_body(function.body):
            if isinstance(node, ast.Assign):
                members.extend(
                    self._instance_assignment_symbols(
                        statement=node,
                        targets=node.targets,
                        value=node.value,
                        annotation=None,
                        container=container,
                    )
                )
            elif isinstance(node, ast.AnnAssign):
                members.extend(
                    self._instance_assignment_symbols(
                        statement=node,
                        targets=(node.target,),
                        value=node.value,
                        annotation=node.annotation,
                        container=container,
                    )
                )

        return members

    def _instance_assignment_symbols(
        self,
        statement: ast.stmt,
        targets: list[ast.expr] | tuple[ast.expr, ...],
        value: ast.expr | None,
        annotation: ast.expr | None,
        container: str,
    ) -> list[Symbol]:
        symbols: list[Symbol] = []

        for target in targets:
            for attribute in self._self_attribute_targets(target):
                name = attribute.attr
                symbol_annotation = (
                    self._annotation(annotation)
                    or self._infer_annotation(value)
                )
                symbols.append(
                    Symbol(
                        name=name,
                        qualified_name=f"{container}.{name}",
                        kind=SymbolKind.VARIABLE,
                        location=self._location_from_name(
                            node=statement,
                            selection_node=attribute,
                            name=name,
                            prefer_last=True,
                        ),
                        annotation=symbol_annotation,
                    )
                )

        return symbols

    def _event_symbols(
        self,
        value: ast.expr,
        container: str,
    ) -> list[Symbol]:
        if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            return []

        symbols: list[Symbol] = []

        for element in value.elts:
            if not isinstance(element, ast.Constant):
                continue

            if not isinstance(element.value, str):
                continue

            name = element.value
            symbols.append(
                Symbol(
                    name=name,
                    qualified_name=f"{container}.{name}",
                    kind=SymbolKind.EVENT,
                    location=self._location(
                        node=element,
                        name=name,
                    ),
                )
            )

        return symbols

    def _parameters(
        self,
        arguments: ast.arguments,
    ) -> tuple[ParameterSymbol, ...]:
        parameters: list[ParameterSymbol] = []
        positional = [
            *arguments.posonlyargs,
            *arguments.args,
        ]
        empty_defaults = len(positional) - len(arguments.defaults)
        positional_defaults: list[ast.expr | None] = [
            *([None] * empty_defaults),
            *arguments.defaults,
        ]

        for index, argument in enumerate(arguments.posonlyargs):
            parameters.append(
                self._parameter(
                    argument=argument,
                    kind=ParameterKind.POSITIONAL_ONLY,
                    default=positional_defaults[index],
                )
            )

        offset = len(arguments.posonlyargs)

        for index, argument in enumerate(arguments.args):
            parameters.append(
                self._parameter(
                    argument=argument,
                    kind=ParameterKind.POSITIONAL_OR_KEYWORD,
                    default=positional_defaults[offset + index],
                )
            )

        if arguments.vararg is not None:
            parameters.append(
                self._parameter(
                    argument=arguments.vararg,
                    kind=ParameterKind.VAR_POSITIONAL,
                    default=None,
                )
            )

        for argument, default in zip(
            arguments.kwonlyargs,
            arguments.kw_defaults,
            strict=True,
        ):
            parameters.append(
                self._parameter(
                    argument=argument,
                    kind=ParameterKind.KEYWORD_ONLY,
                    default=default,
                )
            )

        if arguments.kwarg is not None:
            parameters.append(
                self._parameter(
                    argument=arguments.kwarg,
                    kind=ParameterKind.VAR_KEYWORD,
                    default=None,
                )
            )

        return tuple(parameters)

    def _parameter(
        self,
        argument: ast.arg,
        kind: ParameterKind,
        default: ast.expr | None,
    ) -> ParameterSymbol:
        return ParameterSymbol(
            name=argument.arg,
            kind=kind,
            annotation=self._annotation(argument.annotation),
            default=self._expression_text(default),
        )

    def _signature(
        self,
        name: str,
        parameters: tuple[ParameterSymbol, ...],
        return_annotation: str | None,
    ) -> str:
        parts: list[str] = []
        positional_only_count = sum(
            parameter.kind is ParameterKind.POSITIONAL_ONLY
            for parameter in parameters
        )
        added_positional_only_marker = False
        added_keyword_only_marker = False

        for index, parameter in enumerate(parameters):
            if (
                positional_only_count
                and index == positional_only_count
                and not added_positional_only_marker
            ):
                parts.append("/")
                added_positional_only_marker = True

            if (
                parameter.kind is ParameterKind.KEYWORD_ONLY
                and not added_keyword_only_marker
                and not any(
                    item.kind is ParameterKind.VAR_POSITIONAL
                    for item in parameters
                )
            ):
                parts.append("*")
                added_keyword_only_marker = True

            parts.append(self._parameter_text(parameter))

        if (
            positional_only_count
            and positional_only_count == len(parameters)
        ):
            parts.append("/")

        signature = f"{name}({', '.join(parts)})"

        if return_annotation is not None:
            signature = f"{signature} -> {return_annotation}"

        return signature

    @staticmethod
    def _parameter_text(parameter: ParameterSymbol) -> str:
        prefix = ""

        if parameter.kind is ParameterKind.VAR_POSITIONAL:
            prefix = "*"
        elif parameter.kind is ParameterKind.VAR_KEYWORD:
            prefix = "**"

        text = f"{prefix}{parameter.name}"

        if parameter.annotation is not None:
            text = f"{text}: {parameter.annotation}"

        if parameter.default is not None:
            text = f"{text} = {parameter.default}"

        return text

    def _location(
        self,
        node: ast.AST,
        name: str,
        prefer_last: bool = False,
    ) -> SymbolLocation:
        span = self._node_span(node)
        selection = self._find_name_span(
            span=span,
            name=name,
            prefer_last=prefer_last,
        )
        return SymbolLocation(
            uri=self._document.uri,
            span=span,
            selection_span=selection,
        )

    def _location_from_selection(
        self,
        node: ast.AST,
        selection: ast.AST,
    ) -> SymbolLocation:
        return SymbolLocation(
            uri=self._document.uri,
            span=self._node_span(node),
            selection_span=self._node_span(selection),
        )

    def _location_from_name(
        self,
        node: ast.AST,
        selection_node: ast.AST,
        name: str,
        prefer_last: bool,
    ) -> SymbolLocation:
        selection_source = self._node_span(selection_node)
        selection = self._find_name_span(
            span=selection_source,
            name=name,
            prefer_last=prefer_last,
        )
        return SymbolLocation(
            uri=self._document.uri,
            span=self._node_span(node),
            selection_span=selection,
        )

    def _node_span(self, node: ast.AST) -> Span:
        line = getattr(node, "lineno", 1)
        column = getattr(node, "col_offset", 0)
        end_line = getattr(node, "end_lineno", None) or line
        end_column = getattr(node, "end_col_offset", None)

        start = self._document.offset_at(
            TextPosition(
                line=max(0, line - 1),
                character=max(0, column),
            ),
            PositionEncoding.UTF8,
        )

        if end_column is None:
            return Span.empty(start)

        end = self._document.offset_at(
            TextPosition(
                line=max(0, end_line - 1),
                character=max(0, end_column),
            ),
            PositionEncoding.UTF8,
        )
        return Span(start=start, end=max(start, end))

    def _find_name_span(
        self,
        span: Span,
        name: str,
        prefer_last: bool,
    ) -> Span:
        source = self._document.text[span.start:span.end]

        relative = source.rfind(name) if prefer_last else source.find(name)

        if relative < 0:
            return Span.empty(span.start)

        start = span.start + relative
        return Span(start=start, end=start + len(name))

    @staticmethod
    def _factory_local_names(
        imports: list[ImportBinding],
    ) -> set[str]:
        names = {"Factory"}

        for binding in imports:
            if binding.target_module != "kivy.factory":
                continue

            if binding.target_name != "Factory":
                continue

            names.add(binding.local_name)

        return names

    @staticmethod
    def _is_factory_register_call(
        node: ast.Call,
        factory_names: set[str],
    ) -> bool:
        qualified_name = _ModuleIndexer._qualified_expression(
            node.func,
        )

        if qualified_name is None:
            return False

        parts = qualified_name.split(".")

        if len(parts) < 2 or parts[-1] != "register":
            return False

        receiver = ".".join(parts[:-1])

        return (
            receiver in factory_names
            or parts[-2] == "Factory"
        )

    @staticmethod
    def _call_argument(
        node: ast.Call,
        position: int,
        keyword_name: str,
    ) -> ast.expr | None:
        for keyword_argument in node.keywords:
            if keyword_argument.arg == keyword_name:
                return keyword_argument.value

        if position < len(node.args):
            return node.args[position]

        return None

    @staticmethod
    def _string_value(
        node: ast.expr | None,
    ) -> str | None:
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
        ):
            return node.value

        return None

    @staticmethod
    def _bool_value(
        node: ast.expr | None,
    ) -> bool | None:
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, bool)
        ):
            return node.value

        return None

    @staticmethod
    def _baseclass_names(
        node: ast.expr | None,
    ) -> tuple[str, ...]:
        values: list[str] = []

        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
        ):
            values.append(node.value)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for element in node.elts:
                if (
                    isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                ):
                    values.append(element.value)

        names: list[str] = []

        for value in values:
            pieces = value.replace(",", "+").split("+")

            for piece in pieces:
                name = piece.strip()

                if name and name not in names:
                    names.append(name)

        return tuple(names)

    @staticmethod
    def _name_targets(target: ast.expr) -> tuple[ast.Name, ...]:
        if isinstance(target, ast.Name):
            return (target,)

        if isinstance(target, (ast.List, ast.Tuple)):
            names: list[ast.Name] = []

            for element in target.elts:
                names.extend(_ModuleIndexer._name_targets(element))

            return tuple(names)

        return ()

    @staticmethod
    def _self_attribute_targets(
        target: ast.expr,
    ) -> tuple[ast.Attribute, ...]:
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            return (target,)

        if isinstance(target, (ast.List, ast.Tuple)):
            attributes: list[ast.Attribute] = []

            for element in target.elts:
                attributes.extend(
                    _ModuleIndexer._self_attribute_targets(element)
                )

            return tuple(attributes)

        return ()

    @staticmethod
    def _walk_method_body(
        body: list[ast.stmt],
    ) -> tuple[ast.AST, ...]:
        found: list[ast.AST] = []
        pending: list[ast.AST] = list(reversed(body))

        while pending:
            node = pending.pop()
            found.append(node)
            children: list[ast.AST] = []

            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child,
                    (
                        ast.FunctionDef,
                        ast.AsyncFunctionDef,
                        ast.ClassDef,
                        ast.Lambda,
                    ),
                ):
                    continue

                children.append(child)

            pending.extend(reversed(children))

        return tuple(found)

    @staticmethod
    def _is_events_assignment(
        targets: list[ast.expr],
    ) -> bool:
        return any(
            isinstance(target, ast.Name)
            and target.id == "__events__"
            for target in targets
        )

    @staticmethod
    def _is_property_method(node: FunctionNode) -> bool:
        for decorator in node.decorator_list:
            name = _ModuleIndexer._qualified_expression(decorator)

            if name is None:
                continue

            if name.rsplit(".", 1)[-1] in {
                "property",
                "cached_property",
            }:
                return True

        return False

    def _infer_annotation(
        self,
        value: ast.expr | None,
    ) -> str | None:
        if value is None:
            return None

        if isinstance(value, ast.Constant):
            if value.value is None:
                return "None"

            return type(value.value).__name__

        if isinstance(value, ast.List):
            return "list[Any]"

        if isinstance(value, ast.Tuple):
            return "tuple[Any, ...]"

        if isinstance(value, ast.Set):
            return "set[Any]"

        if isinstance(value, ast.Dict):
            return "dict[Any, Any]"

        if isinstance(value, ast.Call):
            return self._qualified_expression(value.func)

        return None

    @staticmethod
    def _annotation(node: ast.expr | None) -> str | None:
        return _ModuleIndexer._expression_text(node)

    @staticmethod
    def _expression_text(node: ast.AST | None) -> str | None:
        if node is None:
            return None

        return ast.unparse(node)

    @staticmethod
    def _qualified_expression(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id

        if isinstance(node, ast.Attribute):
            parent = _ModuleIndexer._qualified_expression(
                node.value,
            )

            if parent is None:
                return None

            return f"{parent}.{node.attr}"

        if isinstance(node, ast.Subscript):
            parent = _ModuleIndexer._qualified_expression(
                node.value,
            )

            if parent is None:
                return None

            arguments = _ModuleIndexer._expression_text(
                node.slice,
            )

            if arguments is None:
                return None

            return f"{parent}[{arguments}]"

        return None

def _contains_literal(
    values: list[LiteralValue],
    candidate: LiteralValue,
) -> bool:
    return any(
        type(value) is type(candidate)
        and value == candidate
        for value in values
    )

def _syntax_error_span(
    document: TextDocument,
    error: SyntaxError,
) -> Span:
    start_line = max(0, (error.lineno or 1) - 1)
    start_character = max(0, (error.offset or 1) - 1)
    end_line = max(
        start_line,
        (error.end_lineno or start_line + 1) - 1,
    )
    end_character = max(
        start_character,
        (error.end_offset or start_character + 2) - 1,
    )
    start = document.offset_at(
        TextPosition(
            line=start_line,
            character=start_character,
        ),
        PositionEncoding.UTF32,
    )
    end = document.offset_at(
        TextPosition(
            line=end_line,
            character=end_character,
        ),
        PositionEncoding.UTF32,
    )
    return Span(
        start=start,
        end=max(start, end),
    )
