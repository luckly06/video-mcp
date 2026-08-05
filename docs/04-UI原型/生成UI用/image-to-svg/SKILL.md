---
name: image-to-svg
description: Convert user-provided raster images into clean editable transparent-background SVG for icons, logos, UI glyphs, wordmarks, text marks, and other flat graphic assets. Use for PNG, JPG, WebP, or screenshot inputs, including non-transparent images whose background must be separated before vector reconstruction, combined logo-and-text lockups, pixel-faithful icon replication, and requests to turn an image icon or logo into SVG. Do not use for ordinary photo vectorization or bitmap-only output.
---

# Image to SVG

Rebuild the foreground geometry represented by the supplied pixels. Treat the raster as the only source of truth and produce a real vector, not a bitmap wrapper.

Resolve `<skill-dir>` to the directory containing this `SKILL.md`. Resolve every bundled script and reference relative to that directory. Do not assume a vendor-specific home directory or the current working directory.

## Non-negotiable boundary

- Use only the user-provided raster and facts explicitly supplied by the user.
- Do not browse the web, search icon libraries, inspect application bundles, reverse-search the image, or recover an original/candidate SVG.
- Do not embed the source with `<image>`, a data URL, `foreignObject`, a raster pattern, or CSS background imagery.
- Do not present an automatic threshold contour as final smooth artwork unless the source is intentionally pixel art.
- Do not claim that one raster uniquely determines the original authoring geometry. Aim for a pixel-faithful reference-size render plus clean scaling, and disclose underdetermined details.

## Background separation is mandatory

The final SVG must have a transparent canvas and contain only the requested graphic foreground.

- If the source has meaningful transparency, preserve its visible foreground and ignore fully transparent pixels.
- If the source is opaque, identify and exclude the background before reconstructing geometry. This is an internal preprocessing step, not a separate output mode.
- For solid or near-solid backgrounds, infer the background from palette prevalence, borders, corners, and spatial continuity. Do not assume every border pixel is background.
- For gradients, shadows, compression noise, or textured backgrounds, separate the intended foreground semantically and inspect edges at high zoom. Do not vectorize the surrounding scene unless the user asks for it.
- When foreground/background separation is ambiguous or part of the graphic may be occluded, state the ambiguity instead of silently inventing hidden geometry.
- Do not deliver a transparent PNG or mask unless the user explicitly asks for one.

## Runtime levels

The core workflow has no mandatory third-party installation. Viewing the image, reasoning about foreground geometry, writing SVG, and running `scripts/svg_audit.py` are sufficient to produce a useful result.

Optionally check enhanced QA availability:

```bash
python3 <skill-dir>/scripts/doctor.py
```

The doctor searches the current shell, environment overrides, system runtimes, and known agent-managed runtime caches. Set `IMAGE_TO_SVG_RUNTIME_ROOTS` to a path-separated list when an agent stores reusable runtimes elsewhere. Use the executables reported under `selected_runtimes`; do not assume that the default `python3` or `node` owns already-installed packages.

Pillow and NumPy enable automated raster measurement and image-difference metrics. Node.js, Playwright, and Chrome/Chromium enable deterministic browser rendering. Treat all of them as optional enhancements:

- Prefer already-installed system or agent-managed dependencies; using them is not an installation.
- Do not install dependencies unless the user explicitly asks.
- Do not stop SVG reconstruction when optional dependencies are missing.
- Use available visual inspection, local SVG viewing, and the dependency-free SVG audit instead.
- Report which enhanced QA steps were skipped; do not label the SVG unusable merely because automated metrics were unavailable.

## Deliverables

Always deliver the requested editable `.svg` with a transparent canvas.

- Preserve a logo, icon-plus-wordmark, or other intentional lockup as one combined SVG by default.
- Split elements into separate SVG files only when the user asks or when the source clearly contains unrelated assets.
- Preserve source spacing and relative alignment for combined assets.
- When enhanced QA is available, also deliver `final-render.png`, `comparison.png`, and `metrics.json` as evidence.
- Do not create extra extraction artifacts unless they materially help iteration or the user requests them.

## Workflow

### 1. Inspect, separate, and measure

View the original image. Record canvas dimensions, alpha behavior, likely background, foreground bounds, colors, repeated widths, symmetry axes, negative spaces, element grouping, and whether the artwork appears stroke-authored or fill-authored.

For every opaque source, explicitly decide which pixels belong to the background and exclude that background from the SVG. Tighten the output `viewBox` around the requested foreground unless preserving source whitespace or layout is part of the request.

If Pillow and NumPy are already available, run the analyzer on the image or an explicit crop:

```bash
python3 <skill-dir>/scripts/analyze_raster.py input.png --out-dir outputs/analysis
python3 <skill-dir>/scripts/analyze_raster.py input.png --crop X Y W H --out-dir outputs/asset/analysis
```

Inspect `analysis.json`, `coverage.png`, and `zoom.png`. If `qa_blocked` is true, inspect the raster and rerun with explicit evidence:

```bash
python3 <skill-dir>/scripts/analyze_raster.py input.png \
  --background '#f7f7f7' --foreground '#4c4c4c' --polarity dark-on-light \
  --out-dir outputs/analysis
```

Use `--background transparent` for a genuinely transparent canvas. Connected components are measurements only; separate letters, dots, and antialiased fragments do not necessarily imply separate final paths.

When the analyzer is unavailable, perform the same foreground/background decision visually and continue.

### 2. Form geometry hypotheses

Read `references/geometry-recipes.md`. Start at the lowest adequate complexity:

1. circles, ellipses, lines, polylines, rectangles, rounded rectangles, and arcs;
2. small composites of primitives and negative-space cutouts;
3. a few analytic cubic curves with deliberate tangent continuity;
4. a smoothed outline fitted from measured edges;
5. a trace-derived outline only for genuinely irregular silhouettes, followed by simplification and smoothing.

For apparent line icons, test a centerline stroke hypothesis before fitting both outline edges. Infer centerlines from paired edges and estimate width from their separation. Test caps, joins, miter behavior, and open-versus-closed endpoints. Use filled outlines only when constant-width strokes cannot explain the pixels.

Use symmetry and repetition as constraints. Share radii, stroke widths, corner radii, centers, and mirrored control points when supported by the raster.

For wordmarks and combined brand marks, preserve each visible glyph as vector geometry and preserve the measured relationship between symbol and text. Do not substitute editable font text unless the user prefers it or the typeface is known from supplied evidence.

### 3. Author and audit a true SVG

Write a correct tight or intentionally spaced `viewBox`, semantic element ids, explicit colors, and the fewest elements/control points that explain the image. Leave the canvas transparent and keep coordinates readable enough for later editing.

Run the dependency-free audit after every structural rewrite:

```bash
python3 <skill-dir>/scripts/svg_audit.py asset.svg --report outputs/audit.json
```

Any raster embedding or external reference is a hard failure. Internal `url(#id)` references for gradients, masks, clip paths, and filters are valid. Treat excessive path commands and noisy knots as smoothness warnings.

### 4. Render with available tools

If the bundled browser runtime is already available, render at exact physical dimensions:

```bash
node <skill-dir>/scripts/render_svg.cjs asset.svg outputs/final-render.png \
  --physical-width 74 --physical-height 90 --dpr 1 --background '#f7f7f7'
```

For a DPR-2 hypothesis, keep the same physical dimensions and change only `--dpr 2`.

If Playwright is unavailable, use any existing safe local SVG viewer or image preview to inspect the delivered SVG at reference size and 200–400% zoom. Do not install a renderer automatically.

### 5. Compare and refine

If Pillow and NumPy are already available, compare source and render at identical physical dimensions:

```bash
python3 <skill-dir>/scripts/compare_fit.py source-crop.png outputs/final-render.png \
  --background '#f7f7f7' --foreground '#4c4c4c' --out-dir outputs/qa
```

Read `references/qa-metrics.md`, inspect `comparison.png`, and refine in this order:

1. foreground/background separation, canvas, placement, scale, and optical centering;
2. topology, open/closed structure, and negative space;
3. endpoints, extrema, radii, corner positions, and symmetry;
4. stroke width, caps, joins, and local width profile;
5. curve tangents and antialias-sensitive subpixel placement;
6. foreground color.

When automated comparison is unavailable, apply the same ordering through direct visual comparison. Change a small set of parameters per iteration and stop at the first clean, structurally correct fit or after 10 geometry iterations.

### 6. Accept with available evidence

Always require:

- the SVG passes the no-raster/external-reference audit;
- the background is excluded and the SVG canvas is transparent;
- intended curves remain smooth at 200–400% zoom;
- no obvious structural, spacing, endpoint, or negative-space mismatch remains;
- the grouping matches user intent, including combined logo-and-text layouts.

When enhanced QA is available, also require non-ambiguous coverage polarity, exact render dimensions, and locally competitive comparison metrics among clean candidates. When it is unavailable, report the skipped automated checks without blocking delivery.

Do not impose a universal IoU threshold. A smooth primitive can be more correct than a jagged trace with higher IoU; high IoU does not excuse wrong topology or background inclusion.

## Thresholds for enhanced analysis

- Analysis defaults to coverage `0.08` to retain faint antialias fringe for bounds and components.
- Binary QA defaults to `0.10` for a more stable foreground mask.
- Soft IoU and coverage MAE remain continuous and do not use either binary threshold.

Both defaults live in `scripts/raster_utils.py` and every enhanced report records the active value.

## Maintenance

Run the dependency-free skill validation after changing metadata or instructions. Run enhanced regressions only when their dependencies are already available:

```bash
python3 <skill-creator-dir>/scripts/quick_validate.py <skill-dir>
python3 <skill-dir>/scripts/self_test.py
python3 <skill-dir>/scripts/self_test.py --require-browser
```

## References

- `references/geometry-recipes.md` — stroke, primitive, curve, symmetry, grouping, and crop reconstruction guidance.
- `references/qa-metrics.md` — optional metric interpretation, renderer calibration, and acceptance ordering.
