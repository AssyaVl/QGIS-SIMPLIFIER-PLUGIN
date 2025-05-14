from qgis.core import QgsApplication
from .plugin import TopoCartGenPlugin

def classFactory(iface):
    return TopoCartGenPlugin(iface)