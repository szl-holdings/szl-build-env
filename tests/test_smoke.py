"""Smoke tests — estate baseline (2026-11-06).

These tests prove the repository's Python sources compile and that each
declared package imports. A missing *third-party* dependency is reported as
a skip (honest), a broken package is a failure.
"""
import compileall
import importlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT
PACKAGES = []


def test_sources_compile():
    targets = [p for p in ROOT.rglob('*.py') if '.git' not in p.parts and 'node_modules' not in p.parts and '.venv' not in p.parts]
    assert targets, "no Python sources found"
    for t in targets:
        if t.is_dir():
            assert compileall.compile_dir(str(t), quiet=1), f"compile failed under {t}"
        else:
            assert compileall.compile_file(str(t), quiet=1), f"compile failed: {t}"


@pytest.mark.parametrize("pkg", PACKAGES or ["__none__"])
def test_import_package(pkg):
    if pkg == "__none__":
        pytest.skip("repository declares no importable package (scripts only)")
    sys.path.insert(0, str(SRC))
    try:
        importlib.import_module(pkg)
    except ModuleNotFoundError as exc:  # third-party dep absent in this environment
        if exc.name and (exc.name == pkg or exc.name.startswith(pkg + ".")):
            raise
        pytest.skip(f"optional dependency missing: {exc.name}")
