# Forge Extractor

A tool for extracting game assets (meshes, textures, audio) from Ubisoft
"Scimitar" `.forge` archives (AnvilNext 2.0 engine).

## Status

- [x] Parse container header and locate entries
- [x] Oodle (Kraken) chunk decompression via `oo2core`
- [x] Reassemble full asset payloads from chunks
- [x] Identify asset types by magic / structure
- [x] Textures -> PNG (BC1/BC3/BC4/BC5, format auto-detected, full-tier)
- [~] Meshes -> OBJ (header parsing done; vertices/faces next)
- [ ] Audio (Wwise .bnk/.pck)

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

Create a `config.py` in the project root with your local game install path:

```python
# config.py
GAME_DIR = r"Path To Tom Clancy's Rainbow Six Siege"
```

## Usage

```
python main.py
```

## Note

For personal datamining only. Extracted assets are copyrighted; do not
redistribute them.
