"""
GModular Final Verification Render
Produces high-quality renders of KotOR .mod geometry with textures,
proper lighting, and clear mesh face visibility.
"""
import sys, os, math
sys.path.insert(0, '/home/user/webapp')
os.environ['LIBGL_ALWAYS_SOFTWARE'] = '1'
os.environ['MESA_GL_VERSION_OVERRIDE'] = '3.3'

import numpy as np
import moderngl

try:
    CTX = moderngl.create_standalone_context(backend='egl')
except:
    CTX = moderngl.create_standalone_context()
print(f"GL renderer: {CTX.info['GL_RENDERER']}")

from gmodular.formats.mdl_parser import MDLParser

EXTRACT = '/home/user/uploaded_files/_slem_ar_extracted'

# ── Load both room models ──────────────────────────────────────────────────────
all_nodes = []
all_bb_min = []
all_bb_max = []

for mdl_name in ['m31aa_00a', 'm31aa_00l']:
    mdl_path = f'{EXTRACT}/{mdl_name}.mdl'
    mdx_path = f'{EXTRACT}/{mdl_name}.mdx'
    if not os.path.exists(mdl_path):
        print(f"  skip {mdl_name} (no .mdl)")
        continue
    mdx_data = open(mdx_path,'rb').read() if os.path.exists(mdx_path) else b''
    try:
        mesh = MDLParser(open(mdl_path,'rb').read(), mdx_data).parse()
        nodes = mesh.visible_mesh_nodes()
        print(f"  {mdl_name}: {mesh.name}, {len(nodes)} nodes, BB {mesh.bb_min}..{mesh.bb_max}")
        all_nodes.extend(nodes)
        # Only include non-zero BBs
        bb0 = np.array(mesh.bb_min,'f4'); bb1 = np.array(mesh.bb_max,'f4')
        if np.linalg.norm(bb1 - bb0) > 0.1:
            all_bb_min.append(bb0)
            all_bb_max.append(bb1)
    except Exception as e:
        print(f"  {mdl_name}: parse error {e}")

if not all_bb_min:
    print("ERROR: No models loaded"); sys.exit(1)

bb_min = np.stack(all_bb_min).min(axis=0)
bb_max = np.stack(all_bb_max).max(axis=0)
center = (bb_min + bb_max) * 0.5
span   = float(np.linalg.norm(bb_max - bb_min))
print(f"\nCombined BB: {bb_min} → {bb_max}")
print(f"Center: {center}, span: {span:.1f}, total nodes: {len(all_nodes)}")

# ── Shaders ────────────────────────────────────────────────────────────────────
VERT = """
#version 330 core
in vec3 in_pos;
in vec3 in_norm;
in vec2 in_uv;
uniform mat4 mvp;
uniform mat4 model;
out vec3 w_pos;
out vec3 w_norm;
out vec2 v_uv;
void main() {
    vec4 wp = model * vec4(in_pos, 1.0);
    w_pos  = wp.xyz;
    w_norm = normalize(mat3(transpose(inverse(model))) * in_norm);
    v_uv   = in_uv;
    gl_Position = mvp * vec4(in_pos, 1.0);
}
"""
FRAG = """
#version 330 core
in vec3 w_pos;
in vec3 w_norm;
in vec2 v_uv;
uniform vec3 col;
uniform float amb;
uniform bool has_tex;
uniform sampler2D tex0;
out vec4 fragColor;

void main() {
    // Multiple light sources for better interior visibility
    vec3 n = normalize(w_norm);
    
    // Key light from upper-right
    vec3 ld1 = normalize(vec3(0.6, 0.4, 0.9));
    float d1 = max(dot(n, ld1), 0.0) * 0.55;
    
    // Fill light from opposite side
    vec3 ld2 = normalize(vec3(-0.5, -0.3, 0.5));
    float d2 = max(dot(n, ld2), 0.0) * 0.25;
    
    // Back light (upward from floor)
    vec3 ld3 = normalize(vec3(0.0, 0.0, -1.0));
    float d3 = max(dot(n, ld3), 0.0) * 0.15;
    
    float lit = amb + (1.0 - amb) * (d1 + d2 + d3);
    lit = clamp(lit, 0.0, 1.0);
    
    vec3 base = has_tex ? texture(tex0, v_uv).rgb : col;
    fragColor = vec4(base * lit, 1.0);
}
"""
prog = CTX.program(vertex_shader=VERT, fragment_shader=FRAG)
print("Shader compiled OK")

# ── TGA texture loader ─────────────────────────────────────────────────────────
def load_tga(path):
    with open(path,'rb') as f: data = f.read()
    if len(data) < 18: return None
    img_type = data[2]
    bpp  = data[16]
    w    = data[12] | (data[13]<<8)
    h    = data[14] | (data[15]<<8)
    if img_type not in (2,3) or bpp not in (24,32) or w<=0 or h<=0: return None
    hdr_ext = 18 + data[0]
    pixels  = data[hdr_ext : hdr_ext + w*h*(bpp//8)]
    arr  = np.frombuffer(pixels, dtype=np.uint8).reshape(h, w, bpp//8)
    rgba = np.empty((h,w,4), dtype=np.uint8)
    rgba[:,:,0] = arr[:,:,2]  # B→R
    rgba[:,:,1] = arr[:,:,1]  # G
    rgba[:,:,2] = arr[:,:,0]  # R→B
    rgba[:,:,3] = arr[:,:,3] if bpp==32 else 255
    flip_flag = (data[17]>>5)&1
    if not flip_flag:
        rgba = rgba[::-1,:,:]
    tex = CTX.texture((w,h), 4, rgba.tobytes())
    tex.build_mipmaps()
    tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
    tex.repeat_x = True; tex.repeat_y = True
    return tex

tex_cache = {}
file_idx = {}
for f in os.listdir(EXTRACT):
    file_idx[f.lower()] = os.path.join(EXTRACT, f)

for fname, fpath in file_idx.items():
    if fname.endswith('.tga'):
        resref = fname[:-4]
        try:
            t = load_tga(fpath)
            if t: tex_cache[resref] = t
        except: pass
print(f"Loaded {len(tex_cache)} TGA textures")

# ── Upload geometry ────────────────────────────────────────────────────────────
PALETTE = [
    (0.80,0.72,0.60),(0.60,0.72,0.80),(0.72,0.80,0.60),
    (0.80,0.60,0.72),(0.65,0.65,0.70),(0.75,0.70,0.60)
]

vaos = []
total_tris = 0

for idx, node in enumerate(all_nodes):
    verts = node.vertices or []
    faces = node.faces or []
    norms = node.normals or []
    uvs   = getattr(node,'uvs',[]) or []
    if not verts or not faces: continue

    n_v   = len(verts)
    has_n = (len(norms) == n_v)
    has_uv = (len(uvs) == n_v)

    va = np.array(verts,'f4')
    na = np.array(norms,'f4') if has_n else np.tile([0.,0.,1.],(n_v,1)).astype('f4')
    ua = np.array(uvs,'f4')   if has_uv else np.zeros((n_v,2),'f4')

    vidx = []
    for face in faces:
        if len(face) < 3: continue
        a,b,c = int(face[0]),int(face[1]),int(face[2])
        if max(a,b,c) >= n_v: continue
        vidx.extend([a,b,c])
    if not vidx: continue

    ia  = np.array(vidx, dtype=np.int32)
    buf = np.empty((len(ia),8),'f4')
    buf[:,:3] = va[ia]; buf[:,3:6] = na[ia]; buf[:,6:] = ua[ia]

    vbo = CTX.buffer(buf.tobytes())
    vao = CTX.vertex_array(prog, [(vbo,'3f 3f 2f','in_pos','in_norm','in_uv')])

    total_tris += len(ia)//3

    # Get texture
    tn  = (getattr(node,'texture','') or '').lower().strip()
    tex = tex_cache.get(tn)
    if tex is None and '_' in tn:
        suf = tn[tn.find('_'):]
        for k,v in tex_cache.items():
            if k.endswith(suf): tex=v; break

    # Fallback color
    diff = getattr(node,'diffuse',None)
    if diff and len(diff)>=3 and max(diff)>0.05:
        c = (max(.25,min(1.,diff[0])), max(.25,min(1.,diff[1])), max(.25,min(1.,diff[2])))
    else:
        c = PALETTE[idx % len(PALETTE)]

    vaos.append({'vao':vao,'color':c,'tex':tex,'has_uv':has_uv,'name':getattr(node,'name','')})

print(f"Uploaded {len(vaos)} VAOs, {total_tris:,} triangles")
print(f"VAOs with texture: {sum(1 for v in vaos if v['tex'])}")

# ── Render helpers ─────────────────────────────────────────────────────────────
W, H = 1920, 1080

def persp(fov, asp, nr, fr):
    f = 1./math.tan(math.radians(fov)*0.5)
    m = np.zeros((4,4),'f4')
    m[0,0]=f/asp; m[1,1]=f
    m[2,2]=(fr+nr)/(nr-fr); m[2,3]=(2*fr*nr)/(nr-fr)
    m[3,2]=-1.
    return m

def lookat(eye, at):
    up = np.array([0.,0.,1.],'f4')
    f  = np.array(at,'f4') - np.array(eye,'f4')
    f /= np.linalg.norm(f)
    r = np.cross(f, up)
    if np.linalg.norm(r) < 1e-6: r = np.cross(f, np.array([0.,1.,0.],'f4'))
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    m = np.eye(4, dtype='f4')
    m[0,:3]=r;  m[1,:3]=u;  m[2,:3]=-f
    m[0,3]=-float(np.dot(r,eye))
    m[1,3]=-float(np.dot(u,eye))
    m[2,3]= float(np.dot(f,eye))
    return m

def save_ppm_png(raw, path, label=''):
    with open(path,'wb') as f:
        f.write(f"P6\n{W} {H}\n255\n".encode())
        rows = [raw[i*W*3:(i+1)*W*3] for i in range(H)]
        for row in reversed(rows): f.write(row)
    import subprocess
    png = path.replace('.ppm','.png')
    subprocess.run(['convert',path,png], capture_output=True)

    # Stats
    nb = sum(1 for i in range(0,len(raw),3)
             if abs(raw[i]-18)>12 or abs(raw[i+1]-20)>12 or abs(raw[i+2]-36)>12)
    geo_pct = 100*nb//(W*H)
    # Brightness
    geo_vals = [raw[i] for i in range(0,len(raw),3)
                if abs(raw[i]-18)>12 or abs(raw[i+1]-20)>12 or abs(raw[i+2]-36)>12]
    avg_b = sum(geo_vals)//max(1,len(geo_vals))
    print(f"  [{label}] {geo_pct}% geometry, avg brightness {avg_b}/255 → {png}")
    return png

def render_view(eye, tgt, path, label='', amb=0.55):
    fbo = CTX.framebuffer(
        color_attachments=[CTX.renderbuffer((W,H))],
        depth_attachment=CTX.depth_renderbuffer((W,H)))
    fbo.use()
    CTX.viewport = (0,0,W,H)
    CTX.clear(0.07, 0.08, 0.14, 1.0)
    CTX.enable(moderngl.DEPTH_TEST)
    CTX.disable(moderngl.CULL_FACE)

    proj  = persp(60., W/H, 0.5, span*8.)
    view  = lookat(np.array(eye,'f4'), np.array(tgt,'f4'))
    mvp   = (proj @ view).T.astype('f4')
    model_m = np.eye(4, dtype='f4')

    prog['model'].write(model_m.tobytes())
    prog['amb'].value = amb

    for e in vaos:
        prog['mvp'].write(mvp.tobytes())
        prog['col'].write(np.array(e['color'],'f4').tobytes())
        if e['tex'] and e['has_uv']:
            e['tex'].use(0)
            prog['tex0'].value = 0
            prog['has_tex'].value = True
        else:
            prog['has_tex'].value = False
        e['vao'].render(moderngl.TRIANGLES)

    raw = fbo.read(components=3)
    return save_ppm_png(raw, path, label)

cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
d  = span * 0.85
mr = math.radians

print(f"\nRendering views — center({cx:.1f},{cy:.1f},{cz:.1f}), orbit-dist={d:.1f}")

# ── 5 verification renders ─────────────────────────────────────────────────────
renders = {}

# 1. ISO — classic 45°/35° overhead
renders['iso'] = render_view(
    [cx + d*math.cos(mr(45))*math.cos(mr(35)),
     cy + d*math.sin(mr(45))*math.cos(mr(35)),
     cz + d*math.sin(mr(35))],
    [cx, cy, cz], 'render_out_iso.ppm', 'ISO')

# 2. Interior — eye-level looking down corridor
renders['interior'] = render_view(
    [cx - 35., cy + 2., cz + 10.],
    [cx + 40., cy,      cz + 10.],
    'render_out_interior.ppm', 'INTERIOR')

# 3. South — side elevation
renders['south'] = render_view(
    [cx, cy - d*0.9, cz + 30.],
    [cx, cy,         cz + 15.],
    'render_out_south.ppm', 'SOUTH')

# 4. Top-down — orthographic-like (very high pitch)
renders['topdown'] = render_view(
    [cx, cy, cz + d*1.1],
    [cx, cy, cz],
    'render_out_topdown.ppm', 'TOPDOWN')

# 5. NW corner close-up
renders['nw'] = render_view(
    [cx + d*math.cos(mr(135))*math.cos(mr(25)),
     cy + d*math.sin(mr(135))*math.cos(mr(25)),
     cz + d*math.sin(mr(25))],
    [cx, cy, cz], 'render_out_nw.ppm', 'NW')

print("\n=== RENDER VERIFICATION COMPLETE ===")
print("Output files:")
for k,v in renders.items():
    print(f"  {k}: {v}")
