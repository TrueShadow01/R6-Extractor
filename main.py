import os
import argparse
from src.parser import read_container, CONTAINER_MAGIC, parse_header
from src.texture import save_png
from src.mesh import parse_mesh_header
from config import GAME_DIR

# Yield the file offset of very container in the .forge file
def find_containers(data):
    i = 0
    while True:
        j = data.find(CONTAINER_MAGIC, i)
        if j == -1:
            return
        yield j
        i = j + 8

def extract_textures(forge_path, out_dir, verbose=False):
    parse_header(forge_path) # validate scimitar
    with open(forge_path, "rb") as f:
        data = f.read()
    os.makedirs(out_dir, exist_ok=True)
    exported = skipped = 0
    for off in find_containers(data):
        payload = read_container(data, off)
        if len(payload) < 2000:
            continue # skip meta blocks
        out = os.path.join(out_dir, f"tex_{off:X}.png")
        try:
            w, h, fmt = save_png(out, payload)
            exported += 1
            if verbose:
                print(f"0x{off:X}: {w}x{h} fmt={fmt} -> {out}")
        except ValueError as e:
            skipped += 1
            if verbose:
                print(f"0x{off:X}: skip ({e})")
    return exported, skipped

def main():
    print("R6 Forge Extractor")

    ap = argparse.ArgumentParser(description="Extract assets from Rainbow Six Siege .forge archive")
    ap.add_argument("forge", help="path to a .forge file")
    ap.add_argument("-o", "--out", default="output", help="output directory (default: output)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every asset, not just the summary")
    args = ap.parse_args()

    if not os.path.isfile(args.forge):
        ap.error(f"File not found: {args.forge}")

    exported, skipped = extract_textures(args.forge, args.out, args.verbose)
    print(f"{exported} textures -> {args.out} ({skipped} skipped)")

if __name__ == "__main__":
    main()