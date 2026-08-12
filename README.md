# Rainbow Six Siege Forge Extractor

A Python toolkit for extracting assets from Rainbow Six Siege `.forge`
archives and exporting complete models for Blender.

The long-term goal is to support geometry, materials, textures, skeletons, weights and animations. Unsupported assets are preserved as decompressed binary files.

## Current support

- Scimitar archive and container parsing
- Oodle Kraken decompression
- Bounded-memory scanning and lossless raw extraction
- Collision-safe filenames, JSONL manifests and extraction resume
- Persistent SQLite indexing with unchanged archive skipping
- BC1, BC3, BC4 and BC5 textures to PNG
- Wwise audio extraction to WEM
- Optional WEM-to-WAV conversion with vgmstream
- Float and packed-position meshes with UVs and normals
- Composite LOD0 OBJ, MTL and glTF 2.0 export
- Diffuse and normal texture assignment
- Specular texture relationships stored as glTF metadata
- Explicit cross-bundle geometry resolution

## Limitations

- Automatic cross-bundle texture discovery is not available yet
- Streamed and virtual textures are not reconstructed
- Packed PBR and specular channels are not decoded
- Only LOD0 geometry is exported
- Skeletons, weights and animations are not supported
- Model export requires a known UID
- GLB export is not available

## Setup

Requirements:

- Windows
- 64-bit Python 3.10 or newer
- Pillow
- A compatible `oo2core_*_win64.dll`
- vgmstream for optional WAV conversion

Install Python dependency:

```powershell
py -3 -m pip install -r requirements.txt
```

Place the Oodle DLL in the project root:

```text
R6\oo2core_8_win64.dll
```

Alternatively, set its path:

```powershell
$env:R6_OODLE_DLL = "Path\to\oo2core_8_win64.dll"
```

Create an ignored `config.py` in the project root:

```python
GAME_DIR = r"Path\to\Tom Clancy's Rainbow Six Siege"
```

## CLI

**Inspect one archive:**
```powershell
py -3 -B main.py scan <archive.forge>
```

**Scan every archive under `GAME_DIR`:**
```powershell
py -3 -B main.py scan --all
```

**Losslessly extract an archive:**
```powershell
py -3 -B main.py extract <archive.forge> -o output/raw
```

**Add a archive to the persistent asset index:**
```powershell
py -3 -B main.py index <archive.forge> -o output/r6-assets.sqlite
```

Unchanged archives are skipped. Use `--force` to rescan one.

**Discover model UIDs:**
```powershell
py -3 -B main.py models <archive.forge>
```

**Show models with at least two geometry parts:**
```powershell
py -3 -B main.py models <archive.forge> --minimum-parts 2
```

**Write the model catalog as JSON:**
```powershell
py -3 -B main.py models datapc64_mtx_bnk_mesh --json-output output/datapc64_mtx_models.json
```

**Export one model as OBJ and glTF:**
```powershell
py -3 -B main.py model <archive.forge> --uid <modelUID> -o output/model
```

Resume is enabled by default. Use `--no-resume` to force re-extraction.

Every direct geometry child is included in a model export. glTF is the preferred Blender import format, OBJ remains available for diagnostics.

### Cross-bundle geometry

Use an explicit dependency graph when metadata and geometry belong to different bundles:

```powershell
py -3 -B main.py model datapc64_merged_bnk_mesh --depgraph datapc64_ondemand.depgraphbin --archive-only --uid <modelUID> -o output/model
```

`--archive-only` indexes only the selected Forge archive. This provides fast geometry-only exports without scanning the much larger texture archives.

## Audio

Extract embedded WEM streams from a decompressed asset:

```python
from pathlib import Path

from src.audio import extract_wems

payload = Path(r"output/raw/<archive>/<asset>.bin").read_bytes()

for path in extract_wems(payload, r"output/audio"):
  print(path)
```

Convert a WEM file to WAV:

```powershell
.\vgmstream-win64\vgmstream-cli.exe -o output/audio/sound.wav output/audio/sound.wem
```

Audio conversion is not connected to the main CLI yet.

## Raw extraction output

Extracted assets use this filename format:

```text
<UID>_<FILETYPE>_<CONTAINER_OFFSET>.bin
```

Results are recorded in `output/raw/manifest.jsonl`. An asset is resumed only when its output exists and has the expected decompressed size.

## Roadmap

1. Expand the index to the full game and use it for automatic cross-bundle resolution
2. Add UID-to-name mappings and asset search
3. Validate the remaining vertex layouts
4. Reconstruct streamed textures and decode packed PBR channels
5. Add GLB export
6. Decode skeletons, weights and animations
7. Add bulk model export
8. Build a desktop asset browser and Blender integration

## Tests

```powershell
py -3 -B -m unittest discover -s tests -v
```

Tests use synthetic data and do not require game assets or Oodle.

## Legal notice

This project is intended for personal research and interoperability work.

Rainbow Six Siege and its assets are owned by Ubisoft. Do not commit or
redistribute extracted assets. The proprietary Oodle runtime must not be
committed or redistributed.

## License

See [LICENSE](LICENSE)