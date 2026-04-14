from __future__ import annotations

import importlib
import sys


def _resolve_runtime():
    runtime = sys.modules.get("server")
    if runtime is not None:
        return runtime
    return importlib.import_module("server")


runtime = _resolve_runtime()

