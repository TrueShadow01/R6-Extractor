import struct
import io
from PIL import Image

POW2 = {64, 126, 256, 512, 1024, 2048, 4096}
TEXMAPDATA_MAGIC = bytes.fromhex("3d4b0cc3")

FORMATS = {
    2: (71, 8), # BC1UNORM (diffuse)
    5: (77, 16), # BC3UNORM (diffuse + alpha)
    6: (83, 16), # BC5UNORM (normal maps)
    14: (80, 8) # BC4 UNORM (single channel mask)
}

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
            return w, h, fmt, payload[pixel_start:dpos]
    
    raise ValueError("No Full Tier Surface (partial tier or unrecognized)")

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
    w, h, fmt, surface = parse_texture(payload)
    if fmt not in FORMATS:
        raise ValueError(f"Unsupported Format Code {fmt} ({w}x{h})")
    dxgi, _ = FORMATS[fmt]
    dds = _dds_dx10(w, h, surface, dxgi)
    Image.open(io.BytesIO(dds)).convert("RGBA").save(path)
    return w, h, fmt