import struct

def write_dds_dxt1(path, width, height, surface):
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 # caps, height, width, pixelformat, linearsize

    h = b"DDS "
    h += struct.pack("<I", 124) # dwSize
    h += struct.pack("<I", flags) # dwFlags
    h += struct.pack("<I", height)
    h += struct.pack("<I", width)
    h += struct.pack("<I", len(surface)) # dwPitchOrLinearSize
    h += struct.pack("<I", 0) # dwDepth
    h += struct.pack("<I", 0) # dwMipMapCount
    h += b"\x00" * 44 # dwReserved[11]
    # DDS_PIXELFORMAT (32bytes)
    h += struct.pack("<I", 32) # dwSize
    h += struct.pack("<I", 0x4) # DDPF_FOURCC
    h += b"DXT1" # dwFourCC
    h += b"\x00" * 20 # bitCount + 4 channel masks
    # caps
    h += struct.pack("<I", 0x1000) # DDSCAPS_TEXTURE
    h += b"\x00" * 16 # caps2, caps3, caps4, reserved2

    with open(path, "wb") as f:
        f.write(h)
        f.write(surface)