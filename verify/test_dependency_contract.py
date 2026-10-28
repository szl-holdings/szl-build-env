"""Dependency contract for szl-build-env (stdlib only; executes no repository code).

Fails closed when a non-stdlib import maps to no declared distribution, when an import
is missing from IMPORT_TO_DIST, or when requirements.txt and pyproject.toml drift apart.
The negative controls at the end show that each check can fail.
"""
from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNED_DIRS = ("verify", "bootstrap", "scripts")
# import name -> distribution; None marks a test-only dependency declared in the "test" extra.
IMPORT_TO_DIST = {
    "cryptography": "cryptography",
    "yaml": "pyyaml",
    "szl_receipt": "szl-receipt",
    "pytest": None,
}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _dist_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    if not match:
        raise ValueError(f"unparseable requirement: {requirement!r}")
    return _norm(match.group(1))


def parse_requirements(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def third_party_imports(root: Path) -> dict[str, set[str]]:
    local = {p.stem for d in SCANNED_DIRS for p in (root / d).glob("*.py")}
    found: dict[str, set[str]] = {}
    for d in SCANNED_DIRS:
        for path in sorted((root / d).glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module.split(".")[0]]
                else:
                    continue
                for name in names:
                    if name in sys.stdlib_module_names or name in local or name == "__future__":
                        continue
                    found.setdefault(name, set()).add(path.relative_to(root).as_posix())
    return found


def coverage_problems(imports: dict[str, set[str]], declared: list[str]) -> list[str]:
    declared_names = {_dist_name(r) for r in declared}
    problems = []
    for name, where in sorted(imports.items()):
        if name not in IMPORT_TO_DIST:
            problems.append(f"unmapped import {name!r} in {sorted(where)}; extend IMPORT_TO_DIST deliberately")
        elif IMPORT_TO_DIST[name] is not None and _norm(IMPORT_TO_DIST[name]) not in declared_names:
            problems.append(f"import {name!r} needs distribution {IMPORT_TO_DIST[name]!r}, which is not declared")
    return problems


def drift_problems(requirements: list[str], project_deps: list[str]) -> list[str]:
    a = {re.sub(r"\s+", "", r) for r in requirements}
    b = {re.sub(r"\s+", "", r) for r in project_deps}
    return [f"only in requirements.txt: {r}" for r in sorted(a - b)] + [f"only in pyproject.toml: {r}" for r in sorted(b - a)]


def _load(root: Path = REPO_ROOT):
    reqs = parse_requirements((root / "requirements.txt").read_text(encoding="utf-8"))
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return reqs, project


def test_declared_dependencies_cover_imports():
    reqs, _ = _load()
    assert coverage_problems(third_party_imports(REPO_ROOT), reqs) == []


def test_requirements_and_pyproject_agree():
    reqs, project = _load()
    assert drift_problems(reqs, project["dependencies"]) == []


def test_pytest_is_declared_as_test_extra():
    _, project = _load()
    assert "pytest" in {_dist_name(r) for r in project.get("optional-dependencies", {}).get("test", [])}


def test_python_floor_matches_ci():
    _, project = _load()
    assert project["requires-python"] == ">=3.12"
    assert sys.version_info >= (3, 12)


def test_negative_control_pre_fix_declaration_is_rejected():
    pre_fix = [
        "cryptography==50.0.1",
        "pyyaml==6.0.3",
        "szl-receipts @ git+https://github.com/szl-holdings/szl-platform@09b8749e0439cea888a18482bf5ce81db9473ce0"
        "#subdirectory=packages/szl-receipts",
    ]
    problems = coverage_problems({"szl_receipt": {"verify/dsse_verify.py"}}, pre_fix)
    assert len(problems) == 1 and "'szl-receipt'" in problems[0]


def test_negative_control_drift_is_detected():
    assert drift_problems(["pyyaml==6.0.3"], ["pyyaml==6.0.2"]) == [
        "only in requirements.txt: pyyaml==6.0.3",
        "only in pyproject.toml: pyyaml==6.0.2",
    ]


def test_negative_control_unmapped_import_fails_closed():
    assert coverage_problems({"requests": {"verify/x.py"}}, ["requests==2.32.3"]) != []
