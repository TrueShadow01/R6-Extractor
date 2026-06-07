# Forge Extractor

A tool for extracting game assets (meshes, textures, audio) from Ubisoft
"Scimitar" `.forge` archives (AnvilNext 2.0 engine).

## Status

- [x] Parse container header and locate entries
- [x] Oodle (Kraken) chunk decompression via `oo2core`
- [x] Reassemble full asset payloads from chunks
- [x] Identify asset types by magic / structure
- [x] Textures -> PNG (BC1/BC3/BC4/BC5, format auto-detected, full-tier)
- [~] Meshes -> OBJ (in progress, see below)
- [ ] Audio (Wwise .bnk/.pck)

### Mesh progress (in progress)

Cracked so far: the `CompiledMesh` (`0xFC9E1595`) header and all block lengths, the
data-block layout (`VertBlockOffset = len - sum(block lengths)`), the face-index block
(2944 triangles, indices in range), and most vertex positions (~95%) as float32x3.

Still broken: a rendered OBJ comes out scrambled. This build's mesh format diverges
from the public `RainbowForge` reference in several places at once:
- The reference reads faces as one global triangle list, but this build's indices
  behave like **per-island local** indices (repeats like `654,654` / `656,656`);
  read globally they connect the wrong vertices
- The per-island header table (vertex base + counts, needed to fix the indices)
  isn't where the reference puts it, the post-header region is bounding-box/skin
  float data, not the table
- Positions don't fully decode under either planar or interleaved reading, with a
  persistent unexplained ~2240-byte float region

Net: full mesh reconstruction needs a dedicated reverse-engineering pass against this
exact game version.

Known limitation: streamed/virtual-texture tiers (a minority of texture entries)
are skipped, they use a tiled layout, not the standard surface+trailer one.

## Format notes

- `.forge` = header -> entry table -> descriptor section -> payloads.
- Assets are keyed by numeric **UID**, not filenames (filenames are hashed).
- Container magic (u64 LE): `0x1015FA9957FBAA37`.
- Each entry holds one or more **Datablock** chunks. Per chunk the table stores
  `[unpacked_size, packed_size]`; `unpacked > packed` means the chunk is
  Oodle-compressed (Kraken, header byte `0x8C`), otherwise it's stored raw.
- Textures: format code in the trailer (2=BC1, 3=BC1 sRGB, 5=BC3, 6=BC5, 14=BC4);
  decoded by wrapping the surface in a DX10 DDS header and letting Pillow decode.
- Meshes: `CompiledMesh` magic `0xFC9E1595` anchors a header with vertex/face block
  lengths, revision, vertex stride, island (submesh) and LOD counts.
- Offsets and format details shift between game versions, expect to re-verify
  after updates.

## Requirements

- Python 3.x
- `oo2core_*_win64.dll` placed in the project root (the Oodle runtime; sourced
  from any game that ships it, not redistributed here).

## Setup

Optional: create a `config.py` in the project root with your local game install
path.<br>This lets you pass a bare archive name instead of a full path (see Usage).
Without it, you need to pass the full path.

```python
# config.py
GAME_DIR = r"Path To Tom Clancy's Rainbow Six Siege"
```

## Usage

Extract all textures from a `.forge` into an output directory:

```
python main.py <path-to.forge>                            # writes PNGs to ./output
python main.py <path-to.forge> -o <output-folder-name>    # custom output directory
python main.py <path-to.forge> -v                         # list every asset, not just a summary
```

With `config.py` set, a bare archive name (with or without the `.forge`
extension) is resolved against `GAME_DIR`:

```
python main.py "datapc64_dmtx_bnk_textures3"               # found under GAME_DIR
```

Prints a summary like `N textures -> output (M skipped)`. Skipped entries are
non-texture payloads or streamed tiles (see limitation above).

## Note

For personal datamining only. Extracted assets are copyrighted; do not
redistribute them.
