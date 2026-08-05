# QA metrics and acceptance

## Metric meanings

- **Soft IoU** compares continuous estimated ink coverage. It is useful for antialiased edges and subpixel changes.
- **Binary IoU** compares foreground masks at the reported QA threshold, which defaults to `0.10`.
- **Premultiplied RGB MAE** compares visible color multiplied by alpha, so arbitrary RGB stored under fully transparent pixels cannot corrupt the metric.
- **Alpha MAE** measures transparency differences independently.
- **Composited RGB MAE** compares both images on the reported matte/background.
- **Coverage MAE** compares continuous estimated ink coverage.
- **Boundary mean/RMS distance** measures how far source and render boundaries lie from one another; RMS emphasizes localized large misses.
- **Source-only/render-only pixels** expose missing versus extra ink.

No single number proves a vector is correct. Use metrics to rank candidates that already pass topology, smoothness, polarity-confidence, and visual-structure checks.

## Threshold distinction

The analyzer uses `0.08` to retain faint antialias fringe when measuring bounds and connected components. Binary QA uses `0.10` to reduce unstable edge noise. Soft IoU and coverage MAE are continuous and independent of these binary thresholds. Both constants are defined in `scripts/raster_utils.py`.

## Read the comparison image

The overlay uses cyan for source-only ink, magenta for render-only ink, and dark overlap for agreement.

- Uniform cyan/magenta halos on opposite sides usually mean translation.
- Halos that reverse across the shape usually mean scale or radius error.
- Endpoint residual indicates cap or endpoint placement.
- Corner residual indicates radius, join, or miter behavior.
- Residual along one side of a curve indicates a centerline or tangent error.
- Fine edge noise with correct landmarks often indicates renderer/DPR/color differences.

## Acceptance order

Rank candidates by:

1. verified runtime and non-ambiguous polarity;
2. smoothness gate pass;
3. correct topology and negative space;
4. correct placement, scale, endpoints, extrema, and symmetry;
5. highest practical soft/binary IoU and lowest visible pixel deltas;
6. lowest boundary error;
7. lowest editable complexity.

A jagged candidate cannot win merely by having higher IoU. A clean candidate cannot win if it visibly changes the icon's structure.

## Renderer calibration

Compare only renders with identical physical dimensions. Record CSS size, physical size, DPR, background, and foreground color. Keep geometry fixed while testing renderer hypotheses.

For odd physical dimensions at DPR 2, the browser viewport is necessarily larger than the requested device-pixel canvas. The renderer captures that browser output, crops the right/bottom excess to the requested physical dimensions, then verifies the PNG header. Do not replace this with metadata-only arithmetic.

Expect small differences between rasterizers. The decisive contract is that the supplied final PNG was rendered from the supplied SVG using the reported settings.

## Iteration record

For each numbered candidate, retain the SVG or changed parameters, browser render, comparison image, metrics JSON, and a one-sentence structural/smoothness verdict.

Default to at most 10 geometry iterations. If the budget ends first, select the best candidate using the acceptance order and disclose the remaining visible residual instead of claiming perfection.
