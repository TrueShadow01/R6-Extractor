import struct
from PIL import Image

def decode_bc1(surface, width, height):
    img = bytearray(width * height * 4)
    blocks_x = width // 4

    def rgb565(c):
        r = (c >> 11) & 0x1F
        g = (c >> 5) & 0x3F
        b = c & 0x1F
        return(r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)
    
    for i in range(len(surface) // 8):
        block = surface[i * 8:i * 8 + 8]
        c0, c1 = struct.unpack("<HH", block[:4]) # 2 RGB565 endpoint colors
        bits = struct.unpack("<I", block[4:8])[0] # 16 * 2bit pixel indices

        colors = [rgb565(c0), rgb565(c1)]
        if c0 > c1: # 4 color block, interpolate the 2 colors
            colors.append(tuple((2 * colors[0][k] + colors[1][k]) // 3 for k in range(3)))
            colors.append(tuple((colors[0][k] + 2 * colors[1][k]) // 3 for k in range(3)))
        else: # 3 color block, 1 interpolated + transparent black
            colors.append(tuple((colors[0][k] + colors[1][k]) // 2 for k in range(3)))
            colors.append((0, 0, 0))
        
        ox = (i % blocks_x) * 4
        oy = (i // blocks_x) * 4
        for py in range(4):
            for px in range(4):
                idx = (bits >> (2 * (py * 4 + px))) & 3
                o = ((oy + py) * width + (ox + px)) * 4
                img[o:o + 3] = bytes(colors[idx])
                img[o + 3] = 255

    return bytes(img)

def save_png(path, surface, width, height):
    rgba = decode_bc1(surface, width, height)
    Image.frombytes("RGBA", (width, height), rgba).save(path)