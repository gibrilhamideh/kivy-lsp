from lsprotocol import types

from kivy_lsp.analysis.completion import (
    KvCompletionItem,
    KvCompletionKind,
)
from kivy_lsp.features.completion import (
    _to_lsp_item,  # pyright: ignore[reportPrivateUsage]
)


def _replacement_range() -> types.Range:
    position = types.Position(
        line=0,
        character=0,
    )

    return types.Range(
        start=position,
        end=position,
    )


def test_quoted_completion_filters_with_inserted_text() -> None:
    item = KvCompletionItem(
        label="features.ventilation.title",
        kind=KvCompletionKind.CONSTANT,
        insert_text='"features.ventilation.title"',
        sort_text="00:features.ventilation.title",
    )
    lsp_item = _to_lsp_item(
        item,
        _replacement_range(),
    )

    assert lsp_item.filter_text == (
        '"features.ventilation.title"'
    )


def test_unquoted_completion_filters_with_label() -> None:
    item = KvCompletionItem(
        label="orientation",
        kind=KvCompletionKind.PROPERTY,
        insert_text="orientation: ",
        sort_text="00:orientation",
    )
    lsp_item = _to_lsp_item(
        item,
        _replacement_range(),
    )

    assert lsp_item.filter_text == "orientation"
