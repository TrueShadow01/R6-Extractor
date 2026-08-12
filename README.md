# Rainbow Six Siege Forge Extractor

A Python toolkit for extracting Rainbow Six Siege `.forge`
archives and exporting models for Blender.

The long-term goal is geometry, material, texture, skeleton and animation support. Unknown assets are preserved as decompressed binary files.

## Support

- Bounded-memory Scimitar archive scanning
- Oodle Kraken decompression
- Lossless extraction with manifests and resume
- Persistent, resumable SQLite asset indexing
- Cross-bundle geometry and texture resolution
- BC1, BC3, BC4 and BC5 textures to PNG
- Wwise audio extraction to WEM
- Optional WEM-to-WAV conversion with vgmstream
- Float and packed-position meshes with UVs and normals
- Composite LOD0 OBJ, MTL and glTF 2.0 export
- Diffuse and normal material assignment
- Specular relationships stored as glTF metadata
- Siege Z-up to glTF Y-up coordinate conversion

## Limitations

- Relevant archives must be indexed for cross-bundle resolution
- Streamed and virtual textures are not reconstructed
- Packed PBR channels are not decoded
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
# Inspect archives
py -3 -B main.py scan <archive-or-directory>
py -3 -B main.py scan --all

# Extract archives
py -3 -B main.py extract <archive-or-directory> -o output/raw
py -3 -B main.py extract --all -o output/raw

# Build or update the asset index
py -3 -B main.py index <archive-or-directory> -o output/r6-assets.sqlite
py -3 -B main.py index --all -o output/r6-assets.sqlite

# Discover model UIDs
py -3 -B main.py models <mesh.forge>
py -3 -B main.py models <mesh.forge> --minimum-parts 2
py -3 -B main.py models <mesh.forge> --json-output output/models.json

# Export one model
py -3 -B main.py model <mesh.forge> --uid <modelUID> -o output/model
```

`--all` processes every Forge archive under `GAME_DIR`. Run any command with `-h` for its available options.

Index updates are committed per archive. Interrupted scans can be rerun because unchanged archives are skipped. Use `--force` to rescan them.

Raw extraction writes `<UID>_<FILETYPE>_<CONTAINER_OFFSET>.bin` files and records results in `output/raw/manifest.jsonl`.

### Cross-bundle models

Use a dependency graph and the persistent index when geometry and textures belong to different bundles:

```powershell
py -3 -B main.py model datapc64_merged_bnk_mesh --depgraph datapc64_ondemand.depgraphbin --database output/r6-assets.sqlite --uid <modelUID> -o output/model
```

Use `--archive-only` instead of `--database` for a faster untextured geometry diagnostic.

glTF is the preferred Blender import format. OBJ remains available for diagnostics.

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

1. Add UID-to-name mappings and asset search
2. Validate the remaining vertex layouts
3. Reconstruct streamed textures and decode packed PBR channels
4. Add GLB export
5. Decode skeletons, weights and animations
6. Add bulk model export
7. Build a desktop asset browser and Blender integration

## Tests

```powershell
py -3 -B -m unittest discover -s tests -v
```

Tests use synthetic data and do not require game assets or Oodle.

## Legal notice

For personal research and interoperability. Rainbow Six Siege and its assets belong to Ubisoft. Do not redistribute extracted assets or the proprietary Oodle runtime.

## License

See [LICENSE](LICENSE)
