import os
import struct
import shutil
import subprocess

# Dump every raw Wwise .wem (RIFF/WAVE) stream found in `data`
def extract_wems(data, out_dir, prefix="sound"):
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    i = 0
    while True:
        r = data.find(b"RIFF", i)
        if r == -1:
            break
        size = struct.unpack("<I", data[r + 4:r + 8])[0]
        total = size + 8
        if data[r + 8:r + 12] == b"WAVE" and 0 < total <= len(data) - r:
            out = os.path.join(out_dir, f"{prefix}_{r:X}.wem")
            with open(out, "wb") as o:
                o.write(data[r:r + total])
            paths.append(out)
            i = r + total
        else:
            i = r + 4 # false "RIFF" match, keep scanning
    return paths

# Convert .wem to .wav via vgmstream-cli
# Returns the WAV path or None if vgmstream is not on PATH
def wem_to_wav(wem_path, vgmstream="vgmstream-cli"):
    exe = shutil.which(vgmstream) or shutil.which(vgmstream + ".exe")
    if exe is None:
        return None
    wav = os.path.splitext(wem_path)[0] + ".wav"
    subprocess.run([exe, "-o", wav, wem_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return wav