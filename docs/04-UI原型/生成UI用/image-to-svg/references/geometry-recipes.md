# Geometry reconstruction recipes

## Read pixels as samples, not vertices

A raster edge is the convolution of ideal geometry, stroke expansion, antialiasing, scale, color blending, and screenshot resampling. Do not place SVG knots on every boundary pixel. Estimate the latent geometric rule that could have produced the coverage field.

## Stroke-authored icons

Use a centerline stroke when opposite edges stay roughly parallel and width remains constant.

- Estimate the centerline as the midpoint between paired edge samples.
- Estimate width from their perpendicular separation, preferably at several straight or low-curvature locations.
- Fit the centerline first; tune width second.
- Test round, square, and butt caps against endpoint extension.
- Test round, bevel, and miter joins against corner silhouette.
- Keep one shared stroke width unless the image gives consistent evidence for another.

At small sizes, a nominal integer width can cover fractional pixels because its centerline is subpixel-positioned. Test half-pixel placement before inventing variable-width outlines.

## Circles, rings, and ellipses

- Measure outer and inner extrema independently.
- For a true stroked circle, use one circle plus stroke when both boundaries are concentric and width is constant.
- For a filled ring, use an even-odd compound path when independent inner/outer tuning is required.
- Preserve the optical center shown by the raster; do not force arithmetic centering when nearby elements create a deliberate optical shift.
- Fit circles analytically; do not approximate them with many line segments.

## Rounded rectangles and folders

- Infer straight spans before corner radii.
- Pair opposing edges and share dimensions when symmetry is visible.
- Use `<rect rx>` for uniform corners; use a short path only when corners differ or a tab interrupts the perimeter.
- Treat tabs, folds, and notches as semantic parts with aligned anchors.

## Arcs and speech bubbles

- Fit the main circle/ellipse or rounded container first.
- Attach tails at tangent-compatible points; avoid a visible kink where the tail enters the body.
- Determine whether the tail replaces part of the outline or overlays it. The negative-space topology distinguishes the two.
- Use arc commands or a few cubics, not dense traced contours.

## Chevrons and angled strokes

- Measure the vertex, two endpoints, and included angle.
- Check whether both arms have equal projected length and whether the vertex is optically shifted.
- Round joins often extend beyond the mathematical vertex. Compare the visible outer tip, not just the centerline intersection.

## Scissors, microphones, and composite glyphs

- Separate the construction into semantic primitives: handles/blades/pivot, capsule/stem/base, or body/details.
- Share axes and pivot centers.
- Determine occlusion order from crossings and antialiasing; separate elements only when overlap changes the visible result.
- Prefer strokes for uniform skeletal pieces and filled shapes for compact solid details.

## Symmetry and repetition

Use symmetry as a fitting constraint when residuals are compatible with screenshot noise.

- Mirror control points across a measured axis.
- Share eye radii, dot sizes, corner radii, and repeated gaps.
- Use transforms for exact repeated geometry only when the SVG remains easy to edit.
- Break symmetry only when the same asymmetric residual persists across multiple edge samples.

## Cubic curves

- Put anchors at genuine extrema, corners, inflections, and topology changes.
- Align handles with the intended tangent.
- Preserve G1 continuity across smooth joins; use G2-like curvature flow when visible.
- Reduce knots when a curve chatters. Add a knot only where a simpler curve produces a localized, repeatable residual.
- Reject alternating 1 px horizontal/vertical trace runs for smooth source geometry.

## Color and antialiasing

Estimate solid background and foreground colors from stable interior pixels, not edge pixels. Edge pixels are mixtures. Tune geometry before color because a color error can resemble a coverage error.

When a screenshot may have been resampled, compare limited renderer hypotheses:

1. DPR 1 at the physical dimensions;
2. DPR 2 at half CSS dimensions;
3. fractional SVG placement or width;
4. only then, a small color adjustment.

Do not use renderer calibration to conceal incorrect geometry.

## Grouping, crop, and canvas choices

Treat an icon plus wordmark, a multi-part logo, or a deliberately composed badge as one asset when spacing and alignment carry brand meaning. Preserve the measured lockup in one SVG by default. Split it only when the user asks for separate assets.

For a single toolbar screenshot, reconstruct each icon on a crop that preserves its local whitespace. Use the crop dimensions as the SVG viewBox for pixel comparison. Also provide a tight-viewBox version only if requested. A combined toolbar SVG is a separate deliverable and must preserve measured icon centers and gaps.

Do not assume a tight crop's border is background. If automatic palette scoring reports low confidence, test both polarity hypotheses and require an explicit background/foreground choice before final QA. Adding an inset to border sampling alone is not a reliable fix for thick outlines or filled marks.
