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
- Composite LOD0 OBJ and MTL export
- External glTF 2.0 export with binary buffers
- Diffuse and normal texture assignment
- Specular texture relationships preserved as metadata

## Major limitations

- Streamed and virtual textures are not reconstructed
- Packed material and specular channels are not decoded yet
- Cross-bundle dependencies such as `ondemand` metadata referencing `merged` assets are not resolved yet
- Only LOD0 geometry is exported
- Skeletons, weights and animations are not yet supported
- Model export requires a known Mesh UID
- GLB export is not available yet

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

Export one composite model as OBJ and glTF

```powershell
py -3 -B main.py model <archive.forge> --uid <modelUID> -o output/model
```

Every direct geometry child is assembled into the export. Decodable diffuse, normal and specular textures are extracted alongside the model. OBJ remains available for diagnostics, glTF is the preferred Blender import format.

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

1. Resolve cross-bundle `ondemand`and `merged`assets
2. Validate the remaining vertex layouts
3. Decode packed PBR material channels
4. Add GLB export
5. Decode skeletons and skinning weights
6. Export rigged models and animations
7. Add bulk model export

OBJ remains available for diagnostics, while glTF and eventually GLB are the preferred
Blender formats.

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