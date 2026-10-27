import os

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

from .stacked_terrain import VERSION, StackedTerrain

ICON = os.path.join(os.path.dirname(__file__), "icon.png")


class StackedTerrainProvider(QgsProcessingProvider):
    """Processing provider "stackedterrain": the tool's id is
    stackedterrain:stackedterrain (script:stackedterrain when installed as a
    script)."""

    def id(self):
        return "stackedterrain"

    def name(self):
        return "Stacked Terrain"

    def longName(self):
        return f"Stacked Terrain {VERSION}"

    def versionInfo(self):
        return VERSION

    def icon(self):
        return QIcon(ICON)

    def loadAlgorithms(self):
        self.addAlgorithm(StackedTerrain())
