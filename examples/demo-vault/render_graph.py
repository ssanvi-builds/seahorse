#!/usr/bin/env python3
"""Render the demo vault's memory graph, twice:

- graph.svg  static, for embedding in a README (GitHub renders it as an image)
- graph.html self-contained interactive version (zoom, pan, drag, tooltips),
  zero dependencies and zero CDN — open it locally or serve via GitHub Pages.

Stdlib-only, deterministic. Regenerate after editing the demo vault:

    python3 render_graph.py
"""

from __future__ import annotations

import html
import json
import math
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)

# palette: one color per cognitive_type (dark-theme friendly)
COLORS = {
    "social": "#ffa657",        # orange
    "semantic": "#79c0ff",      # blue
    "episodic": "#7ee787",      # green
    "project_doc": "#d2a8ff",   # purple
    "procedural": "#ffd666",    # yellow
}
DEFAULT = "#8b949e"


def parse(path: pathlib.Path):
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        return None
    fm, body = m.group(1), text[m.end():]

    def grab(key: str) -> str:
        r = re.search(rf"^{key}: (.*)$", fm, re.M)
        return r.group(1).strip().strip('"') if r else ""

    links = [t.strip() for t in re.findall(r"\[\[([^\]|#]+)", body)]
    return {
        "title": grab("title") or path.stem,
        "type": grab("cognitive_type") or DEFAULT,
        "supersedes": grab("supersedes") or None,
        "summary": grab("summary"),
        "links": links,
    }


def build():
    notes = {}
    for p in sorted(HERE.glob("*.md")):
        if p.name == "README.md":
            continue
        n = parse(p)
        if n:
            notes[p.stem] = n
    return notes


def short_label(name: str) -> str:
    """Label for the static SVG: no date prefix, truncated."""
    s = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", name)
    return s[:26] + "…" if len(s) > 27 else s


def main() -> None:
    notes = build()
    names = list(notes)
    idx = {n: i for i, n in enumerate(names)}

    # edges: wiki-links + supersedes (id-resolved, directed)
    plain, sup = set(), []
    for name, n in notes.items():
        for t in n["links"]:
            if t != name and t in notes:
                a, b = sorted((idx[name], idx[t]))
                plain.add((a, b))
    # supersedes resolution needs id->name mapping
    text_ids = {}
    for p in sorted(HERE.glob("*.md")):
        if p.name == "README.md":
            continue
        m = re.search(r"^id: (\S+)$", p.read_text(encoding="utf-8"), re.M)
        if m:
            text_ids[m.group(1)] = p.stem
    for name, n in notes.items():
        if n["supersedes"] and n["supersedes"] in text_ids:
            sup.append((idx[text_ids[n["supersedes"]]], idx[name]))

    # --- deterministic spring layout (Fruchterman-Reingold-ish, stdlib only) --
    N = len(names)
    W, H = 1600.0, 900.0
    xs = [0.0] * N
    ys = [0.0] * N
    # circle init by insertion order for determinism
    for i in range(N):
        ang = 2 * math.pi * i / N
        xs[i] = W / 2 + (W / 4) * math.cos(ang)
        ys[i] = H / 2 + (H / 4) * math.sin(ang)

    k = math.sqrt(W * H / N) * 1.1
    all_edges = [(a, b) for a, b in plain] + [(a, b) for a, b in sup]
    for it in range(600):
        temp = (1.0 - it / 600) * 50 + 2
        dx = [0.0] * N
        dy = [0.0] * N
        for i in range(N):
            for j in range(i + 1, N):
                ddx, ddy = xs[i] - xs[j], ys[i] - ys[j]
                d2 = ddx * ddx + ddy * ddy + 1e-6
                f = k * k / d2
                ddx, ddy = ddx / math.sqrt(d2) * f, ddy / math.sqrt(d2) * f
                dx[i] += ddx; dy[i] += ddy
                dx[j] -= ddx; dy[j] -= ddy
        for a, b in all_edges:
            ddx, ddy = xs[a] - xs[b], ys[a] - ys[b]
            d = math.sqrt(ddx * ddx + ddy * ddy) + 1e-6
            f = d * d / k / 8
            ddx, ddy = ddx / d * f, ddy / d * f
            dx[a] -= ddx; dy[a] -= ddy
            dx[b] += ddx; dy[b] += ddy
        for i in range(N):
            # gentle gravity keeps disconnected components on canvas
            dx[i] += (W / 2 - xs[i]) * 0.04
            dy[i] += (H / 2 - ys[i]) * 0.04
            d = math.hypot(dx[i], dy[i]) + 1e-6
            step = min(d, temp)
            xs[i] = min(W - 40, max(40, xs[i] + dx[i] / d * step))
            ys[i] = min(H - 40, max(40, ys[i] + dy[i] / d * step))

    deg = [0] * N
    for a, b in list(plain) + sup:
        deg[a] += 1
        deg[b] += 1
    labeled = {i for i in range(N) if deg[i] >= 6}

    # normalize to fill the canvas (keeps relative layout, adds margin)
    pad = 70
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    sx = (W - 2 * pad) / (x1 - x0)
    sy = (H - 2 * pad) / (y1 - y0)
    s = min(sx, sy)  # uniform scale, no distortion
    xs = [pad + (x - x0) * s + (W - 2 * pad - (x1 - x0) * s) / 2 for x in xs]
    ys = [pad + (y - y0) * s + (H - 2 * pad - (y1 - y0) * s) / 2 for y in ys]

    # ---------------------------------------------------------------- SVG ---
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" '
        'font-family="system-ui, sans-serif">',
        '<rect width="100%" height="100%" fill="#0d1117"/>',
    ]
    for a, b in plain:
        lines.append(f'<line x1="{xs[a]:.1f}" y1="{ys[a]:.1f}" x2="{xs[b]:.1f}" '
                     f'y2="{ys[b]:.1f}" stroke="#8b949e" stroke-opacity="0.18"/>')
    for a, b in sup:
        lines.append(f'<line x1="{xs[a]:.1f}" y1="{ys[a]:.1f}" x2="{xs[b]:.1f}" '
                     f'y2="{ys[b]:.1f}" stroke="#f85149" stroke-opacity="0.9" '
                     'stroke-width="1.5"/>')
    for i, name in enumerate(names):
        n = notes[name]
        r = 3 + math.sqrt(deg[i]) * 1.6
        c = COLORS.get(n["type"], DEFAULT)
        lines.append(f'<circle cx="{xs[i]:.1f}" cy="{ys[i]:.1f}" r="{r:.1f}" '
                     f'fill="{c}"><title>{html.escape(n["title"])}</title></circle>')
        if i in labeled:
            lines.append(f'<text x="{xs[i]:.1f}" y="{ys[i] - r - 3:.1f}" '
                         f'text-anchor="middle" font-size="10" fill="#c9d1d9" '
                         f'stroke="#0d1117" stroke-width="3" '
                         f'paint-order="stroke">{html.escape(short_label(name))}</text>')
    lines.append("</svg>")
    (HERE / "graph.svg").write_text("\n".join(lines), encoding="utf-8")

    # ---------------------------------------------------------------- HTML --
    data = {
        "nodes": [
            {"i": i, "name": name, "label": short_label(name),
             "title": notes[name]["title"],
             "type": notes[name]["type"], "deg": deg[i]}
            for i, name in enumerate(names)
        ],
        "plain": [list(e) for e in plain],
        "sup": [list(e) for e in sup],
        "colors": COLORS,
    }
    page = HTML.replace("/*__DATA__*/", json.dumps(data))
    (HERE / "graph.html").write_text(page, encoding="utf-8")
    print(f"{N} nodes, {len(plain)} link edges, {len(sup)} supersedes edges "
          f"-> graph.svg + graph.html")


HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Demo vault graph</title>
<style>
  html,body{margin:0;height:100%;background:#0d1117;color:#c9d1d9;
    font-family:system-ui,sans-serif;overflow:hidden}
  #tip{position:fixed;pointer-events:none;background:#161b22;border:1px solid
    #30363d;border-radius:6px;padding:6px 10px;font-size:12px;display:none;
    max-width:280px;z-index:2}
  #legend{position:fixed;left:12px;bottom:12px;background:#161b22cc;
    border:1px solid #30363d;border-radius:8px;padding:8px 12px;font-size:12px}
  .sw{display:inline-block;width:10px;height:10px;border-radius:50%;
    margin:0 5px -1px 0}
</style></head>
<body><div id="tip"></div>
<div id="legend"></div>
<canvas id="c"></canvas>
<script>
const DATA = /*__DATA__*/;
const cv = document.getElementById('c'), ctx = cv.getContext('2d');
const tip = document.getElementById('tip');
let W, H;
function resize(){W=cv.width=innerWidth;H=cv.height=innerHeight;}
resize(); addEventListener('resize',resize);
// legend
const leg = document.getElementById('legend');
leg.innerHTML = Object.entries(DATA.colors).map(([k,c]) =>
  `<div><span class="sw" style="background:${c}"></span>${k}</div>`).join('') +
  `<div><span class="sw" style="background:#f85149;border-radius:1px"></span>supersedes</div>`;
// state
const N = DATA.nodes.length;
const pos = DATA.nodes.map((n,i)=>({x:Math.cos(i/N*6.28)*300+innerWidth/2,
  y:Math.sin(i*6.28/N)*300+innerHeight/2, vx:0, vy:0, fixed:false}));
let view = {x:0, y:0, s:1};
let alpha = 1;
function tick(){
  // repulsion (O(n^2), fine for ~100 nodes)
  for(let i=0;i<N;i++) for(let j=i+1;j<N;j++){
    let dx=pos[i].x-pos[j].x, dy=pos[i].y-pos[j].y;
    let d2=dx*dx+dy*dy+1e-4, f=11000/d2;
    let d=Math.sqrt(d2); dx/=d; dy/=d;
    if(!pos[i].fixed){pos[i].vx+=dx*f;pos[i].vy+=dy*f;}
    if(!pos[j].fixed){pos[j].vx-=dx*f;pos[j].vy-=dy*f;}
  }
  // springs
  for(const e of DATA.plain.concat(DATA.sup)){
    const [a,b]=e;
    let dx=pos[a].x-pos[b].x, dy=pos[a].y-pos[b].y;
    const d=Math.sqrt(dx*dx+dy*dy)+1e-4, f=(d-110)*0.02;
    dx/=d; dy/=d;
    if(!pos[a].fixed){pos[a].vx-=dx*f;pos[a].vy-=dy*f;}
    if(!pos[b].fixed){pos[b].vx+=dx*f;pos[b].vy+=dy*f;}
  }
  // integrate: gravity to center + displacement cap (cooling), like FR
  const cap = 40*alpha + 2;
  for(let i=0;i<N;i++){
    const p=pos[i];
    if(p.fixed){p.vx=p.vy=0;continue;}
    p.vx += (W/2-p.x)*0.01;
    p.vy += (H/2-p.y)*0.01;
    p.vx*=.85; p.vy*=.85;
    const sp=Math.hypot(p.vx,p.vy);
    if(sp>cap){p.vx*=cap/sp; p.vy*=cap/sp;}
    p.x+=p.vx; p.y+=p.vy;
  }
  alpha=Math.max(alpha*.995,.05);
}
let hover=-1, drag=-1, pan=false, px=0, py=0;
function at(mx,my){
  const x=(mx-view.x)/view.s, y=(my-view.y)/view.s;
  for(let i=N-1;i>=0;i--){
    const r=(3+Math.sqrt(DATA.nodes[i].deg)*1.6)/view.s+4;
    if(Math.hypot(pos[i].x-x,pos[i].y-y)<r) return i;
  }
  return -1;
}
cv.addEventListener('pointerdown',e=>{
  const i=at(e.clientX,e.clientY);
  if(i>=0){drag=i;pos[i].fixed=true;alpha=0.6;}
  else{pan=true;px=e.clientX;py=e.clientY;}
});
addEventListener('pointerup',()=>{if(drag>=0)pos[drag].fixed=false;drag=-1;pan=false;});
cv.addEventListener('pointermove',e=>{
  if(drag>=0){pos[drag].x=(e.clientX-view.x)/view.s;pos[drag].y=(e.clientY-view.y)/view.s;alpha=.5;}
  else if(pan){view.x+=e.clientX-px;view.y+=e.clientY-py;px=e.clientX;py=e.clientY;}
  else{hover=at(e.clientX,e.clientY);
    if(hover>=0){const n=DATA.nodes[hover];
      tip.style.display='block';
      tip.style.left=(e.clientX+14)+'px';tip.style.top=(e.clientY+14)+'px';
      tip.innerHTML=`<b>${n.title}</b><br><span style="opacity:.7">${n.type}</span>`;}
    else tip.style.display='none';
    cv.style.cursor=hover>=0?'grab':'default';}
});
cv.addEventListener('wheel',e=>{
  e.preventDefault();
  const f=e.deltaY<0?1.12:1/1.12;
  view.x=e.clientX-(e.clientX-view.x)*f;
  view.y=e.clientY-(e.clientY-view.y)*f;
  view.s*=f;
},{passive:false});
function draw(){
  tick();
  ctx.setTransform(1,0,0,1,0,0);
  ctx.fillStyle='#0d1117';ctx.fillRect(0,0,W,H);
  ctx.setTransform(view.s,0,0,view.s,view.x,view.y);
  for(const [a,b] of DATA.plain){
    ctx.strokeStyle='rgba(139,148,158,.18)';ctx.lineWidth=1/view.s;
    ctx.beginPath();ctx.moveTo(pos[a].x,pos[a].y);ctx.lineTo(pos[b].x,pos[b].y);ctx.stroke();
  }
  for(const [a,b] of DATA.sup){
    ctx.strokeStyle='#f85149';ctx.lineWidth=1.6/view.s;
    ctx.beginPath();ctx.moveTo(pos[a].x,pos[a].y);ctx.lineTo(pos[b].x,pos[b].y);ctx.stroke();
  }
  DATA.nodes.forEach((n,i)=>{
    const r=3+Math.sqrt(n.deg)*1.6;
    ctx.fillStyle=DATA.colors[n.type]||'#8b949e';
    ctx.beginPath();ctx.arc(pos[i].x,pos[i].y,r,0,6.28);ctx.fill();
    if(n.deg>=6||i===hover){
      ctx.fillStyle='#c9d1d9';ctx.font=`${11/view.s}px system-ui`;
      ctx.textAlign='center';ctx.fillText(n.label,pos[i].x,pos[i].y-r-4);}
  });
  requestAnimationFrame(draw);
}
draw();
</script></body></html>
"""

if __name__ == "__main__":
    main()