#!/usr/bin/env python3
"""Compare a raster source crop with an exact-size SVG browser render."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from raster_utils import (
    QA_THRESHOLD,
    boundary_mask,
    composite_on_matte,
    directed_boundary_distances,
    estimate_coverage,
    load_rgba,
    parse_color_hint,
    premultiplied_rgba,
)


def metric_or_none(values: np.ndarray, fn) -> float | None:
    return None if values.size == 0 else round(float(fn(values)), 6)


def make_overlay(source_coverage: np.ndarray, render_coverage: np.ndarray) -> Image.Image:
    height, width = source_coverage.shape
    white = np.full((height, width, 3), 250.0, dtype=np.float32)
    source_color = np.array([0.0, 177.0, 255.0], dtype=np.float32)
    render_color = np.array([255.0, 0.0, 166.0], dtype=np.float32)
    overlap_color = np.array([35.0, 35.0, 35.0], dtype=np.float32)
    overlap = np.minimum(source_coverage, render_coverage)
    source_only = np.clip(source_coverage - overlap, 0.0, 1.0)
    render_only = np.clip(render_coverage - overlap, 0.0, 1.0)
    total = np.clip(overlap + source_only + render_only, 0.0, 1.0)
    ink = (
        overlap[..., None] * overlap_color
        + source_only[..., None] * source_color
        + render_only[..., None] * render_color
    )
    result = white * (1.0 - total[..., None]) + ink
    return Image.fromarray(np.rint(np.clip(result, 0, 255)).astype(np.uint8), mode="RGB")


def make_difference(source_coverage: np.ndarray, render_coverage: np.ndarray) -> Image.Image:
    delta = np.abs(source_coverage - render_coverage)
    intensity = np.clip(delta * 2.5, 0.0, 1.0)
    heat = np.full((*delta.shape, 3), 255.0, dtype=np.float32)
    heat[..., 1] *= 1.0 - intensity
    heat[..., 2] *= 1.0 - intensity
    return Image.fromarray(np.rint(heat).astype(np.uint8), mode="RGB")


def labeled_panel(image: Image.Image, label: str, scale: int, matte: tuple[int, int, int]) -> Image.Image:
    composited = Image.fromarray(np.rint(composite_on_matte(image, matte)).astype(np.uint8), mode="RGB")
    if scale > 1:
        composited = composited.resize(
            (composited.width * scale, composited.height * scale),
            Image.Resampling.NEAREST,
        )
    panel = Image.new("RGB", (composited.width, composited.height + 24), "white")
    panel.paste(composited, (0, 24))
    draw = ImageDraw.Draw(panel)
    draw.text((7, 6), label, fill=(35, 35, 35))
    return panel


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("render")
    parser.add_argument("--source-crop", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--render-crop", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--out-dir", default="fit-comparison")
    parser.add_argument("--threshold", type=float, default=QA_THRESHOLD)
    parser.add_argument("--background", default="auto", help="auto, transparent, #RRGGBB, or R,G,B")
    parser.add_argument("--foreground", default="auto", help="auto, #RRGGBB, or R,G,B")
    parser.add_argument("--polarity", choices=("auto", "dark-on-light", "light-on-dark"), default="auto")
    parser.add_argument("--matte", default="auto", help="Visible-comparison matte: auto, #RRGGBB, or R,G,B")
    parser.add_argument("--allow-ambiguous", action="store_true")
    parser.add_argument("--sheet-scale", type=int, default=4)
    args = parser.parse_args()

    source = load_rgba(args.source, args.source_crop)
    render = load_rgba(args.render, args.render_crop)
    if source.size != render.size:
        raise SystemExit(f"Image sizes differ: source={source.size}, render={render.size}")

    try:
        background = parse_color_hint(args.background)
        foreground = parse_color_hint(args.foreground, allow_transparent=False)
        matte_hint = parse_color_hint(args.matte, allow_transparent=False)
    except ValueError as exc:
        parser.error(str(exc))

    source_coverage, source_colors = estimate_coverage(
        source,
        background=background,
        foreground=foreground,
        polarity=args.polarity,
    )
    render_polarity = source_colors.get("polarity", args.polarity)
    if render_polarity == "alpha":
        render_polarity = args.polarity
    render_coverage, render_colors = estimate_coverage(
        render,
        background=background,
        foreground=foreground,
        polarity=render_polarity,
    )
    ambiguous = bool(source_colors.get("ambiguous") or render_colors.get("ambiguous"))
    if ambiguous and not args.allow_ambiguous:
        raise SystemExit(
            "Coverage polarity is ambiguous. Rerun with --background and optionally --foreground/--polarity; "
            "use --allow-ambiguous only for diagnostic output, never final acceptance."
        )

    source_mask = source_coverage >= args.threshold
    render_mask = render_coverage >= args.threshold
    intersection = int(np.count_nonzero(source_mask & render_mask))
    union = int(np.count_nonzero(source_mask | render_mask))
    soft_intersection = float(np.minimum(source_coverage, render_coverage).sum())
    soft_union = float(np.maximum(source_coverage, render_coverage).sum())

    source_boundary = boundary_mask(source_mask)
    render_boundary = boundary_mask(render_mask)
    source_to_render = directed_boundary_distances(source_boundary, render_boundary)
    render_to_source = directed_boundary_distances(render_boundary, source_boundary)
    symmetric_distances = np.concatenate((source_to_render, render_to_source))

    source_premultiplied = premultiplied_rgba(source)
    render_premultiplied = premultiplied_rgba(render)
    if isinstance(matte_hint, tuple):
        matte = matte_hint
    elif source_colors.get("polarity") == "alpha":
        matte = (255, 255, 255)
    else:
        matte = tuple(int(channel) for channel in source_colors["background_rgba"][:3])
    source_visible = composite_on_matte(source, matte)
    render_visible = composite_on_matte(render, matte)

    metrics = {
        "canvas_px": [source.width, source.height],
        "threshold": args.threshold,
        "soft_iou": None if soft_union == 0 else round(soft_intersection / soft_union, 6),
        "binary_iou": None if union == 0 else round(intersection / union, 6),
        "premultiplied_rgb_mae_0_255": round(
            float(np.mean(np.abs(source_premultiplied[..., :3] - render_premultiplied[..., :3])) * 255.0),
            6,
        ),
        "alpha_mae_0_1": round(float(np.mean(np.abs(source_premultiplied[..., 3] - render_premultiplied[..., 3]))), 6),
        "composited_rgb_mae_0_255": round(float(np.mean(np.abs(source_visible - render_visible))), 6),
        "comparison_matte_rgb": list(matte),
        "coverage_mae_0_1": round(float(np.mean(np.abs(source_coverage - render_coverage))), 6),
        "source_only_px": int(np.count_nonzero(source_mask & ~render_mask)),
        "render_only_px": int(np.count_nonzero(render_mask & ~source_mask)),
        "source_ink_px": int(np.count_nonzero(source_mask)),
        "render_ink_px": int(np.count_nonzero(render_mask)),
        "boundary_mean_px": metric_or_none(symmetric_distances, np.mean),
        "boundary_rms_px": metric_or_none(symmetric_distances, lambda values: np.sqrt(np.mean(values ** 2))),
        "source_colors": source_colors,
        "render_colors": render_colors,
        "qa_blocked": ambiguous,
        "interpretation": "Use metrics to rank smooth structurally correct candidates; do not apply a universal pass threshold.",
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source.save(out_dir / "target.png")
    render.save(out_dir / "render.png")
    overlay = make_overlay(source_coverage, render_coverage)
    difference = make_difference(source_coverage, render_coverage)
    overlay.save(out_dir / "overlay.png")
    difference.save(out_dir / "difference.png")
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n")

    scale = max(1, args.sheet_scale)
    panels = [
        labeled_panel(source, "SOURCE", scale, matte),
        labeled_panel(render, "RENDER", scale, matte),
        labeled_panel(overlay, "OVERLAY cyan=source magenta=render", scale, (255, 255, 255)),
        labeled_panel(difference, "ABS COVERAGE DIFFERENCE", scale, (255, 255, 255)),
    ]
    gap = 8
    sheet = Image.new(
        "RGB",
        (sum(panel.width for panel in panels) + gap * (len(panels) - 1), max(panel.height for panel in panels)),
        (230, 230, 230),
    )
    cursor = 0
    for panel in panels:
        sheet.paste(panel, (cursor, 0))
        cursor += panel.width + gap
    sheet.save(out_dir / "comparison.png")

    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
