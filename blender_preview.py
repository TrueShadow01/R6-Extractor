"""Render prepared operator glTF files as review thumbnails in Blender"""

from __future__ import annotations

import sys
import bpy
from pathlib import Path
from mathutils import Vector

def script_arguments() -> list[str]:
    try:
        separator = sys.argv.index("--")
    except ValueError:
        return []

    return sys.argv[separator + 1:]

def point_at(obj: bpy.types.Object, target: Vector) -> None:
    direction = target - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

def add_area_light(name: str, location: Vector, target: Vector, energy: float, size: float) -> None:
    bpy.ops.object.light_add(type="AREA", location=location)

    light = bpy.context.object
    light.name = name
    light.data.energy = energy
    light.data.shape = "DISK"
    light.data.size = size

    point_at(light, target)

def mesh_bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    points= [
        obj.matrix_world @ Vector(corner)
        for obj in objects
        for corner in obj.bound_box
    ]

    minimum = Vector(
        tuple(
            min(point[axis] for point in points)
            for axis in range(3)
        )
    )

    maximum = Vector(
        tuple(
            max(point[axis] for point in points)
            for axis in range(3)
        )
    )

    return minimum, maximum

def render_preview(gltf_path: Path, output_path: Path) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)

    bpy.ops.import_scene.gltf(filepath=str(gltf_path))

    meshes = [
        obj
        for obj in bpy.context.scene.objects
        if obj.type == "MESH"
    ]

    if not meshes:
        raise ValueError("glTF contains no mesh objects")

    minimum, maximum = mesh_bounds(meshes)
    center = (minimum + maximum) * 0.5
    dimensions = maximum - minimum
    extent = max(dimensions)

    if extent <= 0.0:
        raise ValueError("Model has zero size bounds")

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = 512
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.render.filepath = str(output_path)

    world = bpy.data.worlds.new("Preview World")
    world.use_nodes = True

    background = world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (
        0.035,
        0.035,
        0.035,
        1.0
    )
    background.inputs["Strength"].default_value = 0.4

    scene.world = world

    camera_data = bpy.data.cameras.new("Preview Camera")
    camera = bpy.data.objects.new("Preview Camera", camera_data)

    scene.collection.objects.link(camera)

    camera_direction = Vector(
        (
            0.0,
            1.0,
            0.25
        )
    ).normalized()

    camera.location = (
        center + camera_direction * extent * 3.0
    )

    camera.data.type = "ORTHO"
    camera.data.ortho_scale = extent * 1.35
    camera.data.clip_start = max(extent * 0.001, 0.0001)
    camera.data.clip_end = extent * 10.0

    point_at(camera, center)

    scene.camera = camera

    light_energy = max(100.0, 450 * extent * extent)
    light_size = max(extent, 0.1)

    add_area_light(
        "Key",
        center + Vector((1.8, -2.5, 2.4)) * extent,
        center,
        light_energy,
        light_size
    )

    add_area_light(
        "Fill",
        center + Vector((-2.0, -1.0, 1.0)) * extent,
        center,
        light_energy * 0.45,
        light_size * 1.5
    )

    add_area_light(
        "Rim",
        center + Vector((0.5, 2.0, 2.0)) * extent,
        center,
        light_energy * 0.65,
        light_size
    )

    render = scene.render
    render.use_stamp = True
    render.use_stamp_date = False
    render.use_stamp_time = False
    render.use_stamp_render_time = False
    render.use_stamp_frame = False
    render.use_stamp_scene = False
    render.use_stamp_camera = False
    render.use_stamp_filename = False
    render.use_stamp_note = True
    render.stamp_note_text = gltf_path.stem
    render.stamp_font_size = 18
    render.stamp_foreground = (1.0, 1.0, 1.0, 1.0)
    render.stamp_background = (0.0, 0.0, 0.0, 0.65)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    bpy.ops.render.render(write_still=True)

def main() -> None:
    arguments = script_arguments()

    if len(arguments) != 1:
        raise ValueError("Expected the prepared preview directory after --")

    preview_directory = Path(arguments[0]).resolve()
    models_directory = preview_directory / "models"
    thumbnails_directory = preview_directory / "thumbnails"

    gltf_paths = tuple(sorted(models_directory.glob("*/*.gltf")))

    if not gltf_paths:
        raise FileNotFoundError(f"No prepared glTF files found under {models_directory}")

    rendered = 0
    resumed = 0
    failures: list[tuple[Path, Exception]] = []

    for gltf_path in gltf_paths:
        output_path = thumbnails_directory / (gltf_path.stem + ".png")

        if output_path.is_file():
            resumed += 1
            print(f"Resumed: {gltf_path.stem}")
            continue

        try:
            render_preview(gltf_path, output_path)
        except Exception as error:
            failures.append(
                (
                    gltf_path,
                    error
                )
            )
            print(f"Failed: {gltf_path.stem}: {error}")
            continue

        rendered += 1
        print(f"Rendered: {gltf_path.stem}")

    print()
    print(f"Rendered: {rendered}")
    print(f"Resumed: {resumed}")
    print(f"Failed: {len(failures)}")
    print(f"Thumbnails: {thumbnails_directory}")

    if failures:
        raise RuntimeError(f"{len(failures)} previews failed")

if __name__ == "__main__":
    main()