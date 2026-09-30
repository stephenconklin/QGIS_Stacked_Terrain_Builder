# CLAUDE.md — developer notes for QGIS Stacked Terrain Builder

This file orients Claude Code (and any developer) to the project. Read it before changing `stacked_terrain.py`.

## What this is

The project and its GitHub repository are called QGIS Stacked Terrain Builder (`QGIS_Stacked_Terrain_Builder`; renamed from `QGIS_Offset_Stacked_Terrain_Slabs` on 2026-09-30). The tool's name in QGIS, "Stacked terrain (offset elevation slabs)", and its id `script:stackedterrain` are deliberately unchanged: the id is what saved models refer to.

A single-file QGIS Processing script that converts a DEM into nested elevation slabs and styles them as offset "stacked paper" terrain, after John Nelson's ArcGIS Pro technique. User-facing documentation is in `README.md`; version history is in `CHANGELOG.md`.

## Files

| Path | Purpose |
|---|---|
| `stacked_terrain.py` | The whole tool: algorithm, symbology, post-processors, compatibility helpers. |
| `dev/smoke_test.py` | Runs the tool end to end from the QGIS Python console and prints what it produced. |
| `dev/ARCGIS_PRO_PLAN.md` | Plan and status for a native ArcGIS Pro toolbox (`arcgis/StackedTerrain.pyt`), built in iterations tested in Pro on Windows. Read it before working on the Pro tool. |
| `README.md` | User docs: install, parameters, variables, how it works. |
| `docs/images/` | README screenshots, rendered offscreen in QGIS from the Grand Canyon (USGS 3DEP) test DEM: hero 1600×800; before/after 800×800 and the Gallery's `style_*` and `ramp_*` images 600×600, all of the same 16 km square around Bright Angel Canyon (EPSG:32612, 395000–411000 E, 3994000–4010000 N) from runs on the full DEM with auto settings, shading and paper (the ramp images are one Paper layers run with only `stack_ramp` changed; `ramp_spectral.png` doubles as the Paper layers style image). Hillshade the before image from the DEM warped to UTM first: shading the geographic DEM on the fly stripes. Keep the file names and sizes when replacing them. |
| `CHANGELOG.md` | Version history. Bump `VERSION` in the script and add an entry for every change. |
| `QGIS_Stacked_Terrain_Builder.code-workspace` | VS Code workspace. Its `settings` are empty; add `python.analysis.extraPaths` pointing at the QGIS Python bindings if you want Pylance autocompletion. |

## Environment

The primary user runs QGIS 4.2 "Belém do Pará" on macOS with Qt 6.11, Python 3.12 and GDAL 3.13, and works in UTM 13N (EPSG:26913) around southwest Colorado. The script must also keep working on recent QGIS 3.x (Qt5), so compatibility is a hard requirement, not a nice-to-have.

The code only runs inside QGIS. There is no way to execute it with a plain system Python, so "it compiles" (`python3 -m py_compile stacked_terrain.py`) is the only check available outside QGIS. Real testing means running it in QGIS.

## Architecture

`processAlgorithm` runs on a worker thread and does the data work in nine numbered stages (CRS and extent, generalize, AOI clip, auto parameters, contour bands, cumulative slabs, paper texture, shaded faces, ArcGIS Pro export). It must not touch the project, the layer tree or any GUI object.

Anything that styles or rearranges layers happens in the `QgsProcessingLayerPostProcessorInterface` subclasses, `TerrainStyler` and the two `_RasterStyler`s (`ShadeStyler`, `PaperStyler`, which share resampling, Multiply blend, opacity and style saving), which QGIS calls on the main thread after loading the outputs. Instances are appended to the module-level `_KEEP_ALIVE` list; without that, Python garbage-collects them before QGIS calls them and the styling silently never happens.

`apply_stack_style()` builds the renderer and is deliberately independent of the algorithm, so it can be called on any layer with an `ELEV_MIN` field (useful for restyling from the console).

## Contracts — do not break without updating everything that depends on them

**`ELEV_MIN` field.** Every expression in the symbology and the feature draw order reads it. It means the slab's floor elevation, and each feature covers all ground at or above it. Renaming it breaks styling and any saved styles.

**Layer variables.** `stack_base`, `stack_top`, `stack_interval`, `stack_exag`, `stack_ramp`, `stack_hl`, `stack_style`, `stack_wall`, `stack_edge`, `stack_shadow`, `stack_wall_steps`, `stack_view_az` and `stack_light_az` are set on the output layer and referenced by name in expressions (`@stack_exag` and so on). Users edit them in Layer Properties, and they are persisted in the embedded style and sidecar `.qml` as custom properties. Renaming one breaks existing layers in users' projects. Variables added later (`stack_view_az`, `stack_light_az` in 3.0) must be read through `coalesce()` with a default that reproduces the earlier look, because older layers don't have them.

**Project CRS.** The slab lift (geometry generator) is in layer units but the wall offset is in map units, so `TerrainStyler` switches the project CRS (oblique runs only; a nadir run, `stack_exag` = 0, has neither lift nor walls) to the layer's CRS when `_crs_mismatch()` (scale + rotation error of a 1 km northward step) exceeds `CRS_TOLERANCE` (2%). Equivalent CRSs such as EPSG:26913 vs 32613 are left alone. Stage 1 uses the same test to output geographic DEMs in the project CRS when it is equivalent to the local UTM zone. Don't remove the switch without moving the wall into its own geometry generator.

**Symbol layer order.** Inside the geometry generator's fill the list is `[highlight, wall × MAX_WALL_STEPS (deepest first), shadow, face]`; index 0 draws first (bottom). Wall copy k of N (N = `stack_wall_steps`) is offset k/N of a slab step; copies with k > N are switched off with a data-defined LayerEnabled. The wall offset is `stack_interval × stack_exag` in map units toward the viewer, and all walls are off when `stack_exag` is 0. The highlight and shadow offsets are in millimetres, toward and away from the light. The shadow is a blurred fill rather than a paint effect on the face, because paint-effect settings can't be data-defined and `stack_shadow` must stay live.

**Elevation ramps.** Ramps in `ELEVATION_RAMPS` (currently *Grand Canyon* and *Grand Canyon Light*) have stops in metres applied to `ELEV_MIN` directly, ignoring `stack_base`/`stack_top`. Layers styled before a ramp existed can't switch to it by editing `stack_ramp`: their saved color expression only has `CASE` branches for the ramps of their version, so they need restyling with `apply_stack_style()` or a rerun.

**Views and light.** Directions are compass azimuths. The lift moves slabs along `_lift_vector(stack_view_az)` = (−sin v, −cos v), away from the viewer; walls go the other way. Page offsets (x right, y down) toward an azimuth come from `_page_offset()` in Python and `_offset_expr()` in expressions; keep the two in step, since the ArcGIS export uses the Python side. `SHADOW_OFFSETS` holds each style's shadow as (distance mm, turn from straight away from the light); the highlight distance is `stack_hl` × √2. With the defaults (view 180, light 315) this reproduces the 2.x offsets exactly: a 3.0 restyle of a 2.9 layer was pixel-identical. Nadir is `stack_exag` = 0 with `NADIR_SHADOW` / `NADIR_HL` minimums. Lift from a view angle: `exag = VEXAG / tan(VIEW_ALT)`; the `EXAG` parameter id now means "lift per unit of elevation" and overrides it.

**Styles.** `STYLE_DEFAULTS` holds each style's starting variable values and auto lift fraction. `stack_style` itself only switches the edge-line color and the shadow offset in the expressions; the numeric look lives in the other variables.

**Shaded faces.** `_lifted_hillshade()` pastes the hillshade (lit from the light azimuth) under each slab at that slab's lift (`(ELEV_MIN − base) × exag` along the view direction), bottom-up, so the raster is only valid for the exaggeration, view and light of the run. Layer order is set in one place: `_finish()` groups the outputs as paper, shading, slabs once every expected one is loaded (the stylers record their layer ids in the shared `run` dict); there are no individual layer moves any more (removed in 3.1). Any Layers-panel move must insert the cloned node before removing the original: removing a layer's last tree node also removes the layer from the project.

**Simplification.** `_clean()` simplifies each slab after smoothing, at `SIMPLIFY_CELLS` (0.1) of the cell, before the AOI clip so that edge stays exact. Without it Chaikin smoothing leaves millions of nearly collinear vertices and rendering is several times slower. Chosen by comparing renders: 0.25 looked angular at 1:15,000 on a 75 m cell, and simplifying before smoothing altered shapes more.

**Nesting.** Slabs must be cumulative ("at or above"), not bands. Bands leave gaps at map edges and around peaks once offset. The cumulative union is built top-down in stage 6.

## Compatibility rules

QGIS 4 / Qt6 only accepts scoped enums (`Qgis.RenderUnit.MapUnits`, `Qt.PenStyle.NoPen`, `QPainter.CompositionMode.CompositionMode_Multiply`). Qt5 builds of QGIS 3 may only have the flat names (`QgsUnitTypes.RenderMapUnits`, `Qt.NoPen`). Never write a bare enum; always go through `_try()` with the new form first and the old form as fallback, following the existing helpers. The user already hit this once: `QPainter.CompositionMode_Multiply` raised AttributeError on QGIS 4.2.

Field types follow the same rule: `_field()` tries `QMetaType.Type` (QGIS ≥ 3.38) before `QVariant`.

On QGIS 4.2 a valid CRS read from a layer (seen with a GeoPackage layer, EPSG:26912) can return an empty string from every `toWkt()` variant while `authid()` and `toProj()` still work. Always hand GDAL a CRS through `_crs_wkt()`, never `crs.toWkt()` directly.

`QgsProcessingParameterNumber.Double` / `.Integer` and `QgsProcessing.TypeVectorPolygon` are confirmed to still work on QGIS 4.2 (v1.1 ran with them).

## Dialog

Parameters are ordered by how often they are changed: inputs, Style and Color ramp, the view and light, then the optional extras; Contour interval and Target levels are Advanced. Parameters are matched by id, never position, so reordering doesn't break models or scripts. Enum *labels* can change freely (Style carries descriptions, fixed-elevation ramps are marked), because the values are indices into `STYLES` / `RAMPS` / `VIEWS`.

Greying out settings that don't apply: Processing has no API for it, so the View parameter's metadata names `_LinkedWidgets` as its widget wrapper "class". Its `__new__` returns the native wrapper the dialog would have made anyway and, in the single-run dialog (`AlgorithmDialog` in QGIS 3, `AlgorithmWidget` in 4), schedules `_link_widgets()` on the dialog's parameters panel, which applies `_enabled_rules()` whenever any value changes. Batch and modeler dialogs get no linking. Everything is wrapped so a GUI change in a future QGIS only loses the greying. The algorithm doesn't rely on it: `processAlgorithm` warns (`_warn`) about each ignored setting, for models and scripts. Add a rule to `_enabled_rules()` whenever a new parameter only applies in some cases.

## Testing

When testing dialogs from the console, create them with `processing.createAlgorithmDialog()` or pass `alg.create()`: a dialog takes ownership of the algorithm it is given, so handing it the registry's instance and closing it deletes that instance and QGIS segfaults on the next `processing.run` (happened 2026-09-30).

**Always refresh the tool in QGIS after every change to `stacked_terrain.py`**, without being asked: copy the repo file over the one in the QGIS scripts folder (below) and call `refreshAlgorithms()` on the Scripts provider, through the QGIS MCP server's `execute_code` when it is connected. Otherwise the user keeps running the old copy. The tool's id is `script:stackedterrain`; its help text starts with the version, which shows the refresh took.

After editing, re-import the script. QGIS runs its own copy from `~/Library/Application Support/QGIS/QGIS4/profiles/default/processing/scripts/stacked_terrain.py`, not the repo file, so either copy the file there and refresh the Scripts provider (`QgsApplication.processingRegistry().providerById("script").refreshAlgorithms()`) or use Processing Toolbox → right-click the tool → Edit Script…, paste, save; then run `dev/smoke_test.py` from the QGIS Python console after setting `DEM` at the top. It runs the tool with `processing.runAndLoadResults`, which triggers the post-processors exactly as the dialog does, and prints the reported values and the layer's variables.

Manual checklist for a release:

1. Auto parameters on a projected DEM: layer loads styled, log shows interval/exaggeration/cell size.
2. A geographic (EPSG:4326) DEM: with a project in EPSG:26913 the output is in 26913 and the project CRS is left alone; with a project in EPSG:3857 the log reports the UTM zone chosen and the project switch.
3. AOI clip with a polygon in a different CRS than the DEM.
4. Output to `.gpkg` (with shading and paper): the files land in `<name>/`. Copy that folder elsewhere and drag its `.qlr` into a new, empty project: the group should load paper, shading and slabs in order, styled, with the `stack_*` variables present.
5. Paper texture: layer appears above the terrain, grayscale, Multiply blend.
6. Drop shadow and saturation options visibly change rendering.
7. Editing `stack_exag` in Layer Properties moves slabs and walls together.
8. *Also export for ArcGIS Pro* (with shading and paper): `<name>/ArcGIS/` holds the `.shp` set, both rasters and two `.lyrx` files. In Pro, drag `<name>.lyrx` into a new, empty map: the slabs draw straight away (no Zoom To Layer), in order, with soft shadows toward the lower right; paper and shading are Multiply. Add a second export to the same map and check that both draw.

## Status

Version 1.1 (bands + delete-holes, inline styling) ran successfully on QGIS 4.2. Version 2.0.1 ran on QGIS 4.2.2 (2026-09-27) through a harness that mimics load-on-completion in a throwaway project: projected and geographic DEMs, paper texture, embedded GeoPackage style (reopens styled with the `stack_*` variables), project CRS switch, layer naming and grid-snapped `ELEV_MIN` all checked. The same day it also ran from the dialog with a geographic DEM, an AOI shapefile in a different CRS (EPSG:32613), paper texture, drop shadow and the Spectral ramp, with a temporary output. Version 2.0.2's CRS handling was then checked through the harness in four combinations (geographic DEM with a 26913 or 3857 project, projected 26913 DEM with a 32613 or 4326 project): equivalent CRSs were left alone and 3857/4326 were switched. Version 2.0.3's vector AOI clip was run from the console (geographic DEM, AOI in EPSG:32613, output not loaded): the lowest slab matched the AOI polygon exactly (Hausdorff distance 0). Version 2.0.4's layer naming was checked the same way (file output named after the file, temporary output "Stacked terrain"); the stop-at-full-AOI-slab check has not yet been hit by real data. Not yet exercised: QGIS 3.x. The parts most likely to need a fix on first run are the ones that touch newer or less common APIs: `saveStyleToDatabaseV2` in `embed_style()` (its return value is read as `(results, errorMessage)`), the enum fallbacks in `PaperStyler` (grayscale mode, contrast enhancement, raster range limits), `QgsProcessingContext.LayerDetails.layerSortKey`, and whether embedded GeoPackage styles carry the custom properties that hold the layer variables. Each of those is wrapped so a failure is reported in the log without stopping the run.

Version 2.1.0 (styles, shaded faces, custom ramps) was run on QGIS 4.2.2 the same day: all three styles rendered offscreen from a console run on a geographic DEM with an AOI, and a `runAndLoadResults` load with Cut card, Teal-Orange and shading placed the shading directly above the terrain (Multiply, opacity 0.45), left the project CRS and background alone, and a fresh load of the GeoPackage came back with all ten `stack_*` variables. Version 2.2.0 (graded walls) was checked by evaluating every data-defined expression in the renderer against a real feature (the first draft used `greatest()`, which QGIS doesn't have: failing expressions silently fall back to defaults, so parse-check new expressions) and by offscreen renders. Version 2.3.0's folder and bundle were checked with `runAndLoadResults` (Cut card, graded walls, shading, paper): the folder held the gpkg, rasters, three `.qml` files and the `.qlr`; the folder was copied elsewhere and its `.qlr` loaded into a fresh project with all three layers valid, in order, with blend modes, opacities and variables intact, and the shading `.tif` added alone picked up its `.qml`. `create_ramp()` needs QGIS ≥ 3.18; the blur's millimetre unit falls back to pixels on older builds.

**Output folder and bundle.** `_in_own_folder()` rewrites the OUTPUT sink path to `<dir>/<stem>/<stem>.<ext>` before `parameterAsSink`, so every derived file (`_output_path`) lands in the same folder. `_finish()` runs from a doubly deferred timer once every expected output (the shared `run["expect"]`) has been styled: it groups the outputs in the Layers panel (`_group_in_tree`) and writes the `.qlr` (`_write_bundle`); it must not use the algorithm's `feedback`, which is gone by then, so it logs to the message log.

**ArcGIS Pro export.** `export_arcgis()` runs in stage 9 (worker thread, file outputs only) and writes `<folder>/ArcGIS/`: `<name>.shp`, copies of the rasters (the paper as one grayscale band), `<name>.lyrx` (the group) and `<name>_slabs.lyrx` (the slab layer alone, for Pro's Apply Symbology From Layer, which rejects a group with error 000968). Design points and why:

- *Baked, not live.* Pro symbols have no layer variables or map-unit geometry generators, so the lift and every wall copy are real geometry, one feature per wall copy, highlight and face, numbered bottom to top in `DRAW_ORDER`. The `.lyrx` renderer is unique values on `DRAW_ORDER` with one class per feature. Colors come from evaluating the same expressions the QGIS renderer uses (`COLOR_EXPR`, `HL_COLOR_EXPR`, `_wall_color_expr()`, `EDGE_COLOR_EXPR`, `SHADOW_COLOR_EXPR`, shared with `apply_stack_style()`), so a change to the QGIS look must be mirrored in `_arcgis_features()`.
- *CIM details.* The `.lyrx` is hand-written CIM JSON, document version 2.6.0. Symbol layers are listed top first; Move offsets are in points with y up (confirmed in Pro). Every layer `uRI` carries a fresh uuid: Pro identifies a map's layers by `uRI`, and identical ones across exports left the second export's slabs blank.
- *Shadow.* Pro has no blur, so the shadow is `SHADOW_BUFFERS_MM` copies grown and moved, together at `SHADOW_STRENGTH` of the shadow's opacity. Tuned by drawing the `.lyrx` symbols in QGIS (buffer + translate geometry generators in map units at 1:50,000) next to the live layer; an unbuffered copy gave a hard rim.
- *Shapefile.* Not a GeoPackage, because Pro doesn't keep relative paths to SQLite sources. Not a File Geodatabase (2.5.x): GDAL 3.13 writes the feature class's catalog extent as nil (`<Extent xsi:nil="true">` in GDB_Items), with no option to fill it, and Pro then leaves the layer blank until Zoom To Layer makes it compute one (it writes a `.horizon` file into the `.gdb`). The `dataset` value includes `.shp`, as in arcpy's connectionProperties; `DATABASE=.` is resolved relative to the `.lyrx`.

Checked in ArcGIS Pro on Windows (2026-09-28, the user's own tests with a Grand Canyon DEM in EPSG:26912): the group opens with relative paths resolved, slabs draw in order on the first add (2.6.0), shading and paper load as Multiply with stretch type None, the slab layer opens collapsed, and at 1:50,000 the geometry, colors and edge lines match QGIS. The softer 2.6.1 shadow was tuned in QGIS and not yet looked at in Pro. Pro version not recorded; older Pro 2.x not tried.

Version 3.0.0 (views and light) was checked on QGIS 4.2.2 on 2026-09-30. Every data-defined expression was evaluated on a real feature for all three styles, a 2.9 layer restyled with 3.0 defaults was pixel-identical, and three console runs (nadir, oblique auto, oblique from 225° at 60° with 2× exaggeration) with shading, paper and the ArcGIS export all completed. A `runAndLoadResults` nadir run loaded all three layers with the 13 variables and left a 4326 project CRS alone. Nadir defaults were picked from renders at 1:20,000. The ArcGIS export of a non-default view has not been opened in Pro yet.

Version 3.1.0 (dialog and fixes) was checked on QGIS 4.2.2 on 2026-09-30: the greying rules were driven through a real dialog (nadir, angle set, lift set, interval, shading, paper) and the batch dialog still opened; the ignored-setting and feet warnings fired on console runs (feet tested with the DEM × 3.28084); a `runAndLoadResults` run with shading, paper and the ArcGIS export grouped paper, shading, slabs in order without the old layer moves and wrote the `.qlr` and the ArcGIS folder. The Windows drive-letter fix was checked with `ntpath` only; it hasn't run on Windows.

Release checklist items 2, 4 and 7 were rerun on 3.3.0 (2026-09-30) in throwaway `QgsProject`s that mimic load-on-completion (pass OUTPUT as `QgsProcessingOutputLayerDefinition(path, project)`, then load each `layersToLoadOnCompletion()` entry and call its post-processor): a geographic DEM with a 3857 project switched to 32612 with the log message, with a 26912 project used 26912 and left it alone, and nadir left a 3857 project alone; a bundle folder copied elsewhere loaded from its `.qlr` with all three layers valid, in order, Multiply, opacities and 13 variables; editing `stack_exag`, `stack_view_az` and `stack_light_az` on that layer moved the lift, walls and shadow as expected, and `stack_exag` 0 switched the walls off.

Waiting for a Pro round (as of 2026-09-30, 3.3.0): `ProTest_Oblique225_GCLight` (oblique from 225°, Grand Canyon Light, shading, paper) and `ProTest_Nadir_Shaded` (nadir, shading) were written to the user's GEOG-400 Lab-6 `data/StackedTerrain/` folder; each has an `ArcGIS/` folder to open in Pro. Check wall direction, highlights toward the light and shadow softness (2.6.1).

## Ideas not yet built

A print-layout generator (page, cropped map frame, paper texture as a picture item sized to the page); a shorter legend for the Pro slab layer (label only the faces, as an elevation key); an option to export the individual slabs for laser cutting; and packaging the script as a proper QGIS plugin so it can be versioned and installed from a zip.

## Conventions

Keep the tool a single file so users can install it with Add Script to Toolbox. Keep user-visible strings wrapped in `self.tr()`. Put rarely changed parameters behind `_advanced()`. Log every auto-chosen value with `feedback.pushInfo` so users can see and reuse them. Update `VERSION`, `CHANGELOG.md` and the relevant sections of `README.md` with every change.
