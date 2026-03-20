"""
GModular Textured Render — loads TGA textures, renders with UV mapping.
Produces high-quality verification renders showing actual KotOR textures.
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
print(f"GL: {CTX.info['GL_RENDERER']}")

from gmodular.formats.mdl_parser import MDLParser

EXTRACT = '/home/user/uploaded_files/_slem_ar_extracted'
with open(f'{EXTRACT}/m31aa_00a.mdl','rb') as f: mb=f.read()
with open(f'{EXTRACT}/m31aa_00a.mdx','rb') as f: xb=f.read()
mesh = MDLParser(mb, xb).parse()
nodes = mesh.visible_mesh_nodes()
bb_min = np.array(mesh.bb_min,'f4')
bb_max = np.array(mesh.bb_max,'f4')
center = (bb_min+bb_max)*0.5
span = float(np.linalg.norm(bb_max-bb_min))
print(f"Model: {mesh.name}, {len(nodes)} nodes, center={center}, span={span:.1f}")

# ── Textured shader ───────────────────────────────────────────────────────────
VERT = """
#version 330 core
in vec3 in_pos;
in vec3 in_norm;
in vec2 in_uv;
uniform mat4 mvp;
uniform mat4 model;
out vec3 w_norm;
out vec2 v_uv;
void main() {
    w_norm = normalize(mat3(transpose(inverse(model))) * in_norm);
    v_uv = in_uv;
    gl_Position = mvp * vec4(in_pos, 1.0);
}
"""
FRAG = """
#version 330 core
in vec3 w_norm;
in vec2 v_uv;
uniform vec3 col;
uniform float amb;
uniform bool has_tex;
uniform sampler2D tex0;
out vec4 o;
void main() {
    vec3 ld = normalize(vec3(0.6, 0.4, 0.8));
    vec3 n = normalize(w_norm);
    float d = max(dot(n, ld), 0.0);
    float d2 = max(dot(-n, ld), 0.0) * 0.25;
    float lit = amb + (1.0 - amb) * max(d, d2);
    vec3 base = has_tex ? texture(tex0, v_uv).rgb : col;
    o = vec4(clamp(base * lit, 0.0, 1.0), 1.0);
}
"""
prog = CTX.program(vertex_shader=VERT, fragment_shader=FRAG)
print("Textured shader OK")

# ── Load TGA textures ─────────────────────────────────────────────────────────
def load_tga(path):
    """Load TGA file, return (moderngl.Texture, width, height) or None."""
    with open(path,'rb') as f: data = f.read()
    if len(data) < 18: return None
    img_type = data[2]
    bpp = data[16]
    w = data[12] | (data[13]<<8)
    h = data[14] | (data[15]<<8)
    if img_type not in (2,3) or bpp not in (24,32) or w<=0 or h<=0: return None
    hdr_ext = 18 + data[0]
    pixels = data[hdr_ext:hdr_ext + w*h*(bpp//8)]
    
    # Convert BGR(A)→RGBA using numpy
    arr = np.frombuffer(pixels, dtype=np.uint8).reshape(h, w, bpp//8)
    rgba = np.empty((h, w, 4), dtype=np.uint8)
    rgba[:,:,0] = arr[:,:,2]  # R
    rgba[:,:,1] = arr[:,:,1]  # G
    rgba[:,:,2] = arr[:,:,0]  # B
    rgba[:,:,3] = arr[:,:,3] if bpp==32 else 255
    
    # Flip vertically (TGA is bottom-left, GL is bottom-left too but stored top→bottom here)
    flip_flag = (data[17] >> 5) & 1
    if not flip_flag:  # 0 = bottom-left origin = need flip for GL
        rgba = rgba[::-1,:,:]
    
    tex = CTX.texture((w,h), 4, rgba.tobytes())
    tex.build_mipmaps()
    tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
    tex.repeat_x = True; tex.repeat_y = True
    return tex

# Load all TGA textures from extract dir
tex_cache = {}
file_idx = {f.lower(): os.path.join(EXTRACT,f) for f in os.listdir(EXTRACT)}
for fname, fpath in file_idx.items():
    if fname.endswith('.tga'):
        resref = fname[:-4]
        try:
            t = load_tga(fpath)
            if t:
                tex_cache[resref] = t
        except Exception as e:
            pass
print(f"Loaded {len(tex_cache)} TGA textures: {sorted(tex_cache.keys())[:4]}...")

# ── Upload geometry ───────────────────────────────────────────────────────────
PALETTE = [(0.72,0.65,0.55),(0.55,0.65,0.72),(0.65,0.72,0.55),(0.72,0.55,0.65)]
vaos = []

for idx, node in enumerate(nodes):
    verts = node.vertices or []
    faces = node.faces or []
    norms = node.normals or []
    uvs   = getattr(node,'uvs',[]) or []
    if not verts or not faces: continue
    
    n_v = len(verts)
    has_n = (len(norms)==n_v)
    has_uv = (len(uvs)==n_v)
    
    va = np.array(verts,'f4')
    na = np.array(norms,'f4') if has_n else np.tile([0.,0.,1.],(n_v,1)).astype('f4')
    ua = np.array(uvs,'f4') if has_uv else np.zeros((n_v,2),'f4')
    
    vidx = []
    for f in faces:
        if len(f)<3: continue
        a,b,c = int(f[0]),int(f[1]),int(f[2])
        if max(a,b,c)>=n_v: continue
        vidx.extend([a,b,c])
    if not vidx: continue
    
    ia = np.array(vidx, dtype=np.int32)
    # Interleave: pos(3)+norm(3)+uv(2) = 8 floats
    buf = np.empty((len(ia),8),'f4')
    buf[:,:3] = va[ia]; buf[:,3:6] = na[ia]; buf[:,6:] = ua[ia]
    
    vbo = CTX.buffer(buf.tobytes())
    vao = CTX.vertex_array(prog, [(vbo,'3f 3f 2f','in_pos','in_norm','in_uv')])
    
    # Get texture
    tc = getattr(node,'texture_clean',None)
    tn = (tc if isinstance(tc,str) else (getattr(node,'texture','') or '')).strip().lower()
    
    # Fuzzy lookup: lsl_dirt02 might be in cache as lsl_dirt02 or sle_dirt02
    tex = tex_cache.get(tn)
    if tex is None and '_' in tn:
        suf = tn[tn.find('_'):]
        for k,v in tex_cache.items():
            if k.endswith(suf): tex=v; break
    
    diff = getattr(node,'diffuse',None)
    if diff and len(diff)>=3 and max(diff)>0.05:
        c=(max(.2,min(1.,diff[0])),max(.2,min(1.,diff[1])),max(.2,min(1.,diff[2])))
    else:
        c=PALETTE[idx%4]
    
    vaos.append({'vao':vao,'color':c,'tex':tex,'has_uv':has_uv,'name':node.name})

total_with_tex = sum(1 for v in vaos if v['tex'])
print(f"Uploaded {len(vaos)} VAOs, {total_with_tex} with textures")

# ── Render ────────────────────────────────────────────────────────────────────
W,H = 1280, 960

def perspective(fov,asp,nr,fr):
    f=1./math.tan(math.radians(fov)*.5)
    m=np.zeros((4,4),'f4')
    m[0,0]=f/asp;m[1,1]=f;m[2,2]=(fr+nr)/(nr-fr);m[2,3]=(2*fr*nr)/(nr-fr);m[3,2]=-1.
    return m

def lookat(eye, at):
    up = np.array([0.,0.,1.],'f4')
    f = np.array(at,'f4')-np.array(eye,'f4'); f/=np.linalg.norm(f)
    r = np.cross(f,up)
    if np.linalg.norm(r)<1e-6: r=np.cross(f,np.array([0.,1.,0.],'f4'))
    r/=np.linalg.norm(r); u=np.cross(r,f)
    m=np.eye(4, dtype='f4')
    m[0,:3]=r;m[1,:3]=u;m[2,:3]=-f
    m[0,3]=-float(np.dot(r,eye));m[1,3]=-float(np.dot(u,eye));m[2,3]=float(np.dot(f,eye))
    return m

def render_view(eye, tgt, path, label=''):
    fbo = CTX.framebuffer(
        color_attachments=[CTX.renderbuffer((W,H))],
        depth_attachment=CTX.depth_renderbuffer((W,H)))
    fbo.use()
    CTX.viewport=(0,0,W,H)
    CTX.clear(0.07,0.08,0.14,1.0)
    CTX.enable(moderngl.DEPTH_TEST)
    CTX.disable(moderngl.CULL_FACE)
    
    proj = perspective(60.,W/H,0.5,span*6.)
    view = lookat(np.array(eye,'f4'),np.array(tgt,'f4'))
    mvp = (proj@view).T.astype('f4')
    model_m = np.eye(4, dtype='f4')
    
    prog['model'].write(model_m.tobytes())
    prog['amb'].value = 0.38
    
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
    with open(path,'wb') as f:
        f.write(f"P6\n{W} {H}\n255\n".encode())
        for row in reversed([raw[i*W*3:(i+1)*W*3] for i in range(H)]): f.write(row)
    
    nb = sum(1 for i in range(0,len(raw),3) if abs(raw[i]-18)>10 or abs(raw[i+1]-20)>10 or abs(raw[i+2]-36)>10)
    # Check color variety (not just gray)
    unique_colors = set()
    for i in range(0,len(raw),9): unique_colors.add((raw[i]//32,raw[i+1]//32,raw[i+2]//32))
    print(f"  [{label}] {path}: {100*nb//(W*H)}% geom, {len(unique_colors)} color groups")
    
    import subprocess
    subprocess.run(['convert',path,path.replace('.ppm','.png')], capture_output=True)

cx,cy,cz = float(center[0]),float(center[1]),float(center[2])
d = span*0.9
mr = math.radians

print(f"\nRendering 5 views from center ({cx:.1f},{cy:.1f},{cz:.1f}), d={d:.1f}")

# ISO view
render_view([cx+d*math.cos(mr(45))*math.cos(mr(35)),
             cy+d*math.sin(mr(45))*math.cos(mr(35)),
             cz+d*math.sin(mr(35))], [cx,cy,cz], 'final_iso.ppm', 'ISO')

# Interior looking east along corridor
render_view([cx-30.,cy+3.,cz+12.], [cx+45.,cy,cz+10.], 'final_interior.ppm', 'INTERIOR')

# South view
render_view([cx+d*math.cos(mr(270))*math.cos(mr(25)),
             cy+d*math.sin(mr(270))*math.cos(mr(25)),
             cz+d*math.sin(mr(25))], [cx,cy,cz], 'final_south.ppm', 'SOUTH')

# Top-down
render_view([cx, cy-0.001, cz+d*1.15], [cx,cy,cz], 'final_topdown.ppm', 'TOPDOWN')

# NW angle  
render_view([cx+d*math.cos(mr(-130))*math.cos(mr(28)),
             cy+d*math.sin(mr(-130))*math.cos(mr(28)),
             cz+d*math.sin(mr(28))], [cx,cy,cz], 'final_nw.ppm', 'NW')

print("\n=== ALL RENDERS COMPLETE ===")
