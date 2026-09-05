# Rainbow Six Siege Forge Extractor

A Python toolkit for extracting Rainbow Six Siege `.forge` archives and exporting models for Blender. Unknown assets can be preserved as decompressed binary files.

## Support

- Scimitar archive scanning, Oodle Kraken decompression and resumable raw extraction
- Persistent SQLite indexing and human-readable UID catalogs
- Default operator equipment discovery from the inspected Forge registry layout
- Cross-bundle geometry, material and texture resolution
- BC1, BC3, BC4 and BC7 textures, including BC5 normal reconstruction
- LOD0 meshes with UVs, normals, tangents and material islands
- glTF 2.0 export with Siege shader metadata and coordinate conversion
- Character weights, bone palettes, per-part skins and shared facial alignment
- Wwise WEM extraction and optional WAV conversion with bundled vgmstream

## Limitations

- Model export requires a known UID and access to the relevant archives
- Only LOD0 glTF is exported, GLB is unavailable
- Streamed texture reconstruction remains incomplete. Packed metalness/glossiness is applied by the Blender preview helper, not ordinary glTF import
- Eye previews use recovered iris/sclera colors and glossiness with an approximate iris mask. Caveira's eye appearance and neutral gaze have been visually validated in Blender 4.5. Other operators remain unverified
- Layered clothing tint masks, cavity, exact eye parallax and limbus shading remain unresolved
- Skeleton hierarchy and animations are not supported
- General asset names depend on imported catalogs, operator registry discovery resolves names separately

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

### Default operator registry

Read default head/body equipment directly from `datapc64.forge`:

```powershell
py -3 -B main.py operators --registry
```

Use `--limit 3` for a shorter display. This command requires `GAME_DIR` and Oodle but no asset database or imported catalog. It prints equipment UIDs, appearance UIDs, model-reference groups and source offsets without writing files.

Validated against the inspected installation: 78 operators, 241 model references and 238 unique referenced assets. Repeated references across groups are preserved.

The reader supports the currently inspected registry layout and uses fixed layout offsets. Game updates may require adjustments. Model-group roles and four additional packages remain partly unresolved; registry discovery does not establish complete Blender appearance fidelity.

`--registry` cannot be combined with `--defaults` or `--previews`.

### Blender eye preview

Export the head again after updating the eye material parser, then import it into a fresh Blender 4.5 scene. Ordinary glTF import does not apply the Siege preview shader.

Run this in Blender's Python Console, adjusting the repository and model paths:

```python
import runpy
from pathlib import Path

siege = runpy.run_path(
    r"<Repo-Path>\R6\blender_preview.py",
    run_name="siege_material_tools",
)
siege["apply_siege_materials"](
    Path(r"<Repo-Path>\R6\output\caveira-registry\head\000000156B734076.gltf")
)
```

Apply the helper once to a fresh, single-model import. Head and body exports reuse material names, so combined-scene material matching is not yet reliable.

Shared facial geometry uses operator-specific eye positions with bind orientations for a static neutral gaze. This does not reproduce runtime eye animation.

### Operator review
List default-labeled model parents for review:
```powershell
py -3 -B main.py operators --defaults --database output/r6-assets.sqlite
```

Previews are intended for identification.
Some Siege material and alpha variants are not reconstructed accurately yet.

Prepare unknown models for visual review:
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
2. Import bundled UID catalogs during setup
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
