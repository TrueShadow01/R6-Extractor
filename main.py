from src.decompress import oodle_decompress

print("R6 Forge Extractor")

path = r"D:\SteamLibrary\steamapps\common\Tom Clancy's Rainbow Six Siege\datapc64_dmtx_bnk_textures3.forge"
with open(path, "rb") as f:
    data = f.read()

blob = data[0x3066:0x3066 + 103990]
out = oodle_decompress(blob, 262144)
print("Decompressed:", len(out), "bytes, head:", out[:8].hex())