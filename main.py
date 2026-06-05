import os
from src.parser import read_container, CONTAINER_MAGIC
from src.texture import save_png
from src.mesh import parse_mesh_header
from config import GAME_DIR

print("R6 Forge Extractor")

# Open the .forge file
path = os.path.join(GAME_DIR, "datapc64_mtx_set01_bnk_mesh.forge")
with open(path, "rb") as f:
    data = f.read()

# Create output directory to store data in
os.makedirs("output", exist_ok=True)

# Loop over the current .forge file
i = 0
while True:
    # check .forge magic
    j = data.find(CONTAINER_MAGIC, i)
    if j == -1:
        break
    i = j + 8
    # read container if magic matches
    payload = read_container(data, j)
    if len(payload) < 2000:
        continue # skip small meta blocks
    
    # Mesh Stuff

    # parse Mesh header and print header data
    h = parse_mesh_header(payload)
    for k, v in h.items():
        print(f"{k} = {v}")
    break
    # Texture Stuff (commented out for testing the whole mesh stuff)   

    # Try to batch export all textures in the give .forge file
    # try:
    #     w, h, fmt = save_png(f"output/tex{j:X}.png", payload)
    #     print(f"0x{j:X}: {w}x{h} fmt={fmt} -> output/tex{j:X}.png")
    # except ValueError as e:
    #     print(f"0x{j:X}: skip ({e})")