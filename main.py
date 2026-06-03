import os
from src.parser import read_container
from config import GAME_DIR

print("R6 Forge Extractor")

path = os.path.join(GAME_DIR, "datapc64_dmtx_bnk_textures3.forge")
with open(path, "rb") as f:
    data = f.read()

out = read_container(data, 0x303F)
print("Total:", len(out), "bytes")