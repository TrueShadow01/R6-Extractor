"""Render prepared operator glTF files as review thumbnails in Blender"""

from __future__ import annotations

import sys
import bpy
import json
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

def apply_siege_materials(gltf_path: Path) -> None:
    document = json.loads(gltf_path.read_text(encoding="utf-8"))

    extras_by_material = {
        material["name"]: material.get("extras", {})
        for material in document.get("materials", {})
        if "name" in material
    }

    for material in bpy.data.materials:
        extras = extras_by_material.get(material.name, {})

        if not material.use_nodes:
            continue

        nodes = material.node_tree.nodes
        links = material.node_tree.links

        principled = next(
            (
                node
                for node in nodes if node.type == "BSDF_PRINCIPLED"
            ),
            None
        )

        packed_filename = extras.get("siegePackedMaterialTexture")

        if principled is not None and packed_filename is not None:
            packed_path = gltf_path.parent / packed_filename

            if packed_path.is_file():
                image = bpy.data.images.load(str(packed_path), check_existing=True)
                image.colorspace_settings.name = "Non-Color"

                texture = nodes.new("ShaderNodeTexImage")
                texture.name = "Siege Packed Material"
                texture.label = packed_filename
                texture.image = image
                texture.location = (principled.location.x - 600, principled.location.y - 500)

                separate = nodes.new("ShaderNodeSeparateColor")
                separate.name = "Siege Material Channels"
                separate.label = "R: Metalness G: Glossiness B: Cavity"
                separate.location = (principled.location.x - 350, principled.location.y - 500)

                roughness = nodes.new("ShaderNodeMath")
                roughness.name = "Siege Roughness"
                roughness.label = "1 - Glossiness"
                roughness.operation = "SUBTRACT"
                roughness.inputs[0].default_value = 1.0
                roughness.location = (principled.location.x - 120, principled.location.y - 600)

                links.new(texture.outputs["Color"], separate.inputs["Color"])
                links.new(separate.outputs["Green"], roughness.inputs[1])

                metallic_input = principled.inputs.get("Metallic")
                roughness_input = principled.inputs.get("Roughness")

                if metallic_input is not None:
                    if metallic_input.is_linked:
                        links.remove(metallic_input.links[0])

                    links.new(separate.outputs["Red"], metallic_input)

                if roughness_input is not None:
                    if roughness_input.is_linked:
                        links.remove(roughness_input.links[0])

                    links.new(roughness.outputs["Value"], roughness_input)

        if extras.get("siegeShaderUid") == "000000557005948D":
            uniforms = extras.get("siegeShaderUniforms", {})

            sclera_values = uniforms.get(
                "ScleraColorWhite",
                (0.73, 0.73, 0.73, 1.0)
            )

            if principled is not None:
                base_input = principled.inputs.get("Base Color")

                if base_input is not None and base_input.is_linked:
                    base_link = base_input.links[0]
                    base_texture = base_link.from_node
                    alpha_output = base_texture.outputs.get("Alpha")

                    if base_texture.type == "TEX_IMAGE" and alpha_output is not None:
                        links.remove(base_link)

                        eye_mix = nodes.new("ShaderNodeMixRGB")
                        eye_mix.name = "Siege Eye Color"
                        eye_mix.label = "Siege Eye Color"
                        eye_mix.blend_type = "MIX"
                        eye_mix.inputs[0].default_value = 1.0
                        eye_mix.inputs[1].default_value = (
                            0.0,
                            0.0,
                            0.0,
                            1.0
                        )
                        eye_mix.inputs[2].default_value = (
                            float(sclera_values[0]),
                            float(sclera_values[1]),
                            float(sclera_values[2]),
                            1.0
                        )
                        eye_mix.location = (
                            principled.location.x - 300,
                            principled.location.y
                        )

                        links.new(alpha_output, eye_mix.inputs[0])
                        links.new(eye_mix.outputs["Color"], base_input)

        filenames = list(extras.get("siegeDetailNormalTextures", ()))

        shader_textures = extras.get("siegeShaderTextures", {})
        shader_normal = shader_textures.get("NormalDetail")

        if shader_normal is not None and shader_normal not in filenames:
            filenames.append(shader_normal)

        if not filenames:
            continue

        normal_map = next(
            (
                node
                for node in nodes
                if node.type == "NORMAL_MAP"
            ),
            None
        )

        if normal_map is None:
            continue

        color_input = normal_map.inputs.get("Color")

        if color_input is None or not color_input.is_linked:
            continue

        base_link = color_input.links[0]
        combine_color = base_link.from_socket

        links.remove(base_link)

        for index, filename in enumerate(filenames):
            image_path = gltf_path.parent / filename

            if not image_path.is_file():
                continue

            image = bpy.data.images.load(str(image_path), check_existing=True)
            image.colorspace_settings.name = "Non-Color"

            texture = nodes.new("ShaderNodeTexImage")
            texture.name = f"Siege Detail Normal {index + 1}"
            texture.label = filename
            texture.image = image
            texture.location = (
                normal_map.location.x - 600,
                normal_map.location.y - 260 * (index + 1)
            )

            blend = nodes.new("ShaderNodeMixRGB")
            blend.name = f"Siege Detail Normal Blend {index + 1}"
            blend.label = "Siege Detail Normal"
            blend.blend_type = "OVERLAY"
            blend.inputs[0].default_value = 1.0
            blend.location = (
                normal_map.location.x - 300,
                normal_map.location.y - 260 * index
            )

            links.new(combine_color, blend.inputs[1])
            links.new(texture.outputs["Color"], blend.inputs[2])

            combine_color = blend.outputs["Color"]

        links.new(combine_color, color_input)

def render_preview(gltf_path: Path, output_path: Path) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)

    bpy.ops.import_scene.gltf(filepath=str(gltf_path))

    apply_siege_materials(gltf_path)

    meshes = [
        obj
        for obj in bpy.context.scene.objects
        if obj.type == "MESH" and obj.name.startswith("part_")
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