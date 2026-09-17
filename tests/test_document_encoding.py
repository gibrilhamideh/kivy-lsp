import pytest

from kivy_lsp.model.span import Span
from kivy_lsp.model.uri import file_uri_to_path
from kivy_lsp.workspace.document import (
    PositionEncoding,
    TextDocument,
    TextPosition,
)


@pytest.mark.parametrize(
    ("encoding", "units"),
    [
        (PositionEncoding.UTF8, 6),
        (PositionEncoding.UTF16, 3),
        (PositionEncoding.UTF32, 2),
    ],
)
def test_negotiated_unicode_positions_round_trip(encoding, units):
    document = TextDocument("file:///view.kv", "é😀name\r\nnext", 1, encoding)
    assert document.position_at(2) == TextPosition(0, units)
    assert document.offset_at(TextPosition(0, units)) == 2
    assert document.range_at(Span(2, 6)).start.character == units
    assert document.position_at(8) == TextPosition(1, 0)
    updated = document.updated("é😀changed", 2)
    assert updated.position_encoding == encoding
    assert updated.position_at(2).character == units


def test_internal_ast_utf8_override_is_independent_of_client_encoding():
    document = TextDocument(
        "file:///view.kv", "é😀name", position_encoding=PositionEncoding.UTF32
    )
    assert document.offset_at(TextPosition(0, 6), PositionEncoding.UTF8) == 2
    assert document.offset_at(TextPosition(0, 2)) == 2


def test_file_uri_decodes_once_and_accepts_localhost(tmp_path):
    path = tmp_path / "name%20with space.kv"
    assert file_uri_to_path(path.as_uri()) == path
    local_authority = path.as_uri().replace("file:///", "file://localhost/")
    assert file_uri_to_path(local_authority) == path
    assert file_uri_to_path("untitled:widget.kv") is None
