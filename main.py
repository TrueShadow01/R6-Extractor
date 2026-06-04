import os
from src.parser import read_container, CONTAINER_MAGIC
from src.dds import write_dds_dxt1
from config import GAME_DIR

print("R6 Forge Extractor")

path = os.path.join(GAME_DIR, "datapc64_dmtx_bnk_textures3.forge")
with open(path, "rb") as f:
    data = f.read()

out = read_container(data, 0x303F)
print("Total:", len(out), "bytes")

offsets = []
i = 0
while True:
    j = data.find(CONTAINER_MAGIC, i)
    if j == -1:
        break
    offsets.append(j)
    i = j + 8

print(f"Found {len(offsets)} containers")

for off in offsets:
    try:
        out = read_container(data, off)
        print(f"0x{off:<8X} size={len(out):>8} head={out[:8].hex()}")
    except Exception as e:
        print(f"0x{off:<8X} ERROR: {e}")

tex = read_container(data, 0x303F)

width, height = 1024, 512
surface = tex[0x60:0x60 + width * height // 2] # BC1 = 0.5bytes per pixel

os.makedirs("output", exist_ok=True)
write_dds_dxt1("output/tex_303F.dds", width, height, surface)
print("Wrote output/tex_303F.dds, surface=", len(surface), "bytes")