"""Bake resolved patterns for the experimental 841DC11F9 material"""

import math
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageMath

def _linear(value):
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

def _repeat_texture(path, size, scale):
    if any(not math.isfinite(v) or v <= 0 or v > 16 for v in scale):
        raise ValueError(f"Unsupported tint texture tiling: {scale}")

    with Image.open(path) as image:
        image = image.convert("RGB")
        nx, ny = (math.ceil(v) + 2 for v in scale)
        tiled = Image.new("RGB", (image.width * nx, image.height * ny))

        for y in range(ny):
            for x in range(nx):
                tiled.paste(image, (x * image.width, y * image.height))
        
        return tiled.transform(
            size,
            Image.Transform.AFFINE,
            (
                image.width * scale[0] / size[0], 0, image.width,
                0, image.height * scale[1] / size[1], image.height
            ),
            resample=Image.Resampling.BILINEAR
        )

def pattern_image(source, colors, pattern_path, detail_path=None):
    if colors[4][3] != 0.0:
        raise ValueError("Pattern recoloring is not implemented for this material")

    rgba = source.convert("RGBA")
    alpha = rgba.getchannel("A")
    a, b, c, d, e, f, g = colors

    pattern = _repeat_texture(pattern_path, rgba.size, (c[3], d[3]))
    detail = _repeat_texture(detail_path, rgba.size, (a[3], b[3])) if detail_path is not None else None

    linear = [_linear(i / 255) for i in range(256)]
    weight = alpha.point([max(0, 2 * i / 255  - 1) for i in range(256)], "F")
    low = alpha.point([i / 255 - max(0, 2 * i / 255 - 1) for i in range(256)], "F")
    middle = alpha.point([float(round(2 * i / 255) == 1) for i in range(256)], "F")
    high = alpha.point([float(round(2 * i / 255) == 2) for i in range(256)], "F")

    channels = []
    for channel in range(3):
        base = rgba.getchannel(channel).point(linear, "F")
        mask = pattern.getchannel(channel).point(linear, "F")
        custom0 = detail.getchannel(channel).point([i / 255 for i in range(256)], "F") if detail is not None else 1.0

        value = ImageMath.lambda_eval(
            lambda q: q["base"] * 2 * (
                (0.5 + (a[channel] - 0.5) * q["low"]) * (1 - q["weight"]) + q["custom0"] * b[channel] * q["weight"]
            ) * (
                (1 - q["middle"] - q["high"]) + q["middle"] * _linear(g[channel]) + q["high"] * q["mask"]
            ),
            base=base,
            low=low,
            weight=weight,
            custom0=custom0,
            middle=middle,
            high=high,
            mask=mask
        )
        value = ImageMath.lambda_eval(
            lambda q: q["min"](q["max"](q["v"], 0), 1),
            v=value
        )
        encoded = ImageMath.lambda_eval(
            lambda q: q["convert"](
                255 * (
                    (q["v"] <= 0.0031308) * (12.92 * q["v"]) + (q["v"] > 0.0031308) * (1.055 * q["v"] ** (1 / 2.4) - 0.055)
                ) + 0.5,
                "L"
            ),
            v=value
        )
        channels.append(encoded)

    return Image.merge("RGBA", (*channels, alpha))

def bake_pattern_material(slot, directory, uniforms):
    if "ExperimentalTintPatternV1" in uniforms:
        return slot

    textures = dict(slot.shader_textures)
    pattern = textures.get("TintCustom1")
    detail = textures.get("TintCustom0")
    selectors = uniforms["TintSourceSelectors"]
    first_uid = int(selectors[2]) | (int(selectors[3]) << 32)

    # Mode 0 with a stored UID has unresolved selection behavior
    # Preserve the base texture until that behavior is verified
    if selectors[0] == 0 and first_uid != 0:
        return None

    if pattern is None or uniforms["TintSourceE"][3] != 0.0 or selectors[0] not in (0, 2) or selectors[4] not in (0, 2) or selectors[1] != 7 or selectors[5] != 3 or (first_uid != 0 and detail is None):
        return None

    directory = Path(directory)
    colors = [uniforms["TintSource" + name] for name in "ABCDEFG"]
    filename = f"{slot.material_uid:016X}_pattern_v1.png"

    with Image.open(directory / slot.diffuse) as source:
        baked = pattern_image(source, colors, directory / pattern, directory / detail if detail else None)
        baked.save(directory / filename)

    print(f"Pattern tint bake: {slot.material_uid:016X} ({pattern})", flush=True)
    return replace(
        slot,
        diffuse=filename,
        solid_color=(1.0, 1.0, 1.0, 1.0),
        shader_textures=slot.shader_textures + (("SourceAlbedo", slot.diffuse),),
        shader_uniforms=slot.shader_uniforms + (("ExperimentalTintPatternV1", (1.0,)),)
    )