"""
Builds the zip to upload to plugins.qgis.org:

    python3 dev/build_plugin.py

writes dist/stacked_terrain_builder-<version>.zip, holding one folder,
stacked_terrain_builder/, with the plugin's files and the repository's LICENSE.
Caches, .DS_Store files and anything else not listed in FILES are left out.
"""
import configparser
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = "stacked_terrain_builder"
FILES = ["__init__.py", "plugin.py", "provider.py", "stacked_terrain.py",
         "metadata.txt", "icon.png"]

meta = configparser.ConfigParser()
meta.read(os.path.join(ROOT, PLUGIN, "metadata.txt"))
version = meta["general"]["version"]

# The plugin and the script must report the same version.
with open(os.path.join(ROOT, PLUGIN, "stacked_terrain.py")) as f:
    script_version = next(line.split('"')[1] for line in f if line.startswith("VERSION = "))
if script_version != version:
    sys.exit(f"metadata.txt says {version} but stacked_terrain.py says {script_version}")

os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
out = os.path.join(ROOT, "dist", f"{PLUGIN}-{version}.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for name in FILES:
        z.write(os.path.join(ROOT, PLUGIN, name), f"{PLUGIN}/{name}")
    z.write(os.path.join(ROOT, "LICENSE"), f"{PLUGIN}/LICENSE")
print(out)
