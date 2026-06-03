# Forge Extractor

A tool for extracting game assets (meshes, textures, audio) from Ubisoft
"Scimitar" `.forge` archives (AnvilNext 2.0 engine).

## Status

- [x] Parse container header and locate entries
- [x] Oodle (Kraken) chunk decompression via `oo2core`
- [x] Reassemble full asset payloads from chunks
- [ ] Identify asset types by magic / structure
- [ ] Convert: textures -> DDS/PNG, meshes -> glTF/OBJ

## Format notes

- `.forge` = header -> entry table -> descriptor section -> payloads.
- Assets are keyed by numeric **UID**, not filenames (filenames are hashed).
- Container magic (u64 LE): `0x1015FA9957FBAA37`.
- Each entry holds one or more **Datablock** chunks. Per chunk the table stores
  `[unpacked_size, packed_size]`; `unpacked > packed` means the chunk is
  Oodle-compressed (Kraken, header byte `0x8C`), otherwise it's stored raw.
- Offsets and format details shift between game versions — expect to re-verify
  after updates.

## Requirements

- Python 3.x
- `oo2core_*_win64.dll` placed in the project root (the Oodle runtime; sourced
  from any game that ships it — not redistributed here).

## Usage

```
python main.py
```

## Note

For personal datamining only. Extracted assets are copyrighted; do not
redistribute them.
