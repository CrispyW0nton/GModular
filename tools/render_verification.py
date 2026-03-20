"""
GModular Verification Render Script
- Loads m31aa_00a.mdl from extracted slem_ar.mod
- Correctly frames camera at model BB center
- Renders 5 views: iso, south, topdown, interior-east, NW angle
"""
import sys, os, math
sys.path.insert(0, '/home/user/webapp')
os.environ['LIBGL_ALWAYS_SOFTWARE'] = '1'
os.environ['MESA_GL_VERSION_OVERRIDE'] = '3.3'

import numpy as np
import moderngl

def create_ctx():
    for backend in ('egl', None):
        try:
            if backend:
                return moderngl.create_standalone_context(backend=backend)
            else:
                return moderngl.create_standalone_context()
        except Exception as e:
            print(f"  Backend {backend!r} failed: {e}")
    raise RuntimeError("No GL context available")

CTX = create_ctx()
print(f"GL: {CTX.info['GL_RENDERER']}")

from gmodular.formats.mdl_parser import MDLParser

EXTRACT = '/home/user/uploaded_files/_slem_ar_extracted'

# ── Load both room models ─────────────────────────────────────────────────────
ALL_NODES = []
for mdl_name in ['m31aa_00a', 'm31aa_00l']:
    mdl_p = f'{EXTRACT}/{mdl_name}.mdl'
    mdx_p = f'{EXTRACT}/{mdl_name}.mdx'
    if not os.path.exists(mdl_p):
        print(f"  Missing: {mdl_p}")
        continue
    with open(mdl_p,'rb') as f: mb = f.read()
    xb = open(mdx_p,'rb').read() if os.path.exists(mdx_p) else b''
    mesh = MDLParser(mb, xb).parse()
    nds = mesh.visible_mesh_nodes()
    print(f"  {mdl_name}: {mesh.name}, {len(nds)} nodes, BB {mesh.bb_min} .. {mesh.bb_max}")
    ALL_NODES.extend(nds)

print(f"Total nodes: {len(ALL_NODES)}")

# ── Compute overall BB ────────────────────────────────────────────────────────
all_vx, all_vy, all_vz = [], [], []
for node in ALL_NODES:
    for v in node.vertices:
        all_vx.append(v[0]); all_vy.append(v[1]); all_vz.append(v[2])

bb_min = np.array([min(all_vx), min(all_vy), min(all_vz)], dtype='f4')
bb_max = np.array([max(all_vx), max(all_vy), max(all_vz)], dtype='f4')
center = (bb_min + bb_max) * 0.5
span = float(np.linalg.norm(bb_max - bb_min))
print(f"Combined BB: {bb_min} .. {bb_max}")
print(f"Center: {center}, Span: {span:.1f}")

# ── Shader ────────────────────────────────────────────────────────────────────
VERT = """
#version 330 core
in vec3 in_pos;
in vec3 in_norm;
uniform mat4 mvp;
out vec3 fn;
void main() {
    fn = in_norm;
    gl_Position = mvp * vec4(in_pos, 1.0);
}
"""
FRAG = """
#version 330 core
in vec3 fn;
uniform vec3 col;
uniform float amb;
out vec4 o;
void main() {
    vec3 ld = normalize(vec3(0.6, 0.4, 0.8));
    float d = max(dot(normalize(fn), ld), 0.0);
    vec3 c = col * (amb + (1.0 - amb) * d);
    o = vec4(c, 1.0);
}
"""
prog = CTX.program(vertex_shader=VERT, fragment_shader=FRAG)

# ── Upload geometry ────────────────────────────────────────────────────────────
PALETTE = [
    (0.72, 0.65, 0.55), (0.55, 0.65, 0.72), (0.65, 0.72, 0.55),
    (0.72, 0.55, 0.65), (0.60, 0.70, 0.65), (0.70, 0.60, 0.55),
]
vaos = []
total_tris = 0

for idx, node in enumerate(ALL_NODES):
    verts = node.vertices or []
    faces = node.faces or []
    norms = node.normals or []
    if not verts or not faces: continue
    
    n_v = len(verts)
    has_n = (len(norms) == n_v)
    
    # Use numpy for speed
    verts_arr = np.array(verts, dtype='f4')  # (n_v, 3)
    if has_n:
        norms_arr = np.array(norms, dtype='f4')
    else:
        norms_arr = np.zeros((n_v, 3), dtype='f4')
        norms_arr[:, 2] = 1.0  # default up
    
    # Build index list
    valid_faces = []
    for f in faces:
        if len(f) < 3: continue
        a, b, c = int(f[0]), int(f[1]), int(f[2])
        if max(a, b, c) >= n_v: continue
        valid_faces.extend([a, b, c])
    
    if not valid_faces: continue
    
    idx_arr = np.array(valid_faces, dtype=np.int32)
    # Expand to flat vertex stream
    pos_flat = verts_arr[idx_arr]   # (n_tris*3, 3)
    nor_flat = norms_arr[idx_arr]   # (n_tris*3, 3)
    
    # Interleave pos+norm: 6 floats/vertex
    buf = np.empty((len(idx_arr), 6), dtype='f4')
    buf[:, :3] = pos_flat
    buf[:, 3:] = nor_flat
    
    vbo = CTX.buffer(buf.tobytes())
    vao = CTX.vertex_array(prog, [(vbo, '3f 3f', 'in_pos', 'in_norm')])
    
    diff = getattr(node, 'diffuse', None)
    if diff and len(diff) >= 3 and max(diff) > 0.05:
        color = (max(0.2, min(1., diff[0])), max(0.2, min(1., diff[1])), max(0.2, min(1., diff[2])))
    else:
        color = PALETTE[idx % len(PALETTE)]
    
    vaos.append({'vao': vao, 'color': color, 'n': len(idx_arr)})
    total_tris += len(idx_arr) // 3

print(f"Uploaded {len(vaos)} VAOs, {total_tris} triangles")

# ── Render helpers ────────────────────────────────────────────────────────────
W, H = 1280, 960

def perspective(fov, asp, nr, fr):
    f = 1.0 / math.tan(math.radians(fov) * 0.5)
    m = np.zeros((4, 4), dtype='f4')
    m[0, 0] = f / asp; m[1, 1] = f
    m[2, 2] = (fr+nr)/(nr-fr); m[2, 3] = (2*fr*nr)/(nr-fr)
    m[3, 2] = -1.0
    return m

def lookat(eye, at, up=None):
    if up is None: up = np.array([0., 0., 1.], dtype='f4')
    f = np.array(at, 'f4') - np.array(eye, 'f4')
    f /= np.linalg.norm(f)
    r = np.cross(f, up); r_len = np.linalg.norm(r)
    if r_len < 1e-6:
        up = np.array([0., 1., 0.], dtype='f4')
        r = np.cross(f, up); r /= np.linalg.norm(r)
    else:
        r /= r_len
    u = np.cross(r, f)
    m = np.eye(4, dtype='f4')
    m[0, :3] = r; m[1, :3] = u; m[2, :3] = -f
    m[0, 3] = -np.dot(r, eye); m[1, 3] = -np.dot(u, eye); m[2, 3] = np.dot(f, eye)
    return m

def do_render(eye, target, path, label=""):
    fbo = CTX.framebuffer(
        color_attachments=[CTX.renderbuffer((W, H))],
        depth_attachment=CTX.depth_renderbuffer((W, H))
    )
    fbo.use()
    CTX.viewport = (0, 0, W, H)
    CTX.clear(0.07, 0.08, 0.14, 1.0)
    CTX.enable(moderngl.DEPTH_TEST)
    CTX.disable(moderngl.CULL_FACE)
    
    eye_a = np.array(eye, 'f4')
    tgt_a = np.array(target, 'f4')
    proj = perspective(60., W/H, 0.5, span * 6.)
    view = lookat(eye_a, tgt_a)
    mvp = (proj @ view).T.astype('f4')
    
    for e in vaos:
        prog['mvp'].write(mvp.tobytes())
        prog['col'].write(np.array(e['color'], 'f4').tobytes())
        prog['amb'].value = 0.38
        e['vao'].render(moderngl.TRIANGLES)
    
    # Read + flip + save PPM
    raw = fbo.read(components=3)
    with open(path, 'wb') as f:
        f.write(f"P6\n{W} {H}\n255\n".encode())
        for row in reversed([raw[i*W*3:(i+1)*W*3] for i in range(H)]):
            f.write(row)
    
    nb = sum(1 for i in range(0, len(raw), 3)
             if abs(raw[i]-18)>10 or abs(raw[i+1]-20)>10 or abs(raw[i+2]-36)>10)
    pct = 100*nb/(W*H)
    print(f"  [{label}] {path}: {nb}/{W*H} ({pct:.1f}%) geometry pixels")
    return pct

cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
d = span * 0.85
print(f"\nCamera target: ({cx:.1f}, {cy:.1f}, {cz:.1f}), orbit dist: {d:.1f}")
print()

# Isometric (45° azimuth, 35° elevation)
a, el = math.radians(45), math.radians(35)
do_render(
    [cx + d*math.cos(a)*math.cos(el), cy + d*math.sin(a)*math.cos(el), cz + d*math.sin(el)],
    [cx, cy, cz], '/home/user/webapp/v2_iso.ppm', 'ISO'
)

# South view (azimuth=270°, elevation=20°)
a, el = math.radians(270), math.radians(20)
do_render(
    [cx + d*math.cos(a)*math.cos(el), cy + d*math.sin(a)*math.cos(el), cz + d*math.sin(el)],
    [cx, cy, cz], '/home/user/webapp/v2_south.ppm', 'SOUTH'
)

# Top-down (directly above, slight north tilt to show depth)
do_render(
    [cx, cy - d*0.01, cz + d*1.1],
    [cx, cy, cz], '/home/user/webapp/v2_topdown.ppm', 'TOP'
)

# Interior: inside the corridor looking east
do_render(
    [cx - 25., cy + 5., cz + 10.],
    [cx + 40., cy, cz + 10.], '/home/user/webapp/v2_interior.ppm', 'INTERIOR'
)

# NW angle (azimuth=-135°, elevation=28°)
a, el = math.radians(-135), math.radians(28)
do_render(
    [cx + d*math.cos(a)*math.cos(el), cy + d*math.sin(a)*math.cos(el), cz + d*math.sin(el)],
    [cx, cy, cz], '/home/user/webapp/v2_nw.ppm', 'NW'
)

# Convert to PNG
import subprocess
for name in ['v2_iso', 'v2_south', 'v2_topdown', 'v2_interior', 'v2_nw']:
    r = subprocess.run(['convert', f'{name}.ppm', f'{name}.png'], capture_output=True)
    if r.returncode == 0:
        print(f"  -> {name}.png ✓")
    else:
        print(f"  -> {name}.png FAILED: {r.stderr.decode()[:80]}")

print("\n=== RENDER COMPLETE ===")
