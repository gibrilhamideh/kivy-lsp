from pathlib import Path

from kivy_lsp.workspace.kv_scanner import KvScanner


def test_scanner_indexes_ids_with_widget_types() -> None:
    source = (
        "<RootView>:\n"
        "    id: root_view\n"
        "    BoxLayout:\n"
        "        id: content\n"
        "        Button:\n"
        "            id: submit_button\n"
        "<Card@BoxLayout>:\n"
        "    Label:\n"
        "        id: title\n"
    )
    scanner = KvScanner((Path.cwd(),))
    symbols = scanner.scan_text(
        "file:///view.kv",
        source,
    )

    assert [
        (
            symbol.name,
            symbol.bases,
            [
                (
                    item.name,
                    item.widget_class,
                )
                for item in symbol.ids
            ],
        )
        for symbol in symbols
    ] == [
        (
            "RootView",
            (),
            [
                (
                    "root_view",
                    "RootView",
                ),
                (
                    "content",
                    "BoxLayout",
                ),
                (
                    "submit_button",
                    "Button",
                ),
            ],
        ),
        (
            "Card",
            (
                "BoxLayout",
            ),
            [
                (
                    "title",
                    "Label",
                ),
            ],
        ),
    ]
