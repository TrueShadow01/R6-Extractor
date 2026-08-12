# Decode .forge texture payloads (BCn compressed) to PNG via DDS -> Pillow

import struct
import io
import math
from PIL import Image

POW2 = {64, 128, 256, 512, 1024, 2048, 4096}
TEXMAPDATA_MAGIC = bytes.fromhex("3d4b0cc3")

# Siege texture format code (DXGI format, bytes per 4x4 block)
# Code is read from the trailer at dims_pos + 32
FORMATS = {
    2: (71, 8),     # BC1 (diffuse)
    3: (71, 8),     # BC1 (sRGB variant)
    4: (77, 16),    # BC3 (alternate diffuse code)
    5: (77, 16),    # BC3 (diffuse + alpha)
    6: (83, 16),    # BC5 (normal maps)
    14: (80, 8)     # BC4 (single channel mask)
}

BC5_Z_TABLE = bytes(
    round(
        (
            math.sqrt(
                max(
                    0.0,
                    1.0 - (red / 127.5 - 1) ** 2 - (green / 127.5 - 1.0) ** 2
                )
            )
            * 0.5
            + 0.5
        )
        * 255
    )
    for red in range(256)
    for green in range(256)
)

def reconstruct_bc5_z(image: Image.Image) -> Image.Image:
    """Reconstruct the positive Z component of a two-channel BC5 normal"""

    image = image.convert("RGBA")
    pixels = bytearray(image.tobytes())

    for offset in range(0, len(pixels), 4):
        red = pixels[offset]
        green = pixels[offset + 1]

        pixels[offset + 2] = BC5_Z_TABLE[(red << 8) | green]

    return Image.frombytes("RGBA", image.size, bytes(pixels))

# Return (width, height, format_code, surface) from a texture payload
# Pixel data starts 12 bytes after the CompiledTextureMapData magic,
# width/height live in the trailer, we accepd a power-of-two (w, h)
# pair only when the surface size equals (w/4) * (h/4) * blocksize,
# that size check refects coincidental matches. (prevents most false positives)
def parse_texture(payload):
    tPayload = payload.find(TEXMAPDATA_MAGIC)
    if tPayload == -1:
        raise ValueError("Not a texture payload")
    pixel_start = tPayload + 12 # skip magic + data1 + numBlocks + data2 + 0x30

    tail_start = max(pixel_start, len(payload) - 200)
    for dpos in range(tail_start, len(payload) - 36):
        w = int.from_bytes(payload[dpos:dpos + 4], "little")
        h = int.from_bytes(payload[dpos + 4:dpos + 8], "little")
        if w not in POW2 or h not in POW2:
            continue
        surf = dpos - pixel_start
        blocks = (w // 4) * (h // 4)
        if surf == blocks * 8 or surf == blocks * 16: # dimensions confirmed by size
            fmt = int.from_bytes(payload[dpos + 32:dpos + 36], "little")
            textype = int.from_bytes(payload[dpos + 44:dpos + 48] ,"little")
            return w, h, fmt, textype, payload[pixel_start:dpos]
    
    raise ValueError("No Full Tier Surface (partial tier or unrecognized)")

# Wrap a raw BCn surface in DX10-header DDS so Pillow can decode it
def _dds_dx10(width, height, surface, dxgi):
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000

    h = b"DDS "
    h += struct.pack("<I", 124) + struct.pack("<I", flags)
    h += struct.pack("<I", height) + struct.pack("<I", width)
    h += struct.pack("<I", len(surface)) + struct.pack("<I", 0) + struct.pack("<I", 1)
    h += b"\x00" * 44 # reserved
    h += struct.pack("<I", 32) + struct.pack("<I", 0x4) + b"DX10" + b"\x00" * 20
    h += struct.pack("<I", 0x1000) + b"\x00" * 16
    # DX10 header: dxgiFormat, dimension=TEXTURE2D(3), miscFlag, arraySize, miscFlags2
    h += struct.pack("<I", dxgi) + struct.pack("<I", 3)
    h += struct.pack("<I", 0) + struct.pack("<I", 1) + struct.pack("<I", 0)
    return h + surface


def save_png(path, payload):
    w, h, fmt, textype, surface = parse_texture(payload)
    if fmt not in FORMATS:
        raise ValueError(f"Unsupported Format Code {fmt} ({w}x{h})")
    dxgi, _ = FORMATS[fmt]
    dds = _dds_dx10(w, h, surface, dxgi)
    with Image.open(io.BytesIO(dds)) as source:
        image = source.convert("RGBA")

    if fmt == 6 and textype == 1:
        image = reconstruct_bc5_z(image)

    # Some Siege diffuse maps contain valid RGB texture but use a
    # zero unused alpha channel. Blender premultiplies those
    # pixels to black, only fully-zero diffuse alpha channels
    # should be opaque. Preserve alpha when it contains real data.

    if (textype == 0 and image.getextrema()[3] == (0, 0)):
        image.putalpha(255)

    image.save(path)

    return w, h, fmt, textype