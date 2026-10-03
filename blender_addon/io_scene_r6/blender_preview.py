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

def apply_clothing_preview(material, spec, document, gltf_path):
    extras = spec.get("extras", {})
    mask_name = extras.get("siegeMaskTexture")
    alpha_layers = extras.get("siegeShaderUniforms", {}).get("ClothingMaskMode") == [0.0]
    if not mask_name and not alpha_layers:
        return

    uniforms = extras.get("siegeShaderUniforms", {})
    names = ("MaskRed_Color", "MaskGreen_Color", "MaskBlue_Color")
    if any(name not in uniforms for name in names):
        raise ValueError("Re-export this model with clothing color properties")

    nodes = material.node_tree.nodes
    links = material.node_tree.links
    if nodes.get("Siege Clothing Preview"):
        return

    principled = next(
        (node for node in nodes if node.type == "BSDF_PRINCIPLED"),
        None,
    )
    if principled is None:
        return

    texture_index = spec["pbrMetallicRoughness"]["baseColorTexture"]["index"]
    image_index = document["textures"][texture_index]["source"]
    diffuse_name = document["images"][image_index]["uri"]

    def image_node(filename, color_space):
        image = bpy.data.images.load(str(gltf_path.parent / filename), check_existing=True).copy()
        image.colorspace_settings.name = color_space
        node = nodes.new("ShaderNodeTexImage")
        node.image = image
        node.label = filename
        return node

    def math_node(operation, first, second):
        node = nodes.new("ShaderNodeMath")
        node.operation = operation
        for index, value in enumerate((first, second)):
            if isinstance(value, (int, float)):
                node.inputs[index].default_value = value
            else:
                links.new(value, node.inputs[index])
        return node.outputs[0]

    def add3(values):
        return math_node("ADD", math_node("ADD", values[0], values[1]), values[2])

    diffuse = image_node(diffuse_name, "Non-Color")

    if alpha_layers:
        alpha = diffuse.outputs["Alpha"]
        red = math_node("SUBTRACT", 1.0, math_node("LESS_THAN", alpha, 0.75))
        green = math_node("MULTIPLY", math_node("GREATER_THAN", alpha, 0.25), math_node("SUBTRACT", 1.0, red))
        weights = (red, green, 0.0)
    else:
        mask = image_node(mask_name, "Non-Color")
        channels = nodes.new("ShaderNodeSeparateColor")
        links.new(mask.outputs["Color"], channels.inputs["Color"])
        rgb = [
            channels.outputs[name]
            for name in ("Red", "Green", "Blue")
        ]
        divisor = math_node("MAXIMUM", add3(rgb), 1.0)
        weights = tuple(math_node("DIVIDE", value, divisor) for value in rgb)

    squared = [
        math_node("MULTIPLY", value, value)
        for value in weights
    ]
    strength = math_node("MINIMUM", math_node("SQRT", add3(squared), 0.0), 1.0)
    factor = nodes.new("ShaderNodeCombineXYZ")

    # Shader parameters use 0.5 as neutral - Nyx
    for channel in range(3):
        weighted = add3([
            math_node("MULTIPLY", weights[index], uniforms[name][channel])
            for index, name in enumerate(names)
        ])
        gain = math_node("ADD", 1.0, math_node("MULTIPLY", math_node("MULTIPLY", strength, 2.0), math_node("SUBTRACT", weighted, 0.5)))
        links.new(gain, factor.inputs[channel])

    blend = nodes.new("ShaderNodeMixRGB")
    blend.name = "Siege Clothing Preview"
    blend.blend_type = "MULTIPLY"
    blend.use_clamp = True
    blend.inputs[0].default_value = 1.0
    links.new(diffuse.outputs["Color"], blend.inputs[1])
    links.new(factor.outputs["Vector"], blend.inputs[2])

    gamma = nodes.new("ShaderNodeGamma")
    gamma.inputs["Gamma"].default_value = 2.2
    links.new(blend.outputs[0], gamma.inputs["Color"])

    base = principled.inputs["Base Color"]
    for link in list(base.links):
        links.remove(link)
    links.new(gamma.outputs["Color"], base)

def apply_siege_materials(gltf_path: Path, *, materials=None) -> None:
    document = json.loads(gltf_path.read_text(encoding="utf-8"))

    extras_by_material = {
        material["name"]: material.get("extras", {})
        for material in document.get("materials", {})
        if "name" in material
    }

    targets = tuple(bpy.data.materials) if materials is None else tuple(materials)
    for material in targets:
        material_name = material.name
        if material_name not in extras_by_material:
            base_name, separator, suffix = material_name.rpartition(".")
            if separator and suffix.isdigit() and base_name in extras_by_material:
                material_name = base_name
            else:
                continue

        extras = extras_by_material[material_name]

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

        if apply_experimental_hair(material, principled, extras, gltf_path):
            continue

        packed_filename = extras.get("siegePackedMaterialTexture")

        # node crackheads were on it again lol - Isaac
        # incredible crackheads, true - shadow
        if principled is not None and packed_filename is not None and extras.get("siegeShaderUid") != "000000003051C028":
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

                    metalness_output = separate.outputs["Red"]
                    if extras.get("siegeShaderUid") in {
                        "000000003BD13B9E",
                        "000000003CAE6B71",
                        "0000001397A32F38"
                    }:
                        # Nyx, the shader said 2.2. Victor brought receipts - Blake
                        decode = nodes.new("ShaderNodeMath")
                        decode.name = "Siege Metalness Decode"
                        decode.label = "Packed R ^ 2.2"
                        decode.operation = "POWER"
                        decode.inputs[1].default_value = 2.2
                        decode.location = (principled.location.x - 120, principled.location.y - 450)
                        links.new(metalness_output, decode.inputs[0])
                        metalness_output = decode.outputs["Value"]

                    links.new(metalness_output, metallic_input)

                if roughness_input is not None:
                    if roughness_input.is_linked:
                        links.remove(roughness_input.links[0])

                    links.new(roughness.outputs["Value"], roughness_input)

                if extras.get("siegeShaderUid") in {
                    "000000003BD13B9E",
                    "000000003CAE6B71",
                    "0000001397A32F38"
                }:
                    # Victor: blue controls dielectric reflection, not base color
                    separate.label = "R: Metalness G: Glossiness B: Reflectance"

                    reflectance = nodes.new("ShaderNodeMath")
                    reflectance.name = "Siege Reflectance Decode"
                    reflectance.label = "Packed B ^ 2.2"
                    reflectance.operation = "POWER"
                    reflectance.inputs[1].default_value = 2.2
                    reflectance.location = (principled.location.x - 350, principled.location.y - 850)
                    links.new(separate.outputs["Blue"], reflectance.inputs[0])

                    level = nodes.new("ShaderNodeMath")
                    level.name = "Siege Specular Level"
                    level.operation = "MULTIPLY"
                    level.inputs[1].default_value = 0.5
                    level.location = (principled.location.x - 120, principled.location.y - 850)
                    links.new(reflectance.outputs["Value"], level.inputs[0])

                    for socket_name in ("IOR", "Specular IOR Level"):
                        for link in tuple(principled.inputs[socket_name].links):
                            links.remove(link)

                    principled.inputs["IOR"].default_value = 1.5
                    links.new(level.outputs["Value"], principled.inputs["Specular IOR Level"])

        if extras.get("siegeShaderUid") == "0000001397A32F38":
            spec = next(
                item for item in document["materials"]
                if item["name"] == material_name
            )
            apply_clothing_preview(material, spec, document, gltf_path)

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

def size_fk_bones(objects):
    """Shorten imported root and leaf bones without moving their joints"""

    arms = [obj for obj in objects if obj.type == "ARMATURE"]
    if not arms:
        return
    if bpy.context.mode != "OBJECT":
        raise RuntimeError("FK bone sizing requires Object Mode")

    for arm in arms:
        if arm.data.users != 1 or any(bone.use_connect for bone in arm.data.bones):
            raise RuntimeError(f"Unexpected shared or connected armature: {arm.name}")

    selected = list(bpy.context.selected_objects)
    active = bpy.context.view_layer.objects.active

    try:
        bpy.ops.object.select_all(action="DESELECT")
        for arm in arms:
            arm.select_set(True)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode="EDIT")

            for bone in arm.data.edit_bones:
                length = max(bone.length, 0.025)
                if bone.parent is None:
                    length = min(length, 0.04)
                elif not bone.children:
                    length = min(length, 0.025)

                if abs(bone.length - length) > 0.000001:
                    bone.length = length

            bpy.ops.object.mode_set(mode="OBJECT")
            arm.show_in_front = True
            arm.select_set(False)
    finally:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for obj in selected:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = active

    bpy.context.view_layer.update()

def merge_fk_armatures(body, objects):
    """Join supported FK rigs while preserving skinning and head parenting"""
    objects = tuple(objects)
    arms = [obj for obj in objects if obj.type == "ARMATURE"]
    heads = [arm for arm in arms if arm != body]

    if bpy.context.mode != "OBJECT" or body not in arms or not heads:
        raise RuntimeError("FK merge requires body and head rigs in Object Mode")

    names = set()
    for arm in arms:
        if arm.data.users != 1 or arm.animation_data or arm.data.animation_data:
            raise RuntimeError("FK merge requires fresh, unshared armatures")

        if max(abs(arm.matrix_world[r][c] - body.matrix_world[r][c]) for r in range(4) for c in range(4)) > 0.000001:
            raise RuntimeError("FK armature objects transforms do not match")

        for bone in arm.data.bones:
            if bone.name in names:
                raise RuntimeError("Duplicate FK bone name: " + bone.name)
            names.add(bone.name)

    bpy.context.view_layer.update()
    inverse = body.matrix_world.inverted()
    roots = []
    poses = {}

    for arm in heads:
        for bone in arm.pose.bones:
            poses[bone.name] = inverse @ arm.matrix_world @ bone.matrix

            if bone.parent is not None:
                if bone.constraints:
                    raise RuntimeError("Unexpected head constraints: " + bone.name)
                continue

            constraints = list(bone.constraints)
            if len(constraints) != 1 or constraints[0].name != "R6 body follow" or constraints[0].type != "CHILD_OF" or constraints[0].target != body or constraints[0].subtarget not in body.data.bones:
                raise RuntimeError("Unresolved FK head root: " + bone.name)
            roots.append((bone.name, constraints[0].subtarget))

    bindings = [
        (obj, modifier)
        for obj in objects if obj.type == "MESH"
        for modifier in obj.modifiers
        if modifier.type == "ARMATURE" and modifier.object in arms
    ]
    children = [
        (obj, obj.matrix_world.copy())
        for obj in objects
        if obj.type != "ARMATURE" and obj.parent in heads
    ]

    bpy.ops.object.select_all(action="DESELECT")
    for arm in arms:
        arm.select_set(True)
    bpy.context.view_layer.objects.active = body

    if "FINISHED" not in bpy.ops.object.join():
        raise RuntimeError("Could not merge FK armatures")

    for obj, modifier in bindings:
        modifier.object = body

    for obj, world in children:
        obj.parent = body
        obj.matrix_world = world

    bpy.ops.object.mode_set(mode="EDIT")
    try:
        for name, target in roots:
            bone = body.data.edit_bones[name]
            rest = bone.matrix.copy()
            bone.parent = body.data.edit_bones[target]
            bone.use_connect = False
            bone.matrix = rest
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    for name, target in roots:
        bone = body.pose.bones[name]
        bone.constraints.remove(bone.constraints["R6 body follow"])
        bone.matrix = poses[name]

    bpy.context.view_layer.update()

    error = max(
        abs(body.pose.bones[name].matrix[r][c] - matrix[r][c])
        for name, matrix in poses.items()
        for r in range(4) for c in range(4)
    )
    if error > 0.0001:
        raise RuntimeError("FK merge changed the pose, reimport before posing")

    body["r6_fk_merged"] = True

def connect_fk_helpers(arm, head_name):
    from .blender_ik import source_parent_map, source_parent_target

    bones = arm.data.bones
    anchors = [
        bone for bone in bones
        if bone.name.endswith("_LeftArm")
    ]
    if bpy.context.mode != "OBJECT" or len(anchors) != 1:
        raise RuntimeError("FK helper setup requires 1 primary body rig")

    prefix = anchors[0].name.split("_join_", 1)[0] + "_join_"
    primary = {}
    for bone in bones:
        if bone.name.startswith(prefix):
            uid = bone.get("siegeBoneId")
            if uid:
                if uid in primary:
                    raise RuntimeError("Ambiguous primary bone: " + uid)
                primary[uid] = bone.name
    if head_name is not None:
        primary["07C159A2"] = head_name

    parents = source_parent_map((arm,))
    driver_parents = {}

    for bone in bones:
        stored = bone.get("siegeFkDriverParents")
        if stored is not None:
            geometry = bone.name.split("_join_", 1)[0]
            driver_parents[geometry] = dict(stored.items())

    plans = {}

    for bone in bones:
        uid = bone.get("siegeBoneId")
        if bone.name == head_name:
            continue

        geometry = bone.name.split("_join_", 1)[0]
        driver_id = driver_parents.get(geometry, {}).get(uid)

        if driver_id is not None:
            if driver_id not in primary:
                raise RuntimeError("Missing FK driver: " + driver_id)
            target = primary[driver_id]
        elif not bone.name.startswith(prefix) and uid in primary:
            target = primary[uid]
        elif bone.parent is None:
            target_id = source_parent_target(uid, parents, primary)
            target = primary.get(target_id)

            if target is None and head_name is not None and bone.get("siegeHeadDescendant") is True:
                target = head_name
        else:
            continue

        if target is not None and (bone.parent is None or bone.parent.name != target):
            plans[bone.name] = target

    if not plans:
        return

    if any(arm.pose.bones[name].constraints for name in plans):
        raise RuntimeError("FK helpers already have constraints")

    bpy.context.view_layer.update()
    poses = {
        bone.name: bone.matrix.copy()
        for bone in arm.pose.bones
    }

    bpy.ops.object.select_all(action="DESELECT")
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")

    try:
        for name, target in plans.items():
            bone = arm.data.edit_bones[name]
            rest = bone.matrix.copy()
            bone.parent = arm.data.edit_bones[target]
            bone.use_connect = False
            bone.matrix = rest
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    for bone in sorted(arm.pose.bones, key=lambda bone: len(bone.parent_recursive)):
        parent_frames = {}

        if bone.parent is not None:
            parent_frames = {
                "parent_matrix": poses[bone.parent.name],
                "parent_matrix_local": bone.parent.bone.matrix_local
            }

        bone.matrix_basis = bone.bone.convert_local_to_pose(poses[bone.name], bone.bone.matrix_local, invert=True, **parent_frames)

    bpy.context.view_layer.update()
    error = max(
        abs(arm.pose.bones[name].matrix[r][c] - old[r][c])
        for name, old in poses.items()
        for r in range(4)
        for c in range(4)
    )
    if error > 0.0001:
        raise RuntimeError("FK helper setup changed the pose, reimport before posing")

def organize_fk_bones(arm, head_name):
    """Group FK display bones"""
    bones = arm.data.bones
    anchors = [
        bone for bone in bones
        if bone.name.endswith("_LeftArm")
    ]
    if len(anchors) != 1 or head_name is not None and head_name not in bones:
        raise RuntimeError("Cannot identify the primary FK skeleton")

    prefix = anchors[0].name.split("_join_")[0] + "_join_"
    labels = {"Head", "Hips", "Spine", "Spine1", "Spine2"}

    for side in ("Left", "Right"):
        labels.update(
            side + part for part in (
                "Arm", "ForeArm", "Hand",
                "Shoulder", "UpLeg", "Leg",
                "Foot", "ToeBase"
            )
        )
        labels.update(
            side + "Hand" + finger + str(segment)
            for finger in ("Index", "Middle", "Pinky", "Ring", "Thumb")
            for segment in (1, 2, 3)
        )
        labels.update(
            side + "InHand" + finger
            for finger in ("Index", "Middle", "Pinky", "Ring")
        )

    groups = {
        name: arm.data.collections.get(name)
        or arm.data.collections.new(name)
        for name in ("Body", "Face", "Helpers")
    }

    head = bones.get(head_name) if head_name is not None else None
    for bone in bones:
        if bone == head or bone.name.startswith(prefix) and bone.name.rsplit("_", 1)[-1] in labels:
            group = "Body"
        elif head is not None and head in bone.parent_recursive or head is None and bone.get("r6_head_part") is True:
            group = "Face"
        else:
            group = "Helpers"

        for collection in tuple(bone.collections):
            collection.unassign(bone)
        groups[group].assign(bone)

        bone.hide = False
        bone.hide_select = False
        bone.select = False

    for collection in arm.data.collections_all:
        collection.is_solo = False

    for name, collection in groups.items():
        collection.is_visible = name == "Body" or (head is None and name == "Face")

    arm.data.collections.active = groups["Body"]
    arm.show_in_front = True

def connect_fk_head(objects):
    """Connect and merge operator rigs"""
    from .blender_ik import connect_operator_head

    objects = tuple(objects)
    suffixes = (
        "_LeftArm", "_RightArm",
        "_LeftUpLeg", "_RightUpLeg"
    )
    bodies = [
        obj for obj in objects
        if obj.type == "ARMATURE"
        and all(any(bone.name.endswith(suffix) for bone in obj.data.bones) for suffix in suffixes)
    ]
    if len(bodies) != 1:
        raise RuntimeError("FK head setup requires one identifiable body rig")

    body = bodies[0]
    head_name = connect_operator_head(body, objects, allow_no_head=True)
    if head_name is None:
        for obj in objects:
            if obj.type == "ARMATURE" and obj != body:
                for bone in obj.data.bones:
                    bone["r6_head_part"] = True

    merge_fk_armatures(body, objects)
    connect_fk_helpers(body, head_name)
    organize_fk_bones(body, head_name)

    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    body.select_set(True)
    bpy.context.view_layer.objects.active = body

    for bone in body.data.bones:
        bone.select = False

    selected_name = head_name
    if selected_name is None:
        prefix = next(
            bone.name.split("_join_", 1)[0] + "_join_"
            for bone in body.data.bones
            if bone.name.endswith("_LeftArm")
        )
        selected_name = next(
            bone.name
            for bone in body.data.bones
            if bone.name.startswith(prefix)
            and bone.name.endswith("_Spine2")
        )

    body.data.bones[selected_name].select = True
    body.data.bones.active = body.data.bones[selected_name]
    body.show_in_front = True

    return body, head_name

def import_siege_model(gltf_path):
    """Import a prepared glTF and apply its Siege preview materials"""

    gltf_path = Path(gltf_path).expanduser().resolve()
    if not gltf_path.is_file():
        raise FileNotFoundError(gltf_path)

    document = json.loads(gltf_path.read_text(encoding="utf-8"))
    source_model = document["scenes"][document.get("scene", 0)].get("name")

    before_materials = {
        material.as_pointer()
        for material in bpy.data.materials
    }
    before_objects = {
        obj.as_pointer()
        for obj in bpy.data.objects
    }

    result = bpy.ops.import_scene.gltf(filepath=str(gltf_path), disable_bone_shape=True, bone_heuristic="TEMPERANCE")
    if "FINISHED" not in result:
        raise RuntimeError(f"glTF import did not finish: {gltf_path}")

    size_fk_bones(obj for obj in bpy.data.objects if obj.as_pointer() not in before_objects)

    imported_materials = tuple(
        material for material in bpy.data.materials
        if material.as_pointer() not in before_materials
    )
    apply_siege_materials(gltf_path, materials=imported_materials)

    # Manual appearance correction for Fuze's default body only
    if source_model == "000000156B7353F8":
        for obj in bpy.data.objects:
            if obj.as_pointer() not in before_objects and obj.type == "MESH" and obj.name.partition(".")[0] == "part_00000007B8293A4C":
                obj.hide_set(True)
                obj.hide_render = True

def render_preview(gltf_path: Path, output_path: Path) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)

    bpy.ops.import_scene.gltf(filepath=str(gltf_path), disable_bone_shape=True, bone_heuristic="TEMPERANCE")

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

def apply_experimental_hair(material, principled, extras, gltf_path):
    """Approximate two hair lobes. The source field mapping is experimental"""
    import math

    if extras.get("siegeShaderUid") != "000000003051C028" or principled is None:
        return False

    values = extras.get("siegeShaderUniforms", {}).get("ExperimentalHairSourceV1")
    if not isinstance(values, (list, tuple)) or len(values) != 8 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values) or not -3.0 <= values[0] <= 3.0 or any(not 0.0 <= v <= 1.0 for v in values[1:]):
        return False

    nodes, links = material.node_tree.nodes, material.node_tree.links
    if nodes.get("Siege Experimental Hair"):
        return True

    def node(kind):
        result = nodes.new(kind)
        result.label = "Experimental Hair"
        return result

    def connect(value, socket):
        if hasattr(value, "is_output"):
            links.new(value, socket)
        else:
            if socket.type == "RGBA" and isinstance(value, (int, float)):
                value = (value, value, value, 1.0)
            socket.default_value = value

    def source(socket):
        return socket.links[0].from_socket if socket.is_linked else socket.default_value

    def multiply_color(color, factor):
        mix = node("ShaderNodeMixRGB")
        mix.blend_type = "MULTIPLY"
        mix.inputs[0].default_value = 1.0
        connect(color, mix.inputs[1])
        connect(factor, mix.inputs[2])
        return mix.outputs[0]

    geometry = node("ShaderNodeNewGeometry")
    tangent = node("ShaderNodeTangent")
    tangent.direction_type = "UV_MAP"
    normal = source(principled.inputs["Normal"]) if principled.inputs["Normal"].is_linked else geometry.outputs["Normal"]

    bitangent = node("ShaderNodeVectorMath")
    bitangent.operation = "CROSS_PRODUCT"
    connect(normal, bitangent.inputs[0])
    connect(tangent.outputs[0], bitangent.inputs[1])

    shift = node("ShaderNodeVectorMath")
    shift.operation = "SCALE"
    connect(normal, shift.inputs[0])
    shift.inputs["Scale"].default_value = values[0]

    shifted = node("ShaderNodeVectorMath")
    shifted.operation = "ADD"
    links.new(bitangent.outputs[0], shifted.inputs[0])
    links.new(shift.outputs[0], shifted.inputs[1])

    direction = node("ShaderNodeVectorMath")
    direction.operation = "NORMALIZE"
    links.new(shifted.outputs[0], direction.inputs[0])

    mask = 1.0
    packed_path = gltf_path.parent / extras.get("siegePackedMaterialTexture", "")
    if packed_path.is_file():
        texture = node("ShaderNodeTexImage")
        texture.image = bpy.data.images.load(str(packed_path), check_existing=True).copy()
        texture.image.colorspace_settings.name = "Non-Color"
        channels = node("ShaderNodeSeparateColor")
        links.new(texture.outputs["Color"], channels.inputs["Color"])
        mask = channels.outputs["Red"]

    base = source(principled.inputs["Base Color"])
    tint = node("ShaderNodeMixRGB")
    tint.inputs[0].default_value = values[5]
    connect(base, tint.inputs[1])
    tint.inputs[2].default_value = (1.0, 1.0, 1.0, 1.0)

    diffuse = node("ShaderNodeBsdfDiffuse")
    connect(multiply_color(base, (values[6],) * 3 + (1.0,)), diffuse.inputs["Color"])
    connect(normal, diffuse.inputs["Normal"])
    accumulated = diffuse.outputs[0]

    glosses = (
        max(0.0, min(1.0, 1.035 - 1.15 * (1.0 - values[1]))),
        values[3]
    )
    for gloss, strength, color in zip(glosses, (values[2], values[4]), (base, tint.outputs[0])):
        highlight = node("ShaderNodeBsdfAnisotropic")
        highlight.distribution = "GGX"
        highlight.inputs["Roughness"].default_value = (
            2.0 / (2.0 ** (19.0 * gloss) + 2.0)
        ) ** 0.25

        # Two lobes, still not a confession from the serializer - Aiden
        highlight.inputs["Anisotropy"].default_value = 0.8
        weighted = multiply_color(color, (0.125 * strength,) * 3 + (1.0,))
        connect(multiply_color(weighted, mask), highlight.inputs["Color"])
        connect(normal, highlight.inputs["Normal"])
        connect(direction.outputs[0], highlight.inputs["Tangent"])

        addition = node("ShaderNodeAddShader")
        links.new(accumulated, addition.inputs[0])
        links.new(highlight.outputs[0], addition.inputs[1])
        accumulated = addition.outputs[0]

    transparent = node("ShaderNodeBsdfTransparent")
    output = node("ShaderNodeMixShader")
    output.name = "Siege Experimental Hair"
    connect(source(principled.inputs["Alpha"]), output.inputs[0])
    links.new(transparent.outputs[0], output.inputs[1])
    links.new(accumulated, output.inputs[2])

    for link in list(principled.outputs["BSDF"].links):
        links.new(output.outputs[0], link.to_socket)

    return True

if __name__ == "__main__":
    main()