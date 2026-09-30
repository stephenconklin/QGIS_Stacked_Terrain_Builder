# Changelog

## Unreleased

- README: the introduction describes both views (oblique and nadir) instead of only the offset look, and *How it works* no longer assumes an oblique view. New Gallery section showing every style, a nadir view and all nine color ramps, each with shaded faces and a paper texture; the before/after pair is re-rendered (the after image is now Paper layers with Viridis, shading and paper).

## 3.3.0 — 2026-09-30

- The project is now called **QGIS Stacked Terrain Builder** (folder and GitHub repository `QGIS_Stacked_Terrain_Builder`). The tool's name in QGIS and its algorithm id are unchanged, so saved models keep working.
- Removed the *Set project background to the ramp's starting color* option. The tool no longer changes the project background; set it in Project → Properties → General if you want one. Models and scripts that still pass `BACKGROUND` are unaffected, since the value is ignored.

## 3.2.0 — 2026-09-30

- The dialog now starts with the **Paper layers** style and the **Spectral** ramp (previously Offset slabs and Magma). Runs from models and scripts that don't pass `STYLE` or `RAMP` get the new defaults too; pass `STYLE: 0, RAMP: 0` for the old look.

## 3.1.0 — 2026-09-30

- **The dialog greys out settings that don't apply**: in a nadir view the view azimuth, angle, vertical exaggeration, lift and graded walls; the vertical exaggeration until a view elevation angle is set; the view angle and auto lift once a lift per unit of elevation is set; the target number of levels once an interval is set; paper opacity and shading strength until there is a paper image or shading.
- **Fixed: on Windows, file outputs got no folder, no `.qlr` bundle and no ArcGIS Pro export.** A drive letter (`C:`) was mistaken for a URI scheme, so every output was treated as a non-file.
- The run log warns about settings it ignores (for runs from models and scripts, where nothing is greyed out), and when a Grand Canyon ramp is used on a DEM whose elevations look like feet or lie outside the ramp's range.
- Dialog order follows how often settings change: DEM and AOI, Style and Color ramp, the view and light, then shading, walls, paper and the rest. Contour interval and Target number of levels moved to Advanced. Style options carry short descriptions, the fixed-elevation ramps are marked in the ramp list, and the background option now says "starting color" (it was "darkest", which was wrong for several ramps). The help panel is formatted in sections.
- The run log gives the view, light and lift in one line.
- README screenshots are real renders of the Grand Canyon instead of placeholders; stale references to *Vertical exaggeration* as the lift setting fixed; troubleshooting covers nadir, feet DEMs and ignored settings. `dev/smoke_test.py` has switches for style, view, light and shading.
- Developer: the shading and paper post-processors share one base class, and the individual layer moves (made redundant by the output group in 2.4.0) are gone; the group alone sets the order.

## 3.0.0 — 2026-09-30

- **Views.** A new *View* option chooses between **Oblique** (the stacked look so far, slabs lifted with walls showing) and **Nadir** (straight down: slabs stay on their true ground positions, with no lift and no walls; depth comes from shadows and lit edges, so a nadir run raises the shadow to at least 0.85 and the highlight to at least 0.15 mm, and never switches the project CRS).
- **Oblique view direction and angle.** *Oblique view from azimuth* (default 180, from the south, as before) turns the stack: slabs lift away from the viewer and the walls face them. *Oblique view elevation angle* with *Vertical exaggeration* sets the lift as `exaggeration ÷ tan(angle)`; left at 0, the lift is chosen automatically as before, and the log now gives the view angle it amounts to.
- **Light direction.** *Light from azimuth* (default 315) sets which side the highlights face and where the shadows fall, and lights the shaded faces.
- Two new layer variables, `stack_view_az` and `stack_light_az`, keep the view and light live, and `stack_exag = 0` switches a layer to nadir. Layers made by earlier versions have neither variable and draw exactly as before (checked pixel for pixel).
- The previous *Vertical exaggeration* parameter, which set the lift per metre directly, is now the advanced *Lift per unit of elevation* (same parameter id, `EXAG`, so models and scripts keep working). *Vertical exaggeration* now means real vertical exaggeration, used with the view angle.
- Shaded faces, the paper texture's extent and the ArcGIS Pro export follow the view and light directions.
- *Grand Canyon Light* now tops out in a pine green (`#5e8f4e`) instead of olive, with ponderosa nudged to `#a0c46a` to lead into it.

## 2.9.0 — 2026-09-30

- New color ramp, *Grand Canyon Light*: the same life zones and elevations as *Grand Canyon*, in the lighter, brighter tones of the Spectral ramp. Coral-brick rock at the river, red-orange cliffs, light-orange desert scrub, pale gold blackbrush and sage, pale yellow-green pinyon-juniper, yellow-green ponderosa and olive mixed conifer. It keeps to warm greens, since Spectral's blue-greens read as water.

## 2.8.0 — 2026-09-29

- **Faster rendering.** Slab outlines are simplified after smoothing, with a tolerance of a tenth of the generalization cell (logged with the run). Smoothing doubles the vertex count on every pass with points that are nearly in line; removing them cut a whole-Grand-Canyon run (75 m cell, 45 slabs) from 2.9 million vertices to 0.27 million, the GeoPackage from 46 MB to 4.5 MB, and a full-extent render from 6.9 s to 1.9 s. The outlines look the same at the map's own scale. The ArcGIS Pro export, which repeats the outline in every wall, highlight and face feature, shrinks the same way.

## 2.7.0 — 2026-09-29

- New color ramp, *Grand Canyon*: the canyon's life zones from river to North Rim, tied to real elevations rather than stretched over the run's range. Deep red rock at the river (730 m) and in the lower cliffs, desert-scrub ochre from 1220 m, blackbrush and sage from 1520 m, pinyon-juniper olive from 1830 m, ponderosa green from 2130 m and dark mixed-conifer green from 2500 m; slabs below or above that range keep the end colors. It ignores `stack_base` and `stack_top` and assumes a DEM in metres.

## 2.6.1 — 2026-09-28

- Softer shadows in the ArcGIS Pro export. The stand-in for the QGIS blur is now four copies grown by 0.25–1.3 mm at 55% of the shadow's opacity, instead of three copies of which one wasn't grown; that one left a hard dark rim along every slab edge in Pro. Tuned against the QGIS rendering at 1:50,000.
- Fixed: on QGIS 4.2 a valid CRS taken from a layer can give an empty WKT string, which made the ArcGIS Pro export (and would have made the shaded faces) fail with "OGR Error: Corrupt data". GDAL now gets the CRS from its EPSG code or PROJ string when that happens.
- Developer: `dev/smoke_test.py` finds the output where the tool now puts it (in its own folder, since 2.3.0), and has an `ARCGIS` switch that lists the ArcGIS Pro files.

## 2.6.0 — 2026-09-28

- **The ArcGIS Pro export writes the slabs as a shapefile** (`<name>.shp`) instead of a File Geodatabase. GDAL writes a geodatabase feature class without an extent in its catalog, so Pro left the slabs blank until *Zoom To Layer* made it compute one; a shapefile stores its extent in its header and draws straight away. A `<name>.gdb` left in the `ArcGIS` folder by an earlier version is removed when the export is rerun.

## 2.5.2 — 2026-09-28

- Each ArcGIS Pro export now gives its layers unique internal ids (`uRI`). Before, every export used the same ones, so adding a second export to a map that already held one could leave the new group's slab layer drawing nothing.

## 2.5.1 — 2026-09-28

- The ArcGIS Pro export also writes `<name>_slabs.lyrx`, a layer file with the slabs alone. ArcGIS Pro's *Apply Symbology From Layer* rejects the group `.lyrx` (error 000968), but accepts this one.

## 2.5.0 — 2026-09-28

- **Export for ArcGIS Pro.** A new option, *Also export for ArcGIS Pro*, writes an `ArcGIS` folder inside the output folder with a File Geodatabase (a shapefile on GDAL older than 3.6), copies of the shading and paper rasters, and `<name>.lyrx`, a group layer file that opens the styled stack in Pro. Pro symbols have no layer variables or map-unit geometry generators, so the lift and the walls are baked into the geometry: one feature per wall copy and per face, in drawing order, each with its own symbol from the colors QGIS draws. The highlight and shadow stay page-unit offsets; the blurred shadow becomes three buffered copies. Needs a file output.

## 2.4.0 — 2026-09-27

- **Outputs load as a group.** The slabs, shading and paper texture are placed in a Layers-panel group named after the output ("Stacked terrain" for a temporary output), paper on top, then shading, then slabs, matching the `.qlr`. The group goes where QGIS would have added the layers (at the current selection).

## 2.3.1 — 2026-09-27

- *Set project background* is now off by default, so a run no longer changes the project's background color unless asked to.

## 2.3.0 — 2026-09-27

- **One folder per run.** A file output gets a folder named after it (`…/Hermosa.gpkg` becomes `…/Hermosa/Hermosa.gpkg`), and the shading, paper texture and style files are written next to it. Choosing a path already inside a folder of that name doesn't nest another one. Temporary and database outputs are unchanged.
- **Portable styling.** The shading and paper rasters get sidecar `.qml` files (Multiply, opacity, resampling, grayscale), so they come back styled when added to any project.
- **Layer bundle.** `<name>.qlr` adds the whole stack to any project in one step, as a group with the paper texture, shading and slabs in order and fully styled. Its paths are relative, so the folder can be moved or shared as a unit.

## 2.2.0 — 2026-09-27

- **Graded 3-D walls.** New option that builds each wall from a stack of offset copies (8 by default, up to 12), shading from the full `stack_wall` darkness under the slab's edge to about a third of it at the base, like John Nelson's stacked offsets. Controlled afterwards by the new `stack_wall_steps` variable (1 = the flat wall of earlier versions). Rendering is somewhat slower with it on.

## 2.1.0 — 2026-09-27

- **Styles.** A new *Style* option: *Offset slabs* (the previous look, still the default), *Paper layers* (low lift, pale edge lines, light walls, soft shadow) and *Cut card* (moderate lift, thin dark edges, a deep shadow cast onto the layer below). Each style sets its own auto lift and starting values for the new variables.
- **Shaded faces.** Optional hillshade layer, blended with Multiply, that is shifted slab by slab to follow the lift, so it lines up with the lifted faces. Saved as `<name>_shade.tif`; its strength (default 0.45) is the layer's opacity. It is built for the run's exaggeration, so changing `stack_exag` later needs a rerun.
- **New ramps.** *Teal-Orange* and *Lime-Orange*, modelled on John Nelson's maps. They are written into the style with `create_ramp()`, so they need no entry in the ramp library and open correctly on any machine. Custom ramps use their full range; built-in ramps still start at 0.15.
- **New layer variables** `stack_style`, `stack_wall` (wall darkness), `stack_edge` (edge line width, mm) and `stack_shadow` (shadow opacity), editable in Layer Properties without a rerun.
- The shadow is now a blurred symbol layer controlled by `stack_shadow`, replacing the *Soft drop shadow* checkbox (its paint effect couldn't be driven by a variable). The `shadow=True` argument of `apply_stack_style()` still works and means `stack_shadow` 0.45.
- *Highlight offset* and *Lift fraction* moved to Advanced and default to the style's values when left empty.
- Finer auto cell size (about 1000 cells across instead of 600) for more detail, and default paper opacity lowered from 0.35 to 0.2.

## 2.0.4 — 2026-09-27

- **Layers are named after the output file.** Saving to `Hermosa.gpkg` loads the layer as "Hermosa" and the paper texture as "Hermosa paper texture". Temporary outputs are still named "Stacked terrain" and "Paper texture".
- With an AOI, stacking stops at the first slab that covers the whole AOI, so the three-cell padding added in 2.0.3 can't add duplicate floor slabs below the AOI's lowest level or pull `stack_base` (the bottom of the color ramp) down.

## 2.0.3 — 2026-09-27

- **Clean AOI edges.** With an AOI the slab edges followed the generalized DEM's cells and came out stair-stepped. The DEM is now clipped to the AOI padded by three cells, and the slabs are cut to the exact AOI outline after smoothing, so the edge, and the walls stacked along it, are as smooth as the AOI polygon. Multiple AOI features are merged, and only selected features are used when "Selected features only" is checked.

## 2.0.2 — 2026-09-27

- **Project CRS is only switched when it matters.** 2.0.1 switched any project whose CRS differed from the output, even an equivalent one (e.g. EPSG:26913 → 32613). The tool now measures how much the project CRS distorts the wall offset at the data and switches only above 2%, so geographic and Web Mercator projects are switched and equivalent projected CRSs are left alone. The log says which happened.
- **Geographic DEMs use the project CRS when it fits.** If the project is in a projected CRS equivalent to the local UTM zone, a geographic DEM is reprojected to it instead of to WGS 84 UTM.
- The native pixel size of a geographic DEM is now measured in the target CRS's units, so auto cell size is right for projected CRSs in feet.
- README: corrected the project-background and paper-texture descriptions, noted that the sidecar `.qml` is written for any file output, and added a tested console snippet for `apply_stack_style()`. Added image slots (hero, before/after, ramp variations) with placeholder images in `docs/images/`.

## 2.0.1 — 2026-09-27

- **Walls line up in any project.** The project CRS is set to the output's CRS on load, since the wall offset is measured in map units and was wrong (by orders of magnitude for a geographic project) when the two differed.
- The output layer keeps the name "Stacked terrain" (and "Paper texture") even when QGIS is set to name outputs after their file.
- Style-saving failures are now reported instead of being logged as success; the sidecar `.qml` path is logged.
- The generalized DEM gets an explicit nodata value, so a reprojected DEM without one no longer produces a fake 0 m slab in the corners.
- The lowest slab's `ELEV_MIN` is snapped down to the interval grid, so the first step up is a full interval like its wall.
- Auto exaggeration is rounded to 3 significant figures instead of 2 decimals, so it can no longer round to 0 for small, steep areas.
- Smoke test finds the output layer by source path and reports the layer and project CRS.

## 2.0.0 — 2026-09-27

- **True nested slabs.** Slabs are now built as cumulative "at or above" unions of the contour bands instead of bands with holes filled. The lowest slab covers the full extent, so the south edge becomes a stepped cross-section instead of a gap, and real depressions are no longer filled into bumps. Output fields are now `LEVEL` and `ELEV_MIN`.
- **Style travels with the data.** For `.gpkg` outputs the style (including the `stack_*` layer variables) is embedded as the default style, and a sidecar `.qml` is written for any file output.
- **AOI clip.** Optional polygon input clips the DEM before contouring.
- **Paper texture.** Optional image input is georeferenced over the output and styled (grayscale, contrast stretch, cubic resampling, Multiply, adjustable opacity) and placed above the terrain.
- **Finishing options.** Drop shadow on faces, saturation reduction, and setting the project background to the ramp's darkest color.
- Speck and small-hole removal (advanced parameter, default 4 cells).
- Less-used parameters moved to the Advanced section of the dialog.
- The values actually used are exposed as outputs for use in models.

## 1.1.0

- Default target levels raised from 15 to 25.
- Highlight uses a lighter tint of each slab's color instead of flat cream.
- Highlight offset reduced to 0.15 mm, exposed as a parameter and as the `stack_hl` layer variable.

## 1.0.0

- First version: auto UTM reprojection, average-resample generalization, contour bands with holes deleted, smoothing, and face / wall / highlight styling driven by layer variables. Tested on QGIS 4.2.
