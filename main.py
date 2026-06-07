import os
import argparse
from src.parser import read_container, CONTAINER_MAGIC, parse_header
from src.texture import save_png, TEXMAPDATA_MAGIC
from src.audio import extract_wems, wem_to_wav

# Find a .forge file: use the path given or look it up under GAME_DIR set in config.py
# Resolves bare archive names (without .forge extension)
def resolve_forge(name):
    if os.path.isfile(name):
        return name
    try:
        from config import GAME_DIR
    except ImportError:
        return None
    canditate = name if name.lower().endswith(".forge") else name + ".forge"
    full = os.path.join(GAME_DIR, canditate)
    return full if os.path.isfile(full) else None

# Yield the file offset of very container in the .forge file
def find_containers(data):
    i = 0
    while True:
        j = data.find(CONTAINER_MAGIC, i)
        if j == -1:
            return
        yield j
        i = j + 8

def extract(forge, out_dir, want_wav=False, verbose=False):
    with open(forge, "rb") as f:
        data = f.read()
    os.makedirs(out_dir, exist_ok=True)
    tex = wem = skip = 0

    if CONTAINER_MAGIC in data:
        for off in find_containers(data):
            payload = read_container(data, off)
            if len(payload) < 200:
                continue
            if TEXMAPDATA_MAGIC in payload: # texture
                try:
                    w, h, fmt = save_png(os.path.join(out_dir, f"tex_{off:X}.png"), payload)
                    tex += 1
                    if verbose:
                        print(f"tex 0x{off:X}: {w}x{h} fmt={fmt}")
                except ValueError as e:
                    skip += 1
                    if verbose:
                        print(f"skip 0x{off:X} ({e})")
            else: # audio ? (bank/wem)
                got = extract_wems(payload, out_dir, prefix=f"snd_{off:X}")
                if got:
                    wem += len(got)
                else:
                    skip += 1
    else: # raw .wem (soundmedia)
        wem = len(extract_wems(data, out_dir))
    
    if want_wav:
        for fn in os.listdir(out_dir):
            if fn.endswith(".wem"):
                wem_to_wav(os.path.join(out_dir, fn))
    return tex, wem, skip

def main():
    print("R6 Forge Extractor")

    ap = argparse.ArgumentParser(description="Extract assets from Rainbow Six Siege .forge archive")
    ap.add_argument("forge", help="path to a .forge file")
    ap.add_argument("-o", "--out", default="output", help="output directory (default: output)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every asset, not just the summary")
    ap.add_argument("--wav", action="store_true", help="Convert extracted .wem files to .wav files (requires vgmstream-cli on PATH)")
    args = ap.parse_args()

    forge = resolve_forge(args.forge)
    if forge is None:
        ap.error(f".forge file not found: {args.forge} (looked in cwd and GAME_DIR)")

    with open(forge, "rb") as f:
        data = f.read()
    
    tex, wem, skip = extract(forge, args.out, args.wav, args.verbose)
    print(f"{tex} textures, {wem} .wem files -> {args.out} ({skip} skipped)")

if __name__ == "__main__":
    main()