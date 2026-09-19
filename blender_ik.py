"""Optional Blender 4.5 IK controls for an imported R6 body"""
import math
import bpy
from mathutils import Matrix

def create_operator_ik(objects):
    parts = ("Arm", "ForeArm", "Hand", "UpLeg", "Leg", "Foot")
    suffixes = [
        "_" + side + part
        for side in ("Left", "Right")
        for part in parts
    ]
    arms = [
        obj for obj in objects
        if obj.type == "ARMATURE" and all(any(b.name.endswith(s) for b in obj.data.bones) for s in suffixes)
    ]
    if len(arms) != 1:
        raise RuntimeError("IK requires exactly 1 supported body armature")

    arm = arms[0]
    if bpy.context.mode != "OBJECT" or arm.data.users != 1:
        raise RuntimeError("IK requires Object Mode and an unshared armature")
    if arm.get("r6_experimental_ik"):
        raise RuntimeError("This body already has R6 IK controls")

    def find(part):
        matches = [
            b for b in arm.data.bones
            if b.name.endswith("_" + part)
        ]
        if len(matches) != 1:
            raise RuntimeError("Missing or ambiguous bone: " + part)
        return matches[0]

    chains = []
    for side in ("Left", "Right"):
        for parts in (("Arm", "ForeArm", "Hand"), ("UpLeg", "Leg", "Foot")):
            bones = [find(side + part) for part in parts]
            for bone, child in zip(bones, bones[1:]):
                if child.parent != bone:
                    raise RuntimeError("Incorrect hierarchy: " + child.name)

                direction = child.head_local - bone.head_local
                if direction.length < 0.000001 or ((bone.tail_local - bone.head_local).normalized().dot(direction.normalized()) < 0.999):
                    raise RuntimeError("Reimport using TEMPERANCE bone orientation")
            chains.append([b.name for b in bones])

    prefix = chains[0][0].split("_join_")[0] + "_join_"
    roots = sum(
        b.parent is None
        for b in arm.data.bones
        if b.name.startswith(prefix)
    )
    if roots != 1:
        raise RuntimeError("Body has unresolved helpers. Regenerate its export")

    if any(arm.pose.bones[end].constraints for _, _, end in chains):
        raise RuntimeError("Hand or foot constraints already exist")

    bpy.context.view_layer.update()
    poles = {}

    for side in ("Left", "Right"):
        hip, knee, ankle = [
            arm.matrix_world @ arm.pose.bones[find(side + p).name].head
            for p in ("UpLeg", "Leg", "Foot")
        ]
        axis = ankle - hip
        if axis.length < 0.001:
            raise RuntimeError("Collapsed leg: " + side)

        bend = knee - (hip + axis * ((knee - hip).dot(axis) / axis.length_squared))
        if bend.length < 0.002:
            raise RuntimeError("Knee too straight to derive a pole: " + side)

        poles[side] = knee + bend.normalized() * ((knee - hip).length + (ankle - knee).length) * 0.75

    selected = list(bpy.context.selected_objects)
    active = bpy.context.view_layer.objects.active
    lengths = {b.name: b.length for b in arm.data.bones}
    created_objects, created_constraints = [], []

    def empty(name, position, shape):
        obj = bpy.data.objects.new(name, None)
        bpy.context.collection.objects.link(obj)
        created_objects.append(obj)
        obj.empty_display_type = shape
        obj.empty_display_size = 0.06
        obj.show_in_front = True
        obj.location = position
        return obj

    def constraint(bone, kind, name):
        item = bone.constraints.new(kind)
        created_constraints.append((bone, item))
        item.name = name
        return item

    try:
        for obj in selected:
            obj.select_set(False)
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm

        bpy.ops.object.mode_set(mode="EDIT")
        for b in arm.data.edit_bones:
            if b.parent is None:
                b.length = min(b.length, 0.04)

        for upper, lower, end in chains:
            for name, child in ((upper, lower), (lower, end)):
                b = arm.data.edit_bones[name]
                b.length = (arm.data.edit_bones[child].head - b.head).length

        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.update()

        for upper, lower, end in chains:
            bone = arm.pose.bones[end]
            target = empty("R6_IK_" + arm.name + "_" + end.rsplit("_", 1)[-1], arm.matrix_world @ bone.head, "CUBE")
            bpy.context.view_layer.update()

            ik = constraint(bone, "IK", "R6 experimental IK")
            ik.target = target
            ik.chain_count = 2 if end.endswith(("_LeftFoot", "_RightFoot")) else 3
            ik.use_tail = ik.use_stretch = ik.use_rotation = False

        bpy.context.view_layer.update()

        for side in ("Left", "Right"):
            bones = [
                arm.pose.bones[find(side + p).name]
                for p in ("UpLeg", "Leg", "Foot")
            ]
            foot = bones[2]
            ik = foot.constraints["R6 experimental IK"]
            reference = [b.matrix.copy() for b in bones]
            ik.pole_target = empty("R6_KneePole_" + side + "_" + arm.name, poles[side], "SPHERE")

            def score(angle):
                ik.pole_angle = (angle + math.pi) % (2 * math.pi) - math.pi
                bpy.context.view_layer.update()
                return sum(
                    (b.matrix[r][c] - m[r][c]) ** 2
                    for b, m in zip(bones, reference)
                    for r in range(3)
                    for c in range(4)
                )

            step = 2 * math.pi / 72
            best = min((-math.pi + i * step for i in range(72)), key=score)
            for _ in range(4):
                best = min((best + step * i / 10 for i in range(-10, 11)), key=score)
                step /= 10

            if score(best) > 0.0001:
                raise RuntimeError("Knee calibration failed: " + side)

            old_pose = foot.matrix.copy()
            target = ik.target
            location, _, scale = target.matrix_world.decompose()
            target.matrix_world = Matrix.LocRotScale(location, (arm.matrix_world @ old_pose).to_quaternion(), scale)

            rotation = constraint(foot, "COPY_ROTATION", "R6 foot rotation")
            rotation.target = target
            rotation.owner_space = rotation.target_space = "WORLD"
            rotation.mix_mode = "REPLACE"

            bpy.context.view_layer.update()
            error = max(
                abs(foot.matrix[r][c] - old_pose[r][c])
                for r in range(4)
                for c in range(4)
            )
            if error > 0.0001:
                raise RuntimeError("Foot rotation changed the pose: " + side)
    except Exception:
        if arm.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        for bone, item in reversed(created_constraints):
            bone.constraints.remove(item)
        for obj in reversed(created_objects):
            bpy.data.objects.remove(obj, do_unlink=True)

        bpy.ops.object.mode_set(mode="EDIT")
        for b in arm.data.edit_bones:
            b.length = lengths[b.name]
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.update()
        raise
    finally:
        arm.select_set(False)
        for obj in selected:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = active

    arm.data.display_type = "STICK"
    arm.show_in_front = False
    arm["r6_experimental_ik"] = True
    bpy.context.view_layer.update()
    return arm