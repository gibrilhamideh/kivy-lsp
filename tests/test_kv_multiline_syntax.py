from kivy_lsp.kv.parser import parse


def test_parenthesized_multiline_property_is_one_value() -> None:
    source = (
        "<RootView>:\n"
        "    text:\n"
        "        (\n"
        "        \"enabled\"\n"
        "        if root.enabled\n"
        "        else \"disabled\"\n"
        "        )\n"
        "    width: 120\n"
    )

    assert parse(source).diagnostics == ()


def test_backslash_multiline_property_is_one_value() -> None:
    source = (
        "<RootView>:\n"
        "    text:\n"
        "        \"enabled\" \\\n"
        "        if root.enabled \\\n"
        "        else \"disabled\"\n"
        "    width: 120\n"
    )

    assert parse(source).diagnostics == ()


def test_inconsistent_property_indentation_is_invalid() -> None:
    source = (
        "<RootView>:\n"
        "    Label:\n"
        "        text: \"Hello\"\n"
        "      color: 1, 1, 1, 1\n"
    )
    diagnostics = parse(source).diagnostics

    assert any(
        item.code == "kv-inconsistent-dedent"
        for item in diagnostics
    )
