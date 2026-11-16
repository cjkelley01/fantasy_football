"""Guard the real-snapshot parity replay entrypoint against syntax regressions."""
from __future__ import annotations

import ast
from pathlib import Path


def test_v2_parity_replay_module_is_valid_python():
    source = Path(__file__).resolve().parents[1] / "src" / "fantasy_football" / "v2" / "parity_replay.py"
    ast.parse(source.read_text(encoding="utf-8"), filename=str(source))


def test_v2_parity_replay_has_executable_cli_entrypoint():
    import fantasy_football.v2.parity_replay as module
    assert callable(module.main)
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and any(isinstance(item, ast.Constant) and item.value == "__main__"
                for item in node.test.comparators)
        for node in tree.body
    )
