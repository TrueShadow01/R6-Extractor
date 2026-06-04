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
    mi = payload.find(TEXMAPDATA_MAGIC)
    if mi == -1:
        raise ValueError("Not a texture payload")
    pixel_start = mi + 12 # skip magic + data1 + numBlocks + data2 + 0x30

    tail = payload[-120:]
    base = len(payload) - len(tail)
    dpos = width = height = None
    for k in range(len(tail) - 8):
        a = struct.unpack("<I", tail[k:k + 4])[0]
        b = struct.unpack("<I", tail[k + 4:k + 8])[0]
        if a in POW2 and b in POW2:
            width, height, dpos = a, b, base + k
            break

    if dpos is None:
        raise ValueError("Could not find dimensions")
    
    fmt = struct.unpack("<I", payload[dpos + 32:dpos + 36])[0]
    surface = payload[pixel_start:dpos]
    return width, height, fmt, surface

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
    width, height, fmt, surface = parse_texture(payload)
    if fmt not in FORMATS:
        raise ValueError(f"unsupported format code {fmt}")
    
    dxgi, block = FORMATS[fmt]
    expected = (width // 4) * (height // 4) * block
    if len(surface) != expected:
        raise ValueError(f"partial tier ({len(surface)} != {expected})")

    dds = _dds_dx10(width, height, surface, dxgi)
    Image.open(io.BytesIO(dds)).convert("RGBA").save(path)
    return width, height, fmt