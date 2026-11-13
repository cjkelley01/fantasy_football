"""Guard the real-snapshot parity replay entrypoint against syntax regressions."""
from __future__ import annotations

import ast
from pathlib import Path


def test_v2_parity_replay_module_is_valid_python():
    source = Path(__file__).resolve().parents[1] / "src" / "fantasy_football" / "v2" / "parity_replay.py"
    ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
