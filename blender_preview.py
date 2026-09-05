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
            if principled is None:
                continue

            base = principled.inputs["Base Color"]
            if not base.is_linked or base.links[0].from_node.type != "TEX_IMAGE":
                continue # Apply once to a fresh glTF import

            texture = base.links[0].from_node
            uniforms = extras.get("siegeShaderUniforms", {})
            required = (
                "ScleraColorWhite", "ScleraColorBlack",
                "IrisColorWhite", "IrisColorBlack", "IrisUVRadius",
                "IrisGlossiness", "ScleraGlossiness",
            )
            if any(name not in uniforms for name in required):
                raise ValueError("Re-export the head with eye property overrides")

            radius = float(uniforms["IrisUVRadius"][0])
            if not 0.0 < radius < 0.5:
                raise ValueError("Eye radius is still a placeholder, re-export the head")

            # Eyelashes share this image, isolate the eye data color space
            texture.image = texture.image.copy()
            texture.image.colorspace_settings.name = "Non-Color"
            links.remove(base.links[0])

            def color(name):
                return tuple(
                    v / 12.92 if v <= 0.04045
                    else ((v + 0.055) / 1.055) ** 2.4
                    for v in uniforms[name][:3]
                ) + (1.0,)

            def mix(label, factor, dark, light):
                node = nodes.new("ShaderNodeMixRGB")
                node.label = label
                node.inputs[1].default_value = dark
                node.inputs[2].default_value = light
                links.new(factor, node.inputs[0])
                return node

            channels = nodes.new("ShaderNodeSeparateColor")
            links.new(texture.outputs["Color"], channels.inputs["Color"])

            sclera = mix("Sclera tint", channels.outputs["Green"], color("ScleraColorBlack"), color("ScleraColorWhite"))
            iris = mix("Iris tint", channels.outputs["Red"], color("IrisColorBlack"), color("IrisColorWhite"))
            pupil = mix("Pupil / iris detail", texture.outputs["Alpha"], (0, 0, 0, 1), (1, 1, 1, 1),)
            links.new(iris.outputs[0], pupil.inputs[2])

            # Preview mapping for the current eye atlas's right half
            uv = nodes.new("ShaderNodeTexCoord")
            offset = nodes.new("ShaderNodeVectorMath")
            offset.operation = "SUBTRACT"
            offset.inputs[1].default_value = (0.75, 0.5, 0.0)
            links.new(uv.outputs["UV"], offset.inputs[0])

            scale = nodes.new("ShaderNodeVectorMath")
            scale.operation = "MULTIPLY"
            scale.inputs[1].default_value = (2.0, 1.0, 0.0)
            links.new(offset.outputs["Vector"], scale.inputs[0])

            distance = nodes.new("ShaderNodeVectorMath")
            distance.operation = "LENGTH"
            links.new(scale.outputs["Vector"], distance.inputs[0])

            mask = nodes.new("ShaderNodeMapRange")
            mask.clamp = True
            mask.inputs["From Min"].default_value = radius - 0.01
            mask.inputs["From Max"].default_value = radius
            mask.inputs["To Min"].default_value = 1.0
            mask.inputs["To Max"].default_value = 0.0
            links.new(distance.outputs["Value"], mask.inputs["Value"])

            combined = mix("Eye surface", mask.outputs["Result"], (0, 0, 0, 1), (1, 1, 1, 1),)
            links.new(sclera.outputs[0], combined.inputs[1])
            links.new(pupil.outputs[0], combined.inputs[2])
            links.new(combined.outputs[0], base)

            sclera_roughness = 1.0 - float(uniforms["ScleraGlossiness"][0])
            iris_roughness = 1.0 - float(uniforms["IrisGlossiness"][0])
            rough = mix("Eye roughness", mask.outputs["Result"], (sclera_roughness,) * 3 + (1.0,), (iris_roughness,) * 3 + (1.0,),)
            links.new(rough.outputs[0], principled.inputs["Roughness"])
            principled.inputs["Metallic"].default_value = 0.0

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