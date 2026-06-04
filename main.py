import os
from src.parser import read_container, CONTAINER_MAGIC
from src.texture import save_png
from config import GAME_DIR

print("R6 Forge Extractor")

path = os.path.join(GAME_DIR, "datapc64_dmtx_bnk_textures3.forge")
with open(path, "rb") as f:
    data = f.read()

os.makedirs("output", exist_ok=True)

i = 0
while True:
    j = data.find(CONTAINER_MAGIC, i)
    if j == -1:
        break
    i = j + 8
    payload = read_container(data, j)
    if len(payload) < 2000:
        continue # skip small meta blocks

    try:
        w, h, fmt = save_png(f"output/tex{j:X}.png", payload)
        print(f"0x{j:X}: {w}x{h} fmt={fmt} -> output/tex{j:X}.png")
    except ValueError as e:
        print(f"0x{j:X}: skip ({e})")