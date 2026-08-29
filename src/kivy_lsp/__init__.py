# src/kivy_lsp/__init__.py

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

try:
    __version__ = package_version("kivy-lsp")
except PackageNotFoundError:
    __version__ = "0.1.1"


__all__ = [
    "__version__",
]
