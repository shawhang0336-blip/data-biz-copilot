"""Data Biz Copilot 架构分层与 AST 单向依赖红线测试。"""

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

LAYER_RANK = {
    "infra": 0,
    "kernel": 1,
    "policy": 2,
    "ingestion": 3,
    "catalog": 4,
    "copilot": 5,
    "api": 6,
    "main": 7,
}


def _module_layer(rel_parts: tuple) -> str:
    if rel_parts[0] == "main.py":
        return "main"
    return rel_parts[0]


def _imported_app_layers(py_file: Path) -> set:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    layers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    parts = alias.name.split(".")
                    if len(parts) >= 2:
                        layers.add(parts[1])
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            parts = node.module.split(".")
            if len(parts) >= 2:
                layers.add(parts[1])
    return layers


def test_app_directory_layout_matches_8_layer_architecture():
    entries = {p.name for p in APP_DIR.iterdir() if not p.name.startswith("__")}
    assert entries == {"main.py", "infra", "kernel", "policy", "ingestion", "catalog", "copilot", "api"}


def test_strict_one_way_dag_across_all_layers():
    violations = []
    for py_file in sorted(APP_DIR.rglob("*.py")):
        rel = py_file.relative_to(APP_DIR)
        src_layer = _module_layer(rel.parts)
        if src_layer not in LAYER_RANK:
            continue
        src_rank = LAYER_RANK[src_layer]
        for target_layer in _imported_app_layers(py_file):
            if target_layer not in LAYER_RANK:
                continue
            tgt_rank = LAYER_RANK[target_layer]
            if tgt_rank > src_rank:
                violations.append(f"{rel} ({src_layer}:{src_rank}) -> {target_layer}:{tgt_rank}")
    assert violations == []


def test_kernel_has_zero_business_or_upper_layer_imports():
    forbidden = {"policy", "ingestion", "catalog", "copilot", "api", "main"}
    for py_file in sorted((APP_DIR / "kernel").glob("*.py")):
        bad = _imported_app_layers(py_file) & forbidden
        assert bad == set(), f"kernel file {py_file.name} imported forbidden layers: {bad}"


def test_api_routers_do_not_cross_import_other_api_domains():
    domains = {"platform", "ingestion", "catalog", "copilot"}
    for domain in domains:
        for py_file in sorted((APP_DIR / "api" / domain).glob("*.py")):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app.api."):
                    parts = node.module.split(".")
                    if len(parts) >= 3 and parts[2] in domains and parts[2] != domain:
                        raise AssertionError(f"Cross-router import forbidden: {py_file.name} -> {node.module}")
