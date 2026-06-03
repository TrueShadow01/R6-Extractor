from src.parser import read_container

print("R6 Forge Extractor")

path = r"D:\SteamLibrary\steamapps\common\Tom Clancy's Rainbow Six Siege\datapc64_dmtx_bnk_textures3.forge"
with open(path, "rb") as f:
    data = f.read()

out = read_container(data, 0x303F)
print("Total:", len(out), "bytes")