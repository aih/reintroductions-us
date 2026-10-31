"""The version and commit this process was built from.

`SITE_VERSION` is the installed package's version, or the `pyproject.toml`
literal when the package is not installed. `GIT_COMMIT` is the `GIT_COMMIT`
environment variable, set by `Dockerfile` from the build argument that
`deploy/deploy-on-box.sh` passes; a process started without it reports
"unknown".
"""

from __future__ import annotations

import importlib.metadata
import os

_PYPROJECT_VERSION = "0.1.0"


def _site_version() -> str:
    try:
        return importlib.metadata.version("reintroductions")
    except importlib.metadata.PackageNotFoundError:
        return _PYPROJECT_VERSION


SITE_VERSION = _site_version()
GIT_COMMIT = os.environ.get("GIT_COMMIT") or "unknown"
