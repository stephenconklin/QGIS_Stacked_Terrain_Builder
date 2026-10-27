from qgis.core import QgsApplication

from .provider import StackedTerrainProvider


class StackedTerrainPlugin:
    """Registers the Processing provider; the plugin adds no menus or toolbars."""

    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    # QGIS calls initProcessing() on its own where there is no GUI
    # (qgis_process), and initGui() in the desktop app.
    def initProcessing(self):
        if self.provider is None:
            self.provider = StackedTerrainProvider()
            QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
