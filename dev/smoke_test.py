"""
Smoke test for Stacked Terrain. Run inside QGIS:

    Python Console > Show Editor > open this file > Run
    or:  exec(open('/path/to/QGIS_Stacked_Terrain_Builder/dev/smoke_test.py').read())

Requires the script to be installed in the Processing Toolbox
(algorithm id: script:stackedterrain).
"""
import os
import tempfile

import processing
from qgis.core import QgsExpressionContextUtils, QgsProject

# ---- edit these -------------------------------------------------------------
DEM = "/path/to/your_dem.tif"
AOI = None            # e.g. "/path/to/aoi.gpkg" or a loaded layer id
PAPER = None          # e.g. "/path/to/paper_ao.png"
ARCGIS = False        # also write the ArcGIS Pro copy
STYLE = 1             # 0 Offset slabs, 1 Paper layers (default), 2 Cut card
VIEW = 0              # 0 Oblique, 1 Nadir
VIEW_AZ = 180         # oblique: azimuth viewed from
VIEW_ALT = 0          # oblique: view elevation angle (0 = auto)
LIGHT_AZ = 315        # light from
SHADE = False         # shaded faces
# -----------------------------------------------------------------------------

out = os.path.join(tempfile.mkdtemp(prefix="stacked_"), "stacked_test.gpkg")
params = {"DEM": DEM, "OUTPUT": out, "STYLE": STYLE, "VIEW": VIEW, "VIEW_AZ": VIEW_AZ,
          "VIEW_ALT": VIEW_ALT, "LIGHT_AZ": LIGHT_AZ, "SHADE": SHADE}
if AOI:
    params["AOI"] = AOI
if PAPER:
    params["PAPER"] = PAPER
if ARCGIS:
    params["ARCGIS"] = True

res = processing.runAndLoadResults("script:stackedterrain", params)

print("Results:")
for k, v in res.items():
    print(f"  {k}: {v}")

# The tool moves a file output into a folder named after it
# (.../stacked_test.gpkg -> .../stacked_test/stacked_test.gpkg); use where it went.
out = res["OUTPUT"].split("|")[0]

# Match on the source path, not the name, so a renamed layer is still found.
found = [lyr for lyr in QgsProject.instance().mapLayers().values()
         if lyr.source().split("|")[0] == out]
if not found:
    print("ERROR: output layer was not loaded into the project")
for lyr in found:
    print("Layer:", lyr.name(), "| CRS:", lyr.crs().authid(),
          "| project CRS:", QgsProject.instance().crs().authid())
    scope = QgsExpressionContextUtils.layerScope(lyr)
    print("Layer variables:",
          {n: scope.variable(n) for n in scope.variableNames() if n.startswith("stack_")})
    print("Features:", lyr.featureCount(), "| fields:", [f.name() for f in lyr.fields()])

print("Sidecar QML exists:", os.path.exists(os.path.splitext(out)[0] + ".qml"))
print("Output folder:", os.path.dirname(out))
arcgis_dir = os.path.join(os.path.dirname(out), "ArcGIS")
if ARCGIS:
    print("ArcGIS Pro files:", sorted(os.listdir(arcgis_dir)) if os.path.isdir(arcgis_dir)
          else "MISSING (see the log for the export error)")
