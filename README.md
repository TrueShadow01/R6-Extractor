# Forge Extractor

A tool for extracting game assets (meshes, textures, audio) from Ubisoft
"Scimitar" `.forge` archives (AnvilNext 2.0 engine).

## Status

- [x] Parse container header and locate entries
- [x] Oodle (Kraken) chunk decompression via `oo2core`
- [x] Reassemble full asset payloads from chunks
- [x] Identify asset types by magic / structure
- [x] Textures -> PNG (BC1/BC3/BC4/BC5, format auto-detected, full-tier)
- [x] Audio -> .wem (raw soundmedia + embedded in soundbank containers),
  optional .wav via vgmstream
- [x] Meshes -> OBJ

Known limitation: streamed/virtual-texture tiers (a minority of texture entries)
are skipped, they use a tiled layout, not the standard surface+trailer one.

## Format notes

- `.forge` = header -> entry table -> descriptor section -> payloads
- Assets are keyed by numeric **UID**, not filenames (filenames are hashed)
- Container magic (u64 LE): `0x1015FA9957FBAA37`
- Each entry holds one or more **Datablock** chunks. Per chunk the table stores
  `[unpacked_size, packed_size]`; `unpacked > packed` means the chunk is
  Oodle-compressed (Kraken, header byte `0x8C`), otherwise it's stored raw
- Textures: format code in the trailer (2=BC1, 3=BC1 sRGB, 5=BC3, 6=BC5, 14=BC4);
  decoded by wrapping the surface in a DX10 DDS header and letting Pillow decode.
- Meshes: `CompiledMesh` magic `0xFC9E1595` anchors a header with vertex/face block
  lengths, revision, vertex stride, island (submesh) and LOD counts
- Audio: Wwise `.wem` (RIFF/WAVE, Wwise Vorbis). `soundmedia` forges store them raw
  and uncompressed, `soundbank` forges store them inside Oodle containers. Extraction
  scans decompressed payloads for `RIFF`/`WAVE` and dumps each stream, conversion to
  `.wav` is delegated to `vgmstream-cli`
- Offsets and format details shift between game versions, expect to re-verify
  after updates

## Requirements

- Python 3.x (with `Pillow` for texture decoding)
- `oo2core_*_win64.dll` placed in the project root (the Oodle runtime, sourced
  from any game that ships it, not redistributed here)
- `vgmstream-cli` is shipped with the repo (in the `vgmstream-win64/` folder) and is only
  needed for `--wav` audio conversion

## Setup

Optional: create a `config.py` in the project root with your local game install
path.<br>This lets you pass a bare archive name instead of a full path (see Usage).
Without it, you need to pass the full path.

```python
# config.py
GAME_DIR = r"Path To Tom Clancy's Rainbow Six Siege"
```

Audio Conversion to .wav files: Reference `vgmstream-cli.exe` at `vgmstream-win64` in your PATH to be able to convert the .wem files to .wav

## Usage

Point it at any `.forge`; it walks the containers and dispatches each payload by
type (textures -> PNG, audio -> .wem), so one command handles every archive kind:

```
python main.py <path-to.forge>                            # extract to ./output
python main.py <path-to.forge> -o <output-folder-name>    # custom output directory
python main.py <path-to.forge> -v                         # extracts every asset, not just a summary
python main.py <path-to.forge> --wav                      # also convert .wem -> .wav (needs vgmstream-cli)
```

With `config.py` set, a bare archive name (with or without the `.forge`
extension) is resolved against `GAME_DIR`:

```
python main.py <path-to-forge-texture-file>                             # textures
python main.py <path-to-forge-soundmedia-or-soundbank-file> --wav       # audio conversion to .wav file
python main.py <path-to-forge-soundmedia-or-soundbank-file>             # audio conversion to .wem file
python main.py <path-to-forgefile> -v                                   # enable verbose output (-v or --v)
```

Prints a summary like `N textures, X meshes, M .wem files -> output (K skipped)`.<br>Skipped entries
are unsupported payloads or streamed texture tiles (see limitation above).

## Note

For personal datamining only. Extracted assets are copyrighted; do not
redistribute them.
