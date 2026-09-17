from pathlib import Path

import pytest

from kivy_lsp.analysis.completion import KvCompletionEngine
from kivy_lsp.analysis.definition import KvDefinitionEngine
from kivy_lsp.analysis.diagnostics import KvDiagnosticAnalyzer
from kivy_lsp.analysis.expression import KvExpressionResolver
from kivy_lsp.analysis.scope_builder import build_kv_semantic_model
from kivy_lsp.analysis.value_inference import KvValueInferer
from kivy_lsp.analysis.widget_resolution import resolve_widget_class
from kivy_lsp.config import I18nConfig, ServerConfig
from kivy_lsp.i18n.index import TranslationIndex
from kivy_lsp.kv.index import KvClassSymbol, KvIndex
from kivy_lsp.kv.nodes import PropertyNode, RuleNode
from kivy_lsp.kv.parser import parse
from kivy_lsp.model.value_type import literal_type, union_type
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.workspace.document import TextDocument


PYTHON = '''from kivy.properties import StringProperty, ColorProperty
from kivy.properties import NumericProperty
class Demo:
    text = StringProperty("")
    color = ColorProperty([1, 1, 1, 1])
    width = NumericProperty(0)
    name: str | None
    active: bool
class Other:
    height = NumericProperty(0)
'''


def index_python(source=PYTHON):
    index = PythonIndex()
    result = index_python_module(
        TextDocument('file:///tmp/demo.py', source), 'demo',
    )
    assert result.module is not None
    index.replace(result.module)
    return index


def config():
    return ServerConfig(Path('/tmp'), (), ())


def analyze(source, index=None, kv_index=None):
    index = index if index is not None else index_python()
    document = TextDocument('file:///tmp/view.kv', source)
    parsed = parse(source)
    model = build_kv_semantic_model(
        document, parsed, index, config(), kv_index,
    )
    diagnostics = KvDiagnosticAnalyzer(index, config(), kv_index).analyze(
        document, parsed, model,
    )
    return document, parsed, model, diagnostics


def index_kv(index, source, uri='file:///tmp/classes.kv'):
    parsed = parse(source)
    index.replace(uri, (
        KvClassSymbol(
            name=selector.name.text,
            uri=uri,
            span=selector.name.span,
            bases=tuple(base.text for base in selector.base_names),
            is_dynamic=selector.is_dynamic,
            properties=tuple(
                node for node in rule.body if isinstance(node, PropertyNode)
            ),
        )
        for rule in parsed.document.items if isinstance(rule, RuleNode)
        for selector in rule.selectors
    ))


@pytest.mark.parametrize('expression', (
    '"" or "fallback"',
    '"hello" and "world"',
    'root.name or "Unnamed"',
    'root.active and "yes" or "no"',
))
def test_boolean_operators_return_operands(expression):
    *_, diagnostics = analyze(f'<Demo>:\n    text: {expression}\n')
    assert not diagnostics


def test_boolean_short_circuit_retains_known_literal():
    index = index_python()
    _, _, model, _ = analyze('<Demo>:\n    text: ""\n', index)
    result = KvValueInferer(KvExpressionResolver(index, config())).infer(
        'False and unknown_value', model.scopes[0],
    )
    assert result.literal_known and result.literal is False


@pytest.mark.parametrize('expression', (
    '[*[1, 1, 1, 1]]', '[*[1, 1, 1], 1]', '[1, 1, 1, 1]',
))
def test_starred_color_length(expression):
    *_, diagnostics = analyze(f'<Demo>:\n    color: {expression}\n')
    assert not diagnostics


def test_known_wrong_expanded_length_is_reported():
    *_, diagnostics = analyze('<Demo>:\n    color: [*[1]]\n')
    assert any('received 1' in item.message for item in diagnostics)


@pytest.mark.parametrize('expression', (
    '[x for x in []] + [x]',
    '[(lambda x: x)(1), x]',
    '[x for x in x]',
    'lambda x=x: x',
))
def test_expression_local_names_do_not_leak(expression):
    *_, diagnostics = analyze(f'<Demo>:\n    data: {expression}\n')
    errors = [item for item in diagnostics if item.code == 'kv-unknown-name']
    assert len(errors) == 1
    assert '"x"' in errors[0].message


@pytest.mark.parametrize('expression', (
    '[(x, y) for x in [] for y in [x]]',
    '[x for x in [] if x]',
    '(lambda x, *args, **kwargs: (x, args, kwargs))(1)',
))
def test_valid_nested_expression_scopes(expression):
    *_, diagnostics = analyze(f'<Demo>:\n    data: {expression}\n')
    assert not diagnostics


def test_typed_literal_identity_and_hashing():
    members = (literal_type(True), literal_type(1), literal_type(1.0))
    assert len(set(members)) == 3
    assert union_type(*members).arguments == members
    assert len(union_type(literal_type(False), literal_type(0)).arguments) == 2


def test_set_constant_has_type_completion_and_definition():
    source = '#:set GREETING "hello"\n<Demo>:\n    text: GREETING\n'
    index = index_python()
    document, parsed, model, diagnostics = analyze(source, index)
    assert not diagnostics
    binding = model.scopes[0].binding_named('GREETING')
    assert binding is not None
    assert binding.value.type_name == 'str'
    offset = source.rindex('GREETING') + len('GREETING')
    result = KvCompletionEngine(index, KvIndex(), config()).complete(
        document, parsed, model, offset,
    )
    assert result is not None
    assert any(item.label == 'GREETING' for item in result.items)
    definitions = KvDefinitionEngine(
        index, KvIndex(), config(), TranslationIndex(None),
    ).definition_at(document, parsed, model, offset - 1)
    assert definitions
    assert source[definitions[0].selection_span.start:
                  definitions[0].selection_span.end] == 'GREETING'
    assert definitions[0].selection_span.start == source.index('GREETING')


def test_dynamic_widget_preserves_bases_members_and_diagnostics():
    index = index_python()
    kv_index = KvIndex()
    index_kv(kv_index, '<Special@Demo+Other>:\n    caption: "Hello"\n')
    index_kv(
        kv_index, '<Nested@Special>:\n    marker: 2\n',
        'file:///tmp/nested.kv',
    )
    resolved = resolve_widget_class('Nested', index, kv_index)
    assert resolved is not None
    assert resolved.name == 'Nested'
    assert resolved.uri == 'file:///tmp/nested.kv'
    members = {member.name: member for member in index.members_of(resolved)}
    assert {'caption', 'marker', 'height', 'width'} <= members.keys()
    assert members['caption'].annotation == 'str'
    *_, diagnostics = analyze('Nested:\n    width: "bad"\n', index, kv_index)
    assert any(item.code == 'kv-incompatible-property-value'
               for item in diagnostics)


def test_dynamic_class_same_document_without_explicit_index():
    *_, diagnostics = analyze(
        '<Special@Demo>:\n    width: 1\nSpecial:\n    width: "bad"\n',
    )
    assert len(diagnostics) == 1
    assert diagnostics[0].code == 'kv-incompatible-property-value'


def test_dynamic_completion_and_definition_use_the_same_class():
    index = index_python()
    kv_index = KvIndex()
    source = '<Special@Demo>:\n    caption: "Hello"\n'
    index_kv(kv_index, source)
    document, parsed, model, _ = analyze(
        'Special:\n    text: self.caption\n', index, kv_index,
    )
    offset = document.text.index('caption') + len('caption')
    result = KvCompletionEngine(index, kv_index, config()).complete(
        document, parsed, model, offset,
    )
    assert result is not None
    assert [item.label for item in result.items] == ['caption']
    definitions = KvDefinitionEngine(
        index, kv_index, config(), TranslationIndex(None),
    ).definition_at(document, parsed, model, offset - 1)
    assert len(definitions) == 1
    assert definitions[0].uri == 'file:///tmp/classes.kv'
    span = definitions[0].selection_span
    assert source[span.start:span.end] == 'caption'


def test_multiline_error_spans_retain_unicode_and_skipped_lines():
    source = (
        '<Demo>:\n    data:\n        (\n'
        '        "é",\n        # explanation\n        missing\n        )\n'
    )
    *_, diagnostics = analyze(source)
    unknown = [item for item in diagnostics if item.code == 'kv-unknown-name']
    assert len(unknown) == 1
    assert source[unknown[0].span.start:unknown[0].span.end] == 'missing'


def test_dynamic_class_duplicate_and_cycles_are_conservative():
    index = index_python()
    kv_index = KvIndex()
    index_kv(kv_index, '<Special@Demo>:\n')
    index_kv(kv_index, '<Special@Other>:\n', 'file:///tmp/other.kv')
    assert resolve_widget_class('Special', index, kv_index) is None
    cyclic = KvIndex()
    index_kv(cyclic, '<First@Second>:\n<Second@First>:\n')
    resolved = resolve_widget_class('First', index, cyclic)
    assert resolved is not None
    assert index.members_of(resolved) == ()


def test_factory_alias_resolves_in_scopes():
    index = index_python(PYTHON + '''
from kivy.factory import Factory
Factory.register("AliasDemo", cls=Demo)
''')
    *_, diagnostics = analyze('AliasDemo:\n    width: "bad"\n', index)
    assert any(item.code == 'kv-incompatible-property-value'
               for item in diagnostics)


def test_union_method_call_keeps_both_return_types():
    index = index_python('''
class A:
    def get(self) -> int: ...
class B:
    def get(self) -> str: ...
class Demo:
    item: A | B
''')
    _, _, model, _ = analyze('<Demo>:\n    value: root.item.get()\n', index)
    result = KvValueInferer(KvExpressionResolver(index, config())).infer(
        'root.item.get()', model.scopes[0],
    )
    assert result.value_type.display == 'int | str'


def test_escaped_translation_placeholders_and_overlay(tmp_path):
    path = tmp_path / 'translations.json'
    source = r'{"hello": "\ud83d\ude00 \u007bname\u007d"}'
    path.write_text(source)
    index = TranslationIndex(I18nConfig(path))
    entry = index.entry('hello')
    assert entry is not None
    assert entry.placeholder_names == ('name',)
    placeholder = entry.placeholders[0]
    assert source[placeholder.span.start:placeholder.span.end] == (
        r'\u007bname\u007d'
    )
    index.set_overlay('{"hello": "{other}"}')
    assert index.entry('hello').placeholder_names == ('other',)
    path.write_text('{"hello": "{saved}"}')
    assert index.entry('hello').placeholder_names == ('other',)
    index.set_overlay(None)
    assert index.entry('hello').placeholder_names == ('saved',)
