"""Controlled compatibility checks against Kivy's parser and real bindings."""

from __future__ import annotations

import ast
import os

import pytest

from kivy_lsp.formatting.formatter import format_source
from kivy_lsp.formatting.options import FormatOptions


@pytest.fixture(scope="module")
def kivy_runtime():
    for name in (
        "KIVY_NO_ARGS", "KIVY_NO_CONSOLELOG", "KIVY_NO_FILELOG",
        "KIVY_NO_CONFIG",
    ):
        os.environ.setdefault(name, "1")

    parser = pytest.importorskip("kivy.lang.parser")
    builder = pytest.importorskip("kivy.lang.builder")
    event = pytest.importorskip("kivy.event")
    properties = pytest.importorskip("kivy.properties")
    return parser, builder, event, properties


EXPRESSIONS = (
    (
        "app.theme.color.error if root.active else "
        "app.theme.color.primary if root.enabled else "
        "app.theme.color.with_tone(app.theme.color.onBackground, 45)"
    ),
    (
        '{"minimum": root.minimum_temperature, '
        '"maximum": root.maximum_temperature, '
        '"nested": {"enabled": root.enabled, "title": root.title}}'
    ),
    (
        "[root.first_available_measurement, "
        "root.second_available_measurement, root.third_available_measurement]"
    ),
    (
        "app.format_measurement(app.convert_measurement(root.measurement, "
        "root.source_unit, root.display_unit), precision=root.precision)"
    ),
    (
        "root.error_color if (root.active and root.alarm_enabled "
        "and not root.snoozed) else root.normal_color"
    ),
    (
        '"é🙂" + root.current_title + root.current_description '
        '+ root.current_temperature_label + "الحرارة"'
    ),
    (
        "root.primary_value if root.enabled else root.secondary_value "
        "# preserve this inline comment"
    ),
)


def compiled_property(parser, source, name="result"):
    parsed = parser.Parser(content=source)
    return parsed.rules[0][1].properties[name]


@pytest.mark.parametrize("expression", EXPRESSIONS)
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_kivy_precompile_preserves_ast_and_watched_chains(
    kivy_runtime,
    expression,
    newline,
):
    parser, _, _, _ = kivy_runtime
    source = f"<FormatterProbe>:{newline}    result: {expression}{newline}"
    formatted = format_source(source, FormatOptions(line_length=48))
    original = compiled_property(parser, source)
    output = compiled_property(parser, formatted.text)

    assert ast.dump(ast.parse(original.value, mode=original.mode)) == (
        ast.dump(ast.parse(output.value, mode=output.mode))
    )
    assert {tuple(chain) for chain in original.watched_keys or ()} == {
        tuple(chain) for chain in output.watched_keys or ()
    }
    assert format_source(formatted.text, FormatOptions(48)).text == (
        formatted.text
    )

    if newline == "\r\n":
        assert "\n" not in formatted.text.replace("\r\n", "")

    if "# preserve" in expression:
        assert formatted.text == source


@pytest.fixture
def probe_class(kivy_runtime):
    _, _, event, properties = kivy_runtime

    class ProbeState(event.EventDispatcher):
        amount = properties.NumericProperty(3)

    class FormatterProbe(event.EventDispatcher):
        __events__ = ("on_probe",)
        cls = properties.ListProperty([])
        ids = properties.DictProperty({})
        children = properties.ListProperty([])
        left_value = properties.NumericProperty(1)
        right_value = properties.NumericProperty(2)
        bias_value = properties.NumericProperty(4)
        fallback_value = properties.NumericProperty(9)
        enabled = properties.BooleanProperty(True)
        result = properties.NumericProperty(0)
        state = properties.ObjectProperty(None, rebind=True)

        def __init__(self):
            super().__init__()
            self.state = ProbeState()
            self.calls = []

        def record(self, label, value):
            self.calls.append(label)
            return value

        def on_probe(self):
            pass

    return FormatterProbe, ProbeState


def test_formatter_preserves_live_nested_bindings(kivy_runtime, probe_class):
    _, builder, _, _ = kivy_runtime
    probe_type, state_type = probe_class
    source = (
        "<FormatterProbe>:\n"
        "    result: root.left_value + root.right_value + root.state.amount "
        "+ root.bias_value if root.enabled else root.fallback_value\n"
    )
    formatted = format_source(source, FormatOptions(40))
    assert formatted.text != source
    results = []

    for content in (source, formatted.text):
        rules = builder.BuilderBase()
        rules.load_string(content, rulesonly=True)
        probe = probe_type()
        try:
            rules.apply(probe)
            snapshots = [probe.result]
            probe.left_value = 10
            snapshots.append(probe.result)
            probe.state.amount = 20
            snapshots.append(probe.result)
            probe.state = state_type(amount=30)
            snapshots.append(probe.result)
            probe.state.amount = 40
            snapshots.append(probe.result)
            probe.enabled = False
            snapshots.append(probe.result)
            probe.fallback_value = 100
            snapshots.append(probe.result)
            results.append(snapshots)
        finally:
            rules.unbind_widget(probe.uid)

    assert results[0] == results[1] == [10, 19, 36, 46, 56, 9, 100]


def test_formatter_preserves_evaluation_and_event_order(
    kivy_runtime,
    probe_class,
):
    _, builder, _, _ = kivy_runtime
    probe_type, _ = probe_class
    source = (
        "<FormatterProbe>:\n"
        '    result: root.record("first", root.left_value) + '
        'root.record("second", root.right_value) if root.enabled else '
        'root.record("fallback", root.fallback_value)\n'
        "    on_probe:\n"
        '        root.record("event-first", root.left_value)\n'
        '        root.record("event-second", root.right_value)\n'
    )
    formatted = format_source(source, FormatOptions(36))
    assert formatted.text != source
    results = []

    for content in (source, formatted.text):
        rules = builder.BuilderBase()
        rules.load_string(content, rulesonly=True)
        probe = probe_type()
        try:
            rules.apply(probe)
            snapshots = [(probe.result, tuple(probe.calls))]
            probe.calls.clear()
            probe.enabled = False
            snapshots.append((probe.result, tuple(probe.calls)))
            probe.calls.clear()
            probe.dispatch("on_probe")
            snapshots.append((probe.result, tuple(probe.calls)))
            results.append(snapshots)
        finally:
            rules.unbind_widget(probe.uid)

    assert results[0] == results[1] == [
        (3, ("first", "second")),
        (9, ("fallback",)),
        (9, ("event-first", "event-second")),
    ]
