"""Conservative identity-based references for KV IDs and dynamic classes."""

from __future__ import annotations

import ast
import keyword
from collections.abc import Callable
from dataclasses import dataclass

from kivy_lsp.analysis.editor_features import walk_nodes
from kivy_lsp.analysis.scope import KvScope, KvSemanticModel
from kivy_lsp.kv.expression_source import EmbeddedPythonSource
from kivy_lsp.kv.index import KvIndex
from kivy_lsp.kv.nodes import PropertyNode, RuleNode, WidgetNode
from kivy_lsp.kv.parser import ParseResult
from kivy_lsp.model.span import Span
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.workspace.document import (
    PositionEncoding,
    TextDocument,
    TextPosition,
)


@dataclass(frozen=True, slots=True)
class ReferenceDocument:
    document: TextDocument
    parsed: ParseResult
    model: KvSemanticModel


@dataclass(frozen=True, slots=True)
class SymbolIdentity:
    kind: str
    name: str
    uri: str
    declaration: Span
    scope_span: Span | None = None


@dataclass(frozen=True, slots=True)
class Reference:
    identity: SymbolIdentity
    uri: str
    span: Span
    declaration: bool = False


class RenameError(ValueError):
    """A rename is unsupported or cannot be proved unambiguous."""


class KvReferenceEngine:
    """Build references from parsed declarations and resolved lexical scopes.

    Supported Python links are static self.ids attributes/subscripts/get calls.
    Dynamic lookups or ambiguous ownership disable rename for affected IDs.
    """

    def __init__(
        self,
        documents: tuple[ReferenceDocument, ...],
        python_index: PythonIndex,
        kv_index: KvIndex,
        python_documents: tuple[TextDocument, ...] = (),
    ) -> None:
        self.documents = documents
        self.python_index = python_index
        self.kv_index = kv_index
        self.references: list[Reference] = []
        self.blocked: dict[SymbolIdentity, str] = {}
        self.scope_ids: dict[tuple[str, Span], dict[str, SymbolIdentity]] = {}
        self.dynamic: dict[str, SymbolIdentity] = {}
        self._collect_declarations()
        for item in documents:
            self._collect_kv(item)
        for document in python_documents:
            self._collect_python(document)
        self.references = list(dict.fromkeys(self.references))

    def target_at(self, uri: str, offset: int) -> Reference | None:
        matches = [
            item
            for item in self.references
            if item.uri == uri and item.span.contains(offset)
        ]
        identities = {item.identity for item in matches}
        return matches[0] if len(identities) == 1 else None

    def references_to(
        self,
        identity: SymbolIdentity,
        include_declaration: bool = True,
    ) -> tuple[Reference, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self.references
                    if item.identity == identity
                    and (include_declaration or not item.declaration)
                ),
                key=lambda item: (item.uri, item.span.start),
            )
        )

    def prepare_rename(self, target: Reference) -> None:
        reason = self.blocked.get(target.identity)
        if reason:
            raise RenameError(reason)

    def rename(
        self,
        target: Reference,
        new_name: str,
    ) -> tuple[Reference, ...]:
        self.prepare_rename(target)
        if not new_name.isidentifier() or keyword.iskeyword(new_name):
            raise RenameError("The new name must be a non-keyword identifier.")
        identity = target.identity
        if identity.kind == "id":
            if new_name in {"root", "self", "app", "args"}:
                raise RenameError("That name is reserved in KV expressions.")
            for item in self.documents:
                for scope in item.model.scopes:
                    key = (scope.uri, scope.span)
                    ids = self.scope_ids.get(key, {})
                    if identity not in ids.values():
                        continue
                    binding = scope.binding_named(new_name)
                    if binding is not None and new_name != identity.name:
                        raise RenameError(
                            "The name already exists in this scope."
                        )
                    # An otherwise unresolved name would become captured by
                    # the new ID. Reject rather than change its meaning.
                    for token in item.parsed.tokens:
                        if (
                            token.text == new_name
                            and scope.span.encloses(token.span)
                            and new_name != identity.name
                        ):
                            raise RenameError(
                                "The new name would capture an existing name."
                            )
        else:
            if not new_name[0].isupper():
                raise RenameError("Dynamic widget names must start uppercase.")
            if new_name != identity.name and (
                self.kv_index.contains(new_name)
                or self.python_index.classes_named(new_name)
                or self.python_index.factory_registrations_named(new_name)
            ):
                raise RenameError("A widget with that name already exists.")
        return self.references_to(identity)

    def _collect_declarations(self) -> None:
        for name in self.kv_index.names():
            definitions = [
                item for item in self.kv_index.find(name) if item.is_dynamic
            ]
            if len(definitions) != 1:
                continue
            if self.python_index.classes_named(name):
                continue
            symbol = definitions[0]
            self.dynamic[name] = SymbolIdentity(
                "class",
                name,
                symbol.uri,
                symbol.span,
            )
        for item in self.documents:
            for scope in item.model.scopes:
                names: dict[str, SymbolIdentity] = {}
                for binding in scope.id_bindings:
                    span = binding.declaration_span
                    if span is None:
                        continue
                    identity = SymbolIdentity(
                        "id",
                        binding.name,
                        scope.uri,
                        span,
                        scope.span,
                    )
                    names[binding.name] = identity
                    self._add(identity, scope.uri, span, True)
                self.scope_ids[scope.uri, scope.span] = names
                if isinstance(scope.owner, RuleNode) and any(
                    len(self.kv_index.find(selector.name.text)) > 1
                    for selector in scope.owner.selectors
                ):
                    self._block_ids(
                        scope,
                        "IDs shared by multiple rules cannot be renamed.",
                    )
                if any(
                    diagnostic.code == "kv-id-duplicate"
                    and scope.span.encloses(diagnostic.span)
                    for diagnostic in item.model.diagnostics
                ):
                    self._block_ids(scope, "This scope has duplicate IDs.")

    def _add(
        self,
        identity: SymbolIdentity,
        uri: str,
        span: Span,
        declaration: bool = False,
    ) -> None:
        self.references.append(Reference(identity, uri, span, declaration))

    def _block_ids(self, scope: KvScope, reason: str) -> None:
        for identity in self.scope_ids.get(
            (scope.uri, scope.span), {}
        ).values():
            self.blocked[identity] = reason

    def _block_all_ids(self, reason: str) -> None:
        for names in self.scope_ids.values():
            for identity in names.values():
                self.blocked[identity] = reason

    def _class_use(self, name: str, uri: str, span: Span) -> None:
        identity = self.dynamic.get(name)
        if identity is not None:
            declaration = uri == identity.uri and span == identity.declaration
            self._add(identity, uri, span, declaration)

    def _collect_kv(self, item: ReferenceDocument) -> None:
        uri = item.document.uri
        if item.parsed.diagnostics:
            for identity in self.dynamic.values():
                self.blocked[identity] = (
                    "Fix KV syntax errors before renaming a project widget."
                )
        for node in walk_nodes(item.parsed.document):
            if isinstance(node, RuleNode):
                for selector in node.selectors:
                    if selector.is_class_selector:
                        continue
                    self._class_use(
                        selector.name.text, uri, selector.name.span
                    )
                    for base in selector.base_names:
                        self._class_use(base.text, uri, base.span)
            elif isinstance(node, WidgetNode):
                self._class_use(node.name.text, uri, node.name.span)
            elif isinstance(node, PropertyNode) and node.value is not None:
                if node.name == "id":
                    continue
                scope = item.model.scope_at(node.span.start)
                if scope is None:
                    continue
                source = EmbeddedPythonSource.from_source(
                    item.document.text,
                    node.value.span,
                    statement_block=node.is_event_handler,
                )
                try:
                    tree = ast.parse(
                        source.text,
                        mode="exec" if node.is_event_handler else "eval",
                    )
                except SyntaxError:
                    self._block_ids(
                        scope, "Fix this scope's expressions first."
                    )
                    continue
                for target in ast.walk(tree):
                    name = (
                        target.attr
                        if isinstance(target, ast.Attribute)
                        else target.value
                        if isinstance(target, ast.Constant)
                        and isinstance(target.value, str)
                        else None
                    )
                    if name in self.dynamic:
                        self.blocked[self.dynamic[name]] = (
                            "This class is used in an expression or string; "
                            "rename supports KV widget syntax only."
                        )
                visitor = _IdVisitor(self, scope, source)
                visitor.visit(tree)

    def _class_id_targets(
        self,
        class_name: str,
        name: str | None,
    ) -> tuple[SymbolIdentity, ...]:
        declarations = (
            self.kv_index.id_definitions_for_class(class_name, name)
            if name is not None
            else self.kv_index.ids_for_class(class_name)
        )
        return tuple(
            dict.fromkeys(
                ref.identity
                for declaration in declarations
                for ref in self.references
                if ref.declaration
                and ref.uri == declaration.uri
                and ref.span == declaration.span
            )
        )

    def _collect_python(self, document: TextDocument) -> None:
        try:
            tree = ast.parse(document.text)
        except SyntaxError:
            for identity in (
                *self.dynamic.values(),
                *(ref.identity for ref in self.references if ref.declaration),
            ):
                self.blocked[identity] = (
                    "Fix Python syntax errors before cross-language rename."
                )
            return

        def node_span(node: ast.AST) -> Span:
            if not isinstance(node, (ast.expr, ast.stmt)):
                raise TypeError("Expected a Python source node.")
            end_line = node.end_lineno or node.lineno
            end_column = node.end_col_offset or node.col_offset
            return Span(
                document.offset_at(
                    TextPosition(node.lineno - 1, node.col_offset),
                    PositionEncoding.UTF8,
                ),
                document.offset_at(
                    TextPosition(end_line - 1, end_column),
                    PositionEncoding.UTF8,
                ),
            )

        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        for node in ast.walk(tree):
            dynamic_name = (
                node.id
                if isinstance(node, ast.Name)
                else node.attr
                if isinstance(node, ast.Attribute)
                else node.value
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                else None
            )
            if dynamic_name in self.dynamic:
                self.blocked[self.dynamic[dynamic_name]] = (
                    "This widget is referenced from Python; class rename "
                    "currently supports KV declarations and KV uses only."
                )
            namespace = _ids_namespace(node)
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "ids"
                and namespace != "self"
            ):
                self._block_all_ids(
                    "Python ID namespace ownership is unresolved."
                )
                continue
            if namespace != "self":
                continue
            owner = parents.get(node)
            class_node = node
            while class_node in parents and not isinstance(
                class_node,
                ast.ClassDef,
            ):
                class_node = parents[class_node]
            if not isinstance(class_node, ast.ClassDef):
                self._block_all_ids("Python ID ownership is unresolved.")
                continue
            name, target_node = _id_access(owner, node)
            if (
                isinstance(owner, ast.Attribute)
                and owner.attr == "get"
                and isinstance(call := parents.get(owner), ast.Call)
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
            ):
                name, target_node = call.args[0].value, call.args[0]
            identities = self._class_id_targets(class_node.name, name)
            if (
                not identities
                or len(
                    self.python_index.classes_named(
                        class_node.name,
                    )
                )
                > 1
            ):
                self._block_all_ids(
                    "Python ID ownership is inherited or ambiguous."
                )
                continue
            if len(identities) == 1 and name is not None:
                identity = identities[0]
                span = _access_span(target_node, name, node_span)
                if span is not None:
                    self._add(identity, document.uri, span)
                else:
                    self.blocked[identity] = (
                        "This Python ID key cannot be safely edited."
                    )
            else:
                for identity in identities:
                    self.blocked[identity] = (
                        "Python ID access has dynamic or ambiguous ownership."
                    )


def _ids_namespace(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "ids"
        and isinstance(node.value, ast.Name)
    ):
        return node.value.id
    return None


def _id_access(
    node: ast.AST | None,
    namespace: ast.AST,
) -> tuple[str | None, ast.AST | None]:
    if isinstance(node, ast.Attribute) and node.value is namespace:
        if node.attr == "get":
            return None, None
        return node.attr, node
    if isinstance(node, ast.Subscript) and node.value is namespace:
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value, key
    return None, None


def _access_span(
    node: ast.AST | None,
    name: str,
    node_span: Callable[[ast.AST], Span],
) -> Span | None:
    if node is None:
        return None
    span = node_span(node)
    if isinstance(node, ast.Attribute):
        return Span(span.end - len(name), span.end)
    # Only ordinary unescaped string keys are editable. A single quoted
    # identifier occupies exactly len(name) + 2 characters.
    if span.length == len(name) + 2:
        return Span(span.start + 1, span.end - 1)
    return None


class _IdVisitor(ast.NodeVisitor):
    def __init__(
        self,
        engine: KvReferenceEngine,
        scope: KvScope,
        source: EmbeddedPythonSource,
    ) -> None:
        self.engine = engine
        self.scope = scope
        self.source = source
        self.locals: set[str] = set()
        self.ids = engine.scope_ids.get((scope.uri, scope.span), {})

    def _record(self, name: str, node: ast.AST) -> None:
        identity = self.ids.get(name)
        if identity is not None:
            span = _access_span(node, name, self.source.node_span)
            if isinstance(node, ast.Name):
                span = self.source.node_span(node)
            if span is None:
                self.engine.blocked[identity] = (
                    "This ID uses an escaped key that cannot be safely edited."
                )
            else:
                self.engine._add(identity, self.scope.uri, span)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            if node.id in self.ids:
                self.engine._block_ids(
                    self.scope,
                    "An event-local name conflicts with a KV ID.",
                )
            self.locals.add(node.id)
            return
        if node.id not in self.locals:
            self._record(node.id, node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "ids" and _ids_namespace(node) not in {"root", "self"}:
            self.engine._block_all_ids(
                "KV ID namespace ownership is unresolved."
            )
            return
        if _ids_namespace(node) == "self":
            self.engine._block_ids(
                self.scope,
                "self.ids ownership is not established for rename.",
            )
            return
        namespace = _ids_namespace(node.value)
        if namespace == "root" and "root" not in self.locals:
            if node.attr == "get":
                self.engine._block_ids(
                    self.scope,
                    "Dynamic ID namespace access prevents rename.",
                )
            else:
                self._record(node.attr, node)
            return
        if _ids_namespace(node) == "root":
            self.engine._block_ids(
                self.scope,
                "ID namespace escapes prevent a complete rename.",
            )
            return
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _ids_namespace(node.value) == "root" and "root" not in self.locals:
            if isinstance(node.slice, ast.Constant) and isinstance(
                node.slice.value,
                str,
            ):
                self._record(node.slice.value, node.slice)
            else:
                self.engine._block_ids(
                    self.scope,
                    "A computed ID lookup prevents complete rename.",
                )
            self.visit(node.slice)
            return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and function.attr == "get"
            and _ids_namespace(function.value) == "root"
            and "root" not in self.locals
        ):
            if (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and (isinstance(node.args[0].value, str))
            ):
                self._record(node.args[0].value, node.args[0])
                for argument in (*node.args[1:], *node.keywords):
                    self.visit(argument)
            else:
                self.engine._block_ids(
                    self.scope,
                    "A computed ID lookup prevents complete rename.",
                )
            return
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for value in (*node.args.defaults, *node.args.kw_defaults):
            if value is not None:
                self.visit(value)
        previous = self.locals.copy()
        self.locals.update(
            argument.arg
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
        )
        if node.args.vararg:
            self.locals.add(node.args.vararg.arg)
        if node.args.kwarg:
            self.locals.add(node.args.kwarg.arg)
        self.visit(node.body)
        self.locals = previous

    def _comprehension(self, node) -> None:
        previous = self.locals.copy()
        for generator in node.generators:
            self.visit(generator.iter)
            self.locals.update(
                item.id
                for item in ast.walk(generator.target)
                if isinstance(item, ast.Name)
            )
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.locals = previous

    visit_ListComp = _comprehension
    visit_SetComp = _comprehension
    visit_DictComp = _comprehension
    visit_GeneratorExp = _comprehension
