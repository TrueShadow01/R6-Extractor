# Rainbow Six Siege Forge Extractor

A Python toolkit for extracting assets from Rainbow Six Siege `.forge`
archives.

The main goal is to export complete models for Blender including geometry,
materials, textures, skeletons, weights and animations.

Unsupported assets are preserved as decompressed binary files instead of being
silently discarded.

## Current support

### Extraction

- Scimitar archive and container parsing
- Oodle Kraken decompression
- Bounded-memory archive scanning
- Validated asset UID and file-type indexing
- Lossless raw extraction
- Collision-safe output names
- JSONL extraction manifest
- Interrupted-extraction resume support
- Atomic output writes

### Conversion

- BC1, BC3, BC4 and BC5 textures to PNG
- Wwise audio to WEM
- Optional WEM-to-WAV conversion with vgmstream
- Float and packed-position meshes
- UV coordinates and normals
- LOD0 OBJ export
- Basic OBJ, MTL and linked-texture model export

## Major limitations

- The current OBJ model exporter still uses the older archive-loading path
- Streamed and virtual textures are not reconstructed
- Composite models currently export only one geometry child
- Material support is limited
- Skeletons, weights and animations are not yet supported
- Model export currently requires a known Mesh UID

## Requirements

- Windows
- 64-bit Python 3.10 or newer
- Pillow
- A compatible `oo2core_*_win64.dll`
- vgmstream for optional WAV conversion

Install the Python dependency:

```powershell
py -3 -m pip install -r requirements.txt
```

Place the Oodle DLL in the project root:

```text
R6\oo2core_8_win64.dll
```

Alternatively:

```powershell
$env:R6_OODLE_DLL = "Path\to\oo2core_8_win64.dll"
```

## Game directory

Create `config.py` in the project root:

```python
GAME_DIR = r"Path\to\Tom Clancy's Rainbow Six Siege"
```

This file is ignored by Git.

## CLI

Inspect one archive without extracting:

```powershell
py -3 -B main.py scan <archive.forge>
```

Scan every Forge archive und `GAME_DIR`:

```powershell
py -3 -B main.py scan --all
```

Losslessly extract one archive:

```powershell
py -3 -B main.py extract <archive.forge> -o output/raw
```

Resume is enabled by default. Use `--no-resume` to force re-extraction.

Discover model UIDs and geometry-part counts:

```powershell
py -3 -B main.py models <archive.forge>
```

Show only composite models:

```powershell
py -3 -B main.py models <archive.forge> --minimum-parts 2
```

Export the complete model catalog as JSON:

```powershell
py -3 -B main.py models datapc64_mtx_bnk_mesh --json-output output/datapc64_mtx_models.json
```

Run the current OBJ model exporter:

```powershell
py -3 -B main.py model <archive.forge> --uid <modelUID> -o output/model
```

The current OBJ exporter still handles only one geometry child and uses the older archive-loading path. It will be replaced before composite or GLB export.

## Audio extraction and conversion

Siege audio commonly uses Wwise WEM streams. The existing audio module can find embedded `RIFF/WAVE` streams in decompressed asset payloads.

Extract WEM files from a raw `.bin` asset:

```python
from pathlib import Path

from src.audio import extract_wems

payload = Path(r"output/raw/<archive/<asset>.bin").read_bytes()

paths = extract_wems(payload, r"output/audio")

for path in paths:
  print(path)
```

Convert one WEM file to WAV with the bundled vgmstream executable:

```powershell
.\vgmstream-win64\vgmstream-cli.exe -o output/audio/sound.wav output/audio/sound.wem
```

The new CLI does not yet expose WEM extraction or WAV conversion directly. A future `audio` command will connect the existing audio module and bundled vgmstream executable to the main CLI.

## Lossless raw extraction

The new extraction API preserves every validated asset:

```python
import os

import config

from src.extractor import extract_raw_archive

archive = os.path.join(
    config.GAME_DIR,
    "datapc64_mtx_bnk_000000001_mesh.forge",
)

summary = extract_raw_archive(
    archive,
    r"D:\R6\output\raw",
    resume=True,
)

print(summary)
```

Output files use this format:

```text
<UID>_<FILETYPE>_<CONTAINER_OFFSET>.bin
```

Extraction results are recorded in:

```text
output/raw/manifest.jsonl
```

A completed asset is resumed only when its output still exists and has the
expected decompressed size.

## Blender roadmap

1. Discover model UIDs automatically
2. Validate all supported vertex layouts
3. Assemble every geometry child belonging to a model
4. Export static models as GLB
5. Resolve textures and PBR materials
6. Decode skeletons and skinning weights
7. Export rigged models
8. Decode and export animations
9. Add bulk model export

OBJ will remain available for diagnostics but GLB will become the preferred
Blender format.

## Verified smoke test

`datapc64_mtx_bnk_000000001_mesh.forge` currently produces:

```text
102 containers
51 file assets
51 companion containers
51 extracted raw assets
0 failures
0 scan errors
```

A second run resumes all 51 assets without rewriting them.

## Tests

Run the synthetic foundation tests with:

```powershell
py -3 -B -m unittest discover -s tests -v
```

The tests contain no game assets and do not require Oodle.

## Legal notice

This project is intended for personal research and interoperability work.

Rainbow Six Siege and its assets are owned by Ubisoft. Do not commit or
redistribute extracted assets. The proprietary Oodle runtime must not be
committed or redistributed.

## License

See [LICENSE](LICENSE)