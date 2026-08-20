# Rainbow Six Siege Forge Extractor

A Python toolkit for extracting Rainbow Six Siege `.forge` archives and exporting models for Blender. Unknown assets can be preserved as decompressed binary files.

## Support

- Scimitar archive scanning, Oodle Kraken decompression and resumable raw extraction
- Persistent SQLite indexing and human-readable UID catalogs
- Cross-bundle geometry, material and texture resolution
- BC1, BC3, BC4 and BC7 textures, including BC5 normal reconstruction
- LOD0 meshes with UVs, normals, tangents and material islands
- glTF 2.0 export with Siege shader metadata and coordinate conversion
- Character weights, bone palettes, per-part skins and shared facial alignment
- Wwise WEM extraction and optional WAV conversion with bundled vgmstream

## Limitations

- Model export requires a known UID and access to the relevant archives
- Only LOD0 glTF is exported, GLB is unavailable
- Streamed textures and packed PBR channels are not reconstructed
- Layered, detail and eye shaders are preserved as metadata but not fully rebuilt
- Skeleton hierarchy and animations are not supported
- Human-readable names depend on imported catalogs

## Setup

Requirements:

- Windows
- 64-bit Python 3.10 or newer
- Pillow
- A compatible `oo2core_*_win64.dll`

Install Python dependency:
```powershell
py -3 -m pip install -r requirements.txt
```

Place the Oodle DLL in the project root or set its path:
```powershell
$env:R6_OODLE_DLL = "Path\to\oo2core_8_win64.dll"
```

Create an ignored `config.py`:
```python
GAME_DIR = r"Path\to\Tom Clancy's Rainbow Six Siege"
```

## CLI

```powershell
# Inspect, extract or index archives
py -3 -B main.py scan <archive-or-directory>
py -3 -B main.py extract <archive-or-directory> -o output/raw
py -3 -B main.py index <archive-or-directory> -o output/r6-assets.sqlite

# Import and search names
py -3 -B main.py names import <catalog.csv>
py -3 -B main.py search <name-or-UID>

# Discover and export models
py -3 -B main.py models <mesh.forge>
py -3 -B main.py model <mesh.forge> --uid <modelUID> -o output/model
```
Use `--all` with `scan`, `extract` or `index` to process every Forge archive under `GAME_DIR`. Run any command with `-h` for additional options.

`search` reports asset locations, dependency parents and ready-to-run export commands when geometry is available.

### Operator review
Prepare unknown operator models:
```powershell
py -3 -B main.py operators --database output/r6-assets.sqlite --previews output/operator-previews
```

Render their resumable UID-stamped previews:
```powershell
& "<path-to-blender>\blender.exe" --factory-startup --background --python-exit-code 1 --python blender_preview.py -- output/operator-previews
```

Fill confirmed names in `review.csv`, then import it:
```powershell
py -3 -B main.py names import output/operator-previews/review.csv --database output/r6-assets.sqlite
```

### Cross-bundle model export

Use the persistent index when model geometry and textures reside in different bundles:

```powershell
py -3 -B main.py model datapc64_merged_bnk_mesh --depgraph datapc64_ondemand.depgraphbin --database output/r6-assets.sqlite --uid <modelUID> -o output/model
```

Use `--archive-only` for a faster untextured geometry diagnostic.

Raw extraction writes `<UID>_<FILETYPE>_<CONTAINER_OFFSET>.bin` files and records results in `output/raw/manifest.jsonl`.

## Audio

Extract embedded WEM streams from a raw asset:

```python
from pathlib import Path

from src.audio import extract_wems

payload = Path(r"output/raw/<archive>/<asset>.bin").read_bytes()

for path in extract_wems(payload, r"output/audio"):
    print(path)
```

Convert WEM to WAV:

```powershell
.\vgmstream-win64\vgmstream-cli.exe -o output/audio/sound.wav output/audio/sound.wem
```

Audio extraction is not connected to the main CLI yet.

## Roadmap

1. Reconstruct layered, detail and eye shaders from preserved metadata
2. Ship an attributed, versioned UID catalog and import it during setup
3. Validate the remaining vertex layouts
4. Reconstruct streamed textures and decode packed PBR channels
5. Add GLB export
6. Decode skeleton hierarchy and animations for reusable Blender rigs
7. Add general bulk model export
8. Build a desktop asset browser and Blender integration

## Tests

```powershell
py -3 -B -m unittest discover -s tests -v
```

Tests use synthetic data and do not require game assets or Oodle.

## Legal notice

For personal research and interoperability. Rainbow Six Siege and its assets belong to Ubisoft. Do not redistribute extracted assets or the proprietary Oodle runtime.

## License

See [LICENSE](LICENSE)
