"""Open a prepared operator in Blender 4.5 optionally saving a portable file"""

import argparse
import sys
from pathlib import Path

import bpy

IMPORT_WARNINGS = []

def warning(title, message):
    IMPORT_WARNINGS.append(title + ": " + message)
    print(title + ": " + message, flush=True)

    if not bpy.app.background:
        def draw(self, context):
            self.layout.label(text=message)

        bpy.context.window_manager.popup_menu(draw, title=title, icon="ERROR")

def save_portable_blend(destination, args):
    destination = Path(destination).expanduser().resolve()
    if destination.suffix.lower() != ".blend":
        raise ValueError("Choose a .blend filename")
    if destination.exists() and not destination.is_file():
        raise IsADirectoryError("The Destination is a directory.")
    if not destination.parent.is_dir():
        raise FileNotFoundError("The destination folder does not exist")

    images = [
        image for image in bpy.data.images
        if image.users > 0 and image.source != "VIEWER"
    ]
    for image in images:
        if image.source not in {"FILE", "GENERATED"}:
            raise ValueError(f"Cannot pack this image type: {image.name} ({image.source})")
        if image.source == "FILE" and not image.packed_file:
            path = Path(bpy.path.abspath(image.filepath, library=image.library))
            if not path.is_file():
                raise FileNotFoundError(f"Missing texture: {image.name}")
        if not image.packed_file or image.is_dirty:
            image.pack()
        if not image.packed_file:
            raise RuntimeError(f"Texture packing failed: {image.name}")

    lines = [
        "R6 Portable Blender Export",
        f"Blender: {bpy.app.version_string}",
        f"Rig requested: {'Experimental IK' if args.experimental_ik else 'FK'}",
        f"Imported model files: {len(args.models)}",
        f"Packed images: {len(images)}",
        "Visual appearance and deformation still require manual review.",
        "",
        "Rig warnings:",
        *(IMPORT_WARNINGS or ["None reported."]),
        "",
        "Materials:",
        "Siege materials approximate the game appearance.",
    ]
    for material in bpy.data.materials:
        note = material.get("siegeGlassPreviewNote")
        if note:
            lines.append(f"{material.name}: {note}")
        if material.use_nodes and material.node_tree.nodes.get("Siege Experimental Hair"):
            lines.append(f"{material.name}: Experimental Hair approximation.")

    report = bpy.data.texts.get("R6 Export Report") or bpy.data.texts.new("R6 Export Report")
    report.clear()
    report.write("\n".join(lines) + "\n")
    report.use_fake_user = True

    report.cursor_set(0)
    workspace = bpy.data.workspaces.get("Scripting")
    if workspace is None:
        raise RuntimeError("The Scripting workspace is missing.")

    for screen in workspace.screens:
        for area in screen.areas:
            if area.type == "TEXT_EDITOR":
                space = area.spaces.active
                space.text = report
                space.top = 0
                space.show_word_wrap = True
                space.show_line_numbers = False

    def write_file():
        result = bpy.ops.wm.save_as_mainfile(filepath=str(destination), check_existing=False, compress=True)
        if "FINISHED" not in result:
            raise RuntimeError("Blender did not finish saving the portable file.")
        print(f"R6 portable file saved: {destination}", flush=True)

    window = bpy.context.window
    if not bpy.app.background and window is not None and window.workspace != workspace:
        window.workspace = workspace
        attempts = 0

        def save_after_switch():
            nonlocal attempts
            try:
                if window.workspace != workspace:
                    attempts += 1
                    if attempts < 20:
                        return 0.1
                    raise RuntimeError("Could not activate the Scripting workspace")
                write_file()
            except Exception as error:
                warning("Portable save failed", str(error))
            return None

        bpy.app.timer.register(save_after_switch, first_interval=0.1)
    else:
        write_file()

def main():
    if bpy.app.version[:2] != (4, 5):
        raise RuntimeError("Blender 4.5 is required")

    from io_scene_r6.blender_preview import import_siege_model, connect_fk_head

    parser = argparse.ArgumentParser()
    parser.add_argument("--experimental-ik", action="store_true")
    parser.add_argument("--save-blend", type=Path)
    parser.add_argument("models", nargs="+", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    IMPORT_WARNINGS.clear()

    if any(not path.is_file() for path in args.models):
        raise RuntimeError("One or more exported models are missing")

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    for model in args.models:
        import_siege_model(model)

    if not args.experimental_ik:
        try:
            arm, head_name = connect_fk_head(list(bpy.context.scene.objects))
        except Exception as error:
            warning("FK setup incomplete", str(error))
        else:
            if head_name is None:
                warning("FK head mapping unavailable", "Use the separate face or mechanical controls.")
            else:
                print(f"FK head ready: {head_name}. Select Pose Mode and rotate this bone", flush=True)

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
                warning("IK ready, head remain separate", str(error))
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

    if args.save_blend is not None:
        try:
            save_portable_blend(args.save_blend, args)
        except Exception as error:
            warning("Portable save failed", str(error))
            if bpy.app.background:
                raise

if __name__ == "__main__":
    main()
