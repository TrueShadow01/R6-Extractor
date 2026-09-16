"""Experimental reconstruction of shader 841DC11F9's color stage"""

import math
import struct
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageMath

SHADER = 0x841DC11F9
PREFIX = struct.pack("<III", 0xFBF80000, 0, 0xF2CE7E39)

def read_tint_parameters(blob):
    start = blob.find(PREFIX)
    if start < 0 or start + 188 > len(blob):
        return ()

    classes = [
        struct.unpack_from("<I", blob, start + offset)[0]
        for offset in (20, 88)
    ]
    if classes != [0x7C4A77EA, 0x7C4A77EA]:
        return ()

    colors = [
        struct.unpack_from("<4f", blob, start + offset)
        for offset in (40, 56, 108, 124, 140, 156, 172)
    ]
    if not all(math.isfinite(v) for color in colors for v in color):
        return ()
    if not all(0 <= v <= 1 for color in colors for v in color[:3]):
        return ()

    selectors = []
    for offset in (24, 92):
        method, role, uid = struct.unpack_from("<IIQ", blob, start + offset)
        selectors.extend((method, role, uid & 0xFFFFFFFF, uid >> 32))

    return tuple((f"TintSource{name}", tuple(color)) for name, color in zip("ABCDEFG", colors)) + (("TintSourceSelectors", tuple(selectors)),)

def tint_image(source, colors, custom_mask=None):
    rgba = source.convert("RGBA")
    alpha = rgba.getchannel("A")

    linear = [
        v / 12.92
        if v <= 0.04045
        else ((v + 0.055) / 1.055) ** 2.4
        for v in (n / 255 for n in range(256))
    ]

    # A/B use 0.5 as mathematical neutral, preserve them
    a, b = colors[:2]

    def linear_region(color):
        # Experimental: source region RGB may be stored as sRGB
        # leave the scalar in W out of the paint bucket - Victor
        return tuple(
            value / 12.92
            if value < 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
            for value in color[:3]
        ) + (color[3],)

    c, d, e, f, g = (linear_region(color) for color in colors[2:])

    if custom_mask is not None:
        custom_mask = linear_region(tuple(custom_mask) + (1.0,))[:3]
    channels = []

    for channel in range(3):
        table = []

        for alpha_byte in range(256):
            opacity = alpha_byte / 255
            weight = max(0.0, min(1.0, 2 * opacity - 1))
            low = max(0.0, min(1.0, opacity - weight))

            tint = (0.5 + (a[channel] - 0.5) * low) * (1 - weight)
            tint += b[channel] * weight
            gain = 2 * tint

            category = round(2 * opacity)
            if category == 2:
                if custom_mask is not None:
                    gain *= custom_mask[channel]
                else:
                    gain *= c[channel] * e[3]
            elif category == 1:
                gain *= g[channel]

            for value in linear:
                value = max(0.0, min(1.0, value * gain))
                encoded = (
                    12.92 * value
                    if value <= 0.0031308
                    else 1.055 * value ** (1 / 2.4) - 0.055
                )
                table.append(round(encoded * 255))
        indices = ImageMath.lambda_eval(
            lambda args: args["alpha"] * 256 + args["channel"],
            alpha=alpha.convert("I"),
            channel=rgba.getchannel(channel).convert("I")
        )
        channels.append(indices.point(table, "L"))

    return Image.merge("RGBA", (*channels, alpha))

def bake_tinted_material(slot, output_directory):
    if slot.shader_uid != SHADER or not slot.diffuse:
        return slot

    uniforms = dict(slot.shader_uniforms)
    if "ExperimentalTintRegionSRGBV3" in uniforms:
        return slot

    selectors = uniforms.get("TintSourceSelectors", ())
    names = [f"TintSource{name}" for name in "ABCDEFG"]

    if len(selectors) != 8 or any(name not in uniforms for name in names):
        print(f"WARNING: Tint bake skipped for {slot.material_uid:016X}: Missing source parameters", flush=True)
        return slot

    first_is_default = selectors[0] == 0 and selectors[1] == 7 and selectors[2] == 0 and selectors[3] == 0
    second_uid = int(selectors[6]) | (int(selectors[7]) << 32)
    second_is_default = selectors[4] == 0 and second_uid == 0

    # Shared spec 119CC47A7 -> Map 119CC47A9 -> Compiled 359E631C7
    # All 64 source pixels are RGBA (128, 128, 128, 255)
    second_is_verified_gray = selectors[4] == 2 and second_uid == 0x119CC47A7 and uniforms["TintSourceE"][3] == 0.0

    if not first_is_default or selectors[5] != 3 or not (second_is_default or second_is_verified_gray):
        print(f"WARNING: Tint bake skipped for {slot.material_uid:016X}: Unresolved custom texture", flush=True)
        return slot

    custom_mask = (
        (128 / 255,) * 3
        if second_is_verified_gray
        else None
    )

    directory = Path(output_directory)
    filename = f"{slot.material_uid:016X}_tint_v3.png"

    with Image.open(directory / slot.diffuse) as source:
        baked = tint_image(source, [uniforms[name] for name in names], custom_mask=custom_mask)
        baked.save(directory / filename)

    mask_description = (
        "verified shared gray texture"
        if custom_mask is not None
        else "provisional black texture"
    )

    print(f"Experimental tint bake: {slot.material_uid:016X} (custom mask: {mask_description})", flush=True)

    extra_uniforms = (("ExperimentalTintRegionSRGBV3", (1.0,)),)
    if custom_mask is not None:
        extra_uniforms += (("ResolvedTintCustomMaskSRGB", custom_mask),)

    return replace(
        slot,
        diffuse=filename,
        solid_color=(1.0, 1.0, 1.0, 1.0),
        shader_textures=slot.shader_textures + (("SourceAlbedo", slot.diffuse),),
        shader_uniforms=slot.shader_uniforms + extra_uniforms,
    )
