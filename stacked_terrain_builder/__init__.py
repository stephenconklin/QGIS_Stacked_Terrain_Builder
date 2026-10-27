"""
Stacked Terrain Builder: a QGIS plugin that adds the Stacked terrain tool to
the Processing Toolbox. The tool itself is stacked_terrain.py, which also
installs on its own as a Processing script.
"""


def classFactory(iface):
    from .plugin import StackedTerrainPlugin
    return StackedTerrainPlugin(iface)
