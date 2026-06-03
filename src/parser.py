import os
import struct

def parse_header(path):
    f = open(path, "rb")
    if f.read(9) == b"scimitar\x00":
        print("Magic OK")

        f.seek(0)
        data = f.read()
        magic = b'\x37\xAA\xFB\x57'
        pos = 0
        found = 0
        while found < 5:
            idx = data.find(magic, pos)
            if idx == -1:
                break
            print(f"Container magic at 0x{idx:X} ({idx})")
            pos = idx + 4
            found += 1

        for addr in [0x7A, 0x100, 0x1000, 0x5000, 0xF000, 0x12FF8]:
            f.seek(addr)
            print(f"0x{addr:X}: {f.read(32).hex()}")

        f.seek(0)
        data = f.read(0x13000)

        for target in [0x13000, 0x14000, 0x15000]:
            b = struct.pack("<I", target) # 4 byte LE
            pos = 0
            while True:
                idx = data.find(b, pos)
                if idx == -1:
                    break
                print(f"Value 0x{target:X} found at file offset 0x{idx:X}")
                pos = idx + 1
    else:
        raise ValueError("Magic invalid, not a scimitar file")