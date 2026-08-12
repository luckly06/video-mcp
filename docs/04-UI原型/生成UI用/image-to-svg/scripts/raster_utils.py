#!/usr/bin/env python3
"""Shared raster measurements for image-icon reconstruction."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image


ANALYSIS_THRESHOLD = 0.08
QA_THRESHOLD = 0.10
ColorHint = tuple[int, int, int] | str | None


def display_path(path: str | Path) -> str:
    """Resolve a path for diagnostics without exposing the local home directory."""
    resolved = Path(path).resolve()
    try:
        return str(Path("~") / resolved.relative_to(Path.home()))
    except ValueError:
        return str(resolved)


def load_rgba(path: str | Path, crop: Iterable[int] | None = None) -> Image.Image:
    image = Image.open(path).convert("RGBA")
    if crop is not None:
        x, y, width, height = [int(value) for value in crop]
        if width <= 0 or height <= 0:
            raise ValueError("Crop width and height must be positive")
        image = image.crop((x, y, x + width, y + height))
    return image


def parse_color_hint(value: str | None, *, allow_transparent: bool = True) -> ColorHint:
    if value is None or value.strip().lower() == "auto":
        return None
    normalized = value.strip().lower()
    if normalized == "transparent":
        if allow_transparent:
            return "transparent"
        raise ValueError("transparent is not valid for this color option")
    if re.fullmatch(r"#[0-9a-f]{3}", normalized):
        normalized = "#" + "".join(character * 2 for character in normalized[1:])
    if re.fullmatch(r"#[0-9a-f]{6}", normalized):
        return tuple(int(normalized[index : index + 2], 16) for index in (1, 3, 5))
    if re.fullmatch(r"\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}", normalized):
        result = tuple(int(part.strip()) for part in normalized.split(","))
        if all(0 <= channel <= 255 for channel in result):
            return result
    raise ValueError(f"Invalid color {value!r}; use auto, transparent, #RRGGBB, or R,G,B")


def _border_values(array: np.ndarray) -> np.ndarray:
    if array.ndim == 2:
        return np.concatenate((array[0, :], array[-1, :], array[:, 0], array[:, -1]))
    return np.concatenate((array[0, :, :], array[-1, :, :], array[:, 0, :], array[:, -1, :]), axis=0)


def _corner_values(rgb: np.ndarray) -> np.ndarray:
    height, width = rgb.shape[:2]
    radius = max(1, min(5, height // 8 or 1, width // 8 or 1))
    return np.concatenate(
        (
            rgb[:radius, :radius].reshape(-1, 3),
            rgb[:radius, -radius:].reshape(-1, 3),
            rgb[-radius:, :radius].reshape(-1, 3),
            rgb[-radius:, -radius:].reshape(-1, 3),
        ),
        axis=0,
    )


def _candidate_colors(rgb: np.ndarray, limit: int = 6) -> list[np.ndarray]:
    flat = rgb.reshape(-1, 3)
    max_samples = 250_000
    step = max(1, int(math.ceil(len(flat) / max_samples)))
    sample = flat[::step]
    quantized = np.clip(sample.astype(np.uint16) // 8, 0, 31)
    packed = (quantized[:, 0] << 10) | (quantized[:, 1] << 5) | quantized[:, 2]
    keys, counts = np.unique(packed, return_counts=True)
    order = np.argsort(counts)[::-1][:limit]
    candidates: list[np.ndarray] = []
    for key in keys[order]:
        members = sample[packed == key]
        if members.size:
            candidates.append(np.median(members, axis=0))
    candidates.extend(
        (
            np.median(flat, axis=0),
            np.median(_border_values(rgb), axis=0),
            np.median(_corner_values(rgb), axis=0),
        )
    )
    deduplicated: list[np.ndarray] = []
    for candidate in candidates:
        if not any(np.linalg.norm(candidate - existing) < 3.0 for existing in deduplicated):
            deduplicated.append(candidate.astype(np.float32))
    return deduplicated[:limit]


def _luminance(color: np.ndarray) -> float:
    return float(np.dot(color, np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)))


def _choose_background(rgb: np.ndarray, polarity: str) -> tuple[np.ndarray, dict]:
    candidates = _candidate_colors(rgb)
    border = _border_values(rgb)
    corners = _corner_values(rgb)
    scored = []
    for color in candidates:
        area_share = float(np.mean(np.linalg.norm(rgb - color, axis=2) <= 10.0))
        border_share = float(np.mean(np.linalg.norm(border - color, axis=1) <= 10.0))
        corner_share = float(np.mean(np.linalg.norm(corners - color, axis=1) <= 10.0))
        score = 0.82 * area_share + 0.12 * border_share + 0.06 * corner_share
        scored.append(
            {
                "color": color,
                "score": score,
                "area_share": area_share,
                "border_share": border_share,
                "corner_share": corner_share,
            }
        )

    if not scored:
        color = np.median(rgb.reshape(-1, 3), axis=0)
        return color, {"confidence": "low", "confidence_score": 0.0, "ambiguous": True, "candidates": []}

    if polarity == "dark-on-light":
        selected = max(scored, key=lambda item: _luminance(item["color"]))
        margin = 1.0
        confidence = "high"
        ambiguous = False
    elif polarity == "light-on-dark":
        selected = min(scored, key=lambda item: _luminance(item["color"]))
        margin = 1.0
        confidence = "high"
        ambiguous = False
    else:
        scored.sort(key=lambda item: item["score"], reverse=True)
        selected = scored[0]
        margin = selected["score"] - (scored[1]["score"] if len(scored) > 1 else 0.0)
        if selected["area_share"] >= 0.55 and margin >= 0.06:
            confidence, ambiguous = "high", False
        elif selected["area_share"] >= 0.42 and margin >= 0.025:
            confidence, ambiguous = "medium", False
        else:
            confidence, ambiguous = "low", True

    report_candidates = [
        {
            "rgb": np.rint(item["color"]).astype(int).tolist(),
            "score": round(float(item["score"]), 5),
            "area_share": round(float(item["area_share"]), 5),
            "border_share": round(float(item["border_share"]), 5),
            "corner_share": round(float(item["corner_share"]), 5),
        }
        for item in sorted(scored, key=lambda item: item["score"], reverse=True)
    ]
    return selected["color"], {
        "confidence": confidence,
        "confidence_score": round(min(1.0, max(0.0, margin / 0.15)), 5),
        "ambiguous": ambiguous,
        "candidates": report_candidates,
    }


def estimate_coverage(
    image: Image.Image,
    *,
    background: ColorHint = None,
    foreground: ColorHint = None,
    polarity: str = "auto",
) -> tuple[np.ndarray, dict]:
    if polarity not in {"auto", "dark-on-light", "light-on-dark"}:
        raise ValueError("polarity must be auto, dark-on-light, or light-on-dark")

    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
    rgb = rgba[..., :3]
    alpha = rgba[..., 3] / 255.0
    transparent_share = float(np.mean(alpha < 0.98))
    fully_transparent_share = float(np.mean(alpha < 0.02))
    border_transparent_share = float(np.mean(_border_values(alpha) < 0.02))
    explicit_transparent = background == "transparent"
    auto_transparent = (
        background is None
        and fully_transparent_share >= 0.05
        and (fully_transparent_share >= 0.20 or border_transparent_share >= 0.10)
    )

    if explicit_transparent or auto_transparent:
        visible = alpha > 0.9
        ink = np.median(rgb[visible], axis=0) if np.any(visible) else np.array([0, 0, 0])
        return alpha.astype(np.float32), {
            "method": "alpha",
            "background_rgba": [0, 0, 0, 0],
            "ink_rgb": np.rint(ink).astype(int).tolist(),
            "polarity": "alpha",
            "confidence": "high" if explicit_transparent or fully_transparent_share >= 0.20 else "medium",
            "confidence_score": 1.0 if explicit_transparent else round(min(1.0, fully_transparent_share * 3), 5),
            "ambiguous": False,
            "transparent_share": round(transparent_share, 6),
            "warnings": [],
        }

    warnings = []
    if transparent_share > 0:
        warnings.append(
            "Some transparent pixels were ignored because alpha does not clearly encode a transparent background; pass --background transparent to override."
        )

    if isinstance(background, tuple):
        background_rgb = np.array(background, dtype=np.float32)
        selection = {"confidence": "high", "confidence_score": 1.0, "ambiguous": False, "candidates": []}
        method = "explicit-background"
    else:
        background_rgb, selection = _choose_background(rgb, polarity)
        method = "auto-palette-spatial-score"

    distance = np.linalg.norm(rgb - background_rgb, axis=2)
    nonzero = distance[distance > 0.5]
    if nonzero.size == 0:
        coverage = np.zeros(distance.shape, dtype=np.float32)
        ink = background_rgb.copy()
        selection = {**selection, "confidence": "low", "confidence_score": 0.0, "ambiguous": True}
        warnings.append("The image is effectively a single color; foreground coverage is undefined.")
    else:
        if isinstance(foreground, tuple):
            ink = np.array(foreground, dtype=np.float32)
        else:
            strong_cut = float(np.percentile(nonzero, 92))
            strong = rgb[distance >= max(1.0, strong_cut)]
            ink = np.median(strong, axis=0) if strong.size else rgb.reshape(-1, 3)[np.argmax(distance)]
        direction = background_rgb - ink
        denom = float(np.dot(direction, direction))
        projected = (
            np.sum((background_rgb - rgb) * direction, axis=2) / denom
            if denom > 1e-6
            else np.zeros(distance.shape, dtype=np.float32)
        )
        scale = max(float(np.percentile(nonzero, 99)), 1.0)
        magnitude = distance / scale
        coverage = np.clip(np.maximum(projected, magnitude * 0.85), 0.0, 1.0)

    inferred_polarity = "dark-on-light" if _luminance(ink) < _luminance(background_rgb) else "light-on-dark"
    if selection.get("ambiguous"):
        warnings.append("Background polarity is ambiguous; rerun with --background and optionally --foreground/--polarity before QA acceptance.")
    return coverage.astype(np.float32), {
        "method": method,
        "background_rgba": np.rint(np.append(background_rgb, 255)).astype(int).tolist(),
        "ink_rgb": np.rint(ink).astype(int).tolist(),
        "polarity": inferred_polarity,
        "confidence": selection.get("confidence", "low"),
        "confidence_score": selection.get("confidence_score", 0.0),
        "ambiguous": bool(selection.get("ambiguous", True)),
        "transparent_share": round(transparent_share, 6),
        "candidates": selection.get("candidates", []),
        "warnings": warnings,
    }


def premultiplied_rgba(image: Image.Image) -> np.ndarray:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
    alpha = rgba[..., 3:4]
    return np.concatenate((rgba[..., :3] * alpha, alpha), axis=2)


def composite_on_matte(image: Image.Image, matte_rgb: tuple[int, int, int]) -> np.ndarray:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
    alpha = rgba[..., 3:4]
    matte = np.array(matte_rgb, dtype=np.float32).reshape(1, 1, 3) / 255.0
    return (rgba[..., :3] * alpha + matte * (1.0 - alpha)) * 255.0


def foreground_bbox(coverage: np.ndarray, threshold: float = ANALYSIS_THRESHOLD) -> list[int] | None:
    ys, xs = np.nonzero(coverage >= threshold)
    if xs.size == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)]


def connected_components(mask: np.ndarray) -> list[dict]:
    """Label 8-connected runs without a Python operation per foreground pixel."""
    mask = np.asarray(mask, dtype=bool)
    height, _ = mask.shape
    parents: list[int] = []
    runs: list[tuple[int, int, int, int]] = []
    previous: list[tuple[int, int, int]] = []

    def make_set() -> int:
        identifier = len(parents)
        parents.append(identifier)
        return identifier

    def find(identifier: int) -> int:
        while parents[identifier] != identifier:
            parents[identifier] = parents[parents[identifier]]
            identifier = parents[identifier]
        return identifier

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for y in range(height):
        padded = np.pad(mask[y].astype(np.int8), (1, 1))
        transitions = np.diff(padded)
        starts = np.flatnonzero(transitions == 1)
        ends = np.flatnonzero(transitions == -1)
        current: list[tuple[int, int, int]] = []
        previous_index = 0
        for start, end in zip(starts.tolist(), ends.tolist()):
            identifier = make_set()
            while previous_index < len(previous) and previous[previous_index][1] < start:
                previous_index += 1
            scan = previous_index
            while scan < len(previous) and previous[scan][0] <= end:
                prev_start, prev_end, prev_identifier = previous[scan]
                if prev_end >= start and prev_start <= end:
                    union(identifier, prev_identifier)
                scan += 1
            current.append((start, end, identifier))
            runs.append((y, start, end, identifier))
        previous = current

    aggregates: dict[int, dict] = {}
    for y, start, end, identifier in runs:
        root = find(identifier)
        length = end - start
        aggregate = aggregates.setdefault(
            root,
            {"area": 0, "min_x": start, "min_y": y, "max_x": end, "max_y": y + 1, "sum_x": 0.0, "sum_y": 0.0},
        )
        aggregate["area"] += length
        aggregate["min_x"] = min(aggregate["min_x"], start)
        aggregate["min_y"] = min(aggregate["min_y"], y)
        aggregate["max_x"] = max(aggregate["max_x"], end)
        aggregate["max_y"] = max(aggregate["max_y"], y + 1)
        aggregate["sum_x"] += (start + end - 1) * length / 2.0
        aggregate["sum_y"] += y * length

    components = [
        {
            "area_px": int(item["area"]),
            "bbox_xyxy": [int(item["min_x"]), int(item["min_y"]), int(item["max_x"]), int(item["max_y"])],
            "centroid_xy": [round(item["sum_x"] / item["area"], 3), round(item["sum_y"] / item["area"], 3)],
        }
        for item in aggregates.values()
    ]
    return sorted(components, key=lambda item: item["area_px"], reverse=True)


def boundary_mask(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1, mode="constant", constant_values=False)
    eroded = (
        padded[1:-1, 1:-1]
        & padded[:-2, 1:-1]
        & padded[2:, 1:-1]
        & padded[1:-1, :-2]
        & padded[1:-1, 2:]
    )
    return mask & ~eroded


def directed_boundary_distances(source_boundary: np.ndarray, target_boundary: np.ndarray) -> np.ndarray:
    source_points = np.argwhere(source_boundary).astype(np.float32)
    target_points = np.argwhere(target_boundary).astype(np.float32)
    if source_points.size == 0 or target_points.size == 0:
        return np.array([], dtype=np.float32)
    minima = []
    for start in range(0, len(source_points), 256):
        chunk = source_points[start : start + 256]
        squared = np.sum((chunk[:, None, :] - target_points[None, :, :]) ** 2, axis=2)
        minima.append(np.sqrt(np.min(squared, axis=1)))
    return np.concatenate(minima)
