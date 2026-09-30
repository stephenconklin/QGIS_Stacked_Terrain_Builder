# ArcGIS Pro toolbox — plan

A native ArcGIS Pro version of Stacked Terrain: a geoprocessing tool that takes a DEM and produces the same styled, offset stacked-slab map as the QGIS script, without QGIS in the loop. This file is the working plan. Update it as iterations land, and move the settled parts into `CLAUDE.md` and `README.md` when the tool ships.

Status: **planning** (2026-09-28). Nothing built yet.

---

## 1. Goal and scope

**In scope**

- A Python toolbox, `arcgis/StackedTerrain.pyt`, with one tool, *Stacked Terrain*. It's one text file, installed with *Add Toolbox*, just as the QGIS script is one file installed with *Add Script to Toolbox*.
- The same pipeline as `stacked_terrain.py` stages 1–8: CRS and extent, generalize, AOI clip, auto parameters, contour bands, cumulative slabs, paper texture, shaded faces.
- The same look as the QGIS script's *Export for ArcGIS Pro* (2.6.x), which is already checked in Pro: the lift and walls are baked into geometry, and a unique-value renderer on `DRAW_ORDER` draws them. The highlight and shadow are page-unit Move effects, and the shadow uses buffered copies. The styles, ramps, graded walls and saturation all match.
- Two contour engines, chosen by a parameter:
  - **Spatial Analyst / 3D Analyst**: Esri's `Contour` tool.
  - **No extension**: needs only an ArcGIS Pro Basic licence.
- The tool adds the styled group (paper, shading, slabs) to the active map when it finishes. It also writes a `.lyrx` next to the data so the result can be added to other maps or shared.

**Out of scope for now** (see §9)

- Live, editable look variables like QGIS's `@stack_exag`. In Pro, changing the look means re-running the tool.
- ArcMap. It's retired and has no CIM.
- Sharing a code module between the QGIS script and the Pro toolbox (see §3).

---

## 2. What we already know from the QGIS export (2.5.0–2.6.1)

These points were learned the hard way and hold for the Pro tool too:

| Finding | Consequence for the Pro tool |
|---|---|
| Pro symbols have no layer variables or map-unit geometry generators. | Bake the lift and wall copies into geometry, with one feature per wall copy, highlight and face. |
| CIM Move offsets are in points, with y up. | Reuse `_cim_move()`: mm → pt, flipping the sign of y. |
| Pro has no blur. `SHADOW_BUFFERS_MM = (0.25, 0.6, 0.95, 1.3)` at `SHADOW_STRENGTH = 0.55` matches the QGIS blur at 1:50,000. | Use the same constants. The 2.6.1 shadow hasn't been looked at in Pro yet, so check it in iteration 2. |
| Pro identifies layers in a map by `uRI`. Identical ones across exports left the second export blank. | Give each run a fresh uuid in every `uRI`. |
| A File Geodatabase written by GDAL has a nil catalog extent, so Pro leaves it blank until *Zoom To Layer*. | This doesn't apply when **arcpy** writes the feature class, so the Pro tool can use a file geodatabase. Check it in iteration 1. |
| *Apply Symbology From Layer* rejects a group `.lyrx` with error 000968. | Also write `<name>_slabs.lyrx` with the slab layer alone. |
| Rasters display correctly as a stretch colorizer, stretch type None, Multiply blend and transparency = 1 − opacity. | Reuse `_lyrx_raster()`. |
| Pro resolves relative paths in a `.lyrx` with `DATABASE=.` | Keep the `.lyrx` next to the data it references. |

---

## 3. Architecture

### 3.1 Files

| Path | Purpose |
|---|---|
| `arcgis/StackedTerrain.pyt` | The whole Pro tool: parameters, pipeline, color math, CIM, map handling. |
| `arcgis/StackedTerrain.pyt.xml` *(optional, later)* | Tool help and metadata that Pro shows in the Geoprocessing pane. |
| `dev/pro_probe.py` | Iteration 0: run in Pro's Python window. It reports the Pro version, licence, extensions, GDAL/numpy and API availability. |
| `dev/pro_smoke_test.py` | Runs the tool from Pro's Python window and prints the messages, the derived outputs and a summary of the layers it produced (the Pro counterpart of `dev/smoke_test.py`). |
| `dev/dump_qgis_colors.py` | Run in the QGIS console. It writes `dev/qgis_colors.json`: the reference colors that QGIS's expressions give for every ramp, style, wall step and saturation over a grid of `ELEV_MIN` values. |
| `dev/check_parity.py` | Plain `python3`, runs on the Mac. It (a) compares the shared constants in both files using `ast`, and (b) checks the Pro color port against `qgis_colors.json`. |

### 3.2 Why not share a module

A shared `stacked_core.py` would end the one-file install on both sides. The code the two tools could share (numpy hillshade pasting, CIM builders, constants) is small. The code that differs (geometry, I/O, CRS, parameters) is most of each file. **Decision: duplicate deliberately**, and make `dev/check_parity.py` catch drift in:

`STYLE_DEFAULTS`, `CUSTOM_RAMPS`, `RAMPS`, `SHADOW_OFFSETS`, `SHADOW_BUFFERS_MM`, `SHADOW_STRENGTH`, `MAX_WALL_STEPS`, `GRADED_WALL_STEPS`, `CRS_TOLERANCE`, `LYRX_VERSION`, the `_nice` step list and the auto formulas (checked by reading the functions and comparing them).

### 3.3 Pipeline in Pro

The stages match the QGIS script so that logs, bug reports and this plan line up.

| # | Stage | Both engines | Spatial Analyst engine | No-extension engine |
|---|---|---|---|---|
| 1 | CRS and extent | `arcpy.Describe`, `SpatialReference`. Geographic DEM → UTM zone at the centre. Use the active map's SR instead when it's projected and within `CRS_TOLERANCE` of that zone (same test as `_crs_mismatch`, using `PointGeometry.projectAs`). | | |
| 2 | Generalize | `arcpy.management.ProjectRaster` / `Resample` (AVERAGE) to a scratch GeoTIFF, with nodata −9999. Always through arcpy, so any raster Pro can read works as input: FGDB rasters, mosaic datasets, `.crf`, image services. | | |
| 3 | AOI clip | AOI dissolved to one polygon in the target SR (`arcpy.da.SearchCursor` with `spatial_reference=`), buffered 3 cells, then `arcpy.management.Clip` with `ClippingGeometry`. | | |
| 4 | Auto parameters | min/max from `RasterToNumPyArray` of the generalized DEM (not `GetRasterProperties`, which can be stale). `_nice`, `_round_sig` and the lift formula are copied verbatim. | | |
| 5 | Contour bands | | `arcpy.sa.Contour(..., contour_type="CONTOUR_SHELL_UP")` (or `arcpy.ddd.Contour`). It gives the "at or above" shells directly. | GDAL `ContourGenerateEx` with `POLYGONIZE=YES` (the same call QGIS's `gdal:contour_polygon` makes) if the probe finds GDAL. Otherwise numpy classification → `NumPyArrayToRaster` → `RasterToPolygon` → group by class. |
| 6 | Cumulative slabs | Top-down union, then `_clean` ported: remove small holes and specks, **Chaikin smoothing ported to pure Python** (so there's no licence need and it matches `QgsGeometry.smooth(n, 0.25)`), exact AOI clip, and the stop when a slab covers the whole AOI. Grid-snap `ELEV_MIN` exactly as QGIS does. The shells engine skips the union but still snaps and cleans. | | |
| 7 | Paper texture | `RasterToNumPyArray` on the image → grayscale luminance → `NumPyArrayToRaster` with the same padded bounds as `_paper_texture` (separate x and y cell sizes, so the image is stretched to fit). | | |
| 8 | Shaded faces | The `_lifted_hillshade` paste loop, ported with numpy. | `arcpy.sa.Hillshade` on the 3-cell-averaged DEM. | GDAL `DEMProcessing` if present, else a Horn-method hillshade in numpy (about 15 lines). Burn step: GDAL `RasterizeLayer`, or `PolygonToRaster` (Basic) without GDAL. |
| 9 | Baked display + map | `_arcgis_features` (with the color port, §4) → `arcpy.management.CreateFeatureclass` + `InsertCursor`. Write the group `.lyrx` and `_slabs.lyrx` (the proven CIM JSON), then add the group to the active map. | | |

**Engine parameter:** *Contour engine* = `Auto` (default) / `Spatial Analyst or 3D Analyst` / `No extension`. `Auto` uses Spatial Analyst if `arcpy.CheckExtension("Spatial") == "Available"`, then 3D Analyst, else no extension. The tool checks the extension out and back in around the stages that use it. The log names the engine it used.

**Why the licensed engine matters even though GDAL may exist:** Esri's Contour and Hillshade are supported, handle very large rasters, and give users with a full licence the tools they know. The no-extension engine is the one that matches QGIS most closely, because it's the same GDAL call. Iteration 3 compares the two on the same DEM. If they differ visibly (shells vs. bands can disagree at map edges and around nodata), the difference goes in the README.

### 3.4 Outputs and where they go

- **Output**: `Output feature class` (`DEFeatureClass`, direction Output). The baked display features go here, with fields `DRAW_ORDER`, `PART`, `ELEV_MIN`, as in the QGIS export.
- **Also written, next to it** (in the same file geodatabase, or the same folder for a shapefile output):
  - `<name>_slabs`: the clean nested slabs (`LEVEL`, `ELEV_MIN`), equivalent to the QGIS output. Useful for analysis, 3D and a future laser-cutting export.
  - `<name>_shade`, `<name>_paper`: rasters, when those options are on.
- **Layer files**: `<name>.lyrx` and `<name>_slabs.lyrx` go in the folder that holds the gdb, so `DATABASE=.` paths need care. **Decision for iteration 2:** either point the `.lyrx` at `<gdb name>.gdb` relative to the file, or keep the whole run in a `<name>/` folder as QGIS 2.3.0 does (`<name>/<name>.gdb`, `<name>/<name>.lyrx`, `<name>/<name>_shade.tif`, …). The folder is more portable and matches QGIS, so it's the leaning.
- **Derived outputs**: interval, exaggeration, cell size, base and top elevations (as in QGIS), and the `.lyrx` path.
- **Adding to the map**: two options, to try in iteration 2 in this order:
  1. `arcpy.mp.ArcGISProject("CURRENT").activeMap.addLayer(arcpy.mp.LayerFile(lyrx))`. This adds exactly the tested group. To avoid a duplicate unstyled layer, the output feature-class parameter must not be auto-added (use a derived `DEFile`/`DELayer` output, or remove the auto-added layer).
  2. `arcpy.SetParameterSymbology` on the output parameter, plus a CIM pass for blend modes. Use this if (1) turns out unreliable from inside `execute`.

### 3.5 Parameters (Pro dialog)

These mirror the QGIS dialog. *Advanced* is a parameter `category`, which collapses in the pane.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| DEM | `GPRasterLayer` | | |
| Clip to area of interest | `GPFeatureLayer`, polygon filter, optional | | |
| Contour interval (0 = auto) | `GPDouble` ≥ 0 | 0 | |
| Target number of levels | `GPLong` 3–100 | 25 | |
| Vertical exaggeration (0 = auto) | `GPDouble` ≥ 0 | 0 | |
| Style | `GPString` value list | Offset slabs | Offset slabs / Paper layers / Cut card |
| Color ramp | `GPString` value list | Magma | Same seven names as QGIS |
| Shade slab faces | `GPBoolean` | false | |
| Graded 3-D walls | `GPBoolean` | false | More features rather than slower rendering: 8 wall copies per slab |
| Saturation change, % | `GPDouble` −100–0 | 0 | Baked into the colors |
| Paper texture image | `DEFile` (png/jpg/tif), optional | | |
| Paper texture opacity | `GPDouble` 0–1 | 0.2 | |
| Contour engine | `GPString` value list | Auto | New for Pro |
| Output feature class | `DEFeatureClass` | | |
| *Advanced:* shading strength, highlight offset, auto-lift fraction, cell size, smoothing iterations, speck size | | as QGIS | |
| *Advanced:* set map background to the ramp's darkest color | `GPBoolean` | false | Via the map CIM `backgroundColor`. Low priority. |

`updateMessages` validation: warn when the DEM is geographic (reprojecting to UTM zone N), error when the AOI doesn't overlap the DEM, error when "Spatial Analyst" is chosen but unavailable, and warn when the active map's SR differs from the output's by more than `CRS_TOLERANCE` (the slabs will lean slightly; the walls are baked, so they stay attached).

---

## 4. Colors: the one real porting risk

Today the Pro export gets its colors by evaluating QGIS expressions (`COLOR_EXPR`, `HL_COLOR_EXPR`, `_wall_color_expr(k)`, `EDGE_COLOR_EXPR`, `SHADOW_COLOR_EXPR`) with QGIS's engine. The Pro tool needs a pure-Python copy that gives the **same RGBA values**:

1. **Ramp stops.** Magma, Inferno, Plasma, Viridis and Spectral come from QGIS's default style library. `dev/dump_qgis_colors.py` also dumps their exact stops (`QgsStyle.defaultStyle().colorRamp(name)`: color1, color2, stops) into the reference file, and they're pasted into the `.pyt` as a `BUILTIN_RAMPS` table. The custom ramps are already literal stops. The interpolation is linear in RGB, as `QgsGradientColorRamp` does by default. Confirm that against the dump.
2. **`scale_linear(ELEV_MIN, base, top, lo, 1)`**, with lo = 0.15 for built-in ramps and 0 for custom ones, and clamping as QGIS does.
3. **`lighter(c, f)` / `darker(c, f)`**: Qt's `QColor.lighter/darker`, which work in HSV and include Qt's quirk of moving saturation when V saturates. Port Qt's source exactly, not an approximation.
4. **`set_color_part(…, 'alpha', 190)`**, `color_rgba`, and the HSL saturation change in `_ExprColors`.
5. **Parity test.** `check_parity.py` compares every combination against `qgis_colors.json` and allows ±1 per channel for rounding. It runs on the Mac with plain `python3`, so this part can be checked fully without Pro.

---

## 5. Geometry details

- **Container:** `arcpy.Polygon` for everything that ends up in Pro, since it's always available. If the probe finds GDAL, it may do the contouring, and its results are converted with `arcpy.FromWKB`.
- **Union:** `Polygon.union`, top-down, one per level, as in QGIS. If it's slow on big DEMs, fall back to `PairwiseDissolve` on the band feature class, with the cumulative class computed as an attribute.
- **Speck/hole removal:** walk the parts and rings (arcpy separates interior rings with `None`) and drop any ring with area < `speck × cell²`.
- **Chaikin smoothing:** a pure-Python port of `QgsGeometry.smooth(iterations, 0.25, -1, 180)` for closed rings. Check it on the Mac inside QGIS by running the ported function and `QgsGeometry.smooth` on the same polygon and comparing vertices.
- **AOI clip:** `Polygon.intersect(aoi, 4)` after smoothing, then drop parts with zero area or below the speck size.
- **Lift/walls:** translate by rebuilding the arrays with y + dy. It's a one-line helper; arcpy has no `translate`.
- **Shells engine:** CONTOUR_SHELL_UP already gives the cumulative area, but its field names and how it treats the lowest shell must be checked in iteration 3 (the "to confirm" list in §7).

---

## 6. Iterations

Each iteration ends with something you can run in Pro, a short checklist of what to look at, and what to send back: the Geoprocessing messages, a screenshot at 1:50,000 next to the QGIS rendering of the same DEM, and anything odd. After each round, the plan's status and §7 are updated.

**Test data.** A projected DEM in EPSG:26913 (southwest Colorado); the Grand Canyon DEM in EPSG:26912 (already used for the Pro export tests, so there's a QGIS baseline); a geographic (EPSG:4326) DEM; an AOI polygon in EPSG:32613.

### Iteration 0: probe (tiny)

`dev/pro_probe.py` prints:

- the Pro version, Python version, licence level, and whether Spatial and 3D are available
- `import numpy`, and `from osgeo import gdal` with its version and whether `ContourGenerateEx` and `DEMProcessing` exist
- whether `arcpy.mp.ArcGISProject("CURRENT")` works from the Python window
- whether `arcpy.env.isCancelled` exists
- whether `Resample` accepts `AVERAGE`, and whether `Contour` accepts `CONTOUR_SHELL_UP` (read from `arcpy.Usage`)

Also, run it once under a Basic licence if you can switch (Settings → Licensing), or tell me if you can't.

*This decides:* which no-extension path to use (GDAL vs RasterToPolygon), the minimum Pro version, and how progress and cancellation work.

### Iteration 1: slabs, no extension, projected DEM

- The `.pyt` skeleton with all parameters (the unused ones are accepted and ignored for now).
- Stages 1 (projected only), 2, 4, 5 (no-extension engine), 6. Writes `<name>_slabs` to a file geodatabase with default symbology.
- Logs the auto interval, exaggeration and cell size, as the QGIS tool does.

*Check:* the tool opens and validates. On the 26913 DEM, the level count, `ELEV_MIN` values and slab outlines match a QGIS run with the same interval and cell size (overlay the two). The feature class draws without *Zoom To Layer*.

### Iteration 2: the look

- The color port and parity test (checked on the Mac before you see it).
- `_arcgis_features` ported to arcpy geometry → display feature class → `.lyrx` files → added to the map as a group.
- All three styles, graded walls and saturation.

*Check:* side by side with QGIS at 1:50,000 for each style. The group appears in the map without being dragged in. The layer files open in a second map. Two runs in one map both draw. The 2.6.1 shadow gets its first look in Pro.

### Iteration 3: Spatial Analyst engine

- Stage 5 with `Contour` CONTOUR_SHELL_UP, the engine parameter, and extension checkout.
- Also `arcpy.sa.Hillshade` ready for iteration 5.

*Check:* both engines on the same DEM. Slab counts, extents and look. Timing on a large DEM. The error message when the engine isn't available.

### Iteration 4: CRS and AOI

- Geographic DEM → UTM, or the active map's SR when equivalent. AOI in another SR. The early stop when a slab covers the whole AOI.

*Check:* release checklist items 2 and 3 (from `CLAUDE.md`), Pro edition. A 4326 DEM with a 26913 map gives output in 26913. With a 3857 map it gives UTM, and a warning notes that the map's SR differs. The AOI edge is exact.

### Iteration 5: paper texture and shading

- Stages 7 and 8 for both engines. Raster layers in the group with Multiply and opacity.

*Check:* the shading follows the lift (faces shaded, walls not). The paper covers the lifted extent. Both are Multiply, with stretch type None.

### Iteration 6: polish and release

- Validation messages, progress text, cancellation, and clearing out scratch data.
- Tool metadata and help. The README gets an *ArcGIS Pro toolbox* section: install, parameters, engines, differences from QGIS. The CHANGELOG gets an entry, `CLAUDE.md` gets the Pro architecture and contracts, and `check_parity.py` joins the release checklist.
- Optional: a map background option.

*Check:* the full Pro checklist (to be written in iteration 6, modelled on the QGIS one), on a Basic licence if possible.

---

## 7. To confirm (answered by iterations)

| Question | Iteration | Answer |
|---|---|---|
| Does Pro's Python have GDAL, and which version (`ContourGenerateEx` needs ≥ 3.1)? | 0 | |
| Licence level needed by Resample (AVERAGE), ProjectRaster, Clip, RasterToPolygon, PolygonToRaster, NumPyArrayToRaster | 0–1 | Believed to be Basic; confirm |
| Can `execute` add a `.lyrx` group to the active map? Does the auto-added output then duplicate it? | 2 | |
| Does a file geodatabase written by arcpy draw straight away (no nil-extent problem)? | 1 | |
| `CONTOUR_SHELL_UP` field names, and the lowest shell's value | 3 | |
| Does `SetParameterSymbology` accept CIM JSON on the minimum Pro version? | 2 (only if needed) | |
| How is cancellation detected (`arcpy.env.isCancelled`)? | 0 | |
| Is the 2.6.1 shadow as soft in Pro as in QGIS? | 2 | |

---

## 8. Decisions for you

1. **Minimum Pro version.** Recommended: **Pro 3.0** (Python 3.9, current arcpy.mp and CIM APIs). The existing `.lyrx` targets 2.6.0, which can stay.
2. **How files get to the Windows machine.** A git push and pull, a synced folder (Dropbox, OneDrive, iCloud for Windows), or copying by hand. `dev/pro_smoke_test.py` will reload the toolbox from wherever it lands.
3. **Output layout.** Everything in `<name>/` (a `.gdb` inside it, plus the `.lyrx` files and rasters; this is the leaning, see §3.4), or outputs where the user points and the `.lyrx` beside the gdb.
4. **Versioning.** The Pro toolbox gets its own `VERSION` (0.x while iterating, 1.0.0 at parity) and its own entries in the shared `CHANGELOG.md`, marked *Pro toolbox*.

---

## 9. Later

- **Live look in Pro (experiment).** Drive a Move effect's offset from `ELEV_MIN` with an Arcade expression (symbol property connections), and set a map reference scale so point offsets behave like map units. If it works, exaggeration could be edited without re-running. It's unproven, so it's an experiment, not a feature.
- **Laser-cut export.** The `<name>_slabs` feature class is already the right data. Export each level to its own SVG or DXF.
- **Pro layout generator.** The counterpart of the QGIS print-layout idea, using `arcpy.mp` layouts.
- **Shorter legend.** Label only the faces, as an elevation key. The same idea is pending for QGIS's Pro export.
