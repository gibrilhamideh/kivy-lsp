"""Shared conversion from file URIs to local platform paths."""

from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname


def file_uri_to_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    path = url2pathname(parsed.path)
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        path = f"//{parsed.netloc}{path}"
    return Path(path).resolve()
