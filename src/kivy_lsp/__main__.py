# src/kivy_lsp/__main__.py

from pygls.cli import start_server

from kivy_lsp.server import server


def main() -> None:
    start_server(server)


if __name__ == "__main__":
    main()
