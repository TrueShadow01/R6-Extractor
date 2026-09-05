"""Build and install the R6 add-on into Blender 4.5"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

def install(blender):
    blender = Path(blender).resolve()
    if not blender.is_file():
        raise FileNotFoundError(blender)

    version = subprocess.run(
        [str(blender), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    )
    match = re.search(r"^Blender (\d+)\.(\d+)\.(\d+)", version.stdout, re.MULTILINE)
    if not match or tuple(map(int, match.groups()[:2])) != (4, 5):
        raise RuntimeError("Select Blender 4.5, other versions are not supported.")

    print(f"Detected Belnder {'.'.join(match.groups())}", flush=True)

    project = Path(__file__).resolve().parent
    sources = {
        "io_scene_r6/__init__.py": project / "blender_addon" / "io_scene_r6" / "__init__.py",
        "io_scene_r6/blender_preview.py": project / "blender_preview.py",
    }
    for source in sources.values():
        if not source.is_file():
            raise FileNotFoundError(source)

    with tempfile.TemporaryDirectory(prefix="r6-addon-") as temporary:
        archive = Path(temporary) / "io_scene_r6.zip"
        with ZipFile(archive, "w",  ZIP_DEFLATED) as package:
            for destination, source in sources.items():
                package.write(source, destination)

        expression = f"""
import bpy
import addon_utils
import sys

if bpy.app.version[:2] != (4, 5):
    raise RuntimeError("Blender 4.5 is required")

module = "io_scene_r6"
addon_utils.disable(module, default_set=True)

for name in list(sys.modules):
    if name == module or name.startswith(module + "."):
        del sys.modules[name]

result = bpy.ops.preferences.addon_install(filepath={str(archive)!r}, overwrite=True)
if "FINISHED" not in result:
    raise RuntimeError("Add-on installation failed")

result = bpy.ops.preferences.addon_enable(module=module)
if "FINISHED" not in result or not addon_utils.check(module)[1]:
    raise RuntimeError("Add-on could not be enabled")

result = bpy.ops.wm.save_userpref()
if "FINISHED" not in result:
    raise RuntimeError("Could not save Blender preferences")

print("R6 add-on installed and enabled:", sys.modules[module].__file__)
"""
        print("Installing the current R6 add-on and material helper...", flush=True)
        subprocess.run(
            [
                str(blender),
                "--background",
                "--python-exit-code", "1",
                "--python-expr", expression
            ],
            check=True
        )

    print("Blender 4.5 add-on installation complete.", flush=True)

if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise RuntimeError("Usage: install_blender_addon.py <blender.exe>")
        install(sys.argv[1])
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)