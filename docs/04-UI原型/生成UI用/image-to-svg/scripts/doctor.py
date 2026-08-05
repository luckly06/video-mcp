#!/usr/bin/env python3
"""Discover existing image-to-SVG QA runtimes without installing dependencies."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def unique_paths(values: list[str | None]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        expanded = str(Path(value).expanduser())
        key = str(Path(expanded).resolve()) if Path(expanded).exists() else expanded
        if key not in seen:
            seen.add(key)
            result.append(expanded)
    return result


def managed_runtime_roots() -> list[Path]:
    configured = os.environ.get("IMAGE_TO_SVG_RUNTIME_ROOTS", "").split(os.pathsep)
    roots = unique_paths([*configured, str(Path.home() / ".cache" / "codex-runtimes")])
    return [Path(root).expanduser() for root in roots if Path(root).expanduser().is_dir()]


def managed_runtime_paths(relative: str) -> list[str]:
    candidates: list[str] = []
    for root in managed_runtime_roots():
        direct = root / relative
        if direct.exists():
            candidates.append(str(direct))
        candidates.extend(str(path) for path in sorted(root.glob(f"*/{relative}")) if path.exists())
    return unique_paths(candidates)


def redact_home(value):
    """Keep diagnostics useful while removing the local account path from shareable JSON."""
    if isinstance(value, str):
        return value.replace(str(Path.home()), "~")
    if isinstance(value, list):
        return [redact_home(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_home(item) for key, item in value.items()}
    return value


def python_candidates() -> list[str]:
    return unique_paths(
        [
            os.environ.get("IMAGE_TO_SVG_PYTHON"),
            os.environ.get("PYTHON_BIN"),
            sys.executable,
            *managed_runtime_paths("dependencies/python/bin/python3"),
            shutil.which("python3"),
            "/usr/bin/python3",
        ]
    )


def probe_python(executable: str) -> dict:
    code = (
        "import importlib.util,json,sys;"
        "print(json.dumps({'executable':sys.executable,'version':list(sys.version_info[:3]),"
        "'pillow':importlib.util.find_spec('PIL') is not None,"
        "'numpy':importlib.util.find_spec('numpy') is not None}))"
    )
    completed = subprocess.run([executable, "-c", code], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return {"executable": executable, "usable": False, "error": completed.stderr.strip() or completed.stdout.strip()}
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"executable": executable, "usable": False, "error": completed.stdout.strip()}
    result["version"] = ".".join(str(part) for part in result["version"])
    result["supported"] = tuple(int(part) for part in result["version"].split(".")) >= (3, 10, 0)
    result["usable"] = bool(result["supported"] and result["pillow"] and result["numpy"])
    return result


def node_candidates() -> list[str]:
    return unique_paths(
        [
            os.environ.get("IMAGE_TO_SVG_NODE"),
            shutil.which("node"),
            *managed_runtime_paths("dependencies/node/bin/node"),
        ]
    )


def playwright_paths(skill_dir: Path) -> list[str]:
    environment_paths = os.environ.get("NODE_PATH", "").split(os.pathsep)
    return unique_paths(
        [
            os.environ.get("PLAYWRIGHT_PATH"),
            str(skill_dir / "node_modules"),
            *environment_paths,
            *managed_runtime_paths("dependencies/node/node_modules"),
        ]
    )


def probe_node(executable: str) -> dict:
    completed = subprocess.run(
        [executable, "-e", "console.log(JSON.stringify({version:process.versions.node,executable:process.execPath}))"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return {"executable": executable, "usable": False, "error": completed.stderr.strip() or completed.stdout.strip()}
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"executable": executable, "usable": False, "error": completed.stdout.strip()}
    major = int(result["version"].split(".", 1)[0])
    result["supported"] = major >= 18
    result["usable"] = result["supported"]
    return result


def probe_playwright(node: str, module_paths: list[str]) -> dict | None:
    code = (
        "const p=require('playwright/package.json');"
        "console.log(JSON.stringify({version:p.version,resolved:require.resolve('playwright')}))"
    )
    for module_path in [None, *module_paths]:
        environment = os.environ.copy()
        if module_path:
            environment["NODE_PATH"] = module_path
        completed = subprocess.run([node, "-e", code], capture_output=True, text=True, check=False, env=environment)
        if completed.returncode == 0:
            result = json.loads(completed.stdout)
            result["node_path"] = module_path
            return result
    return None


def main() -> int:
    skill_dir = Path(__file__).resolve().parent.parent
    python_probes = [probe_python(candidate) for candidate in python_candidates()]
    selected_python = next((item for item in python_probes if item.get("usable")), None)

    node_probes = [probe_node(candidate) for candidate in node_candidates()]
    selected_node = next((item for item in node_probes if item.get("usable")), None)
    playwright = (
        probe_playwright(selected_node["executable"], playwright_paths(skill_dir)) if selected_node else None
    )

    browser_renderer = None
    if selected_node and playwright:
        environment = os.environ.copy()
        if playwright.get("node_path"):
            environment["NODE_PATH"] = playwright["node_path"]
        completed = subprocess.run(
            [selected_node["executable"], str(skill_dir / "scripts" / "render_svg.cjs"), "--doctor"],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        if completed.returncode == 0:
            try:
                browser_renderer = json.loads(completed.stdout)
            except json.JSONDecodeError:
                browser_renderer = {"raw": completed.stdout.strip()}
        else:
            browser_renderer = {"error": completed.stderr.strip() or completed.stdout.strip()}

    raster_ready = selected_python is not None
    browser_ready = bool(browser_renderer and browser_renderer.get("launchable"))
    notes: list[str] = []
    if selected_python and selected_python["executable"] != sys.executable:
        notes.append("Pillow and NumPy were found in an existing non-default Python runtime; use the selected executable for raster scripts.")
    if playwright and playwright.get("node_path"):
        notes.append("Playwright was found through an existing NODE_PATH; no installation is needed.")
    if playwright and not browser_ready:
        notes.append("Playwright is installed, but browser launch is blocked or unavailable in the current execution context.")
    if not selected_python:
        notes.append("No existing Python 3.10+ runtime with both Pillow and NumPy was found; continue with visual analysis.")
    if not playwright:
        notes.append("No existing Playwright package was found; continue without deterministic browser QA.")

    checks = {
        "core_workflow_available": True,
        "automated_raster_qa_available": raster_ready,
        "browser_package_available": playwright is not None,
        "browser_qa_available": browser_ready,
        "enhanced_qa_available": raster_ready and browser_ready,
        "selected_runtimes": {
            "python": selected_python,
            "node": selected_node,
            "playwright": playwright,
        },
        "python_candidates": python_probes,
        "node_candidates": node_probes,
        "browser_renderer": browser_renderer,
        "notes": notes,
    }
    print(json.dumps(redact_home(checks), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
