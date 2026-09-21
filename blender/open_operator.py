"""Open prepared operator in a new Blender 4.5 session"""

import argparse
import sys
from pathlib import Path

import bpy

def warning(title, message):
    print(title + ": " + message, flush=True)

    if not bpy.app.background:
        def draw(self, context):
            self.layout.label(text=message)

        bpy.context.window_manager.popup_menu(draw, title=title, icon="ERROR")

def main():
    if bpy.app.version[:2] != (4, 5):
        raise RuntimeError("Blender 4.5 is required")

    from io_scene_r6.blender_preview import import_siege_model

    parser = argparse.ArgumentParser()
    parser.add_argument("--experimental-ik", action="store_true")
    parser.add_argument("models", nargs="+", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])

    if any(not path.is_file() for path in args.models):
        raise RuntimeError("One or more exported models are missing")

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    for model in args.models:
        import_siege_model(model)

    if args.experimental_ik:
        objects = list(bpy.context.scene.objects)

        try:
            from io_scene_r6.blender_ik import create_operator_ik, connect_operator_head, create_head_control

            arm = create_operator_ik(list(bpy.context.scene.objects))
        except Exception as error:
            warning("Operator imported without IK", str(error))
        else:
            try:
                head_name = connect_operator_head(arm, objects)
            except Exception as error:
                warning("IK ready, head remains separate", str(error))
            else:
                try:
                    control = create_head_control(arm, head_name, objects)
                except Exception as error:
                    warning("Head connected, visible control unavailable", str(error))
                else:
                    print("R6 head control ready: "  + control.name + ". Use G and R in Object Mode", flush=True)

            for obj in bpy.context.selected_objects:
                obj.select_set(False)
            arm.select_set(True)
            bpy.context.view_layer.objects.active = arm

            print("R6 experimental IK ready. Save as .blend to retain controls.", flush=True)

    print(f"R6 import complete: {len(args.models)} models.", flush=True)

if __name__ == "__main__":
    main()
