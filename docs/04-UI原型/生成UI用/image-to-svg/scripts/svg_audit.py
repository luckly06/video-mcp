#!/usr/bin/env python3
"""Audit SVG structure and reject raster/external embedding."""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


COMMAND_RE = re.compile(r"[AaCcHhLlMmQqSsTtVvZz]")
URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
INTERNAL_ID_RE = re.compile(r"^#[A-Za-z_][A-Za-z0-9_.:-]*$")
FORBIDDEN_ELEMENTS = {"image", "feimage", "foreignobject", "script", "iframe", "object", "embed", "video", "canvas"}


def display_path(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(Path("~") / resolved.relative_to(Path.home()))
    except ValueError:
        return str(resolved)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def validate_url_tokens(value: str, label: str, errors: list[str], internal_refs: set[str]) -> None:
    if "url(" not in value.lower():
        return
    matches = list(URL_RE.finditer(value))
    if not matches:
        errors.append(f"Malformed CSS URL reference in {label}: {value!r}")
        return
    for match in matches:
        target = match.group(2).strip()
        if INTERNAL_ID_RE.fullmatch(target):
            internal_refs.add(target[1:])
        else:
            errors.append(f"External or embedded URL reference is forbidden in {label}: {target!r}")


def audit_svg(source: str | Path) -> dict:
    source = Path(source)
    errors: list[str] = []
    warnings: list[str] = []
    try:
        root = ET.parse(source).getroot()
    except ET.ParseError as exc:
        return {
            "svg": display_path(source),
            "pass": False,
            "errors": [f"Invalid XML: {exc}"],
            "warnings": [],
            "element_counts": {},
            "total_path_commands": 0,
        }

    element_counts: dict[str, int] = {}
    total_path_commands = 0
    ids = [element.get("id") for element in root.iter() if element.get("id")]
    id_set = set(ids)
    if len(ids) != len(id_set):
        warnings.append("Duplicate element ids can make internal references ambiguous")
    internal_refs: set[str] = set()

    if local_name(root.tag) != "svg":
        errors.append("Root element is not <svg>")
    if not root.get("viewBox"):
        warnings.append("Missing viewBox; scalable placement may be ambiguous")

    for element in root.iter():
        name = local_name(element.tag)
        element_counts[name] = element_counts.get(name, 0) + 1
        if name in FORBIDDEN_ELEMENTS:
            errors.append(f"Forbidden embedded/raster-capable element: <{name}>")

        style_text = (element.text or "") if name == "style" else ""
        inline_style = element.get("style", "")
        combined_style = f"{style_text}\n{inline_style}"
        lowered_style = combined_style.lower()
        if "background-image" in lowered_style:
            errors.append("CSS background imagery is forbidden")
        if "@import" in lowered_style:
            errors.append("CSS @import is forbidden")
        validate_url_tokens(combined_style, "CSS style", errors, internal_refs)

        for key, value in element.attrib.items():
            attr = local_name(key)
            lowered = value.strip().lower()
            if attr in {"href", "src"}:
                if INTERNAL_ID_RE.fullmatch(value.strip()):
                    internal_refs.add(value.strip()[1:])
                else:
                    errors.append(f"External or embedded reference is forbidden: {attr}={value!r}")
            if attr != "style":
                validate_url_tokens(value, f"attribute {key}", errors, internal_refs)
            if lowered.startswith(("data:", "http:", "https:", "file:")):
                errors.append(f"External or embedded value is forbidden: {key}={value!r}")

        if name == "path":
            command_count = len(COMMAND_RE.findall(element.get("d", "")))
            total_path_commands += command_count
            if command_count > 150:
                warnings.append(
                    f"Path {element.get('id', '<unnamed>')} has {command_count} commands; inspect for trace noise"
                )

    unresolved = sorted(internal_refs - id_set)
    if unresolved:
        warnings.append(f"Unresolved internal references: {', '.join(unresolved)}")
    if total_path_commands > 250:
        warnings.append(f"SVG has {total_path_commands} path commands; verify that complexity is justified")

    return {
        "svg": display_path(source),
        "pass": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "element_counts": element_counts,
        "total_path_commands": total_path_commands,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("svg")
    parser.add_argument("--report")
    args = parser.parse_args()
    report = audit_svg(args.svg)
    serialized = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(serialized)
    print(serialized, end="")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
