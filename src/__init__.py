# Rainbow Six Siege .forge Asset Extractor

# Pipeline:
#   parser      - locate containers in a .forge file and reassamble the entry payloads
#   decompress  - Oodle Kraken decompression via oo2core DLL (ctypes)
#   texture     - decode texture payloads (BCn) to PNG
#   mesh        - decode mesh payloads to geometry (WIP)
#   dds         - Obsolete DDS conversion code that should no longer be used and has been replaced by '_dds_dx10' in texture.py

# Assets are keyed by numeric UIDs, filenames are hashed, so type is identified by magic numbers inside each decompressed payload.