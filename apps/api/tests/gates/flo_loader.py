"""Load agents/scripts/flo (no .py suffix) as a module without registering it as `flo`.

`flo` is also the product package's name, so the script is loaded under `flo_cli`.
"""

from __future__ import annotations

import sys
from importlib import machinery, util
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).resolve().parents[4] / "agents" / "scripts"


def load_flo() -> ModuleType:
    if "flo_cli" in sys.modules:
        return sys.modules["flo_cli"]
    loader = machinery.SourceFileLoader("flo_cli", str(SCRIPTS / "flo"))
    spec = util.spec_from_loader("flo_cli", loader)
    assert spec is not None
    module = util.module_from_spec(spec)
    sys.modules["flo_cli"] = module
    loader.exec_module(module)
    return module
