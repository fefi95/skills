import importlib.util
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True  # keep __pycache__ out of the plugin folder

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "plugins" / "plan-board" / "skills" / "plan-board"
SCRIPT = SKILL / "scripts" / "plan_board.py"
EXAMPLES = SKILL / "examples"


def _load():
    spec = importlib.util.spec_from_file_location("plan_board", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["plan_board"] = mod
    spec.loader.exec_module(mod)
    return mod


pb = _load()


@pytest.fixture
def plan_board():
    return pb


@pytest.fixture
def skill_dir():
    return SKILL


@pytest.fixture
def examples():
    return EXAMPLES


def make_plan(*steps, **meta):
    """Build a normalized plan from compact step dicts."""
    return pb.normalize({"plan": meta, "steps": list(steps)})
