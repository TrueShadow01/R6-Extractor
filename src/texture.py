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
    14: (80, 8),    # BC4 (single channel mask)
    15: (98, 16),   # BC7
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

def compressed_mip_chain_size(width: int, height: int, block_size: int) -> int:
    """Return the exact byte size of a complete BCn mip chain"""

    total = 0

    while True:
        blocks = (
            ((width + 3) // 4)
            * ((height + 3) // 4)
        )

        total += blocks * block_size

        if width == 1 and height == 1:
            return total

        width = max(1, width // 2)
        height = max(1, height // 2)

def parse_texture(payload):
    texture_offset = payload.find(TEXMAPDATA_MAGIC)

    if texture_offset == -1:
        raise ValueError("Not a texture payload")

    pixel_start = texture_offset + 12
    tail_start = max(pixel_start, len(payload) - 200)

    # A complete current texture trailer requires at least 48 bytes
    for dimension_offset in range(tail_start, len(payload) - 47):
        stored_width = int.from_bytes(
            payload[dimension_offset: dimension_offset + 4],
            "little"
        )
        stored_height = int.from_bytes(
            payload[dimension_offset + 4: dimension_offset + 8],
            "little"
        )

        if stored_width not in POW2 or stored_height not in POW2:
            continue

        channel_shift = int.from_bytes(
            payload[dimension_offset  + 16:dimension_offset + 20],
            "little"
        )

        if channel_shift > 16:
            continue

        width = stored_width >> channel_shift
        height = stored_height >> channel_shift

        if width <= 0 or height <= 0:
            continue

        surface_size = dimension_offset - pixel_start

        format_code = int.from_bytes(
            payload[dimension_offset + 32: dimension_offset + 36],
            "little"
        )

        format_info = FORMATS.get(format_code)

        surface_end = dimension_offset

        if format_code == 0:
            # PixelFormat_RGBA8888 is stored as 4 BGRA bytes per pixel
            if surface_size != width * height * 4:
                continue
        else:
            blocks = ((width + 3) // 4) * ((height + 3) // 4)

            if format_info is not None:
                _, block_size = format_info
                top_level_size = blocks * block_size
                mip_chain_size = compressed_mip_chain_size(width, height, block_size)

                if surface_size == top_level_size:
                    pass
                elif surface_size == mip_chain_size:
                    # PNG export only needs the largest mip
                    surface_end = pixel_start + top_level_size
                else:
                    continue
            elif surface_size not in (blocks * 8, blocks * 16):
                # Preserve detection of unknown block formats so save_png()
                # can report their numeric format code
                continue

        texture_type = int.from_bytes(
            payload[dimension_offset + 44: dimension_offset + 48],
            "little"
        )

        return(
            width,
            height,
            format_code,
            texture_type,
            payload[pixel_start:surface_end]
        )
    
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
    width, height, format_code, texture_type, surface = parse_texture(payload)
    if format_code == 0:
        image = Image.frombytes("RGBA", (width, height), surface, "raw", "BGRA")
    else:
        if format_code not in FORMATS:
            raise ValueError(f"Unsupported Format Code {format_code} ({width}x{height})")

        dxgi, _ = FORMATS[format_code]
        dds = _dds_dx10(width, height, surface, dxgi)

        with Image.open(io.BytesIO(dds)) as source:
            image = source.convert("RGBA")

    if format_code == 6 and texture_type == 1:
        image = reconstruct_bc5_z(image)

    # Some Siege diffuse maps contain valid RGB texture but use a
    # zero unused alpha channel. Blender premultiplies those
    # pixels to black. Only fully-zero diffuse alpha channels
    # should become opaque.
    if texture_type == 0 and image.getextrema()[3] == (0, 0):
        image.putalpha(255)

    image.save(path)

    return width, height, format_code, texture_type