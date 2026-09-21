# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import shutil
from PyInstaller.utils.hooks import copy_metadata

project = Path(SPECPATH)

datas = [
    (str(project / "viewer"), "viewer"),
    (str(project / "main.py"), "."),
    (str(project / "blender" / "install_addon.py"), "blender"),
    (str(project / "blender" / "open_operator.py"), "blender"),
    (
        str(project / "blender_addon" / "io_scene_r6" / "__init__.py"),
        "blender_addon/io_scene_r6",
    ),
    (
        str(project / "blender_addon" / "io_scene_r6" / "blender_preview.py"),
        "blender_addon/io_scene_r6",
    ),
    (
        str(project / "blender_addon" / "io_scene_r6" / "blender_ik.py"),
        "blender_addon/io_scene_r6",
    ),
    (str(project / "docs" / "images" / "app.ico"), "docs/images"),
]

for distribution in (
    "Pillow",
    "PySide6",
    "PySide6_Addons",
    "PySide6_Essentials",
    "shiboken6",
):
    datas += copy_metadata(distribution)

analysis = Analysis(
    [str(project / "app.py")],
    pathex=[str(project)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["config"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

gui_exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="R6ForgeExtractor",
    icon=str(project / "docs" / "images" / "app.ico"),
    console=False,
    debug=False,
    strip=False,
    upx=False,
    contents_directory="_internal",
)

worker_exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="R6Worker",
    icon=str(project / "docs" / "images" / "app.ico"),
    console=True,
    debug=False,
    strip=False,
    upx=False,
    contents_directory="_internal",
)

collection = COLLECT(
    gui_exe,
    worker_exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="R6ForgeExtractor",
)

for filename in ("README.md", "LICENSE"):
    shutil.copy2(project / filename, Path(collection.name) / filename)

image_directory = Path(collection.name) / "docs" / "images"
image_directory.mkdir(parents=True, exist_ok=True)
shutil.copy2(
    project / "docs" / "images" / "desktop-ui.png",
    image_directory / "desktop-ui.png",
)

shutil.copy2(
    project / "THIRD_PARTY.md",
    Path(collection.name) / "THIRD_PARTY.md",
)
shutil.copytree(
    project / "licenses",
    Path(collection.name) / "licenses",
    dirs_exist_ok=True,
)