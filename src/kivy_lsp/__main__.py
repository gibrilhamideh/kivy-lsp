# src/kivy_lsp/__main__.py

import sys


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "format":
        from kivy_lsp.cli import main as format_main

        raise SystemExit(format_main(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "check":
        from kivy_lsp.check_cli import main as check_main

        raise SystemExit(check_main(sys.argv[2:]))

    if any(argument in {"--help", "-h"} for argument in sys.argv[1:]):
        print(
            "Commands:\n"
            "  check   Check saved KV code (kivy-lsp check --help)\n"
            "  format  Format KV files (kivy-lsp format --help)\n\n"
            "Without a command, start the language server.\n"
        )

    from pygls.cli import start_server

    from kivy_lsp.server import server

    start_server(server)


if __name__ == "__main__":
    main()
