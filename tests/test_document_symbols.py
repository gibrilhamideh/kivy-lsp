from typing import cast

from lsprotocol import types

from kivy_lsp.analysis.document_symbols import (
    KvDocumentSymbolAnalyzer,
)
from kivy_lsp.kv.parser import parse
from kivy_lsp.model.document_symbol import (
    KvDocumentSymbol,
    KvDocumentSymbolKind,
)
from kivy_lsp.server import create_server
from kivy_lsp.workspace.document import TextDocument


def _symbols(source: str) -> tuple[KvDocumentSymbol, ...]:
    document = TextDocument(
        uri="file:///outline.kv",
        text=source,
    )

    return KvDocumentSymbolAnalyzer().analyze(
        document,
        parse(source),
    )


def _child(
    symbol: KvDocumentSymbol,
    name: str,
) -> KvDocumentSymbol:
    return next(
        child
        for child in symbol.children
        if child.name == name
    )


def test_document_symbols_preserve_kv_hierarchy() -> None:
    source = (
        "<RootView@FnBoxLayout>:\n"
        '    title: "Dashboard"\n'
        "\n"
        "    # ======================== #\n"
        "    # section: Header\n"
        "    # ======================== #\n"
        "    FnBoxLayout:\n"
        '        orientation: "horizontal"\n'
        "\n"
        "        canvas.before:\n"
        "            Color:\n"
        "                rgba: app.theme.color.surface\n"
        "\n"
        "    # section: Actions\n"
        "    UIButton:\n"
        "        on_release:\n"
        "            root.save()\n"
    )
    symbols = _symbols(source)

    assert len(symbols) == 1
    rule = symbols[0]
    assert rule.name == "RootView@FnBoxLayout"
    assert rule.kind is KvDocumentSymbolKind.CLASS
    assert [child.name for child in rule.children] == [
        "title",
        "Header",
        "Actions",
    ]

    header = _child(rule, "Header")
    assert header.kind is KvDocumentSymbolKind.NAMESPACE
    layout = _child(header, "FnBoxLayout")
    assert layout.kind is KvDocumentSymbolKind.CONSTRUCTOR
    assert [child.name for child in layout.children] == [
        "orientation",
        "canvas.before",
    ]

    canvas = _child(layout, "canvas.before")
    assert canvas.kind is KvDocumentSymbolKind.NAMESPACE
    color = _child(canvas, "Color")
    assert _child(color, "rgba").kind is (
        KvDocumentSymbolKind.PROPERTY
    )

    actions = _child(rule, "Actions")
    button = _child(actions, "UIButton")
    assert _child(button, "on_release").kind is (
        KvDocumentSymbolKind.EVENT
    )


def test_nested_section_stays_inside_its_widget() -> None:
    source = (
        "<RootView>:\n"
        "    # section: Outer\n"
        "    FnBoxLayout:\n"
        "        # section: Nested\n"
        "        UIText:\n"
        '            text: "Hello"\n'
        "\n"
        "    # section: Next\n"
        "    UIButton:\n"
    )
    rule = _symbols(source)[0]

    assert [child.name for child in rule.children] == [
        "Outer",
        "Next",
    ]
    outer = _child(rule, "Outer")
    layout = _child(outer, "FnBoxLayout")
    nested = _child(layout, "Nested")
    text = _child(nested, "UIText")
    assert _child(text, "text").kind is (
        KvDocumentSymbolKind.PROPERTY
    )


def test_symbol_selection_spans_select_visible_names() -> None:
    source = (
        "<RootView>:\n"
        "    # section: Navigation\n"
        "    FnBoxLayout:\n"
        '        orientation: "vertical"\n'
    )
    rule = _symbols(source)[0]
    section = _child(rule, "Navigation")
    widget = _child(section, "FnBoxLayout")
    prop = _child(widget, "orientation")

    for symbol in (rule, section, widget, prop):
        selected = source[
            symbol.selection_span.start:
            symbol.selection_span.end
        ]
        assert selected == symbol.name


def test_server_advertises_document_symbols() -> None:
    server = create_server()
    feature_manager = server.protocol.fm
    features = cast(
        dict[str, object],
        feature_manager.features,  # pyright: ignore[reportUnknownMemberType]
    )

    assert (
        types.TEXT_DOCUMENT_DOCUMENT_SYMBOL
        in features
    )
