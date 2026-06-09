import os
from src.parser import read_container
from src.mesh import read_mesh
from src.texture import save_png
from PIL import Image

MESH = 0x415D9568
COMPILED_MESH_OBJ = 0xABEB2DFB
TEXTURE_TYPES = {
    0x13237FE9, # CompiledTextureMap
    0x9F492D22, # UltraResTexMap
    0x3876CCDF, # FutureResTexMap
    0x59CE4D13, # HiResTexMap
    0xF9C80707, # MedResTexMap
    0xD7B5C478, # LowResTexMap
}

def resolve(mesh_uid, children, index):
    seen, textures, stack = set(), [], [mesh_uid]
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        if index.get(u, (None,))[0] in TEXTURE_TYPES:
            textures.append(u)
        stack.extend(children.get(u, []))
    return textures

def export_model(mesh_uid, children, index, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    name = f"{mesh_uid:016X}"
    cache = {}
    def load(path):
        if path not in cache:
            with open(path, "rb") as f:
                cache[path] = f.read()
        return cache[path]
    
    decoded = []
    for u in resolve(mesh_uid, children, index):
        ft, path, off = index[u]
        try:
            png = f"{u:016X}.png"
            w, h, fmt, textype = save_png(os.path.join(out_dir, png), read_container(load(path), off))
            decoded.append((w * h, textype, png))
        except ValueError:
            pass
    
    def is_blank(png):
        im = Image.open(os.path.join(out_dir, png)).convert("RGBA").resize((16, 16))
        return max(max(p) for p in im.getdata()) < 8
    
    diffuse = normal = None
    for _, textype, png in sorted(decoded, reverse=True):
        if textype == 0 and diffuse is None and not is_blank(png):
            diffuse = png
        elif textype == 1 and normal is None:
            normal = png

    geo = next((c for c in children.get(mesh_uid, []) if index.get(c, (0,))[0] == COMPILED_MESH_OBJ), None)
    if geo is None:
        raise ValueError(f"No CompiledMeshObject child for mesh {name}")
    _, path, off = index[geo]
    verts, uvs, normals, faces = read_mesh(read_container(load(path), off))

    with open(os.path.join(out_dir, name + ".obj"), "w") as o:
        o.write(f"mtllib {name}.mtl\n")
        for x, y, z in verts:
            o.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
        for u, v in uvs:
            o.write(f"vt {u:.6f} {v:.6f}\n")
        for nx, ny, nz in normals:
            o.write(f"vn {nx:.6f} {ny:.6f} {nz:.6f}\n")
        o.write("usemtl mat0\n")
        for a, b, c in faces:
            o.write(f"f {a+1}/{a+1}/{a+1} {b+1}/{b+1}/{b+1} {c+1}/{c+1}/{c+1}\n")
    
    with open(os.path.join(out_dir, name + ".mtl"), "w") as m:
        m.write("newmtl mat0\nKd 1 1 1\n")
        if diffuse:
            m.write(f"map_Kd {diffuse}\n")
        if normal:
            m.write(f"map_Bump {normal}\n")
    return len(verts), len(faces), diffuse, normal