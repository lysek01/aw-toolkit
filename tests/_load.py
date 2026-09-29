"""Import the watcher scripts by path (their folders have dashes, so they aren't packages)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(rel_path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
