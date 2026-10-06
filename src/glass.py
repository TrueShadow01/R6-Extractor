import math
from dataclasses import replace

GLASS_PROFILES = {
    # Shared clear lenses including Blitz, Sledge and Ace's lamp
    (0x000000003BD13B9E, 0x00000001134CB4E0): (0.12, 0.08, 1.5),
    # Individually identified, untextured lens materials
    (0x000000003BD13B9E, 0x000000167CE4C172): (0.22, 0.08, 1.5),
    (0x000000003BD13B9E, 0x0000005B35F8027F): (0.22, 0.08, 1.5),
    (0x000000003BD13B9E, 0x000000207BA7F9D2): (0.22, 0.08, 1.5),
    (0x000000003BD13B9E, 0x000000162B4A763C): (0.22, 0.08, 1.5),
    (0x000000003BD13B9E, 0x0000001708A03E1C): (0.22, 0.08, 1.5),
    (0x000000003BD13B9E, 0x00000061CE5AA613): (0.22, 0.08, 1.5),
    (0x000000003BD13B9E, 0x00000056E92BCC64): (0.22, 0.08, 1.5),
    (0x000000003BD13B9E, 0x0000005F69BB983F): (0.22, 0.08, 1.5),
    (0x00000000523BA2BF, 0x0000001987553C8E): (0.22, 0.08, 1.5),
}

GLASS_APPROXIMATION = (
    "Experimental thin glass: source RGB and normals, approximate coverage, "
    "roughness and IOR, studio reflections, no refraction or game shader parity."
)

# Current source geometry, material and UV evidence
# Counts deliberately guard against reusing these atlas regions after an asset/layout change
LENS_REGIONS = {
    (0x00000009D00E1FFB, 0x0000000A8717A57B): ((0.216, 0.323, 0.598, 0.653), 1529, 112),
    (0x0000000FF51C27F0, 0x0000000B0BABF6F7): ((0.237, 0.367, 0.008, 0.082), 2441, 68),
    (0x00000009D00DE165, 0x0000000A8715DE27): ((0.350, 0.980, 0.230, 0.400), 568, 72),
    (0x00000026FDC82372, 0x00000026FDC823B3): ((0.497, 0.638, 0.257, 0.669), 3514, 94),
}

def split_atlas_lenses(parts, material_textures):
    """Preserve geometry/weights, assign verified lens faces a derived slot"""
    if not material_textures:
        return parts, material_textures

    slots = list(material_textures)
    result = []

    for part in parts:
        islands = []
        changed = False

        for island in part.islands:
            if not 0 <= island.material_id < len(material_textures):
                islands.append(island)
                continue

            slot = material_textures[island.material_id]
            region = LENS_REGIONS.get((part.uid, slot.material_uid))

            if region is None:
                islands.append(island)
                continue

            (u0, u1, v0, v1), total, expected = region
            lens, opaque = [], []

            for face in island.faces:
                inside = all(
                    u0 <= part.uvs[index][0] <= u1
                    and v0 <= part.uvs[index][1] <= v1
                    for index in face
                )
                (lens if inside else opaque).append(face)
            if len(island.faces) != total or len(lens) != expected:
                print(f"WARNING: Glass atlas layout changed for {part.uid:016X}. Keeping its original material")
                islands.append(island)
                continue

            derived = replace(
                slot,
                shader_uniforms=(*slot.shader_uniforms, ("ExperimentalGlassLensV1", (0.22, 0.08, 1.5)),)
            )

            index = len(slots)
            slots.append(derived)
            islands.append(replace(island, faces=tuple(opaque)))
            islands.append(replace(island, material_id=index, faces=tuple(lens)))
            changed = True
        result.append(replace(part, islands=tuple(islands)) if changed else part)

    return tuple(result), tuple(slots)

def apply_optical_preview(material, slot):
    """Attach opt-in rendering metadata and a vanilla glTF alpha fallback"""
    shader = slot.shader_uid
    profile = GLASS_PROFILES.get((shader, slot.material_uid))
    isolated = dict(slot.shader_uniforms).get("ExperimentalGlassLensV1")

    if isolated is not None:
        profile = isolated

    if shader == 0x0000000099E2C950:
        profile = (0.12, 0.08, 1.5)

    if profile is not None:
        extras = material.setdefault("extras", {})
        extras["siegeGlassPreviewV1"] = list(profile)
        extras["siegeOpticalApproximation"] = GLASS_APPROXIMATION

        pbr = material["pbrMetallicRoughness"]
        pbr["baseColorFactor"][3] = profile[0]
        pbr["metallicFactor"] = 0.0
        pbr["roughnessFactor"] = profile[1]

        material["alphaMode"] = "BLEND"
        material.pop("alphaCutoff", None)
        return

    if shader != 0x0000002B188360D9:
        return

    uniforms = dict(slot.shader_uniforms)
    exponent = uniforms.get("FresnelExponent", ())
    depth = uniforms.get("DepthScale", ())

    if slot.solid_color is None or len(exponent) != 1 or len(depth) != 1 or not math.isfinite(exponent[0]) or exponent[0] <= 0 or not math.isfinite(depth[0]) or depth[0] <= 0:
        raise ValueError("Missing or invalid source eye-overlay properties. Regenerate using the current material parser")

    alpha = slot.solid_color[3]
    extras = material.setdefault("extras", {})
    extras["siegeEyeOverlayPreviewV1"] = [alpha, exponent[0]]
    extras["siegeOpticalApproximation"] = (
        "Source color, alpha and angular opacity, depth fade approximated as 1, "
        "studio reflections replace the source matcap, no game shader parity"
    )

    material["alphaMode"] = "BLEND"
    material.pop("alphaCutoff", None)
    material["pbrMetallicRoughness"]["baseColorFactor"][3] = alpha