#!/usr/bin/env python3
"""Measure a raster graphic or crop without tracing it into SVG."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from raster_utils import (
    ANALYSIS_THRESHOLD,
    connected_components,
    display_path,
    estimate_coverage,
    foreground_bbox,
    load_rgba,
    parse_color_hint,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="PNG/JPG/WebP raster input")
    parser.add_argument("--crop", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--out-dir", default="raster-analysis")
    parser.add_argument("--threshold", type=float, default=ANALYSIS_THRESHOLD)
    parser.add_argument("--background", default="auto", help="auto, transparent, #RRGGBB, or R,G,B")
    parser.add_argument("--foreground", default="auto", help="auto, #RRGGBB, or R,G,B")
    parser.add_argument("--polarity", choices=("auto", "dark-on-light", "light-on-dark"), default="auto")
    parser.add_argument("--allow-ambiguous", action="store_true")
    parser.add_argument("--zoom", type=int, default=8)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    image = load_rgba(args.input, args.crop)
    try:
        background = parse_color_hint(args.background)
        foreground = parse_color_hint(args.foreground, allow_transparent=False)
    except ValueError as exc:
        parser.error(str(exc))

    coverage, color_report = estimate_coverage(
        image,
        background=background,
        foreground=foreground,
        polarity=args.polarity,
    )
    mask = coverage >= args.threshold
    bbox = foreground_bbox(coverage, args.threshold)
    components = connected_components(mask)

    image.save(out_dir / "source_crop.png")
    coverage_image = Image.fromarray(np.rint(coverage * 255).astype(np.uint8), mode="L")
    coverage_image.save(out_dir / "coverage.png")
    zoom = max(1, args.zoom)
    image.resize((image.width * zoom, image.height * zoom), Image.Resampling.NEAREST).save(out_dir / "zoom.png")

    report = {
        "input": display_path(args.input),
        "crop_xywh": args.crop,
        "canvas_px": [image.width, image.height],
        "foreground_bbox_xyxy": bbox,
        "foreground_bbox_size": None if bbox is None else [bbox[2] - bbox[0], bbox[3] - bbox[1]],
        "foreground_area_px_at_threshold": int(np.count_nonzero(mask)),
        "coverage_sum_px": round(float(np.sum(coverage)), 3),
        "threshold": args.threshold,
        "colors": color_report,
        "connected_components": components,
        "qa_blocked": bool(color_report.get("ambiguous") and not args.allow_ambiguous),
        "note": "Components and coverage are measurements only; do not convert their pixel boundaries directly into final smooth paths.",
    }
    (out_dir / "analysis.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["qa_blocked"]:
        print("Background polarity is ambiguous. Rerun with --background/--foreground or --allow-ambiguous.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
