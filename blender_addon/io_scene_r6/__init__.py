"""Import prepare Rainbow Six Siege operator exports"""

from pathlib import Path
import traceback

import bpy
from bpy.props import StringProperty, BoolProperty

from .blender_preview import import_siege_model

bl_info = {
    "name": "Rainbow Six Siege Operator Import",
    "author": "TrueShadow01",
    "version": (0, 1, 0),
    "blender": (4, 5, 0),
    "location": "File > Import > Rainbow Six Siege Operator",
    "description": "Import exported head/body models with Siege Materials",
    "category": "Import-Export",
}

class IMPORT_SCENE_OT_r6_operator(bpy.types.Operator):
    bl_idname = "import_scene.r6_operator"
    bl_label = "Import R6 Operator"
    bl_description = "Choose an exported operator folder containing body and head"
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(name="Operator Folder", subtype="DIR_PATH")
    filter_folder: BoolProperty(default=True, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.directory:
            self.report({"ERROR"}, "Choose an operator folder.")
            return {"CANCELLED"}

        folder = Path(bpy.path.abspath(self.directory)).resolve()
        models = []

        for part in ("body", "head"):
            files = sorted((folder / part).glob("*/*.gltf"))
            if not files:
                self.report({"ERROR"}, f"No {part} models found. Choose the operator folder.")
                return {"CANCELLED"}

            models.extend(files)

        completed = 0
        try:
            for gltf in models:
                import_siege_model(gltf)
                completed += 1
        except Exception:
            traceback.print_exc()
            self.report({"WARNING"}, f"Import stopped after {completed}/{len(models)} models. Partial objects may remain, Undo before retrying. See the system console for details.")
            # Preserve a undo step for changes made before the failure
            return {"FINISHED"}

        self.report({"INFO"}, f"Imported {folder.name}: {completed} models with Siege materials.")
        return {"FINISHED"}

def menu_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_r6_operator.bl_idname, text="Rainbow Six Siege Operator")

def register():
    if bpy.app.version[:2] != (4, 5):
        raise RuntimeError("This alpha add-on targets Blender 4.5 only.")

    bpy.utils.register_class(IMPORT_SCENE_OT_r6_operator)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)

def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.utils.unregister_class(IMPORT_SCENE_OT_r6_operator)