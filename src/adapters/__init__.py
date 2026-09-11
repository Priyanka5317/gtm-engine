"""ATS adapter registry.

Importing this package registers every adapter. Use `fetch(url)` and let the
registry pick, or call an adapter directly when you already know the vendor.
"""

from .base import (           # noqa: F401
    FetchError,
    PostingClosed,
    JobPosting,
    detect,
    fetch,
    html_to_text,
    http,
    http_json,
    parse_comp,
    registry,
)

from . import json_board      # noqa: F401  registers family 1
from . import embedded        # noqa: F401  registers family 2
from . import internal        # noqa: F401  registers family 3

__all__ = [
    "FetchError", "PostingClosed", "JobPosting", "detect", "fetch", "registry",
    "html_to_text", "http", "http_json", "parse_comp",
]
