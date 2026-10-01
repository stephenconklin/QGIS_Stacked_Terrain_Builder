"""
Stacked Terrain — John Nelson-style offset elevation slabs for QGIS Processing.

Pipeline
--------
DEM
  -> reproject to UTM if geographic, and generalize (average resample)
  -> optional clip to an AOI polygon, padded a few cells
  -> gdal_contour polygon bands
  -> fix geometries
  -> cumulative union: slab_k = everything at or above level_k   (true nesting)
  -> drop specks / tiny holes, smooth, clip exactly to the AOI
  -> styled layer (highlight / wall / shadow / face via a geometry generator)
  -> style embedded in the GeoPackage + sidecar .qml
  -> optional paper texture raster, georeferenced and styled
  -> optional shaded faces: a hillshade pasted slab by slab at each lift
  -> file outputs go in their own folder, with .qml styles for every layer
     and a <name>.qlr that adds the whole styled stack to any project
  -> optional ArcGIS Pro copy in <folder>/ArcGIS: lift and walls baked into
     geometry, styled by a .lyrx

Contracts other code relies on (see CLAUDE.md before changing):
  * Output field ELEV_MIN = floor elevation of the slab (area at/above it).
  * Layer variables @stack_base, @stack_top, @stack_interval, @stack_exag,
    @stack_ramp, @stack_hl, @stack_style, @stack_wall, @stack_edge,
    @stack_shadow, @stack_wall_steps, @stack_view_az and @stack_light_az
    drive every expression in the symbology.
  * Sub-symbol layer order, bottom to top: highlight, walls (deepest first),
    shadow, face.
"""

import math
import os
import re

from qgis.PyQt.QtCore import QCoreApplication, QPointF, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QPainter
from qgis.core import (
    Qgis,
    QgsBilinearRasterResampler,
    QgsBlurEffect,
    QgsColorEffect,
    QgsContrastEnhancement,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCubicRasterResampler,
    QgsEffectStack,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextScope,
    QgsExpressionContextUtils,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsFillSymbol,
    QgsGeometry,
    QgsGeometryGeneratorSymbolLayer,
    QgsHueSaturationFilter,
    QgsLayerDefinition,
    QgsLayerTreeGroup,
    QgsMessageLog,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingOutputFile,
    QgsProcessingOutputLayerDefinition,
    QgsProcessingOutputNumber,
    QgsProcessingOutputRasterLayer,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingUtils,
    QgsProject,
    QgsProperty,
    QgsRasterLayer,
    QgsRasterMinMaxOrigin,
    QgsRectangle,
    QgsSimpleFillSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsSymbolLayer,
    QgsSymbolLayerUtils,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)
import processing

VERSION = "3.3.1"

# Custom ramps as (position, color) stops, drawn with create_ramp() so they
# don't depend on the user's style library. Modelled on John Nelson's maps.
CUSTOM_RAMPS = {
    "Teal-Orange": [(0, "#1f6f6b"), (0.3, "#2f8f55"), (0.55, "#8fbf3a"),
                    (0.78, "#e2d64c"), (1, "#e2622a")],
    "Lime-Orange": [(0, "#b9cf45"), (0.3, "#e9d24a"), (0.55, "#f0a53a"),
                    (0.8, "#e26f25"), (1, "#c9461c")],
}

# Ramps tied to real elevations: (metres, color) stops, applied to ELEV_MIN
# directly rather than stretched over stack_base-stack_top, and held at the end
# colors outside their range. They assume a DEM in metres.
ELEVATION_RAMPS = {
    # Grand Canyon life zones, river to North Rim, each stop at the zone's
    # lower limit: red rock at the river (Phantom Ranch, 730 m) and in the
    # lower cliffs, desert scrub from 1220 m, blackbrush and sage from 1520 m,
    # pinyon-juniper from 1830 m, ponderosa pine from 2130 m and mixed conifer
    # from 2500 m up to the North Rim's high point (about 2800 m).
    "Grand Canyon": [(730, "#6e2a1c"), (1000, "#a4462b"), (1220, "#c8844a"),
                     (1520, "#c9b27c"), (1830, "#8a8f55"), (2130, "#4f7040"),
                     (2500, "#2c4f3c")],
    # The same zones and stops in Spectral's lighter, brighter tones: coral-
    # brick rock, orange and gold scrub, then warm yellow-greens ending in a
    # pine green for the conifers (no blue-green, which reads as water).
    "Grand Canyon Light": [(730, "#c8553d"), (1000, "#e8744a"), (1220, "#f7ad5f"),
                           (1520, "#f5dc8c"), (1830, "#dde6a0"), (2130, "#a0c46a"),
                           (2500, "#5e8f4e")],
}
RAMPS = (["Magma", "Inferno", "Plasma", "Viridis", "Spectral"]
         + list(CUSTOM_RAMPS) + list(ELEVATION_RAMPS))
# What the dialog starts with (by name, so reordering the lists is safe).
DEFAULT_STYLE, DEFAULT_RAMP = "paper", "Spectral"

# Rendering styles. The stack_* entries become layer variables (editable
# afterwards); lift is the auto-exaggeration target when LIFT is left empty.
STYLES = [("slabs", "Offset slabs (tall dark walls)"),
          ("paper", "Paper layers (thin sheets, pale edges, soft shadows)"),
          ("card", "Cut card (dark edges, deep shadows)")]
VIEWS = [("oblique", "Oblique (slabs lifted, walls showing)"),
         ("nadir", "Nadir (straight down: slabs in place, depth from shadows)")]
STYLE_DEFAULTS = {
    "slabs": {"stack_wall": 250, "stack_edge": 0, "stack_shadow": 0,
              "stack_hl": 0.15, "stack_wall_steps": 1, "lift": 0.08},
    "paper": {"stack_wall": 140, "stack_edge": 0.3, "stack_shadow": 0.5,
              "stack_hl": 0, "stack_wall_steps": 1, "lift": 0.02},
    "card": {"stack_wall": 160, "stack_edge": 0.2, "stack_shadow": 0.85,
             "stack_hl": 0, "stack_wall_steps": 1, "lift": 0.05},
}

# Graded walls are built from up to this many offset copies; stack_wall_steps
# enables the first N. GRADED_WALL_STEPS is what the dialog option sets.
MAX_WALL_STEPS = 12
GRADED_WALL_STEPS = 8

# Slab outlines are simplified after smoothing with a tolerance of this
# fraction of the generalization cell. Each smoothing pass doubles the vertex
# count with points that are nearly in line. On a 75 m cell (whole Grand Canyon
# DEM) 0.1 took 2.9 million vertices to 0.27 million and the full-extent render
# from 6.9 s to 1.9 s; the outlines look unchanged at the map's own scale and
# only slightly faceted when zoomed ten times closer. 0.25 was visibly angular,
# and simplifying before smoothing changed the shapes more.
SIMPLIFY_CELLS = 0.1

# Post-processors must outlive processAlgorithm(); QGIS calls them later on
# the main thread. Holding references here prevents garbage collection.
_KEEP_ALIVE = []


# =============================================================================
# Compatibility helpers
# QGIS 4 / Qt6 requires scoped enums (Qgis.RenderUnit.MapUnits); older QGIS 3
# builds only have the flat names (QgsUnitTypes.RenderMapUnits). Every enum in
# this file goes through _try() so the script runs on both.
# =============================================================================

def _try(*getters):
    """Return the first getter that doesn't raise AttributeError/TypeError."""
    for getter in getters:
        try:
            return getter()
        except (AttributeError, TypeError):
            continue
    raise AttributeError("No compatible QGIS/Qt API found")


def _map_units():
    return _try(lambda: Qgis.RenderUnit.MapUnits, lambda: QgsUnitTypes.RenderMapUnits)


def _mm():
    return _try(lambda: Qgis.RenderUnit.Millimeters, lambda: QgsUnitTypes.RenderMillimeters)


def _prop(name):
    return _try(lambda: getattr(QgsSymbolLayer.Property, name),
                lambda: getattr(QgsSymbolLayer, "Property" + name))


def _no_pen():
    return _try(lambda: Qt.PenStyle.NoPen, lambda: Qt.NoPen)


def _multiply():
    return _try(lambda: QPainter.CompositionMode.CompositionMode_Multiply,
                lambda: QPainter.CompositionMode_Multiply)


def _multipolygon():
    return _try(lambda: Qgis.WkbType.MultiPolygon, lambda: QgsWkbTypes.MultiPolygon)


def _field(name, kind):
    """kind: 'double' or 'int'. QGIS >= 3.38 wants QMetaType, older wants QVariant."""
    try:
        from qgis.PyQt.QtCore import QMetaType
        t = QMetaType.Type.Double if kind == "double" else QMetaType.Type.Int
        return QgsField(name, t)
    except (AttributeError, TypeError):
        from qgis.PyQt.QtCore import QVariant
        return QgsField(name, QVariant.Double if kind == "double" else QVariant.Int)


def _advanced(param):
    """Move a parameter into the dialog's collapsed 'Advanced' section."""
    try:
        flag = _try(lambda: Qgis.ProcessingParameterFlag.Advanced,
                    lambda: QgsProcessingParameterDefinition.FlagAdvanced)
        param.setFlags(param.flags() | flag)
    except Exception:
        pass  # purely cosmetic; never fail over it
    return param


def _warn(feedback, message):
    """pushWarning where it exists (QGIS >= 3.16), otherwise plain info."""
    getattr(feedback, "pushWarning", feedback.pushInfo)(message)


# -----------------------------------------------------------------------------
# Dialog: grey out settings that don't apply
# -----------------------------------------------------------------------------
# The Processing dialog has no API for one parameter's value to disable
# another's widget. It does let a parameter name its own widget wrapper
# class, and the View parameter uses that hook: _LinkedWidgets builds the
# normal native widget and, once the dialog has been built, connects every
# rule in _ENABLED_WHEN. In any other dialog (batch, modeler) or if the
# dialog isn't laid out as expected, nothing is linked and all fields stay
# enabled; the algorithm itself logs any setting it ignores.

def _enabled_rules():
    """{parameter: predicate(values)}; values maps parameter names to the
    dialog's current values."""
    def num(v, name):
        try:
            return float(v.get(name) or 0)
        except (TypeError, ValueError):
            return 0.0

    def oblique(v):
        return v.get("VIEW") in (0, None)

    return {
        "VIEW_AZ": oblique,
        "EXAG": oblique,
        "WALLS3D": oblique,
        "VIEW_ALT": lambda v: oblique(v) and num(v, "EXAG") == 0,
        "VEXAG": lambda v: oblique(v) and num(v, "EXAG") == 0 and num(v, "VIEW_ALT") > 0,
        "LIFT": lambda v: oblique(v) and num(v, "EXAG") == 0 and num(v, "VIEW_ALT") == 0,
        "LEVELS": lambda v: num(v, "INTERVAL") == 0,
        "PAPER_OPACITY": lambda v: bool(v.get("PAPER")),
        "SHADE_STRENGTH": lambda v: bool(v.get("SHADE")),
    }


def _link_widgets(panel):
    wrappers = getattr(panel, "wrappers", None)
    if not isinstance(wrappers, dict):
        return
    rules = {name: rule for name, rule in _enabled_rules().items() if name in wrappers}

    def update(*_):
        values = {}
        for name, w in wrappers.items():
            try:
                values[name] = w.parameterValue()
            except Exception:  # noqa: BLE001
                values[name] = None
        for name, rule in rules.items():
            on = bool(rule(values))
            for widget in (wrappers[name].wrappedWidget(), wrappers[name].wrappedLabel()):
                if widget is not None:
                    widget.setEnabled(on)

    for w in wrappers.values():
        w.widgetValueHasChanged.connect(update)
    update()


class _LinkedWidgets:
    """Widget wrapper "class" for the View parameter: returns the native
    wrapper the dialog would have made, and links the rules once the
    parameters panel has collected all its wrappers."""

    def __new__(cls, param, dialog, row=0, col=0, **kwargs):
        from qgis.gui import QgsGui, QgsProcessingGui
        registry = QgsGui.processingGuiRegistry()
        kind = dialog.__class__.__name__
        if kind == "ModelerParametersDialog":
            wrapper = registry.createModelerParameterWidget(
                dialog.model, dialog.childId, param, dialog.context)
        else:
            try:
                from processing.gui.wrappers import dialogTypes
                widget_type = dialogTypes.get(kind, QgsProcessingGui.WidgetType.Standard)
            except Exception:  # noqa: BLE001
                widget_type = QgsProcessingGui.WidgetType.Standard
            wrapper = registry.createParameterWidgetWrapper(param, widget_type)
        if wrapper is not None:
            wrapper.setDialog(dialog)
        # The single-run dialog (AlgorithmDialog in QGIS 3, AlgorithmWidget in
        # 4) has a parameters panel with a dict of wrappers; the batch
        # dialog's panel doesn't, so _link_widgets() leaves it alone.
        if kind != "ModelerParametersDialog" and hasattr(dialog, "mainWidget"):
            def link():
                try:
                    _link_widgets(dialog.mainWidget())
                except Exception:  # noqa: BLE001  cosmetic only
                    pass
            QTimer.singleShot(0, link)
        return wrapper


def _nice(x):
    """Round a raw contour interval to a 'clean' step (…, 20, 25, 50, 100, …)."""
    steps = [1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000]
    return min(steps, key=lambda s: abs(math.log(s / x)))


def _round_sig(x, digits):
    """Round to significant figures, so small values don't collapse to 0."""
    if x == 0:
        return 0.0
    return round(x, digits - 1 - int(math.floor(math.log10(abs(x)))))


# How far the map CRS may distort a northward step in the layer CRS (scale
# and rotation together) before walls visibly detach from their slabs.
CRS_TOLERANCE = 0.02


def _crs_wkt(crs):
    """WKT for GDAL. On QGIS 4.2 a valid CRS read from a layer can return an
    empty string from every toWkt() variant, so fall back to its authority id
    or PROJ string and let GDAL build the WKT."""
    wkt = crs.toWkt()
    if wkt:
        return wkt
    from osgeo import osr
    for source in (crs.authid(), crs.toProj()):
        if not source:
            continue
        srs = osr.SpatialReference()
        try:
            if srs.SetFromUserInput(source) == 0:
                return srs.ExportToWkt()
        except RuntimeError:
            continue
    raise QgsProcessingException(f"Could not describe CRS {crs.authid() or crs.description()}")


def _crs_mismatch(layer_crs, map_crs, point, transform_context):
    """Compare a 1000-unit northward step at `point` (in layer_crs) with the
    same step in map_crs. Returns the relative error: 0 when both CRSs have
    the same scale and orientation there, ~1 or more for e.g. degrees vs
    metres. The slab lift is measured in layer units and the wall offset in
    map units, so this is how far the walls drift from their slabs."""
    if layer_crs == map_crs:
        return 0.0
    try:
        ct = QgsCoordinateTransform(layer_crs, map_crs, transform_context)
        a = ct.transform(point)
        b = ct.transform(QgsPointXY(point.x(), point.y() + 1000.0))
    except Exception:  # noqa: BLE001  transform failure: treat as incompatible
        return float("inf")
    return math.hypot(b.x() - a.x(), b.y() - a.y() - 1000.0) / 1000.0


# =============================================================================
# Geometry
# =============================================================================

def _clean(geom, min_area, smooth_iterations, mask=None, tolerance=0):
    """Drop tiny holes and specks, smooth, simplify, clip to mask, force
    multipart.

    The mask is applied after smoothing so its edge stays exact rather than
    following (and rounding) the raster cells. Returns None if empty.
    """
    g = QgsGeometry(geom)
    if min_area > 0:
        g = g.removeInteriorRings(min_area)
        parts = [p for p in g.asGeometryCollection() if p.area() >= min_area]
        if not parts:
            return None
        g = QgsGeometry.collectGeometry(parts)
    if smooth_iterations > 0:
        g = g.smooth(smooth_iterations, 0.25, -1, 180)
    if tolerance > 0:
        simple = g.simplify(tolerance)
        if not simple.isNull() and not simple.isEmpty():
            # Douglas-Peucker can make a narrow neck cross itself. Repair it,
            # keeping only the polygons (makeValid can add stray lines).
            if not simple.isGeosValid():
                parts = [p for p in simple.makeValid().asGeometryCollection()
                         if p.area() > 0]
                simple = QgsGeometry.collectGeometry(parts) if parts else None
            if simple is not None:
                g = simple
    if mask is not None and not g.isEmpty():
        clipped = g.intersection(mask)
        if clipped.isNull():  # GEOS error, usually a self-touching ring
            clipped = g.makeValid().intersection(mask)
        # Drop the lines and points an intersection can leave along the edge,
        # and slivers the clip creates.
        parts = [p for p in clipped.asGeometryCollection()
                 if p.area() > 0 and p.area() >= min_area]
        if not parts:
            return None
        g = QgsGeometry.collectGeometry(parts)
    if g.isNull() or g.isEmpty():
        return None
    g.convertToMultiType()
    return g


# =============================================================================
# Symbology
# =============================================================================

def _color_expr():
    """Slab color. Built-in ramps start at 0.15 to skip their near-black end;
    the custom ramps are drawn in full. Custom ramps are written into the
    expression with create_ramp(), so they need no entry in the user's style
    library and travel with the layer's style. Elevation ramps ignore
    stack_base and stack_top."""
    t = 'scale_linear("ELEV_MIN", @stack_base, @stack_top, {}, 1)'
    cases = []

    def case(name, stops, pos):
        pairs = ",".join(f"'{s:g}','{c}'" for s, c in stops)
        cases.append(f"WHEN @stack_ramp = '{name}' "
                     f"THEN ramp_color(create_ramp(map({pairs})), {pos})")

    for name, stops in CUSTOM_RAMPS.items():
        case(name, stops, t.format(0))
    for name, stops in ELEVATION_RAMPS.items():
        lo, hi = stops[0][0], stops[-1][0]
        case(name, [((z - lo) / (hi - lo), c) for z, c in stops],
             f'scale_linear("ELEV_MIN", {lo}, {hi}, 0, 1)')
    return (f"CASE {' '.join(cases)} "
            f"ELSE ramp_color(@stack_ramp, {t.format(0.15)}) END")


COLOR_EXPR = _color_expr()
HL_COLOR_EXPR = f"lighter({COLOR_EXPR}, 140)"
SHADOW_COLOR_EXPR = "color_rgba(26, 10, 5, 255 * to_real(@stack_shadow))"
EDGE_COLOR_EXPR = (f"CASE WHEN @stack_style = 'paper' "
                   f"THEN set_color_part(lighter({COLOR_EXPR}, 170), 'alpha', 190) "
                   f"ELSE darker({COLOR_EXPR}, 350) END")
WALL_STEPS_EXPR = "max(1, coalesce(to_int(@stack_wall_steps), 1))"

# View and light directions, as compass azimuths in degrees. The viewer looks
# from stack_view_az: slabs shift away from the viewer by their lift and the
# walls face the viewer. The light comes from stack_light_az: highlights move
# toward it and shadows away from it. Layers from before 3.0 have neither
# variable and get these defaults, which reproduce the earlier fixed look.
DEFAULT_VIEW_AZ, DEFAULT_LIGHT_AZ = 180.0, 315.0
VIEW_RAD_EXPR = f"radians(coalesce(to_real(@stack_view_az), {DEFAULT_VIEW_AZ:g}))"
LIGHT_RAD_EXPR = f"radians(coalesce(to_real(@stack_light_az), {DEFAULT_LIGHT_AZ:g}))"
WALLS_ON_EXPR = "to_real(@stack_exag) > 0"  # no lift (nadir): no walls to draw

# Shadow per style: distance in mm and a turn in degrees from straight away
# from the light. With the default light (315) these give the earlier fixed
# offsets: 0.35 mm right and down for paper, 0.55 for slabs, and straight
# down 0.9 mm for card.
SHADOW_OFFSETS = {"paper": (0.35 * math.sqrt(2), 0), "card": (0.9, 45),
                  "slabs": (0.55 * math.sqrt(2), 0)}

# Nadir has no walls, so shadows and lit edges carry the depth: at least this
# shadow opacity and highlight offset (mm), whatever the style's defaults.
# Chosen from renders of paper, card and slabs at 1:20,000 on the Grand
# Canyon: 0.6 with no highlight read as a flat hillshade.
NADIR_SHADOW, NADIR_HL = 0.85, 0.15


def _lift_vector(view_az):
    """Unit map vector (east, north) along which the slabs are lifted: away
    from the viewer. A view from the south (180) lifts them north."""
    a = math.radians(view_az)
    return -math.sin(a), -math.cos(a)


def _page_offset(distance, azimuth):
    """QGIS page offset (x right, y down, in the distance's units) of a
    move toward a compass azimuth on a north-up map."""
    a = math.radians(azimuth)
    return distance * math.sin(a), -distance * math.cos(a)


def _drawn_extent(bbox, max_lift, step, view_az):
    """Extent the stack covers once drawn: the ground extent, the highest
    slab's lifted position and the lowest wall, which reaches one step
    toward the viewer."""
    ux, uy = _lift_vector(view_az)
    out = QgsRectangle(bbox)
    for d in (max_lift, -step):
        out.combineExtentWith(QgsRectangle(
            bbox.xMinimum() + d * ux, bbox.yMinimum() + d * uy,
            bbox.xMaximum() + d * ux, bbox.yMaximum() + d * uy))
    return out


def _burn_slabs(slabs, w, h, transform, wkt):
    """Grid of the ELEV_MIN of the highest slab over each pixel (-1e30 where
    there is none). slabs are (elev, geometry) pairs in ascending order."""
    from osgeo import gdal, ogr, osr

    srs = osr.SpatialReference()
    srs.ImportFromWkt(wkt)
    vds = ogr.GetDriverByName("Memory").CreateDataSource("")
    vlyr = vds.CreateLayer("slabs", srs, ogr.wkbMultiPolygon)
    vlyr.CreateField(ogr.FieldDefn("ELEV", ogr.OFTReal))
    for lvl, g in slabs:  # ascending, so higher slabs burn last
        f = ogr.Feature(vlyr.GetLayerDefn())
        f.SetField("ELEV", float(lvl))
        f.SetGeometry(ogr.CreateGeometryFromWkb(bytes(g.asWkb())))
        vlyr.CreateFeature(f)
    burn_ds = gdal.GetDriverByName("MEM").Create("", w, h, 1, gdal.GDT_Float64)
    burn_ds.SetGeoTransform(transform)
    burn_ds.SetProjection(wkt)
    burn_ds.GetRasterBand(1).Fill(-1e30)
    gdal.RasterizeLayer(burn_ds, [1], vlyr, options=["ATTRIBUTE=ELEV"])
    return burn_ds.GetRasterBand(1).ReadAsArray()


def _shifted(a, dr, dc):
    """a moved dr rows down and dc columns right, filled with zeros."""
    import numpy as np

    out = np.zeros_like(a)
    h, w = a.shape
    if abs(dr) < h and abs(dc) < w:
        out[max(0, dr):h - max(0, -dr), max(0, dc):w - max(0, -dc)] = \
            a[max(0, -dr):h - max(0, dr), max(0, -dc):w - max(0, dc)]
    return out


def _stack_mask(slabs, base, exag, step, view_az, bbox, px, pad, wkt):
    """Where the stack draws, on a grid of about px over bbox, as 0/255 bytes
    (row 0 north): each slab at its lift, swept one step back toward the
    viewer for its wall (none in a nadir view), then grown by pad map units
    for the edge lines, highlights and shadows."""
    import numpy as np

    w = max(1, int(math.ceil(bbox.width() / px)))
    h = max(1, int(math.ceil(bbox.height() / px)))
    pxx, pxy = bbox.width() / w, bbox.height() / h
    burn = _burn_slabs(slabs, w, h, (bbox.xMinimum(), pxx, 0, bbox.yMaximum(), 0, -pxy), wkt)
    ux, uy = _lift_vector(view_az)
    mask = np.zeros((h, w), dtype=bool)
    # Nested slabs: in a nadir view the lowest one already covers the rest.
    for lvl, _ in (slabs if exag > 0 else slabs[:1]):
        ground = burn >= lvl - 1e-6
        lift = (lvl - base) * exag
        n = int(math.ceil(step / min(pxx, pxy))) if exag > 0 else 0
        lifts = [lift - step * (1 - j / n) for j in range(n + 1)] if n else [lift]
        for dr, dc in {(int(round(-d * uy / pxy)), int(round(d * ux / pxx))) for d in lifts}:
            mask |= _shifted(ground, dr, dc)
    r = max(1, int(math.ceil(pad / min(pxx, pxy))))
    grown = mask.copy()
    for dr in range(-r, r + 1):
        for dc in range(-r, r + 1):
            if dr * dr + dc * dc <= r * r:
                grown |= _shifted(mask, dr, dc)
    return grown.astype("uint8") * 255


def _shadow_offset(style, light_az):
    dist, turn = SHADOW_OFFSETS.get(style, SHADOW_OFFSETS["slabs"])
    return _page_offset(dist, light_az + 180 + turn)


def _wall_color_expr(k):
    """Color of wall copy k: the full @stack_wall darkness under the edge
    (k = 1), lightening to about a third of it at the base (k = N)."""
    n = WALL_STEPS_EXPR
    return (f"darker({COLOR_EXPR}, to_int(100 + (to_real(@stack_wall) - 100)"
            f" * (1 - 0.65 * ({k} - 1) / max(1, {n} - 1))))")


def _offset_expr(dist, rad):
    """Page offset 'x,y' of a move by dist toward the azimuth rad (both
    expressions); the same math as _page_offset()."""
    return (f"to_string(round(({dist}) * sin({rad}), 6)) || ',' || "
            f"to_string(round(-({dist}) * cos({rad}), 6))")


def _shadow_offset_expr():
    def by_style(i):
        cases = " ".join(f"WHEN @stack_style = '{s}' THEN {v[i]:g}"
                         for s, v in SHADOW_OFFSETS.items() if s != "slabs")
        return f"CASE {cases} ELSE {SHADOW_OFFSETS['slabs'][i]:g} END"
    rad = f"({LIGHT_RAD_EXPR} + radians(180 + {by_style(1)}))"
    return _offset_expr(by_style(0), rad)


def _blur_effect():
    blur = QgsBlurEffect()
    try:
        blur.setBlurUnit(_mm())
        blur.setBlurLevel(1.8)
    except (AttributeError, TypeError):  # older QGIS: integer pixels only
        blur.setBlurLevel(8)
    return blur


def _saturation_effect(change_pct):
    """change_pct: -100 (grayscale) .. 0 (unchanged)."""
    stack = QgsEffectStack()
    color = QgsColorEffect()
    color.setSaturation(max(0.0, 1.0 + change_pct / 100.0))
    stack.appendEffect(color)
    return stack


def apply_stack_style(layer, variables, shadow=False, saturation=0.0):
    """Build the highlight / wall / shadow / face renderer and attach layer
    variables. Style variables missing from `variables` get the defaults of
    its stack_style ('slabs' if unset); shadow=True is kept for older console
    snippets and means a stack_shadow of 0.45 when none is given."""
    variables = dict(variables)
    style = variables.setdefault("stack_style", "slabs")
    variables.setdefault("stack_view_az", DEFAULT_VIEW_AZ)
    variables.setdefault("stack_light_az", DEFAULT_LIGHT_AZ)
    if shadow:
        variables.setdefault("stack_shadow", 0.45)
    for key, val in STYLE_DEFAULTS.get(style, STYLE_DEFAULTS["slabs"]).items():
        if key.startswith("stack_"):
            variables.setdefault(key, val)
    for key, val in variables.items():
        QgsExpressionContextUtils.setLayerVariable(layer, key, val)

    # Highlight (bottom): lighter tint of the slab color, peeking out toward
    # the light. stack_hl is the offset along each axis at the default light,
    # so the distance is stack_hl x sqrt(2).
    hl = QgsSimpleFillSymbolLayer(QColor("#fff4e0"))
    hl.setStrokeStyle(_no_pen())
    hl.setOffset(QPointF(-0.15, -0.15))
    hl.setOffsetUnit(_mm())
    hl.setDataDefinedProperty(
        _prop("FillColor"), QgsProperty.fromExpression(HL_COLOR_EXPR))
    hl.setDataDefinedProperty(_prop("Offset"), QgsProperty.fromExpression(
        _offset_expr("to_real(@stack_hl) * sqrt(2)", LIGHT_RAD_EXPR)))

    # Wall (middle): copies of the slab pushed toward the viewer, together
    # one slab step (stack_interval x stack_exag) deep. With stack_wall_steps
    # = 1 that is a single flat copy; with more, copy k of N sits k/N of the
    # way out, and the copies lighten from the full @stack_wall darkness under
    # the edge to about a third of it at the base: a graded, 3-D side.
    # Deepest copy first, so each shallower one draws over it. With no lift
    # (nadir) every copy would sit under the face, so all are switched off.
    n = WALL_STEPS_EXPR
    walls = []
    for k in range(MAX_WALL_STEPS, 0, -1):
        wall = QgsSimpleFillSymbolLayer(QColor("#1a1030"))
        wall.setStrokeStyle(_no_pen())
        wall.setOffsetUnit(_map_units())
        wall.setDataDefinedProperty(_prop("FillColor"),
                                    QgsProperty.fromExpression(_wall_color_expr(k)))
        wall.setDataDefinedProperty(_prop("Offset"), QgsProperty.fromExpression(
            _offset_expr(f"@stack_interval * @stack_exag * {k} / {n}", VIEW_RAD_EXPR)))
        enabled = WALLS_ON_EXPR if k == 1 else f"{k} <= {n} AND {WALLS_ON_EXPR}"
        try:
            wall.setDataDefinedProperty(
                _prop("LayerEnabled"), QgsProperty.fromExpression(enabled))
        except AttributeError:
            if k > 1:
                continue  # can't switch it off: leave it out
        walls.append(wall)

    # Shadow: a blurred dark copy of the face, cast onto the slab below. A
    # symbol layer rather than a drop-shadow paint effect, because paint
    # effects can't be driven by expressions and @stack_shadow must be live.
    shade = QgsSimpleFillSymbolLayer(QColor(26, 10, 5))
    shade.setStrokeStyle(_no_pen())
    shade.setOffsetUnit(_mm())
    shade.setDataDefinedProperty(
        _prop("FillColor"), QgsProperty.fromExpression(SHADOW_COLOR_EXPR))
    shade.setDataDefinedProperty(
        _prop("Offset"), QgsProperty.fromExpression(_shadow_offset_expr()))
    try:  # skip the (costly) blur entirely when the shadow is off
        shade.setDataDefinedProperty(
            _prop("LayerEnabled"), QgsProperty.fromExpression("to_real(@stack_shadow) > 0"))
    except AttributeError:
        pass
    shade.setPaintEffect(_blur_effect())

    # Face (top), with an optional edge line: pale for paper, dark otherwise.
    face = QgsSimpleFillSymbolLayer()
    face.setDataDefinedProperty(_prop("FillColor"), QgsProperty.fromExpression(COLOR_EXPR))
    face.setStrokeWidthUnit(_mm())
    face.setDataDefinedProperty(
        _prop("StrokeWidth"), QgsProperty.fromExpression("to_real(@stack_edge)"))
    face.setDataDefinedProperty(_prop("StrokeStyle"), QgsProperty.fromExpression(
        "CASE WHEN to_real(@stack_edge) > 0 THEN 'solid' ELSE 'no' END"))
    face.setDataDefinedProperty(
        _prop("StrokeColor"), QgsProperty.fromExpression(EDGE_COLOR_EXPR))

    # List order = draw order (first = bottom).
    sub = QgsFillSymbol([hl] + walls + [shade, face])

    # Lift: each slab moves away from the viewer by its height above the base
    # times stack_exag (see _lift_vector()).
    lift = '("ELEV_MIN" - @stack_base) * @stack_exag'
    gen = QgsGeometryGeneratorSymbolLayer.create({
        "geometryModifier":
            f"translate($geometry, -{lift} * sin({VIEW_RAD_EXPR}), "
            f"-{lift} * cos({VIEW_RAD_EXPR}))",
        "SymbolType": "Fill",
    })
    gen.setSubSymbol(sub)

    renderer = QgsSingleSymbolRenderer(QgsFillSymbol([gen]))
    renderer.setOrderBy(QgsFeatureRequest.OrderBy(
        [QgsFeatureRequest.OrderByClause("ELEV_MIN", True)]))
    renderer.setOrderByEnabled(True)
    if saturation < 0:
        renderer.setPaintEffect(_saturation_effect(saturation))

    layer.setRenderer(renderer)
    layer.triggerRepaint()


def embed_style(layer, feedback):
    """Write a sidecar .qml and, for GeoPackages, a default style inside the file.

    Both include custom properties, which is where layer variables live, so the
    @stack_* expressions keep working when the file is opened in another project.
    """
    src = layer.source().split("|")[0]
    if not os.path.isfile(src):
        return  # memory/temporary layer: nothing to write into

    # Neither call raises on failure; both report through their return values.
    qml = os.path.splitext(src)[0] + ".qml"
    try:
        msg, ok = layer.saveNamedStyle(qml)
        if ok:
            feedback.pushInfo(f"Sidecar style written: {qml}")
        else:
            feedback.reportError(f"Could not write sidecar .qml: {msg}", False)
    except Exception as e:  # noqa: BLE001
        feedback.reportError(f"Could not write sidecar .qml: {e}", False)

    if src.lower().endswith(".gpkg"):
        name, desc = "stacked_terrain", f"Stacked terrain v{VERSION}"
        try:
            if hasattr(layer, "saveStyleToDatabaseV2"):  # QGIS >= 3.34
                _, msg = layer.saveStyleToDatabaseV2(name, desc, True, "")
            else:
                msg = layer.saveStyleToDatabase(name, desc, True, "")
            if msg:
                feedback.reportError(f"Could not embed style in GeoPackage: {msg}", False)
            else:
                feedback.pushInfo("Style embedded in GeoPackage as default")
        except Exception as e:  # noqa: BLE001
            feedback.reportError(f"Could not embed style in GeoPackage: {e}", False)


def _in_own_folder(value):
    """Put a plain file output in a folder named after it, so everything the
    run writes stays together: .../Hermosa.gpkg -> .../Hermosa/Hermosa.gpkg.
    A file already in a folder of that name is left where it is. Returns the
    (possibly rewritten) OUTPUT value and the folder, or None for temporary,
    database and other non-file outputs."""
    is_def = isinstance(value, QgsProcessingOutputLayerDefinition)
    path = value.sink.staticValue() if is_def else value
    # A URI scheme has two or more letters before its colon, so a Windows
    # drive (C:\ or C:/) is not mistaken for one.
    if (not isinstance(path, str) or "|" in path or not os.path.isabs(path)
            or re.match(r"[A-Za-z][A-Za-z0-9+.-]+:", path)):
        return value, None  # TEMPORARY_OUTPUT, ogr:/postgres: URIs, layer options
    base, ext = os.path.splitext(path)
    if ext.lower() not in (".gpkg", ".shp", ".geojson", ".fgb"):
        return value, None
    stem, parent = os.path.basename(base), os.path.dirname(path)
    folder = parent if os.path.basename(parent) == stem else os.path.join(parent, stem)
    os.makedirs(folder, exist_ok=True)
    new_path = os.path.join(folder, stem + ext)
    if not is_def:
        return new_path, folder
    new = QgsProcessingOutputLayerDefinition(new_path, value.destinationProject)
    new.destinationName = value.destinationName
    new.createOptions = value.createOptions
    return new, folder


def _save_raster_style(layer, feedback, label):
    """Sidecar .qml next to a raster; QGIS applies it whenever the file is
    added to a project, so blend mode and opacity travel with the image."""
    src = layer.source()
    if not os.path.isfile(src):
        return
    try:
        msg, ok = layer.saveNamedStyle(os.path.splitext(src)[0] + ".qml")
        if not ok:
            feedback.reportError(f"{label}: could not write style file: {msg}", False)
    except Exception as e:  # noqa: BLE001
        feedback.reportError(f"{label}: could not write style file: {e}", False)


def _schedule_finish(run, project):
    """Group the outputs in order and write the .qlr once every output is
    loaded. QGIS may add a layer's Layers-panel node only after its
    post-processor returns, so wait two event-loop turns before looking for
    the nodes. Each styler calls this; the last one to finish does the work."""
    QTimer.singleShot(0, lambda: QTimer.singleShot(0, lambda: _finish(run, project)))


def _finish(run, project):
    if run.get("done"):
        return
    layers = [project.mapLayer(run.get(role + "_id") or "")
              for role in ("paper", "shade", "terrain") if role in run["expect"]]
    if not all(layers):
        return  # a styler hasn't run yet
    run["done"] = True
    _group_in_tree(layers, run["name"], project)
    if run.get("qlr"):
        _write_bundle(layers, run["name"], run["qlr"])


def _group_in_tree(layers, name, project):
    """Put the outputs in a Layers-panel group, in the given order (top
    first), where the topmost of them currently sits."""
    root = project.layerTreeRoot()
    nodes = [root.findLayer(layer.id()) for layer in layers]
    if not all(nodes):
        return
    parent = nodes[-1].parent() or root  # the slabs' group, usually the root
    siblings = parent.children()
    ids = {layer.id() for layer in layers}
    index = next((i for i, c in enumerate(siblings)
                  if getattr(c, "layerId", lambda: None)() in ids), 0)
    group = parent.insertGroup(index, name)
    # Clone into the group before removing the originals: removing a layer's
    # last tree node also removes the layer from the project.
    for node in nodes:
        group.addChildNode(node.clone())
    for node in nodes:
        node.parent().removeChildNode(node)


def _write_bundle(layers, name, path):
    """<name>.qlr: one file that adds the whole stack (paper, shading, slabs,
    top to bottom, with their styles) to any project as a group. Paths are
    written relative to it, so the folder can be moved as a unit. Runs from a
    timer after the algorithm has finished, so it logs rather than using the
    (by then deleted) feedback object."""
    group = QgsLayerTreeGroup(name)
    for layer in layers:
        group.addLayer(layer)
    try:
        try:
            ok, err = QgsLayerDefinition.exportLayerDefinition(
                path, [group], Qgis.FilePathType.Relative)
        except (AttributeError, TypeError):  # QGIS < 3.28
            ok, err = QgsLayerDefinition.exportLayerDefinition(path, [group])
    except Exception as e:  # noqa: BLE001
        ok, err = False, str(e)
    if ok:
        QgsMessageLog.logMessage(f"Layer bundle written: {path}", "Stacked terrain")
    else:
        QgsMessageLog.logMessage(f"Could not write layer bundle {path}: {err}",
                                 "Stacked terrain", _try(lambda: Qgis.MessageLevel.Warning,
                                                         lambda: Qgis.Warning))


def _output_path(dest_id, suffix, temp_name):
    """Path for a raster that accompanies the slab output: next to a file
    output (Hermosa.gpkg -> Hermosa_paper.tif), otherwise a temp file."""
    dest_file = dest_id.split("|")[0]
    if os.path.splitext(dest_file)[1].lower() in (".gpkg", ".shp", ".geojson", ".fgb"):
        return os.path.splitext(dest_file)[0] + suffix
    return QgsProcessingUtils.generateTempFilename(temp_name)


# =============================================================================
# ArcGIS Pro export
# Pro can't read QGIS styles, and its symbols have no layer variables, map-unit
# geometry generators or blur. So the export bakes the lift and the walls into
# geometry (one feature per wall copy and per face, stored in drawing order)
# and writes a .lyrx whose unique-value renderer gives each feature its own
# fixed symbol. The highlight and shadow stay page-unit Move effects, like
# their millimetre offsets in QGIS; the blur becomes four buffered copies.
# =============================================================================

PT_PER_MM = 72 / 25.4
# Stand-in for the 1.8 mm blur: copies grown by these distances, together at
# SHADOW_STRENGTH of the shadow's opacity. Tuned against the QGIS rendering at
# 1:50,000; an unbuffered copy left a hard dark rim along every edge.
SHADOW_BUFFERS_MM = (0.25, 0.6, 0.95, 1.3)
SHADOW_STRENGTH = 0.55
LYRX_VERSION, LYRX_BUILD = "2.6.0", 24783  # oldest Pro the file targets


class _ExprColors:
    """Evaluate the renderer's color expressions for a given ELEV_MIN, so the
    export uses exactly the colors QGIS draws, saturation change included."""

    def __init__(self, variables, saturation):
        self.fields = QgsFields()
        self.fields.append(_field("ELEV_MIN", "double"))
        self.ctx = QgsExpressionContext()
        self.ctx.appendScope(QgsExpressionContextUtils.globalScope())
        scope = QgsExpressionContextScope()
        for key, val in variables.items():
            scope.setVariable(key, val)
        self.ctx.appendScope(scope)
        self.ctx.setFields(self.fields)
        self.saturation = max(0.0, 1.0 + saturation / 100.0)
        self.exprs = {}

    def __call__(self, expr, elev):
        e = self.exprs.get(expr)
        if e is None:
            e = self.exprs[expr] = QgsExpression(expr)
            if e.hasParserError():
                raise QgsProcessingException(f"Color expression: {e.parserErrorString()}")
        feat = QgsFeature(self.fields)
        feat.setAttributes([float(elev)])
        self.ctx.setFeature(feat)
        val = e.evaluate(self.ctx)
        if e.hasEvalError():
            raise QgsProcessingException(f"Color expression: {e.evalErrorString()}")
        # Color functions return a QColor on newer QGIS, "r,g,b,a" on older.
        c = QColor(val) if isinstance(val, QColor) else QgsSymbolLayerUtils.decodeColor(str(val))
        if self.saturation < 1:
            h, s, lum, a = c.getHslF()
            if h >= 0:  # -1 = achromatic, nothing to desaturate
                c.setHslF(h, s * self.saturation, lum, a)
        return c


def _cim_color(c, alpha=None):
    a = c.alphaF() if alpha is None else alpha
    return {"type": "CIMRGBColor", "values": [c.red(), c.green(), c.blue(), round(100 * a, 1)]}


def _cim_move(dx_mm, dy_mm):
    """Page-unit move; dx right and dy down in mm, as QGIS offsets are.
    CIM offsets are in points with y up."""
    return {"type": "CIMGeometricEffectMove",
            "offsetX": dx_mm * PT_PER_MM, "offsetY": -dy_mm * PT_PER_MM}


def _cim_fill(c, effects=(), alpha=None):
    layer = {"type": "CIMSolidFill", "enable": True, "color": _cim_color(c, alpha)}
    if effects:
        layer["effects"] = list(effects)
    return layer


def _cim_symbol(layers):
    """Polygon symbol; the first symbol layer draws on top."""
    return {"type": "CIMSymbolReference",
            "symbol": {"type": "CIMPolygonSymbol", "symbolLayers": layers}}


def _arcgis_features(slabs, variables, saturation):
    """(part, level, geometry, symbol, label) in drawing order, matching what
    apply_stack_style() draws with the run's variables: per slab, bottom
    up, the highlight, the walls (deepest first) and the face with its
    shadow and edge line."""
    colors = _ExprColors(variables, saturation)
    base, exag = variables["stack_base"], variables["stack_exag"]
    step = variables["stack_interval"] * exag
    n = max(1, min(MAX_WALL_STEPS, int(variables["stack_wall_steps"])))
    hl = float(variables["stack_hl"])
    edge = float(variables["stack_edge"])
    shadow = float(variables["stack_shadow"])
    light_az = float(variables.get("stack_light_az", DEFAULT_LIGHT_AZ))
    ux, uy = _lift_vector(float(variables.get("stack_view_az", DEFAULT_VIEW_AZ)))
    sdx, sdy = _shadow_offset(variables["stack_style"], light_az)
    hdx, hdy = _page_offset(hl * math.sqrt(2), light_az)
    if exag <= 0:
        n = 0  # nadir: no walls

    out = []
    for lvl, g in slabs:
        lift = (lvl - base) * exag

        def moved(d):
            m = QgsGeometry(g)
            m.translate(d * ux, d * uy)
            return m

        face = moved(lift)
        if hl > 0:
            out.append(("highlight", lvl, face, _cim_symbol(
                [_cim_fill(colors(HL_COLOR_EXPR, lvl), [_cim_move(hdx, hdy)])]),
                f"{lvl:g} highlight"))
        for k in range(n, 0, -1):
            out.append(("wall", lvl, moved(lift - step * k / n), _cim_symbol(
                [_cim_fill(colors(_wall_color_expr(k), lvl))]),
                f"{lvl:g} wall" + (f" {k}/{n}" if n > 1 else "")))
        layers = []
        if edge > 0:
            layers.append({"type": "CIMSolidStroke", "enable": True,
                           "capStyle": "Round", "joinStyle": "Round", "miterLimit": 10,
                           "width": edge * PT_PER_MM,
                           "color": _cim_color(colors(EDGE_COLOR_EXPR, lvl))})
        layers.append(_cim_fill(colors(COLOR_EXPR, lvl)))
        if shadow > 0:
            sc = colors(SHADOW_COLOR_EXPR, lvl)
            # Per-copy opacity so where all copies overlap they reach
            # SHADOW_STRENGTH of the shadow's opacity.
            target = min(sc.alphaF() * SHADOW_STRENGTH, 0.999)
            a = 1 - (1 - target) ** (1 / len(SHADOW_BUFFERS_MM))
            for b in SHADOW_BUFFERS_MM:
                layers.append(_cim_fill(sc, [
                    {"type": "CIMGeometricEffectBuffer", "size": b * PT_PER_MM},
                    _cim_move(sdx, sdy)], alpha=a))
        out.append(("face", lvl, face, _cim_symbol(layers), f"{lvl:g} face"))
    return out


def _write_arcgis_vector(out_dir, stem, features, crs):
    """Write the baked features, in drawing order, to <stem>.shp. Returns the
    .lyrx data connection, with a path relative to the .lyrx.

    A shapefile rather than a File Geodatabase: GDAL writes a geodatabase
    feature class with no extent in its catalog, and Pro doesn't draw such a
    layer until something (Zoom To Layer) makes it compute one. A shapefile
    carries its extent in its header, and Pro reads it without modifying it."""
    import shutil
    from osgeo import ogr, osr

    old_gdb = os.path.join(out_dir, stem + ".gdb")  # written by 2.5.0-2.5.2
    if os.path.isdir(old_gdb):
        shutil.rmtree(old_gdb)
    drv = ogr.GetDriverByName("ESRI Shapefile")
    path = os.path.join(out_dir, stem + ".shp")
    if os.path.exists(path):
        drv.DeleteDataSource(path)

    ds = drv.CreateDataSource(path)
    if ds is None:
        raise QgsProcessingException(f"Could not create {path}")
    srs = osr.SpatialReference()
    srs.ImportFromWkt(_crs_wkt(crs))
    lyr = ds.CreateLayer(stem, srs, ogr.wkbMultiPolygon, options=["ENCODING=UTF-8"])
    for fname, ftype in [("DRAW_ORDER", ogr.OFTInteger), ("PART", ogr.OFTString),
                         ("ELEV_MIN", ogr.OFTReal)]:
        lyr.CreateField(ogr.FieldDefn(fname, ftype))
    for i, (part, lvl, geom, _, _) in enumerate(features):
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("DRAW_ORDER", i)
        f.SetField("PART", part)
        f.SetField("ELEV_MIN", float(lvl))
        f.SetGeometry(ogr.CreateGeometryFromWkb(bytes(geom.asWkb())))
        lyr.CreateFeature(f)
    lyr = ds = None  # close and flush
    return {"type": "CIMStandardDataConnection", "workspaceConnectionString": "DATABASE=.",
            "workspaceFactory": "Shapefile", "dataset": stem + ".shp",
            "datasetType": "esriDTFeatureClass"}


def _write_gray(src, out):
    """Paper texture as one luminosity band, stretched to 0-255: what QGIS
    shows with grayscale mode and a min/max stretch. Where its alpha band
    hides it, it fades to white, which is neutral under Multiply."""
    import numpy as np
    from osgeo import gdal

    ds = gdal.Open(src)
    bands = [ds.GetRasterBand(i + 1) for i in range(ds.RasterCount)]
    is_alpha = [b.GetColorInterpretation() == gdal.GCI_AlphaBand for b in bands]
    alpha = [b for b, x in zip(bands, is_alpha) if x]
    color = [b.ReadAsArray().astype("float32") for b, x in zip(bands, is_alpha) if not x][:3]
    gray = (0.21 * color[0] + 0.72 * color[1] + 0.07 * color[2]
            if len(color) == 3 else color[0])
    lo, hi = float(gray.min()), float(gray.max())
    gray = (gray - lo) * (255.0 / (hi - lo)) if hi > lo else np.full_like(gray, 255.0)
    if alpha:
        a = alpha[0].ReadAsArray().astype("float32") / 255.0
        gray = 255.0 - (255.0 - gray) * a
    out_ds = gdal.GetDriverByName("GTiff").Create(
        out, ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Byte, options=["COMPRESS=DEFLATE"])
    out_ds.SetGeoTransform(ds.GetGeoTransform())
    out_ds.SetProjection(ds.GetProjection())
    out_ds.GetRasterBand(1).WriteArray(gray.clip(0, 255).astype("uint8"))
    out_ds = ds = None


def _lyrx_layer(kind, name, uri, extra):
    layer = {"type": kind, "name": name, "uRI": uri, "useSourceMetadata": True,
             "layerType": "Operational", "showLegends": True, "visibility": True,
             "displayCacheType": "Permanent", "maxDisplayCacheAge": 5,
             "showPopups": True, "serviceLayerID": -1, "refreshRate": -1,
             "refreshRateUnit": "esriTimeUnitsSeconds"}
    layer.update(extra)
    return layer


def _lyrx_raster(name, uri, filename, opacity):
    """Grayscale 8-bit raster shown as is (no stretch), multiplied over the
    layers below: white is neutral."""
    return _lyrx_layer("CIMRasterLayer", name, uri, {
        "blendingMode": "Multiply",
        "transparency": round(100 * (1 - opacity), 1),
        "dataConnection": {"type": "CIMStandardDataConnection",
                           "workspaceConnectionString": "DATABASE=.",
                           "workspaceFactory": "Raster", "dataset": filename,
                           "datasetType": "esriDTAny"},
        "colorizer": {
            "type": "CIMRasterStretchColorizer", "resamplingType": "Bilinear",
            "bandIndex": 0, "stretchType": "None",
            "colorRamp": {"type": "CIMLinearContinuousColorRamp",
                          "colorSpace": {"type": "CIMICCColorSpace", "url": "Default RGB"},
                          "fromColor": {"type": "CIMRGBColor", "values": [0, 0, 0, 100]},
                          "toColor": {"type": "CIMRGBColor", "values": [255, 255, 255, 100]}}},
    })


def export_arcgis(out_dir, stem, slabs, variables, saturation, crs,
                  shade=None, paper=None, feedback=None):
    """Write <out_dir>/<stem>.shp, copies of the rasters and
    <stem>.lyrx, a group layer file for ArcGIS Pro: paper texture, shading,
    slabs; plus <stem>_slabs.lyrx with the slabs alone, for Pro's Apply
    Symbology From Layer. shade and paper are (path, opacity) or None.
    Returns the group .lyrx path."""
    import json
    import shutil
    import uuid

    os.makedirs(out_dir, exist_ok=True)
    features = _arcgis_features(slabs, variables, saturation)
    conn = _write_arcgis_vector(out_dir, stem, features, crs)

    classes = [{"type": "CIMUniqueValueClass", "label": label, "patch": "Default",
                "symbol": symbol, "visible": True,
                "values": [{"type": "CIMUniqueValue", "fieldValues": [str(i)]}]}
               for i, (_, _, _, symbol, label) in enumerate(features)]
    # Pro identifies a map's layers by uRI, so every export gets its own:
    # a second export added to the same map must not collide with the first.
    run_id = uuid.uuid4().hex[:12]

    def uri(role):
        return f"CIMPATH=stacked_terrain/{run_id}_{role}.json"

    slab_layer = _lyrx_layer("CIMFeatureLayer", stem, uri("slabs"), {
        "expanded": False,
        "featureTable": {"type": "CIMFeatureTable", "displayField": "ELEV_MIN",
                         "editable": True, "dataConnection": conn,
                         "studyAreaSpatialRel": "esriSpatialRelUndefined",
                         "searchOrder": "esriSearchOrderSpatial"},
        "renderer": {"type": "CIMUniqueValueRenderer", "fields": ["DRAW_ORDER"],
                     "useDefaultSymbol": False, "defaultLabel": "<all other values>",
                     "groups": [{"type": "CIMUniqueValueGroup", "heading": "DRAW_ORDER",
                                 "classes": classes}],
                     "polygonSymbolColorTarget": "Fill"},
        "selectable": True, "scaleSymbols": False, "snappable": True,
    })

    layers = []  # top first
    for role, src, label in [("paper", paper, "paper texture"), ("shade", shade, "shading")]:
        if not src:
            continue
        name = f"{stem}_{role}.tif"
        if role == "paper":
            _write_gray(src[0], os.path.join(out_dir, name))
        else:
            shutil.copyfile(src[0], os.path.join(out_dir, name))
        layers.append(_lyrx_raster(f"{stem} {label}", uri(role),
                                   name, src[1]))
    layers.append(slab_layer)

    group = _lyrx_layer("CIMGroupLayer", stem, uri("group"),
                        {"expanded": True, "layers": [lyr["uRI"] for lyr in layers]})
    def write(name, top, definitions):
        doc = {"type": "CIMLayerDocument", "version": LYRX_VERSION, "build": LYRX_BUILD,
               "layers": [top["uRI"]], "layerDefinitions": definitions}
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)

    # The group adds the whole stack; Apply Symbology From Layer rejects a
    # group, so the slabs also get a layer file of their own.
    write(stem + ".lyrx", group, [group] + layers)
    alone = dict(slab_layer, uRI=uri("slabs_alone"))  # may share a map with the group
    write(stem + "_slabs.lyrx", alone, [alone])
    path = os.path.join(out_dir, stem + ".lyrx")
    if feedback:
        feedback.pushInfo(f"ArcGIS Pro export: {len(features)} features in "
                          f"{conn['workspaceFactory']} {conn['dataset']}, layer file {path}")
    return path


# =============================================================================
# Post-processors (run on the main thread after the layers are loaded)
# =============================================================================

class TerrainStyler(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, variables, saturation, run):
        super().__init__()
        self.variables = variables
        self.saturation = saturation
        self.run = run  # shared with the raster stylers and _finish()

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsVectorLayer):
            return
        self.run["terrain_id"] = layer.id()
        apply_stack_style(layer, self.variables, saturation=self.saturation)
        embed_style(layer, feedback)
        project = context.project() or QgsProject.instance()
        # Slab lift is applied in layer units but the wall offset is in map
        # units. Leave an equivalent project CRS alone (e.g. NAD83 vs WGS84
        # UTM 13N); switch only when walls would visibly detach. A nadir view
        # has neither lift nor walls, so any project CRS will do.
        if float(self.variables["stack_exag"]) > 0 and project.crs() != layer.crs():
            old = project.crs().authid() or "custom"
            err = _crs_mismatch(layer.crs(), project.crs(), layer.extent().center(),
                                project.transformContext())
            if err > CRS_TOLERANCE:
                feedback.pushInfo(
                    f"Project CRS changed from {old} to {layer.crs().authid()} "
                    f"so walls line up with the slabs")
                project.setCrs(layer.crs())
            else:
                feedback.pushInfo(
                    f"Project CRS {old} left unchanged; it matches the layer's "
                    f"{layer.crs().authid()} within {err:.2%}")
        _schedule_finish(self.run, project)

    @staticmethod
    def create(*args):
        inst = TerrainStyler(*args)
        _KEEP_ALIVE.append(inst)
        return inst


class _RasterStyler(QgsProcessingLayerPostProcessorInterface):
    """Shared styling for the rasters drawn over the slabs: smooth
    resampling, Multiply blend (white is neutral) and an opacity. Their
    place in the Layers panel is set when _finish() groups the outputs."""
    role = label = None  # set by subclasses

    def __init__(self, opacity, run):
        super().__init__()
        self.opacity = opacity
        self.run = run

    def steps(self, layer):
        def resampling():
            layer.resampleFilter().setZoomedInResampler(QgsCubicRasterResampler())
            layer.resampleFilter().setZoomedOutResampler(QgsBilinearRasterResampler())

        return [("resampling", resampling),
                ("Multiply blend", lambda: layer.setBlendMode(_multiply())),
                ("opacity", lambda: layer.renderer().setOpacity(self.opacity))]

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsRasterLayer):
            return
        # Each step is independent: one API mismatch shouldn't lose the rest.
        for label, step in self.steps(layer):
            try:
                step()
            except Exception as e:  # noqa: BLE001
                feedback.reportError(f"{self.label}: could not set {label} ({e})", False)
        layer.triggerRepaint()
        _save_raster_style(layer, feedback, self.label)
        self.run[self.role + "_id"] = layer.id()
        _schedule_finish(self.run, context.project() or QgsProject.instance())

    @classmethod
    def create(cls, *args):
        inst = cls(*args)
        _KEEP_ALIVE.append(inst)
        return inst


class ShadeStyler(_RasterStyler):
    """The lifted hillshade. Its opacity is the shading strength, since under
    Multiply opacity s gives color * (1 - s * (1 - shade))."""
    role, label = "shade", "Shaded faces"


class PaperStyler(_RasterStyler):
    """The paper texture, shown as a contrast-stretched grayscale."""
    role, label = "paper", "Paper texture"

    def steps(self, layer):
        def grayscale():
            layer.hueSaturationFilter().setGrayscaleMode(_try(
                lambda: QgsHueSaturationFilter.GrayscaleMode.GrayscaleLuminosity,
                lambda: QgsHueSaturationFilter.GrayscaleLuminosity))

        def contrast():
            layer.setContrastEnhancement(
                _try(lambda: QgsContrastEnhancement.ContrastEnhancementAlgorithm
                     .StretchToMinimumMaximum,
                     lambda: QgsContrastEnhancement.StretchToMinimumMaximum),
                _try(lambda: Qgis.RasterRangeLimit.MinimumMaximum,
                     lambda: QgsRasterMinMaxOrigin.Limits.MinMax,
                     lambda: QgsRasterMinMaxOrigin.MinMax))

        return [("grayscale", grayscale), ("contrast stretch", contrast)] + super().steps(layer)


# =============================================================================
# Algorithm
# =============================================================================

class StackedTerrain(QgsProcessingAlgorithm):
    DEM = "DEM"
    AOI = "AOI"
    INTERVAL = "INTERVAL"
    LEVELS = "LEVELS"
    EXAG = "EXAG"
    VIEW = "VIEW"
    VIEW_AZ = "VIEW_AZ"
    VIEW_ALT = "VIEW_ALT"
    VEXAG = "VEXAG"
    LIGHT_AZ = "LIGHT_AZ"
    LIFT = "LIFT"
    CELL = "CELL"
    SMOOTH = "SMOOTH"
    SPECK = "SPECK"
    HL = "HL"
    RAMP = "RAMP"
    STYLE = "STYLE"
    SHADE = "SHADE"
    WALLS3D = "WALLS3D"
    SHADE_STRENGTH = "SHADE_STRENGTH"
    SATURATION = "SATURATION"
    PAPER = "PAPER"
    PAPER_OPACITY = "PAPER_OPACITY"
    ARCGIS = "ARCGIS"
    OUTPUT = "OUTPUT"
    PAPER_OUT = "PAPER_OUT"
    SHADE_OUT = "SHADE_OUT"
    ARCGIS_OUT = "ARCGIS_OUT"

    def tr(self, s):
        return QCoreApplication.translate("StackedTerrain", s)

    def createInstance(self):
        return StackedTerrain()

    def name(self):
        return "stackedterrain"

    def displayName(self):
        return self.tr("Stacked terrain (offset elevation slabs)")

    def group(self):
        return self.tr("Cartography")

    def groupId(self):
        return "cartography"

    def shortHelpString(self):
        return self.tr(
            f"<p><i>Version {VERSION}</i></p>"
            "<p>Builds nested 'at or above' elevation slabs from a DEM and styles "
            "them as stacked layers of paper. Only a DEM is needed; save the output "
            "as a .gpkg to keep the style with the file.</p>"
            "<p><b>Style</b>: Offset slabs, Paper layers or Cut card. <b>Shade slab "
            "faces</b> adds a hillshade that follows the slabs.</p>"
            "<p><b>View</b>: <i>Oblique</i> lifts each slab away from the viewer so "
            "its walls show; set the azimuth you view from and, optionally, a view "
            "elevation angle (lower = more lift) with a vertical exaggeration. "
            "<i>Nadir</i> looks straight down: slabs stay in place and depth comes "
            "from shadows cast away from the light. Settings that don't apply to "
            "the current choices are greyed out.</p>"
            "<p><b>After a run</b>, adjust the look in Layer Properties &gt; "
            "Variables: stack_exag (lift, 0 = nadir), stack_view_az and "
            "stack_light_az (view and light azimuths), stack_wall (wall darkness, "
            "100 = none), stack_edge (edge line, mm), stack_shadow (shadow opacity, "
            "0-1), stack_wall_steps (1 = flat wall, up to 12 = graded), stack_hl "
            "(highlight, mm), stack_ramp (color ramp name), stack_base / stack_top "
            "(color range). Shading strength is the shading layer's opacity.</p>"
            "<p><b>ArcGIS Pro</b>: the export writes an ArcGIS folder with the lift "
            "and walls built into the geometry; rerun to change the look there.</p>")

    def initAlgorithm(self, config=None):
        Num = QgsProcessingParameterNumber

        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM, self.tr("DEM")))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.AOI, self.tr("Clip to area of interest (optional)"),
            [QgsProcessing.TypeVectorPolygon], optional=True))

        self.addParameter(QgsProcessingParameterEnum(
            self.STYLE, self.tr("Style"), options=[self.tr(label) for _, label in STYLES],
            defaultValue=[key for key, _ in STYLES].index(DEFAULT_STYLE)))
        self.addParameter(QgsProcessingParameterEnum(
            self.RAMP, self.tr("Color ramp"),
            options=[self.tr(f"{r} (fixed elevations, metres)") if r in ELEVATION_RAMPS
                     else r for r in RAMPS],
            defaultValue=RAMPS.index(DEFAULT_RAMP)))

        view = QgsProcessingParameterEnum(
            self.VIEW, self.tr("View"), options=[self.tr(label) for _, label in VIEWS],
            defaultValue=0)
        view.setMetadata({"widget_wrapper": {"class": _LinkedWidgets}})
        self.addParameter(view)
        self.addParameter(Num(
            self.VIEW_AZ, self.tr("Oblique view from azimuth, degrees (180 = from the south)"),
            Num.Double, DEFAULT_VIEW_AZ, minValue=0, maxValue=360))
        self.addParameter(Num(
            self.VIEW_ALT, self.tr("Oblique view elevation angle, degrees (0 = auto)"),
            Num.Double, 0, minValue=0, maxValue=89))
        self.addParameter(Num(
            self.VEXAG, self.tr("Vertical exaggeration (with a view elevation angle)"),
            Num.Double, 1, minValue=0.01))
        self.addParameter(Num(
            self.LIGHT_AZ, self.tr("Light from azimuth, degrees (shadows, highlights, shading)"),
            Num.Double, DEFAULT_LIGHT_AZ, minValue=0, maxValue=360))

        self.addParameter(QgsProcessingParameterBoolean(
            self.SHADE, self.tr("Shade slab faces with a hillshade"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.WALLS3D, self.tr("Graded 3-D walls (oblique only; slower rendering)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterFile(
            self.PAPER, self.tr("Paper texture image (optional)"), optional=True,
            fileFilter="Images (*.png *.jpg *.jpeg *.tif *.tiff);;All files (*.*)"))
        self.addParameter(Num(
            self.PAPER_OPACITY, self.tr("Paper texture opacity (0-1)"),
            Num.Double, 0.2, minValue=0, maxValue=1))
        self.addParameter(Num(
            self.SATURATION, self.tr("Saturation change, % (-100 = grayscale, 0 = none)"),
            Num.Double, 0, minValue=-100, maxValue=0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.ARCGIS, self.tr("Also export for ArcGIS Pro (needs a file output)"),
            defaultValue=False))

        self.addParameter(_advanced(Num(
            self.INTERVAL, self.tr("Contour interval (0 = auto)"),
            Num.Double, 0, minValue=0)))
        self.addParameter(_advanced(Num(
            self.LEVELS, self.tr("Target number of levels (when the interval is auto)"),
            Num.Integer, 25, minValue=3, maxValue=100)))
        self.addParameter(_advanced(Num(
            self.SHADE_STRENGTH, self.tr("Shading strength (0-1)"),
            Num.Double, 0.45, minValue=0, maxValue=1)))
        self.addParameter(_advanced(Num(
            self.HL, self.tr("Highlight offset, mm (empty = style default)"),
            Num.Double, None, optional=True, minValue=0, maxValue=2)))
        self.addParameter(_advanced(Num(
            self.EXAG, self.tr("Lift per unit of elevation, overrides the view angle "
                               "(0 = from the view angle, or auto)"),
            Num.Double, 0, minValue=0)))
        self.addParameter(_advanced(Num(
            self.LIFT, self.tr("Auto lift: total lift as fraction of map "
                               "height (empty = style default)"),
            Num.Double, None, optional=True, minValue=0.005, maxValue=0.5)))
        self.addParameter(_advanced(Num(
            self.CELL, self.tr("Generalization cell size, map units (0 = auto)"),
            Num.Double, 0, minValue=0)))
        self.addParameter(_advanced(Num(
            self.SMOOTH, self.tr("Polygon smoothing iterations (0 = none)"),
            Num.Integer, 2, minValue=0, maxValue=10)))
        self.addParameter(_advanced(Num(
            self.SPECK, self.tr("Remove specks and holes smaller than N cells"),
            Num.Double, 4, minValue=0)))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Stacked terrain"), QgsProcessing.TypeVectorPolygon))

        self.addOutput(QgsProcessingOutputRasterLayer(self.PAPER_OUT, self.tr("Paper texture")))
        self.addOutput(QgsProcessingOutputRasterLayer(self.SHADE_OUT, self.tr("Shaded faces")))
        self.addOutput(QgsProcessingOutputFile(self.ARCGIS_OUT, self.tr("ArcGIS Pro layer file")))
        for name, label in [("USED_INTERVAL", "Contour interval used"),
                            ("USED_EXAGGERATION", "Exaggeration used"),
                            ("USED_CELL_SIZE", "Generalization cell size used"),
                            ("BASE_ELEV", "Lowest slab elevation"),
                            ("TOP_ELEV", "Highest slab elevation")]:
            self.addOutput(QgsProcessingOutputNumber(name, self.tr(label)))

    # -------------------------------------------------------------------------

    def processAlgorithm(self, parameters, context, feedback):
        dem = self.parameterAsRasterLayer(parameters, self.DEM, context)
        if dem is None:
            raise QgsProcessingException("Invalid DEM")
        aoi = self.parameterAsSource(parameters, self.AOI, context)

        interval = self.parameterAsDouble(parameters, self.INTERVAL, context)
        levels = self.parameterAsInt(parameters, self.LEVELS, context)
        exag = self.parameterAsDouble(parameters, self.EXAG, context)
        view = VIEWS[self.parameterAsEnum(parameters, self.VIEW, context)][0]
        view_az = self.parameterAsDouble(parameters, self.VIEW_AZ, context) % 360
        view_alt = self.parameterAsDouble(parameters, self.VIEW_ALT, context)
        vexag = self.parameterAsDouble(parameters, self.VEXAG, context)
        light_az = self.parameterAsDouble(parameters, self.LIGHT_AZ, context) % 360
        style = STYLES[self.parameterAsEnum(parameters, self.STYLE, context)][0]
        style_defaults = STYLE_DEFAULTS[style]
        # Optional numbers: empty means "use the style's default".
        lift = (self.parameterAsDouble(parameters, self.LIFT, context)
                if parameters.get(self.LIFT) not in (None, "") else style_defaults["lift"])
        cell = self.parameterAsDouble(parameters, self.CELL, context)
        smooth = self.parameterAsInt(parameters, self.SMOOTH, context)
        speck_cells = self.parameterAsDouble(parameters, self.SPECK, context)
        hl = (self.parameterAsDouble(parameters, self.HL, context)
              if parameters.get(self.HL) not in (None, "")
              else max(style_defaults["stack_hl"], NADIR_HL) if view == "nadir"
              else style_defaults["stack_hl"])
        ramp = RAMPS[self.parameterAsEnum(parameters, self.RAMP, context)]
        shade = self.parameterAsBool(parameters, self.SHADE, context)
        walls3d = self.parameterAsBool(parameters, self.WALLS3D, context)
        shade_strength = self.parameterAsDouble(parameters, self.SHADE_STRENGTH, context)
        saturation = self.parameterAsDouble(parameters, self.SATURATION, context)
        paper_src = self.parameterAsFile(parameters, self.PAPER, context)
        paper_opacity = self.parameterAsDouble(parameters, self.PAPER_OPACITY, context)
        arcgis = self.parameterAsBool(parameters, self.ARCGIS, context)

        # --- 1. Target CRS: a projected CRS when the DEM is geographic -------
        # Prefer the project's CRS if it is equivalent to the local UTM zone
        # (so the project needn't be switched afterwards); otherwise auto UTM.
        src_crs = dem.crs()
        if src_crs.isGeographic():
            to_wgs = QgsCoordinateTransform(
                src_crs, QgsCoordinateReferenceSystem("EPSG:4326"),
                context.transformContext())
            c = to_wgs.transform(dem.extent().center())
            zone = int((c.x() + 180) / 6) + 1
            epsg = (32600 if c.y() >= 0 else 32700) + zone
            target_crs = QgsCoordinateReferenceSystem(f"EPSG:{epsg}")
            project = context.project()
            proj_crs = project.crs() if project else QgsCoordinateReferenceSystem()
            if proj_crs.isValid() and not proj_crs.isGeographic():
                utm_center = QgsCoordinateTransform(
                    src_crs, target_crs, context.transformContext()
                ).transform(dem.extent().center())
                if _crs_mismatch(target_crs, proj_crs, utm_center,
                                 context.transformContext()) <= CRS_TOLERANCE:
                    target_crs = proj_crs
            feedback.pushInfo(f"DEM is geographic; reprojecting to {target_crs.authid()}"
                              + (" (project CRS)" if target_crs == proj_crs else ""))
            # Native pixel size in target units: one DEM pixel, transformed.
            to_target = QgsCoordinateTransform(src_crs, target_crs, context.transformContext())
            p0 = dem.extent().center()
            a = to_target.transform(p0)
            b = to_target.transform(QgsPointXY(p0.x() + dem.rasterUnitsPerPixelX(), p0.y()))
            native = math.hypot(b.x() - a.x(), b.y() - a.y())
        else:
            target_crs = src_crs
            native = dem.rasterUnitsPerPixelX()

        dem_ext = QgsCoordinateTransform(
            src_crs, target_crs, context.transformContext()
        ).transformBoundingBox(dem.extent())
        ext = dem_ext

        # The AOI as one polygon in the target CRS. The slabs are clipped to it
        # as vectors in stage 6; the raster clip in stage 3 is only padding.
        aoi_geom = None
        if aoi is not None:
            request = QgsFeatureRequest().setDestinationCrs(
                target_crs, context.transformContext())
            geoms = [f.geometry() for f in aoi.getFeatures(request)
                     if f.hasGeometry()]
            if geoms:
                aoi_geom = QgsGeometry.unaryUnion(geoms).makeValid()
            if aoi_geom is None or aoi_geom.isEmpty():
                raise QgsProcessingException("The AOI layer has no polygons")
            ext = ext.intersect(aoi_geom.boundingBox())
            if ext.isEmpty():
                raise QgsProcessingException("The AOI does not overlap the DEM")

        if cell <= 0:
            cell = max(native, ext.width() / 1000.0)
        feedback.pushInfo(f"Generalization cell size: {cell:.1f}")

        # With an AOI, generalize a few cells beyond it so the contours have
        # real data right up to its edge (otherwise the edge is traced along
        # the nodata cells and comes out stair-stepped).
        pad = 3 * cell
        warp_ext = ext
        if aoi_geom is not None:
            warp_ext = ext.buffered(pad).intersect(dem_ext)

        # --- 2. Generalize (average resample -> smooth, sinuous contours) ----
        feedback.setProgressText("Generalizing DEM")
        warped = processing.run("gdal:warpreproject", {
            "INPUT": dem, "TARGET_CRS": target_crs,
            "RESAMPLING": 5,  # Average
            "TARGET_RESOLUTION": cell,
            "TARGET_EXTENT": warp_ext, "TARGET_EXTENT_CRS": target_crs,
            # Without an explicit nodata, cells outside a reprojected DEM's
            # footprint become 0 and turn into a fake sea-level slab.
            "NODATA": -9999,
            "OUTPUT": "TEMPORARY_OUTPUT",
        }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
        if feedback.isCanceled():
            return {}

        # --- 3. Optional AOI clip (padded; the exact clip is in stage 6) -----
        if aoi_geom is not None:
            feedback.setProgressText("Clipping to area of interest")
            mask_layer = QgsVectorLayer("MultiPolygon", "aoi_padded", "memory")
            mask_layer.setCrs(target_crs)
            mask_feat = QgsFeature()
            mask_feat.setGeometry(aoi_geom.buffer(pad, 8))
            mask_layer.dataProvider().addFeatures([mask_feat])
            warped = processing.run("gdal:cliprasterbymasklayer", {
                "INPUT": warped, "MASK": mask_layer,
                "CROP_TO_CUTLINE": True, "KEEP_RESOLUTION": True, "NODATA": -9999,
                "OUTPUT": "TEMPORARY_OUTPUT",
            }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
            if feedback.isCanceled():
                return {}

        # --- 4. Auto interval / exaggeration ---------------------------------
        stats = processing.run("native:rasterlayerstatistics", {
            "INPUT": warped, "BAND": 1,
        }, context=context, feedback=feedback, is_child_algorithm=True)
        zmin, zmax = stats["MIN"], stats["MAX"]
        zrange = zmax - zmin
        if zrange <= 0:
            raise QgsProcessingException("DEM has no elevation range in this area")

        if interval <= 0:
            interval = _nice(zrange / levels)
        feedback.pushInfo(f"Style {style}; elevation {zmin:.0f}-{zmax:.0f}, "
                          f"interval {interval}")

        # Settings that don't apply to the chosen view are ignored; say so,
        # since only the dialog greys them out (not models or scripts).
        ignored = []
        if view == "nadir":
            ignored = [label for label, used in [
                (f"view azimuth {view_az:g}", view_az != DEFAULT_VIEW_AZ),
                (f"view elevation angle {view_alt:g}", view_alt > 0),
                (f"vertical exaggeration {vexag:g}", vexag != 1),
                (f"lift per unit of elevation {exag:g}", exag > 0),
                ("graded walls", walls3d)] if used]
            reason = "a nadir view has no lift or walls"
        elif exag > 0:
            ignored = [label for label, used in [
                (f"view elevation angle {view_alt:g}", view_alt > 0),
                (f"vertical exaggeration {vexag:g}", vexag != 1)] if used]
            reason = "Lift per unit of elevation is set"
        elif view_alt <= 0 and vexag != 1:
            ignored = [f"vertical exaggeration {vexag:g}"]
            reason = "it only applies with a view elevation angle"
        if ignored:
            _warn(feedback, f"Ignored {', '.join(ignored)}: {reason}")

        # Lift per unit of elevation. In a parallel view from elevation angle
        # e, a point h above the base appears h x VE / tan(e) further away.
        if view == "nadir":
            exag = 0.0
            feedback.pushInfo(f"Nadir view, light from azimuth {light_az:g}: slabs "
                              f"stay in place, depth comes from shadows and highlights")
        else:
            if exag > 0:
                how = "set directly"
            elif view_alt > 0:
                exag = _round_sig(vexag / math.tan(math.radians(view_alt)), 3)
                how = (f"from a view elevation of {view_alt:g} degrees and "
                       f"vertical exaggeration {vexag:g}")
            else:
                exag = _round_sig(lift * ext.height() / zrange, 3)
                how = (f"auto: a view from {math.degrees(math.atan(1 / exag)):.0f} "
                       f"degrees with no vertical exaggeration, or "
                       f"{math.degrees(math.atan(2 / exag)):.0f} with 2x")
            feedback.pushInfo(f"Oblique view from azimuth {view_az:g}, light from "
                              f"{light_az:g}; lift per unit of elevation {exag} ({how})")

        # Elevation ramps are pinned to metres; a DEM in feet (or a region far
        # outside the ramp's range) paints nearly every slab one color.
        if ramp in ELEVATION_RAMPS:
            lo, hi = ELEVATION_RAMPS[ramp][0][0], ELEVATION_RAMPS[ramp][-1][0]
            if zmax > 4500:
                _warn(feedback, f"Elevations reach {zmax:.0f}, but the {ramp} ramp expects "
                                f"metres ({lo:g}-{hi:g}). If the DEM is in feet, nearly "
                                f"every slab gets the ramp's top color; choose another ramp.")
            elif zmin >= hi or zmax <= lo:
                _warn(feedback, f"The {ramp} ramp covers {lo:g}-{hi:g} m and this area "
                                f"is {zmin:.0f}-{zmax:.0f}, so every slab gets one color.")

        # --- 5. Contour bands -> fixed geometries ----------------------------
        feedback.setProgressText("Building contour bands")
        bands_out = processing.run("gdal:contour_polygon", {
            "INPUT": warped, "BAND": 1, "INTERVAL": interval,
            "FIELD_NAME_MIN": "ELEV_MIN", "FIELD_NAME_MAX": "ELEV_MAX",
            "OUTPUT": "TEMPORARY_OUTPUT",
        }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
        if feedback.isCanceled():
            return {}
        fixed = processing.run("native:fixgeometries", {
            "INPUT": bands_out, "OUTPUT": "TEMPORARY_OUTPUT",
        }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
        band_layer = QgsProcessingUtils.mapLayerFromString(fixed, context)
        if band_layer is None:
            raise QgsProcessingException("Could not read contour bands")

        # gdal_contour gives the lowest band the raster minimum as ELEV_MIN
        # (e.g. 1837.4). Snap every level down to the interval grid so the
        # first step up is a full interval, like its wall.
        bands = {}
        for f in band_layer.getFeatures():
            lvl, g = f["ELEV_MIN"], f.geometry()
            if lvl is None or g is None or g.isEmpty():
                continue
            lvl = math.floor(float(lvl) / interval + 1e-9) * interval
            bands[lvl] = bands[lvl].combine(g) if lvl in bands else QgsGeometry(g)
        if not bands:
            raise QgsProcessingException("No contour bands were produced")

        # --- 6. Cumulative slabs: everything at or above each level ----------
        # Built top-down so each step is one union with the running total.
        # The lowest slab covers the full extent, which gives solid walls along
        # the map edges instead of gaps.
        feedback.setProgressText("Stacking slabs")
        min_area = speck_cells * cell * cell
        tolerance = SIMPLIFY_CELLS * cell
        feedback.pushInfo(f"Simplifying slab outlines to {tolerance:.1f} map units")
        slabs, acc = [], None
        levels_desc = sorted(bands, reverse=True)
        # With an AOI the padding can dip below the AOI's lowest level, which
        # would add lower slabs identical to the full AOI (a duplicate floor
        # that also drags the color ramp's base down). Stop at the first slab
        # that already covers all of the AOI the DEM reaches.
        full_area = None
        if aoi_geom is not None:
            full_area = aoi_geom.intersection(QgsGeometry.fromRect(dem_ext)).area()
        for i, lvl in enumerate(levels_desc):
            if feedback.isCanceled():
                return {}
            acc = QgsGeometry(bands[lvl]) if acc is None else acc.combine(bands[lvl])
            g = _clean(acc, min_area, smooth, aoi_geom, tolerance)
            if g is not None:
                slabs.append((lvl, g))
                if full_area and g.area() >= full_area * (1 - 1e-6):
                    break
            feedback.setProgress(int(100 * (i + 1) / len(levels_desc)))
        if not slabs:
            raise QgsProcessingException("All slabs were removed; lower the speck size")
        slabs.sort(key=lambda s: s[0])

        fields = QgsFields()
        fields.append(_field("LEVEL", "int"))
        fields.append(_field("ELEV_MIN", "double"))
        parameters = dict(parameters)
        parameters[self.OUTPUT], folder = _in_own_folder(parameters.get(self.OUTPUT))
        if folder:
            feedback.pushInfo(f"Writing all files to {folder}")
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, _multipolygon(), target_crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        for n, (lvl, g) in enumerate(slabs):
            feat = QgsFeature(fields)
            feat.setGeometry(g)
            feat.setAttributes([n, lvl])
            sink.addFeature(feat)

        base, top = slabs[0][0], slabs[-1][0]
        variables = {
            "stack_base": base,
            "stack_top": top,
            "stack_interval": interval,
            "stack_exag": exag,
            "stack_ramp": ramp,
            "stack_hl": hl,
            "stack_style": style,
            "stack_wall": style_defaults["stack_wall"],
            "stack_edge": style_defaults["stack_edge"],
            "stack_shadow": (max(style_defaults["stack_shadow"], NADIR_SHADOW)
                             if view == "nadir" else style_defaults["stack_shadow"]),
            "stack_wall_steps": GRADED_WALL_STEPS if walls3d else 1,
            "stack_view_az": view_az,
            "stack_light_az": light_az,
        }
        results = {
            self.OUTPUT: dest_id,
            "USED_INTERVAL": interval,
            "USED_EXAGGERATION": exag,
            "USED_CELL_SIZE": cell,
            "BASE_ELEV": base,
            "TOP_ELEV": top,
        }

        # File outputs are named after the file (Hermosa.gpkg -> "Hermosa"),
        # temporary ones "Stacked terrain". forceName stops QGIS's "Prefer
        # output filename" setting from overriding either.
        dest_file = dest_id.split("|")[0]
        stem = (os.path.splitext(os.path.basename(dest_file))[0]
                if os.path.isfile(dest_file) else None)

        # Shared by the post-processors: layer ids (ShadeStyler needs the
        # terrain's; the bundle needs all) and which outputs to expect.
        run = {"name": stem or "Stacked terrain", "expect": {"terrain"},
               "qlr": os.path.join(folder, stem + ".qlr") if folder else None}
        if context.willLoadLayerOnCompletion(dest_id):
            details = context.layerToLoadOnCompletionDetails(dest_id)
            details.name = stem or "Stacked terrain"
            details.forceName = True
            details.setPostProcessor(
                TerrainStyler.create(variables, saturation, run))

        # --- 7. Optional paper texture ---------------------------------------
        if paper_src:
            paper_box = _drawn_extent(slabs[0][1].boundingBox(), (top - base) * exag,
                                      interval * exag, view_az)
            # Half a cell is fine enough: the edge is softened when the mask
            # is resampled to the image. Grown by a cell for edges and shadows.
            px = max(cell / 2.0, math.sqrt(paper_box.width() * paper_box.height() / 16e6))
            try:
                paper_path = self._paper_texture(
                    paper_src, dest_id, paper_box,
                    _stack_mask(slabs, base, exag, interval * exag, view_az, paper_box,
                                px, cell, _crs_wkt(target_crs)),
                    target_crs, feedback)
            except Exception as e:  # noqa: BLE001  the slabs are still good
                feedback.reportError(f"Could not add the paper texture: {e}", False)
                paper_path = None
            if paper_path:
                details = QgsProcessingContext.LayerDetails(
                    f"{stem} paper texture" if stem else "Paper texture",
                    context.project(), self.PAPER_OUT)
                details.forceName = True
                details.setPostProcessor(PaperStyler.create(paper_opacity, run))
                context.addLayerToLoadOnCompletion(paper_path, details)
                run["expect"].add("paper")
                results[self.PAPER_OUT] = paper_path

        # --- 8. Optional shaded faces ----------------------------------------
        if shade:
            feedback.setProgressText("Shading slab faces")
            try:
                shade_path = self._lifted_hillshade(
                    warped, slabs, base, exag, view_az, light_az, cell, target_crs,
                    _output_path(dest_id, "_shade.tif", "stacked_terrain_shade.tif"),
                    feedback)
            except Exception as e:  # noqa: BLE001  the slabs are still good
                feedback.reportError(f"Could not shade slab faces: {e}", False)
                shade_path = None
            if shade_path:
                feedback.pushInfo(
                    f"Shading matches exaggeration {exag}, view azimuth {view_az:g} "
                    f"and light azimuth {light_az:g}; rerun if you change stack_exag, "
                    f"stack_view_az or stack_light_az. Its strength is the shading "
                    f"layer's opacity.")
                details = QgsProcessingContext.LayerDetails(
                    f"{stem} shading" if stem else "Shaded faces",
                    context.project(), self.SHADE_OUT)
                details.forceName = True
                details.setPostProcessor(ShadeStyler.create(shade_strength, run))
                context.addLayerToLoadOnCompletion(shade_path, details)
                run["expect"].add("shade")
                results[self.SHADE_OUT] = shade_path

        # --- 9. Optional ArcGIS Pro copy -------------------------------------
        if arcgis and not folder:
            feedback.reportError("ArcGIS Pro export needs a file output (e.g. .gpkg); "
                                 "skipped for this temporary output", False)
        elif arcgis:
            feedback.setProgressText("Exporting for ArcGIS Pro")
            try:
                results[self.ARCGIS_OUT] = export_arcgis(
                    os.path.join(folder, "ArcGIS"), stem, slabs, variables, saturation,
                    target_crs,
                    shade=(shade_path, shade_strength) if shade and shade_path else None,
                    paper=(paper_path, paper_opacity) if paper_src and paper_path else None,
                    feedback=feedback)
            except Exception as e:  # noqa: BLE001  the QGIS outputs are still good
                feedback.reportError(f"Could not export for ArcGIS Pro: {e}", False)

        return results

    # -------------------------------------------------------------------------

    def _paper_texture(self, src, dest_id, bbox, mask, crs, feedback):
        """Stretch an image over bbox, the drawn extent of the slabs (lift and
        walls included), with mask (from _stack_mask) as its alpha band so the
        paper only covers the stack, not the whole rectangle. The image's own
        values are kept, so the contrast stretch is the same as without it.
        Returns the GeoTIFF path, or None on failure."""
        import numpy as np
        from osgeo import gdal

        out = _output_path(dest_id, "_paper.tif", "stacked_terrain_paper.tif")

        feedback.setProgressText("Georeferencing paper texture")
        ds = gdal.Open(src)
        if ds is None:
            feedback.reportError(f"Could not open paper texture: {src}", False)
            return None
        if ds.GetRasterBand(1).GetColorTable() is not None:
            ds = gdal.Translate("", ds, format="VRT", rgbExpand="rgba")
        bands = [ds.GetRasterBand(i + 1) for i in range(ds.RasterCount)]
        is_alpha = [b.GetColorInterpretation() == gdal.GCI_AlphaBand for b in bands]
        alpha = [b for b, x in zip(bands, is_alpha) if x]
        color = [b for b, x in zip(bands, is_alpha) if not x][:3]
        w, h = ds.RasterXSize, ds.RasterYSize

        def as_bytes(band):  # 16-bit and float images scaled to 0-255
            a = band.ReadAsArray()
            if a.dtype == np.uint8:
                return a
            a = a.astype("float32")
            lo, hi = float(a.min()), float(a.max())
            return ((a - lo) * (255.0 / (hi - lo)) if hi > lo else a * 0 + 255).astype("uint8")

        # The mask, resampled to the image with a soft edge.
        mds = gdal.GetDriverByName("MEM").Create("", mask.shape[1], mask.shape[0], 1, gdal.GDT_Byte)
        mds.GetRasterBand(1).WriteArray(mask)
        # (Keep each dataset in a variable: a band outlives an unreferenced one.)
        sized = gdal.Translate("", mds, format="MEM", width=w, height=h,
                               resampleAlg="bilinear")
        a = sized.GetRasterBand(1).ReadAsArray()
        sized = mds = None
        if alpha:
            a = (a.astype("uint16") * as_bytes(alpha[0]) // 255).astype("uint8")

        rgb = len(color) == 3
        out_ds = gdal.GetDriverByName("GTiff").Create(
            out, w, h, len(color) + 1, gdal.GDT_Byte,
            options=["COMPRESS=DEFLATE", "ALPHA=YES",
                     "PHOTOMETRIC=" + ("RGB" if rgb else "MINISBLACK")])
        if out_ds is None:
            feedback.reportError(f"Could not write paper texture: {out}", False)
            return None
        out_ds.SetGeoTransform((bbox.xMinimum(), bbox.width() / w, 0,
                                bbox.yMaximum(), 0, -bbox.height() / h))
        out_ds.SetProjection(_crs_wkt(crs))
        interps = ([gdal.GCI_RedBand, gdal.GCI_GreenBand, gdal.GCI_BlueBand] if rgb
                   else [gdal.GCI_GrayIndex] * len(color))
        for i, (band, interp) in enumerate(zip(color, interps)):
            out_ds.GetRasterBand(i + 1).WriteArray(as_bytes(band))
            out_ds.GetRasterBand(i + 1).SetColorInterpretation(interp)
        out_ds.GetRasterBand(len(color) + 1).WriteArray(a)
        out_ds.GetRasterBand(len(color) + 1).SetColorInterpretation(gdal.GCI_AlphaBand)
        out_ds = ds = None  # close and flush
        return out

    def _lifted_hillshade(self, dem_path, slabs, base, exag, view_az, light_az,
                          cell, crs, out, feedback):
        """Hillshade that lines up with the lifted slabs.

        A plain hillshade can't follow the symbology, because each slab is
        shifted away from the viewer by its own lift. So the ground under each
        slab is shaded and pasted at that slab's lifted position, lowest slab
        first, as the renderer draws them. The DEM is averaged to 3 cells
        first so the shading stays broad and soft. Returns the GeoTIFF path.
        """
        import numpy as np
        from osgeo import gdal

        top = slabs[-1][0]
        ground = slabs[0][1].boundingBox()  # the lowest slab covers every other
        ux, uy = _lift_vector(view_az)
        bbox = _drawn_extent(ground, (top - base) * exag, 0, view_az)
        px = cell / 3.0
        # Keep the grid under ~25 megapixels for very large areas.
        px = max(px, math.sqrt(bbox.width() * bbox.height() / 25e6))
        w = int(math.ceil(bbox.width() / px))
        h = int(math.ceil(bbox.height() / px))
        xmin, ytop = bbox.xMinimum(), bbox.yMinimum() + h * px
        bounds = (xmin, ytop - h * px, xmin + w * px, ytop)
        wkt = _crs_wkt(crs)

        coarse = gdal.Warp("", dem_path, format="MEM", xRes=3 * cell, yRes=3 * cell,
                           outputBounds=(bbox.xMinimum() - 6 * cell, bbox.yMinimum() - 6 * cell,
                                         bbox.xMaximum() + 6 * cell, bbox.yMaximum() + 6 * cell),
                           resampleAlg="average", dstNodata=-9999)
        # Fill nodata (AOI and DEM edges) so the edge cells don't shade black.
        gdal.FillNodata(coarse.GetRasterBand(1), None, 20, 0)
        hs = gdal.DEMProcessing("", coarse, "hillshade", format="MEM", azimuth=light_az,
                                altitude=45, zFactor=1, computeEdges=True)
        fine = gdal.Warp("", hs, format="MEM", width=w, height=h, outputBounds=bounds,
                         resampleAlg="cubicspline")
        shade = fine.GetRasterBand(1).ReadAsArray().astype("float32")
        coarse = hs = fine = None
        if feedback.isCanceled():
            return None

        # Burn each ground pixel with the ELEV_MIN of the highest slab over it.
        burn = _burn_slabs(slabs, w, h, (xmin, px, 0, ytop, 0, -px), wkt)

        # Paint bottom-up, each slab moved by its lift: dc columns east and dr
        # rows south (row 0 is north, so a northward lift is a negative dr).
        img = np.full((h, w), 255.0, dtype="float32")  # white: neutral under Multiply

        def span(d, size):  # (source, destination) slices for a shift of d
            return (slice(max(0, -d), size - max(0, d)),
                    slice(max(0, d), size - max(0, -d)))

        for lvl, _ in slabs:
            if feedback.isCanceled():
                return None
            lift = (lvl - base) * exag
            dc, dr = int(round(lift * ux / px)), int(round(-lift * uy / px))
            if abs(dc) >= w or abs(dr) >= h:
                continue
            (sr, tr), (sc, tc) = span(dr, h), span(dc, w)
            m = burn[sr, sc] >= lvl - 1e-6
            img[tr, tc][m] = shade[sr, sc][m]

        ds = gdal.GetDriverByName("GTiff").Create(out, w, h, 1, gdal.GDT_Byte,
                                                  options=["COMPRESS=DEFLATE"])
        if ds is None:
            feedback.reportError(f"Could not write shading raster: {out}", False)
            return None
        ds.SetGeoTransform((xmin, px, 0, ytop, 0, -px))
        ds.SetProjection(wkt)
        ds.GetRasterBand(1).WriteArray(img.clip(0, 255).astype("uint8"))
        ds = None  # close and flush
        return out
