"""Draw a structure as a cartoon (ribbon) SVG straight from PDB coordinates.

No image generation is involved. The backbone comes from the deposited mmCIF file
(alpha-carbon / phosphorus positions), the secondary structure from its own
_struct_conf / _struct_sheet_range records: helices are drawn as shaded cylinders,
strands as arrows, loops as thin tubes and nucleic acids as a thin tube,
coloured by entity and shaded by depth, with the ligands of interest as dots.
"""

from __future__ import annotations

import gzip
import math
import re
from collections import defaultdict

PALETTE = ["#3B6FB6", "#E0A030", "#3E9E8F", "#9C5FB5", "#D0605E", "#6C8EBF",
           "#8DA94A", "#C17C4E", "#5AA9C8", "#B5869E"]
LIGAND_COLOR = "#E4572E"
# Carbon colours of the ligands in a figure, in order; other elements keep their usual colours
# (N blue, O red, S yellow, P orange), so none of these is red, blue or orange.
LIGAND_PALETTE = ["#C23B8E", "#2F9E57", "#7A52C7", "#D6A21E", "#178F8F", "#8C5A3C"]
ELEMENT_COLORS = {"N": "#3461D1", "O": "#E8303A", "S": "#E0B21C", "P": "#F08A24", "F": "#3FA34D",
                  "CL": "#3FA34D", "BR": "#9A3B1F", "I": "#7A2E99", "SE": "#E58A1C", "B": "#E6918A"}
COVALENT_RADIUS = {"H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66, "S": 1.05, "P": 1.07, "F": 0.57,
                   "CL": 1.02, "BR": 1.20, "I": 1.39, "SE": 1.20, "B": 0.84, "SI": 1.11}
SUBDIV = 3  # spline samples per residue
TOKEN = re.compile(r"""'(?:[^']|'(?=\S))*'|"(?:[^"]|"(?=\S))*"|\S+""")
MAX_TRACE_POINTS = 6000


def _unquote(v: str) -> str:
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"" else v


def parse_cif(text: str, categories=("_atom_site", "_entity", "_struct_ref", "_struct_conf", "_struct_sheet_range")) -> dict[str, list[dict]]:
    """Rows of the requested mmCIF categories, from both loop_ and single-row key-value form."""
    lines = text.splitlines()
    out: dict[str, list[dict]] = {c: [] for c in categories}
    single: dict[str, dict] = {}
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].strip()
        if line == "loop_":
            j = i + 1
            fields = []
            while j < n and lines[j].startswith("_"):
                fields.append(lines[j].strip())
                j += 1
            cat = fields[0].split(".")[0] if fields else ""
            if cat in out:
                names = [f.split(".", 1)[1] for f in fields]
                buf = []
                while j < n and not lines[j].startswith(("#", "loop_", "_")):
                    if lines[j].startswith(";"):  # multi-line text field, closed by a line starting with ';'
                        block = [lines[j][1:]]
                        while j + 1 < n and not lines[j + 1].startswith(";"):
                            j += 1
                            block.append(lines[j])
                        j += 1
                        buf.append("\n".join(block).strip())
                    else:
                        buf += TOKEN.findall(lines[j])
                    while len(buf) >= len(names):
                        out[cat].append(dict(zip(names, (_unquote(v) for v in buf[:len(names)]))))
                        buf = buf[len(names):]
                    j += 1
            i = j
            continue
        if line.startswith("_") and "." in line:
            key, _, rest = line.partition(" ")
            cat, item = key.split(".", 1)
            if cat in out:
                value = rest.strip()
                if not value and i + 1 < n:
                    i += 1
                    if lines[i].startswith(";"):
                        block = [lines[i][1:]]
                        while i + 1 < n and not lines[i + 1].startswith(";"):
                            i += 1
                            block.append(lines[i])
                        i += 1
                        value = " ".join(block).strip()
                    else:
                        value = lines[i].strip()
                single.setdefault(cat, {})[item] = _unquote(value)
        i += 1
    for cat, row in single.items():
        out[cat].append(row)
    return out


def _jacobi_eigen(a: list[list[float]]) -> list[list[float]]:
    """Eigenvectors (as columns, sorted by descending eigenvalue) of a symmetric 3x3 matrix."""
    a = [row[:] for row in a]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(50):
        p, q = max(((0, 1), (0, 2), (1, 2)), key=lambda pq: abs(a[pq[0]][pq[1]]))
        if abs(a[p][q]) < 1e-9:
            break
        theta = 0.5 * math.atan2(2 * a[p][q], a[q][q] - a[p][p])
        c, s = math.cos(theta), math.sin(theta)
        for k in range(3):
            akp, akq = a[k][p], a[k][q]
            a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
        for k in range(3):
            apk, aqk = a[p][k], a[q][k]
            a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
        for k in range(3):
            vkp, vkq = v[k][p], v[k][q]
            v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    order = sorted(range(3), key=lambda i: -a[i][i])
    return [[v[r][c] for c in order] for r in range(3)]


def _mix(hex_color: str, white: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    r, g, b = (round(x + (255 - x) * white) for x in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def _toward(hex_color: str, target: str, f: float) -> str:
    """Blend a colour toward another (the depth cue: far parts melt into the background)."""
    a = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(target[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))


def _cycled(palette: list[str], i: int) -> str:
    """The i-th colour; past the end of the palette, the same hues again but darker, so no two entities look alike."""
    n = len(palette)
    return palette[i % n] if i < n else _toward(palette[i % n], "#000000", min(0.55, 0.3 * (i // n)))


class _Burial:
    """How buried a point is (0 = exposed, 1 = deep inside), from the number of backbone points around it.

    A cheap stand-in for the ambient occlusion of ChimeraX's `lighting soft`: crowded regions are darker,
    exposed ones lighter, which is what gives the structure its volume without any directional light.
    """

    def __init__(self, pts: list[tuple], radius: float = 11.0):
        self.r, self.cells = radius, defaultdict(list)
        for p in pts:
            self.cells[self._key(p)].append(p)
        sample = pts[::max(1, len(pts) // 1500)]
        counts = sorted(self._count(p) for p in sample) or [0]
        self.lo = counts[int(0.1 * (len(counts) - 1))]
        self.hi = max(counts[int(0.9 * (len(counts) - 1))], self.lo + 1)

    def _key(self, p):
        return (int(p[0] // self.r), int(p[1] // self.r), int(p[2] // self.r))

    def _count(self, p) -> int:
        kx, ky, kz = self._key(p)
        n = 0
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for q in self.cells.get((kx + dx, ky + dy, kz + dz), ()):
                        if math.dist(p, q) <= self.r:
                            n += 1
        return n

    def __call__(self, p) -> float:
        return max(0.0, min(1.0, (self._count(p) - self.lo) / (self.hi - self.lo)))


def _shade(hex_color: str, dark: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"#{round(r * (1 - dark)):02x}{round(g * (1 - dark)):02x}{round(b * (1 - dark)):02x}"


def _spline(pts: list[tuple], sub: int) -> list[tuple]:
    """Catmull-Rom curve through pts with `sub` samples per interval; sample i*sub is pts[i]."""
    n = len(pts)
    if n < 3 or sub < 2:
        return list(pts)
    out = []
    for i in range(n - 1):
        p0, p1, p2, p3 = pts[max(i - 1, 0)], pts[i], pts[i + 1], pts[min(i + 2, n - 1)]
        for k in range(sub):
            t = k / sub
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * (2 * p1[c] + (p2[c] - p0[c]) * t
                                    + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * t2
                                    + (3 * p1[c] - p0[c] - 3 * p2[c] + p3[c]) * t3) for c in range(3)))
    out.append(pts[-1])
    return out


def _secondary_structure(cif: dict) -> dict[tuple[str, int], str]:
    """(asym id, seq id) -> 'H' or 'E' from the entry's own annotation."""
    ss: dict[tuple[str, int], str] = {}

    def mark(rows, kind, ok=lambda r: True):
        for r in rows:
            if not ok(r):
                continue
            try:
                asym, a, b = r["beg_label_asym_id"], int(r["beg_label_seq_id"]), int(r["end_label_seq_id"])
            except (KeyError, ValueError):
                continue
            for i in range(a, b + 1):
                ss[(asym, i)] = kind

    mark(cif["_struct_sheet_range"], "E")
    mark(cif["_struct_conf"], "H", lambda r: r.get("conf_type_id", "HELX_P").startswith("HELX"))
    return ss


def _elements(res: list[dict]) -> list[tuple[str, int, int, bool]]:
    """Consecutive residues of one secondary-structure type (and one highlight state) as
    (type, first, last, highlighted) index ranges.

    Helices shorter than 4 residues and strands shorter than 3 are drawn as loop.
    """
    groups: list[list] = []
    for i, r in enumerate(res):
        kind, hl = r["ss"], bool(r.get("hl"))
        if groups and groups[-1][0] == kind and groups[-1][3] == hl:
            groups[-1][2] = i
        else:
            groups.append([kind, i, i, hl])
    for g in groups:
        n = g[2] - g[1] + 1
        if (g[0] == "H" and n < 4) or (g[0] == "E" and n < 3):
            g[0] = "C"
    merged: list[list] = []
    for g in groups:
        if merged and merged[-1][0] == g[0] and merged[-1][3] == g[3] and g[0] in ("C", "N"):
            merged[-1][2] = g[2]
        else:
            merged.append(g)
    return [tuple(g) for g in merged]


def _split_straight(pts: list[tuple], tol: float) -> list[tuple[int, int]]:
    """Index ranges (first, last) of near-straight runs of an axis polyline (Douglas-Peucker)."""
    def worst(a, b):
        ax, ay, az = pts[a]
        d = (pts[b][0] - ax, pts[b][1] - ay, pts[b][2] - az)
        dd = sum(c * c for c in d) or 1e-9
        best, at = 0.0, a
        for i in range(a + 1, b):
            v = (pts[i][0] - ax, pts[i][1] - ay, pts[i][2] - az)
            u = max(0.0, min(1.0, sum(v[c] * d[c] for c in range(3)) / dd))
            dist = math.dist(v, tuple(u * d[c] for c in range(3)))
            if dist > best:
                best, at = dist, i
        return best, at

    out: list[tuple[int, int]] = []
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        dist, at = worst(a, b)
        if dist > tol and b - a > 3:
            stack += [(a, at), (at, b)]
        else:
            out.append((a, b))
    return sorted(out)


def _cylinder(p0: tuple, p1: tuple, scale: float, radius: float, col: str, uid: int,
              sx) -> str:
    """A shaded cylinder from p0 to p1 (projected 3D, Å), lit from the upper left."""
    length3 = math.dist(p0, p1)
    if length3 < 1e-6:
        return ""
    d3 = [(p1[c] - p0[c]) / length3 for c in range(3)]
    a, b = sx(p0), sx(p1)
    length = math.dist(a, b)
    if length < 0.5:
        return ""
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    nx, ny = -uy, ux
    if nx + ny > 0:  # make +n face the light (up and to the left)
        nx, ny = -nx, -ny
    m = max(0.6, radius * abs(d3[2]))  # semi-minor axis of the end caps
    ang = math.degrees(math.atan2(uy, ux))
    edge = _shade(col, 0.30)
    lit, dark = _mix(col, 0.16), _shade(col, 0.16)
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    gid = f"c{uid}"
    grad = (f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" '
            f'x1="{mx + nx * radius:.1f}" y1="{my + ny * radius:.1f}" '
            f'x2="{mx - nx * radius:.1f}" y2="{my - ny * radius:.1f}">'
            f'<stop offset="0" stop-color="{lit}"/><stop offset="0.35" stop-color="{col}"/>'
            f'<stop offset="1" stop-color="{dark}"/></linearGradient>')
    p = lambda pt, s: f"{pt[0] + nx * radius * s:.1f},{pt[1] + ny * radius * s:.1f}"
    body = (f'M{p(a, 1)} L{p(b, 1)} A{m:.1f},{radius:.1f} {ang:.1f} 0 0 {p(b, -1)} '
            f'L{p(a, -1)} A{m:.1f},{radius:.1f} {ang:.1f} 0 0 {p(a, 1)} Z')
    near = b if p1[2] >= p0[2] else a
    cap = (f'<ellipse cx="{near[0]:.1f}" cy="{near[1]:.1f}" rx="{m:.1f}" ry="{radius:.1f}" '
           f'transform="rotate({ang:.1f} {near[0]:.1f} {near[1]:.1f})" fill="{_mix(col, 0.2)}" '
           f'stroke="{edge}" stroke-width="0.9"/>')
    return (f'<defs>{grad}</defs><path d="{body}" fill="url(#{gid})" stroke="{edge}" '
            f'stroke-width="1" stroke-linejoin="round"/>{cap}')


def _points(pts: list[tuple[float, float]]) -> str:
    return " ".join(f"{a:.1f},{b:.1f}" for a, b in pts)


AA1 = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E", "GLY": "G",
       "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P", "SER": "S",
       "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V", "MSE": "M", "SEC": "U"}
BACKBONE = {"N", "C", "O", "OXT"}
CONTACT = 4.0  # Å: residues with any heavy atom this close to the ligand are drawn and listed
MAX_SITES = 24  # copies of one ligand listed for the 3D viewer's "next binding site" button
POLAR = 3.3    # Å: N/O pairs this close are drawn as polar contacts


def _load(cif_gz: bytes, ligand_ids: set[str], gene_names: dict[str, str] | None,
          polymer_atoms: bool = False) -> dict:
    """Backbone trace (CA/P with secondary structure), ligand atoms and, if asked, every polymer heavy atom."""
    cif = parse_cif(gzip.decompress(cif_gz).decode("utf-8", "replace"))
    atoms = cif["_atom_site"]
    entities = {e.get("id", ""): e.get("pdbx_description", "") for e in cif["_entity"]}
    descriptions = dict(entities)
    for ref in cif["_struct_ref"]:
        gene = (gene_names or {}).get(ref.get("pdbx_db_accession", ""))
        if ref.get("db_name") == "UNP" and gene:
            entities[ref.get("entity_id", "")] = gene
    ss_map = _secondary_structure(cif)
    model = next((a.get("pdbx_PDB_model_num") for a in atoms), "1")
    chains: dict[str, list[dict]] = defaultdict(list)
    chain_entity: dict[str, str] = {}
    seen: set[tuple] = set()
    lig_atoms, poly = [], []
    for a in atoms:
        if a.get("pdbx_PDB_model_num", "1") != model:
            continue
        el = (a.get("type_symbol") or "C").upper()
        if el in ("H", "D"):
            continue
        xyz = (float(a["Cartn_x"]), float(a["Cartn_y"]), float(a["Cartn_z"]))
        if a.get("group_PDB") == "ATOM":
            key = a.get("label_asym_id", "?")
            try:
                seq = int(a.get("label_seq_id", ""))
            except ValueError:
                continue
            name = a.get("label_atom_id", "")
            if (key, seq, name) in seen:  # alternate locations
                continue
            seen.add((key, seq, name))
            if polymer_atoms:
                poly.append({"xyz": xyz, "el": el, "name": name, "res": (key, seq),
                             "comp": a.get("label_comp_id", ""), "auth": a.get("auth_seq_id", str(seq)),
                             "chain": a.get("auth_asym_id", key), "entity": a.get("label_entity_id", "?")})
            if name in ("CA", "P"):
                auth = a.get("auth_seq_id", "")
                chains[key].append({"seq": seq, "xyz": xyz, "at": name,
                                    "auth": int(auth) if auth.lstrip("-").isdigit() else None,
                                    "achain": a.get("auth_asym_id", key),
                                    "ss": "N" if name == "P" else ss_map.get((key, seq), "C")})
                chain_entity[key] = a.get("label_entity_id", "?")
        elif a.get("group_PDB") == "HETATM" and a.get("label_comp_id") in ligand_ids:
            inst = (a.get("label_asym_id"), a.get("auth_seq_id"), a.get("label_comp_id"))
            if (inst, a.get("label_atom_id")) in seen:
                continue
            seen.add((inst, a.get("label_atom_id")))
            lig_atoms.append({"xyz": xyz, "inst": inst, "el": el, "entity": a.get("label_entity_id", "")})
    return {"entities": entities, "descriptions": descriptions, "chains": chains, "chain_entity": chain_entity,
            "lig_atoms": lig_atoms, "poly": poly}


def _runs(chains: dict[str, list[dict]], n_points: int, max_points: int = MAX_TRACE_POINTS
          ) -> list[tuple[str, list[dict]]]:
    """Continuous stretches of each chain, thinned for very large models."""
    runs: list[tuple[str, list[dict]]] = []
    for k, v in chains.items():
        cur: list[dict] = []
        for r in v:
            if cur:
                limit = 9.5 if r["at"] == "P" else 5.6
                if r["seq"] - cur[-1]["seq"] != 1 or math.dist(cur[-1]["xyz"], r["xyz"]) > limit:
                    runs.append((k, cur))
                    cur = []
            cur.append(r)
        runs.append((k, cur))
    runs = [(k, v) for k, v in runs if len(v) > 1]
    step = max(1, math.ceil(n_points / max_points))
    if step > 1:
        runs = [(k, v[::step] + ([v[-1]] if (len(v) - 1) % step else [])) for k, v in runs]
    return runs


def _basis(points: list[tuple]) -> tuple[tuple, list[list[float]]]:
    """Centroid and principal axes (longest first) of a point cloud."""
    c = tuple(sum(p[i] for p in points) / len(points) for i in range(3))
    cov = [[sum((p[i] - c[i]) * (p[j] - c[j]) for p in points) for j in range(3)] for i in range(3)]
    return c, _jacobi_eigen(cov)


def _det3(m: list[list[float]]) -> float:
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def _projector(center: tuple, ev: list[list[float]], swap: bool):
    """Maps a point to (x right, y up, z toward the viewer). The frame is kept right-handed (the eigenvectors alone
    may form a left-handed one, which would draw the mirror image of the molecule)."""
    flip = -1.0 if (_det3(ev) < 0) != swap else 1.0

    def proj(p):
        d = (p[0] - center[0], p[1] - center[1], p[2] - center[2])
        q = tuple(sum(d[k] * ev[k][c] for k in range(3)) for c in range(3))
        q = (q[1], q[0], q[2]) if swap else q
        return (q[0], q[1], q[2] * flip)
    return proj


ON_TOP = 1e6   # added to the depth of a highlighted stretch so it is drawn after (over) everything else


def _cartoon(runs3: list[tuple[str, list[dict]]], chain_entity: dict, color: dict, sx, scale: float,
             tint, hl_color: str | None = None) -> list[tuple[float, str]]:
    """Depth-tagged SVG pieces: cylinders for helices, arrows for strands, tubes for loops and nucleic acids.
    Residues flagged "hl" (the stretch a figure note points at) are drawn in hl_color, undimmed."""
    tube = max(4.5, min(20.0, 2.7 * scale))   # helix cylinder diameter in px
    coil_w = max(1.4, 0.36 * tube)
    nuc_w = max(2.0, 0.5 * tube)
    arrow_hw = 0.5 * tube
    edge = 1.2 if tube > 8 else 0.8             # outline width
    parts: list[tuple[float, str]] = []
    uid = 0
    for k, v in runs3:
        for kind, a, b, hl in _elements(v):
            base = hl_color if (hl and hl_color) else color[chain_entity[k]]
            if kind == "H":
                win = []
                for i in range(a, b + 1):
                    lo, hi = max(a, i - 1), min(b, i + 2)
                    n = hi - lo + 1
                    win.append(tuple(sum(v[j]["q"][c] for j in range(lo, hi + 1)) / n for c in range(3)))
                for s0, s1 in _split_straight(win, 2.2):
                    p0, p1 = win[s0], win[s1]
                    ln = math.dist(p0, p1) or 1.0
                    ext = [0.9 * (p1[c] - p0[c]) / ln for c in range(3)]  # reach the helix ends
                    q0 = tuple(p0[c] - ext[c] for c in range(3))
                    q1 = tuple(p1[c] + ext[c] for c in range(3))
                    z = (q0[2] + q1[2]) / 2
                    uid += 1
                    parts.append((z + (ON_TOP if hl else 0), _cylinder(q0, q1, scale, tube / 2,
                                               tint(base, z, tuple((q0[c] + q1[c]) / 2 for c in range(3)), hl), uid, sx)))
            elif kind == "E":
                path = _spline([r["q"] for r in v[a:b + 1]], SUBDIV)
                z = sum(q[2] for q in path) / len(path)
                col = tint(base, z, path[len(path) // 2], hl)
                sp = [sx(q) for q in path]
                m = len(sp)
                head = min(m - 1, int(1.6 * SUBDIV))
                left, right = [], []
                for i, (px, py) in enumerate(sp):
                    ax, ay = sp[min(i + 1, m - 1)]
                    bx, by = sp[max(i - 1, 0)]
                    tx, ty = ax - bx, ay - by
                    norm = math.hypot(tx, ty) or 1
                    nx, ny = -ty / norm, tx / norm
                    if i < m - head:
                        hw = arrow_hw
                    else:
                        hw = arrow_hw * 1.75 * (m - 1 - i) / head if i < m - 1 else 0
                    if i == m - head:  # flare at the arrowhead base
                        left.append((px + nx * arrow_hw * 1.75, py + ny * arrow_hw * 1.75))
                        right.append((px - nx * arrow_hw * 1.75, py - ny * arrow_hw * 1.75))
                        continue
                    left.append((px + nx * hw, py + ny * hw))
                    right.append((px - nx * hw, py - ny * hw))
                parts.append((z + (ON_TOP if hl else 0), f'<polygon points="{_points(left + right[::-1])}" fill="{col}" '
                                 f'stroke="{_shade(col, 0.3)}" stroke-width="{edge}" stroke-linejoin="round"/>'))
            else:
                lo, hi = max(0, a - 1), min(len(v) - 1, b + 1)
                path = _spline([r["q"] for r in v[lo:hi + 1]], SUBDIV)
                width = nuc_w if kind == "N" else coil_w
                piece = 6 if kind == "N" else 7
                i = 0
                while i < len(path) - 1:
                    seg = path[i:i + piece]
                    z = sum(q[2] for q in seg) / len(seg)
                    col = tint(_shade(base, 0.12), z, seg[len(seg) // 2], hl)
                    parts.append((z + (ON_TOP if hl else 0), f'<polyline points="{_points([sx(q) for q in seg])}" '
                                     f'stroke="{col}" stroke-width="{width:.1f}" fill="none" '
                                     f'stroke-linecap="round" stroke-linejoin="round"/>'))
                    i += piece - 1
    return parts


def _ligand_colors(lig_atoms: list[dict], palette: list[str]) -> dict[str, str]:
    comps: list[str] = []
    for a in lig_atoms:
        if a["inst"][2] not in comps:
            comps.append(a["inst"][2])
    return {c: palette[i % len(palette)] for i, c in enumerate(comps)}


HIGHLIGHT = "#C8442A"   # the accent colour: the stretch a figure note points at


TITLE_PX, SUB_PX = 19, 15.5   # leader-line labels: the name and, under it, what it is (in figure units)


def _text_width(text: str, size: float, bold: bool = False) -> float:
    """Rough width of a line of text: full-width characters are one em, Latin ones about 0.6 em."""
    return sum(size if ord(c) > 0x2E7F else size * (0.68 if bold else 0.6) for c in text)


def _clip(text: str, size: float, bold: bool, max_px: float) -> str:
    """The text, cut with an ellipsis when it would run wider than max_px (the full name is in the legend)."""
    if _text_width(text, size, bold) <= max_px:
        return text
    while text and _text_width(text + "…", size, bold) > max_px:
        text = text[:-1]
    return text.rstrip() + "…"


def _leaders(items: list[dict], x0: float, y0: float, w: float, h: float, bl: float, br: float,
             ink: str, bg: str) -> str:
    """Leader-line labels in the left and right bands of the figure box.

    items: {"ax", "ay" (where the line ends, in the figure), "side" ("L"/"R"), "title", "sub", "swatch" (colour or
    None), "color" (line and title colour)}. Labels on one side are spread so they never overlap, in the order of
    their anchors, so the lines do not cross each other."""
    parts: list[str] = []
    for side in ("L", "R"):
        group = sorted((i for i in items if i["side"] == side), key=lambda i: i["ay"])
        if not group:
            continue
        gap = 56
        lo, hi = y0 + 28, y0 + h - 46
        if (len(group) - 1) * gap > hi - lo:
            gap = max(40, (hi - lo) / max(1, len(group) - 1))
        pos = [min(max(i["ay"], lo), hi) for i in group]
        for k in range(1, len(pos)):
            pos[k] = max(pos[k], pos[k - 1] + gap)
        if pos[-1] > hi:
            pos = [p - (pos[-1] - hi) for p in pos]
            for k in range(len(pos) - 2, -1, -1):     # keep the order and the gaps after the shift
                pos[k] = min(pos[k], pos[k + 1] - gap)
        edge = x0 + bl - 12 if side == "L" else x0 + w - br + 12
        for it, ly in zip(group, pos):
            col = it.get("color") or ink
            sgn = 1 if side == "L" else -1
            anchor = "end" if side == "L" else "start"
            title, sub = it["title"], it.get("sub") or ""
            parts.append(f'<path d="M{edge:.1f},{ly - 5:.1f}h{sgn * 16:.1f}L{it["ax"]:.1f},{it["ay"]:.1f}" '
                         f'stroke="{bg}" stroke-width="4" fill="none" stroke-linejoin="round" opacity=".85"/>'
                         f'<path d="M{edge:.1f},{ly - 5:.1f}h{sgn * 16:.1f}L{it["ax"]:.1f},{it["ay"]:.1f}" '
                         f'stroke="{col}" stroke-width="1.4" fill="none" stroke-linejoin="round"/>'
                         f'<circle cx="{it["ax"]:.1f}" cy="{it["ay"]:.1f}" r="3.8" fill="{bg}" stroke="{col}" '
                         f'stroke-width="1.8"/>')
            tx = edge - sgn * 10                 # the text sits beside the line; a colour chip, if any, between the two
            sw = ""
            if it.get("swatch"):
                sx0 = tx - 12 if side == "L" else tx
                sw = (f'<rect x="{sx0:.1f}" y="{ly - 14:.1f}" width="13" height="13" rx="1.5" fill="{it["swatch"]}" '
                      f'stroke="{_shade(it["swatch"], 0.35)}" stroke-width="1"/>')
                tx -= sgn * 19
            parts.append(f'{sw}<text x="{tx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" font-size="{TITLE_PX}" '
                         f'font-weight="700" fill="{col}">{_esc(title)}</text>')
            if sub:
                parts.append(f'<text x="{tx:.1f}" y="{ly + 21:.1f}" text-anchor="{anchor}" font-size="{SUB_PX}" '
                             f'fill="{ink}" opacity=".78">{_esc(sub)}</text>')
    return "".join(parts)


def _esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(cif_gz: bytes, ligand_ids: set[str], x0: float, y0: float, w: float, h: float,
           gene_names: dict[str, str] | None = None, margin: float = 28,
           entity_colors: list[str] | None = None, ligand_colors: list[str] | None = None,
           max_points: int = MAX_TRACE_POINTS, bg: str = "#FFFFFF",
           notes: list[dict] | None = None, labels: dict | None = None, bands: tuple[float, float] = (0, 0),
           site_xyz: tuple | None = None, ink: str = "#1B2638", hl_color: str = HIGHLIGHT,
           probe: dict | None = None,
           ) -> tuple[str, list[tuple[str, str, str]], list[tuple[str, str]]]:
    """SVG fragment for the box (x0, y0, w, h), a legend [(colour, label, description)] per polymer
    entity, and [(colour, component id, [entity ids], [[asym id, residue number] per copy])] for the ligands drawn as sticks. `max_points` thins very large
    models (every n-th residue is kept) — the list thumbnails use a small value. `bg` is the colour the
    depth cue fades into (the page ground: the figure itself has a transparent background).

    Shading imitates ChimeraX's `lighting soft`: no key light, ambient occlusion (here: burial, see _Burial)
    and a depth cue, so volume comes from crowding and distance rather than from a highlight.

    Labels are UniProt gene names (gene_names: accession -> gene) when the entity maps to one,
    otherwise the entity description.

    Annotation (all optional). `notes`: [{"entity": index in legend order, "range": [first, last] in the paper's
    residue numbering, "title", "sub"}] draws that stretch in hl_color with a leader-line label. `labels`:
    {"entities": [(title, sub) or None per entity in legend order], "ligands": {component id: (title, sub)}}
    puts a leader-line label on each. `bands` = (left, right) widths kept free in the box for the labels.
    `site_xyz` circles the ligand copy nearest to that point (the zoomed site); its position in the figure
    comes back in probe["site"].
    """
    palette = entity_colors or PALETTE
    m = _load(cif_gz, ligand_ids, gene_names)
    pts = [r["xyz"] for c in m["chains"].values() for r in c]
    if not pts:
        return "", [], []
    runs = _runs(m["chains"], len(pts), max_points)
    step = max(1, math.ceil(len(pts) / max_points))
    center, ev = _basis(pts[::step])
    annotated = bool(labels or notes or site_xyz)
    pad = max(bands) if annotated else 0                    # widest band a label may need (a first, symmetric layout)
    # Wider than tall: put the longest axis horizontally if the box is wide, vertically otherwise.
    proj = _projector(center, ev, h > w - 2 * pad)

    entity_order = []
    for k, _ in runs:
        if m["chain_entity"][k] not in entity_order:
            entity_order.append(m["chain_entity"][k])
    color = {e: _cycled(palette, i) for i, e in enumerate(entity_order)}

    runs3 = [(k, [dict(r, q=proj(r["xyz"])) for r in v]) for k, v in runs]
    for note in notes or []:     # flag the residues each note points at
        idx = note.get("entity", 0)
        if idx >= len(entity_order):
            continue
        a, b = note["range"]
        for k, v in runs3:
            if m["chain_entity"][k] == entity_order[idx]:
                for r in v:
                    if r["auth"] is not None and a <= r["auth"] <= b:
                        r["hl"] = True
    lig_atoms = m["lig_atoms"]
    ligq = [proj(a["xyz"]) for a in lig_atoms]
    allq = [r["q"] for _, v in runs3 for r in v]
    xs, ys, zs = [q[0] for q in allq + ligq], [q[1] for q in allq + ligq], [q[2] for q in allq]
    span_x, span_y = max(xs) - min(xs) or 1, max(ys) - min(ys) or 1
    zmin, zmax = min(zs), max(zs)

    def layout(bl: float, br: float):
        bw = w - bl - br
        sc = min((bw - 2 * margin) / span_x, (h - 2 * margin) / span_y)
        ox_ = x0 + bl + bw / 2 - (min(xs) + max(xs)) / 2 * sc
        oy_ = y0 + h / 2 + (min(ys) + max(ys)) / 2 * sc
        return sc, ox_, oy_

    by_inst: dict[tuple, list[int]] = defaultdict(list)
    for i, a in enumerate(lig_atoms):
        by_inst[a["inst"]].append(i)
    ccol = _ligand_colors(lig_atoms, ligand_colors or LIGAND_PALETTE)
    for a in lig_atoms:
        a["ccol"] = ccol[a["inst"][2]]

    def items_for(sx, cx, sides: dict | None) -> list[dict]:
        """Leader-line labels with their anchors. sides: key -> "L"/"R" from an earlier layout, or None (by position)."""
        items: list[dict] = []

        def put(key, ax, ay, title, sub, swatch, col):
            side = (sides or {}).get(key) or ("L" if ax < cx else "R")
            items.append({"key": key, "ax": ax, "ay": ay, "side": side, "title": _clip(title, TITLE_PX, True, 176),
                          "sub": _clip(sub, SUB_PX, False, 176), "swatch": swatch, "color": col})

        for idx, lab in enumerate((labels or {}).get("entities") or []):
            if not lab or idx >= len(entity_order):
                continue
            ent = entity_order[idx]
            pp = [(sx(r["q"]), r["q"][2]) for k, v in runs3 if m["chain_entity"][k] == ent for r in v]
            if not pp:
                continue
            side = (sides or {}).get(("e", idx)) or ("L" if sum(p_[0][0] for p_ in pp) / len(pp) < cx else "R")
            lo_x, hi_x = min(p_[0][0] for p_ in pp), max(p_[0][0] for p_ in pp)
            cut = lo_x + 0.4 * (hi_x - lo_x) if side == "L" else hi_x - 0.4 * (hi_x - lo_x)
            near = [p_ for p_ in pp if (p_[0][0] <= cut if side == "L" else p_[0][0] >= cut)] or pp
            (ax, ay), _ = max(near, key=lambda p_: p_[1])
            put(("e", idx), ax, ay, lab[0], lab[1] if len(lab) > 1 else "", color[ent], ink)
            items[-1]["side"] = side

        def inst_center(inst):
            q = [sx(ligq[i]) for i in by_inst[inst]]
            return sum(p_[0] for p_ in q) / len(q), sum(p_[1] for p_ in q) / len(q)

        for comp, lab in ((labels or {}).get("ligands") or {}).items():
            insts = [k for k in by_inst if k[2] == comp]
            if not insts:
                continue
            if site_xyz is not None:
                inst = min(insts, key=lambda k: min(math.dist(lig_atoms[i]["xyz"], site_xyz) for i in by_inst[k]))
            else:
                inst = max(insts, key=lambda k: sum(ligq[i][2] for i in by_inst[k]) / len(by_inst[k]))
            ax, ay = inst_center(inst)
            put(("l", comp), ax, ay, lab[0], lab[1] if len(lab) > 1 else "", ccol.get(comp), ink)
        for j, note in enumerate(notes or []):
            idx = note.get("entity", 0)
            if idx >= len(entity_order):
                continue
            hit = [(sx(r["q"]), r["q"][2]) for k, v in runs3 if m["chain_entity"][k] == entity_order[idx]
                   for r in v if r.get("hl")]
            if hit:
                (ax, ay), _ = max(hit, key=lambda p_: p_[1])
                put(("n", j), ax, ay, note["title"], note.get("sub", ""), None, hl_color)
        return items

    bl = br = pad
    if annotated and pad:
        # first layout (equal bands) decides which side each label goes to; then each side gets only the width its
        # own labels need, and the structure takes the rest
        sc, ox, oy = layout(pad, pad)
        first = items_for(lambda q: (ox + q[0] * sc, oy - q[1] * sc), x0 + w / 2, None)
        need = {"L": 0.0, "R": 0.0}
        for it in first:
            need[it["side"]] = max(need[it["side"]], _text_width(it["title"], TITLE_PX, True) + 42,
                                   _text_width(it["sub"], SUB_PX) + 42)
        bl, br = min(pad, need["L"]), min(pad, need["R"])
        sides = {it["key"]: it["side"] for it in first}
    else:
        sides = None
    scale, ox, oy = layout(bl, br)

    def sx(q):
        return ox + q[0] * scale, oy - q[1] * scale

    burial = _Burial(allq[::max(1, len(allq) // 6000)])

    def tint(col, z, p, hl=False):
        depth = (z - zmin) / ((zmax - zmin) or 1)  # 0 = far, 1 = near
        if hl:                                       # the stretch a note points at stays strong
            return _toward(_shade(col, 0.10 * burial(p)), bg, 0.22 * (1 - depth))
        col = _shade(col, 0.36 * burial(p))          # crowded = dark (ambient occlusion)
        return _toward(col, bg, 0.66 * (1 - depth) ** 1.15)  # depth cue

    parts = _cartoon(runs3, m["chain_entity"], color, sx, scale, tint, hl_color)
    parts.sort(key=lambda p: p[0])
    width = max(1.8, min(7.0, 0.5 * scale))
    out = "".join(s for _, s in parts) + _sticks(lig_atoms, ligq, sx, width)
    legend = [(color[e], m["entities"].get(e, f"entity {e}"), m["descriptions"].get(e, ""), e) for e in entity_order]
    lig_entities: dict[str, set] = defaultdict(set)
    lig_sites: dict[str, list] = defaultdict(list)  # every copy of the ligand: [label_asym_id, auth_seq_id]
    for a in lig_atoms:
        lig_entities[a["inst"][2]].add(a["entity"])
        site = [a["inst"][0], a["inst"][1]]
        if site not in lig_sites[a["inst"][2]]:
            lig_sites[a["inst"][2]].append(site)

    if annotated:
        if site_xyz is not None and lig_atoms:
            near_inst = min(by_inst, key=lambda k: min(math.dist(lig_atoms[i]["xyz"], site_xyz) for i in by_inst[k]))
            q = [sx(ligq[i]) for i in by_inst[near_inst]]
            px, py = sum(p_[0] for p_ in q) / len(q), sum(p_[1] for p_ in q) / len(q)
            out += (f'<circle cx="{px:.1f}" cy="{py:.1f}" r="30" fill="none" stroke="{ink}" stroke-width="1.8" '
                    f'stroke-dasharray="5 4"/>')
            if probe is not None:
                probe["site"] = (px, py, 30.0)
        items = items_for(sx, x0 + w / 2, sides)
        if items:
            out += _leaders(items, x0, y0, w, h, bl, br, ink, bg)
    return out, legend, list((c, k, sorted(lig_entities[k]), lig_sites[k][:MAX_SITES]) for k, c in ccol.items())


def _kabsch(P: list[tuple], Q: list[tuple]):
    """Rotation matrix R and the two centroids (cp, cq) such that R·(p − cp) + cq best fits q (least squares)."""
    n = len(P)
    cp = tuple(sum(p[i] for p in P) / n for i in range(3))
    cq = tuple(sum(q[i] for q in Q) / n for i in range(3))
    H = [[sum((P[k][i] - cp[i]) * (Q[k][j] - cq[j]) for k in range(n)) for j in range(3)] for i in range(3)]
    HtH = [[sum(H[k][i] * H[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    V = _jacobi_eigen(HtH)                                   # right singular vectors, as columns
    cols = []
    for c in range(3):
        v = [V[r][c] for r in range(3)]
        hv = [sum(H[i][k] * v[k] for k in range(3)) for i in range(3)]   # H v = s u
        norm = math.sqrt(sum(x * x for x in hv))
        cols.append((v, [x / norm for x in hv] if norm > 1e-9 else None))
    if cols[2][1] is None or cols[1][1] is None:             # degenerate (planar) sets
        u0, u1 = cols[0][1], cols[1][1]
        cols[2] = (cols[2][0], [u0[1] * u1[2] - u0[2] * u1[1], u0[2] * u1[0] - u0[0] * u1[2],
                                u0[0] * u1[1] - u0[1] * u1[0]])
    Vm = [[cols[c][0][r] for c in range(3)] for r in range(3)]
    Um = [[cols[c][1][r] for c in range(3)] for r in range(3)]
    # R^T = V D U^T with D = diag(1, 1, ±1) so that det R = +1 (no reflection)
    def rt(d):
        return [[sum(Vm[i][k] * (d if k == 2 else 1.0) * Um[j][k] for k in range(3)) for j in range(3)]
                for i in range(3)]
    M = rt(1.0)
    if _det3(M) < 0:
        M = rt(-1.0)
    R = [[M[j][i] for j in range(3)] for i in range(3)]     # R = M^T (p row-vector convention → column)
    return R, cp, cq


def _apply(R, cp, cq, p):
    d = (p[0] - cp[0], p[1] - cp[1], p[2] - cp[2])
    return tuple(sum(R[i][k] * d[k] for k in range(3)) + cq[i] for i in range(3))


def compare(panels: list[dict], x0: float, y0: float, w: float, h: float, align: int, focus: dict,
            mark: dict | None = None, gene_names: dict[str, str] | None = None, entity_colors: list[str] | None = None,
            ligand_colors: list[str] | None = None, bg: str = "#FFFFFF", ink: str = "#1B2638",
            hl_color: str = HIGHLIGHT, gap: float = 36, names: list | None = None) -> tuple[str, dict]:
    """Two (or more) structures of the same assembly side by side, superposed on one polymer entity, with the
    same view, the stretch `focus` in the accent colour and, when `mark` is given, an arrow from where one
    residue sits in the first panel to where it sits in the next.

    panels: [{"cif": gz bytes, "ligands": set of component ids, "title", "sub"}].
    align: index (legend order) of the polymer entity used to superpose the structures.
    focus: {"entity": index, "range": [first, last] (the paper's residue numbering), "title"}.
    mark: {"entity": index, "residue": number, "text"}: the arrow's residue and its label.
    names: a short name per polymer entity (legend order, None to skip), written on the first panel.
    The view is chosen so that the arrow is vertical (the first panel's residue on top): the movement shows.
    Returns (svg fragment, {"distance": Å between the marked residue's Cα in panels 1 and 2 or None}); the
    fragment is "" when the structures cannot be superposed."""
    palette = entity_colors or PALETTE
    ms = [_load(pn["cif"], pn.get("ligands") or set(), gene_names) for pn in panels]
    orders = []
    for m in ms:
        order = []
        for k, v in _runs(m["chains"], sum(len(c) for c in m["chains"].values())):
            if m["chain_entity"][k] not in order:
                order.append(m["chain_entity"][k])
        orders.append(order)

    def entity_ca(i: int, idx: int) -> dict:
        """(auth chain, auth number) -> xyz of the alpha carbons of one polymer entity."""
        out = {}
        if idx >= len(orders[i]):
            return out
        for k, v in ms[i]["chains"].items():
            if ms[i]["chain_entity"][k] == orders[i][idx]:
                for r in v:
                    if r["at"] == "CA" and r["auth"] is not None:
                        out[(r["achain"], r["auth"])] = r["xyz"]
        return out

    ref = entity_ca(0, align)
    xf = [None]
    for i in range(1, len(panels)):
        cur = entity_ca(i, align)
        keys = sorted(set(ref) & set(cur))
        if len(keys) < 12:
            return "", {"distance": None}
        xf.append(_kabsch([cur[k] for k in keys], [ref[k] for k in keys]))

    def to_ref(i, p):
        return p if xf[i] is None else _apply(*xf[i], p)

    # the marked residue, one chain copy: its place in each panel (in the first panel's frame)
    marks, mark_achain = None, None
    if mark:
        idx = mark.get("entity", 0)
        pos = []
        for i in range(len(panels)):
            ca = entity_ca(i, idx)
            pos.append({k: to_ref(i, xyz) for k, xyz in ca.items() if k[1] == mark["residue"]})
        shared = sorted(set(pos[0]) & set(pos[1])) if len(pos) > 1 else []
        if shared:
            marks = [pos[i][shared[0]] for i in range(len(panels))]
            mark_achain = shared[0][0]

    all_pts = [to_ref(i, r["xyz"]) for i, m in enumerate(ms) for c in m["chains"].values() for r in c]
    center = tuple(sum(p[c] for p in all_pts) / len(all_pts) for c in range(3))
    if marks:
        d = tuple(marks[1][c] - marks[0][c] for c in range(3))
        dist = math.sqrt(sum(x * x for x in d)) or 1.0
        yv = tuple(-x / dist for x in d)                      # panel 1's residue above panel 2's
    else:
        yv = None
    if yv:
        proj_pts = []
        for p in all_pts[::max(1, len(all_pts) // 1500)]:
            dd = tuple(p[c] - center[c] for c in range(3))
            t = sum(dd[c] * yv[c] for c in range(3))
            proj_pts.append(tuple(dd[c] - t * yv[c] for c in range(3)))
        cov = [[sum(p[i] * p[j] for p in proj_pts) for j in range(3)] for i in range(3)]
        xv = tuple(_jacobi_eigen(cov)[r][0] for r in range(3))
        zv = (xv[1] * yv[2] - xv[2] * yv[1], xv[2] * yv[0] - xv[0] * yv[2], xv[0] * yv[1] - xv[1] * yv[0])
        ev = [[xv[r], yv[r], zv[r]] for r in range(3)]
        proj = _projector(center, ev, False)
    else:
        c0, ev = _basis(all_pts[::max(1, len(all_pts) // 1500)])
        proj = _projector(c0, ev, h > (w - gap) / len(panels))

    pw = (w - gap * (len(panels) - 1)) / len(panels)
    built = []
    for i, m in enumerate(ms):
        runs = _runs(m["chains"], sum(len(c) for c in m["chains"].values()))
        runs3 = [(k, [dict(r, q=proj(to_ref(i, r["xyz"]))) for r in v]) for k, v in runs]
        fidx = focus.get("entity", 0)
        a, b = focus["range"]
        for k, v in runs3:
            if fidx < len(orders[i]) and m["chain_entity"][k] == orders[i][fidx]:
                for r in v:
                    if r["auth"] is not None and a <= r["auth"] <= b:
                        r["hl"] = True
        lig = m["lig_atoms"]
        ligq = [proj(to_ref(i, a_["xyz"])) for a_ in lig]
        built.append((m, runs3, lig, ligq))
    allq = [r["q"] for _, runs3, _, _ in built for _, v in runs3 for r in v] + [q for _, _, _, lq in built for q in lq]
    xs, ys, zs = [q[0] for q in allq], [q[1] for q in allq], [q[2] for q in allq]
    margin_top = 64                                          # room for each panel's title
    scale = min((pw - 40) / ((max(xs) - min(xs)) or 1), (h - margin_top - 30) / ((max(ys) - min(ys)) or 1))
    cy = y0 + margin_top + (h - margin_top - 30) / 2
    zmin, zmax = min(zs), max(zs)
    burial = _Burial([q for q in allq[::max(1, len(allq) // 6000)]])
    out, info_dist = [], None

    for i, (m, runs3, lig, ligq) in enumerate(built):
        px0 = x0 + i * (pw + gap)
        ox = px0 + pw / 2 - (min(xs) + max(xs)) / 2 * scale
        oy = cy + (min(ys) + max(ys)) / 2 * scale

        def sx(q, ox=ox, oy=oy):
            return ox + q[0] * scale, oy - q[1] * scale

        def tint(col, z, p, hl=False):
            depth = (z - zmin) / ((zmax - zmin) or 1)
            if hl:
                return _toward(_shade(col, 0.02 * burial(p)), bg, 0.10 * (1 - depth))
            col = _shade(col, 0.30 * burial(p))
            return _toward(col, bg, 0.50 + 0.38 * (1 - depth) ** 1.1)   # everything but the focus stays quiet

        color = {e: _cycled(palette, j) for j, e in enumerate(orders[i])}
        parts = _cartoon(runs3, m["chain_entity"], color, sx, scale, tint, hl_color)
        parts.sort(key=lambda p: p[0])
        ccol = _ligand_colors(lig, ligand_colors or LIGAND_PALETTE)
        for a_ in lig:
            a_["ccol"] = ccol[a_["inst"][2]]
        body = "".join(s_ for _, s_ in parts) + _sticks(lig, ligq, sx, max(1.8, min(6.0, 0.5 * scale)))
        extra = ""
        hit = [(sx(r["q"]), r["q"][2]) for _, v in runs3 for r in v if r.get("hl")]
        if hit:
            (ax, ay), _ = max(hit, key=lambda p: p[1])
            extra += (f'<text x="{ax + 16:.1f}" y="{ay + 5:.1f}" font-size="20" font-weight="700" fill="{hl_color}" '
                      f'stroke="{bg}" stroke-width="5" paint-order="stroke" stroke-linejoin="round">'
                      f'{_esc(focus["title"])}</text>')
        if i == 0:
            for idx, nm in enumerate(names or []):
                if not nm or idx >= len(orders[0]):
                    continue
                pp = [(sx(r["q"]), r["q"][2]) for k, v in runs3 if m["chain_entity"][k] == orders[0][idx] for r in v]
                if not pp:
                    continue
                cxm = sum(p_[0][0] for p_ in pp) / len(pp)
                cym = sum(p_[0][1] for p_ in pp) / len(pp)
                near = sorted(pp, key=lambda p_: math.dist(p_[0], (cxm, cym)))[:max(3, len(pp) // 8)]
                (ax, ay), _ = max(near, key=lambda p_: p_[1])
                extra += (f'<text x="{ax:.1f}" y="{ay:.1f}" text-anchor="middle" font-size="18" font-weight="700" fill="{ink}" '
                          f'stroke="{bg}" stroke-width="5" paint-order="stroke" stroke-linejoin="round">{_esc(nm)}</text>')
        if marks and i > 0:
            # where the marked residue was in the first panel (ghost), and the arrow to where it is now
            ghost = sx(proj(marks[0]))
            now = sx(proj(marks[i]))
            ghost_pts = [sx(r["q"]) for k, v in built[0][1] for r in v if r.get("hl") and r["achain"] == mark_achain]
            if len(ghost_pts) > 1:
                extra += (f'<polyline points="{_points(ghost_pts)}" fill="none" stroke="{hl_color}" stroke-width="3.5" '
                          f'stroke-dasharray="7 6" stroke-linecap="round" stroke-linejoin="round" opacity=".6"/>')
            dx, dy = now[0] - ghost[0], now[1] - ghost[1]
            ln = math.hypot(dx, dy) or 1
            ux, uy = dx / ln, dy / ln
            tip = (now[0] - ux * 7, now[1] - uy * 7)
            extra += (f'<path d="M{ghost[0]:.1f},{ghost[1]:.1f}L{tip[0]:.1f},{tip[1]:.1f}" stroke="{ink}" stroke-width="2.2" '
                      f'fill="none"/>'
                      f'<path d="M{now[0]:.1f},{now[1]:.1f}L{now[0] - ux * 14 - uy * 6:.1f},{now[1] - uy * 14 + ux * 6:.1f}'
                      f'L{now[0] - ux * 14 + uy * 6:.1f},{now[1] - uy * 14 - ux * 6:.1f}Z" fill="{ink}"/>'
                      f'<circle cx="{ghost[0]:.1f}" cy="{ghost[1]:.1f}" r="4.5" fill="{bg}" stroke="{hl_color}" stroke-width="2"/>')
            if mark.get("text"):
                extra += (f'<text x="{(ghost[0] + now[0]) / 2 + 12:.1f}" y="{(ghost[1] + now[1]) / 2 + 5:.1f}" font-size="20" '
                          f'font-weight="700" fill="{ink}" stroke="{bg}" stroke-width="5" paint-order="stroke" '
                          f'stroke-linejoin="round">{_esc(mark["text"])}</text>')
        title = panels[i].get("title") or ""
        sub = panels[i].get("sub") or ""
        head = (f'<text x="{px0 + 4:.1f}" y="{y0 + 26:.1f}" font-size="23" font-weight="700" fill="{ink}">{_esc(title)}</text>'
                + (f'<text x="{px0 + 4:.1f}" y="{y0 + 48:.1f}" font-size="15.5" fill="{ink}" opacity=".75">{_esc(sub)}</text>'
                   if sub else ""))
        out.append(f'<g>{body}{extra}</g>{head}')
        if i > 0:
            vx = px0 - gap / 2
            out.insert(0, f'<path d="M{vx:.1f},{y0 + 8:.1f}V{y0 + h - 8:.1f}" stroke="{ink}" stroke-width="1" opacity=".18"/>')
    if marks:
        info_dist = round(math.dist(marks[0], marks[1]), 1)
    return "".join(out), {"distance": info_dist}


class _Grid:
    """Neighbour search over atoms in cubic cells."""

    def __init__(self, atoms: list[dict], cell: float = 5.0):
        self.cell, self.atoms = cell, atoms
        self.cells: dict[tuple, list[int]] = defaultdict(list)
        for i, a in enumerate(atoms):
            self.cells[self._key(a["xyz"])].append(i)

    def _key(self, p):
        return tuple(int(math.floor(c / self.cell)) for c in p)

    def near(self, p, r: float):
        kx, ky, kz = self._key(p)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for i in self.cells.get((kx + dx, ky + dy, kz + dz), ()):
                        d = math.dist(p, self.atoms[i]["xyz"])
                        if d <= r:
                            yield i, d


def render_site(cif_gz: bytes, ligand_ids: set[str], prefer: list[str], w: float, h: float,
                gene_names: dict[str, str] | None = None, entity_colors: list[str] | None = None,
                ligand_colors: list[str] | None = None, ink: str = "#1B2638", bg: str = "#FFFFFF",
                label_size: float = 15) -> tuple[str, dict | None]:
    """Close-up of the binding site of one ligand: the first of `prefer` (component IDs) present in the
    model, otherwise the largest ligand. Ligands under 5 heavy atoms (ions, BeF3) get no close-up.

    Draws the residues within CONTACT Å as sticks with labels, N/O pairs within POLAR Å as dashes,
    neighbouring ligands, and the surrounding cartoon faded and clipped in front.
    Returns (svg fragment for a w x h canvas, {"ligand", "residues", "polar", "neighbours"}) or ("", None).
    """
    palette = entity_colors or PALETTE
    m = _load(cif_gz, ligand_ids, gene_names, polymer_atoms=True)
    lig_atoms, poly = m["lig_atoms"], m["poly"]
    ccol = _ligand_colors(lig_atoms, ligand_colors or LIGAND_PALETTE)
    for a in lig_atoms:
        a["ccol"] = ccol[a["inst"][2]]
    insts: dict[tuple, list[int]] = defaultdict(list)
    for i, a in enumerate(lig_atoms):
        insts[a["inst"]].append(i)
    size: dict[str, int] = {}
    for k, idx in insts.items():
        size[k[2]] = max(size.get(k[2], 0), len(idx))
    order = [c for c in prefer if size.get(c, 0) >= 5]
    order += sorted((c for c in size if c not in order and size[c] >= 5), key=lambda c: -size[c])
    if not order or not poly:
        return "", None
    focus = order[0]
    candidates = [k for k in insts if k[2] == focus]
    grid = _Grid(poly)

    def contacts(idx):
        return {j for i in idx for j, _ in grid.near(lig_atoms[i]["xyz"], CONTACT)}

    best = max(candidates, key=lambda k: len(contacts(insts[k])))
    fidx = insts[best]
    if len(fidx) < 4:
        return "", None
    near_atoms = contacts(fidx)
    if not near_atoms:
        return "", None
    pocket = {poly[j]["res"] for j in near_atoms}
    polar = []
    for i in fidx:
        if lig_atoms[i]["el"] in ("N", "O"):
            for j, d in grid.near(lig_atoms[i]["xyz"], POLAR):
                if poly[j]["el"] in ("N", "O"):
                    polar.append((i, j))
    polar_atoms = {j for _, j in polar}

    # Ligand molecules touching the focus one (cofactors, a stacked second ligand) come along.
    lig_grid = _Grid(lig_atoms)
    keep_insts = {best}
    for i in fidx:
        for j, _ in lig_grid.near(lig_atoms[i]["xyz"], 4.5):
            keep_insts.add(lig_atoms[j]["inst"])
    lig_keep = [i for k in keep_insts for i in insts[k]]

    # Side chains (from CA) of pocket residues; backbone atoms only when they make a polar contact.
    side = []
    for j, a in enumerate(poly):
        if a["res"] not in pocket:
            continue
        if a["name"] in BACKBONE and j not in polar_atoms:
            if not (a["name"] == "C" and any(poly[k]["res"] == a["res"] and poly[k]["name"] == "O"
                                             for k in polar_atoms)):
                continue
        side.append(j)

    focus_xyz = [lig_atoms[i]["xyz"] for i in fidx]
    center, ev = _basis(focus_xyz + [poly[j]["xyz"] for j in side])
    proj = _projector(center, ev, h > w)
    fq = [proj(lig_atoms[i]["xyz"]) for i in lig_keep] + [proj(poly[j]["xyz"]) for j in side]
    xs, ys = [q[0] for q in fq], [q[1] for q in fq]
    mg = 46
    scale = min((w - 2 * mg) / ((max(xs) - min(xs)) or 1), (h - 2 * mg) / ((max(ys) - min(ys)) or 1), 42.0)
    ox = w / 2 - (min(xs) + max(xs)) / 2 * scale
    oy = h / 2 + (min(ys) + max(ys)) / 2 * scale

    def sx(q):
        return ox + q[0] * scale, oy - q[1] * scale

    # Faded cartoon around the site, with everything well in front of the ligand cut away.
    zc = sum(proj(p)[2] for p in focus_xyz) / len(focus_xyz)
    radius = max(w, h) / scale * 0.75
    runs = []
    for k, v in _runs(m["chains"], 0):
        cur = []
        for r in v:
            q = proj(r["xyz"])
            if math.dist(r["xyz"], center) <= radius and q[2] <= zc + 6.0:
                cur.append(dict(r, q=q))
            else:
                if len(cur) > 1:
                    runs.append((k, cur))
                cur = []
        if len(cur) > 1:
            runs.append((k, cur))
    entity_order = []
    for k in m["chains"]:
        if m["chain_entity"][k] not in entity_order:
            entity_order.append(m["chain_entity"][k])
    color = {e: _cycled(palette, i) for i, e in enumerate(entity_order)}
    zs = [r["q"][2] for _, v in runs for r in v] or [zc]
    zmin, zmax = min(zs), max(zs)

    def tint(col, z, p, hl=False):
        depth = (z - zmin) / ((zmax - zmin) or 1)
        return _toward(col, bg, 0.74 - 0.28 * depth)

    parts = _cartoon(runs, m["chain_entity"], color, sx, scale, tint)
    parts.sort(key=lambda p: p[0])

    # Sticks: pocket residues (carbon in the chain's colour), then the ligands on top.
    res_atoms = []
    for j in side:
        a = poly[j]
        ent = a["entity"]
        res_atoms.append({"xyz": a["xyz"], "el": a["el"], "inst": (a["res"], a["comp"], ""),
                          "ccol": _shade(color.get(ent, palette[0]), 0.08)})
    rq = [proj(a["xyz"]) for a in res_atoms]
    lk = [lig_atoms[i] for i in lig_keep]
    lq = [proj(a["xyz"]) for a in lk]
    res_w = max(2.2, min(6.5, 0.24 * scale))
    lig_w = max(3.0, min(10.0, 0.36 * scale))

    dashes = []
    for i, j in polar:
        (ax, ay), (bx, by) = sx(proj(lig_atoms[i]["xyz"])), sx(proj(poly[j]["xyz"]))
        dashes.append(f"M{ax:.1f},{ay:.1f}L{bx:.1f},{by:.1f}")

    # Labels: one-letter code + author numbering, at the tip of each side chain.
    by_res: dict[tuple, list[int]] = defaultdict(list)
    for j in side:
        by_res[poly[j]["res"]].append(j)
    mind = {}
    for j in near_atoms:
        r = poly[j]["res"]
        d = min(math.dist(poly[j]["xyz"], p) for p in focus_xyz)
        mind[r] = min(d, mind.get(r, 99))
    first = {}
    for a in poly:
        if a["res"] in pocket and a["res"] not in first:
            first[a["res"]] = a
    names = {r: (AA1.get(a["comp"], a["comp"]) + a["auth"], a["chain"]) for r, a in first.items()}
    dup = defaultdict(int)
    for n, _ in names.values():
        dup[n] += 1
    lcx, lcy = sx(proj(center))
    labels, placed = [], []
    for r in sorted(pocket, key=lambda r: mind.get(r, 99))[:14]:
        idx = by_res.get(r) or []
        ca = next((poly[j] for j in idx if poly[j]["name"] == "CA"), None)
        pts2 = [sx(proj(poly[j]["xyz"])) for j in idx] or [(lcx, lcy)]
        if ca:
            cxy = sx(proj(ca["xyz"]))
            tip = max(pts2, key=lambda p: math.dist(p, cxy))
        else:
            tip = pts2[0]
        dx, dy = tip[0] - lcx, tip[1] - lcy
        n = math.hypot(dx, dy) or 1
        tx, ty = tip[0] + dx / n * 16, tip[1] + dy / n * 16 + 5
        for _ in range(4):  # nudge away from labels already placed
            if all(abs(tx - px) > 42 or abs(ty - py) > 18 for px, py in placed):
                break
            tx, ty = tx + dx / n * 16, ty + dy / n * 16
        tx = max(24, min(w - 24, tx))
        ty = max(18, min(h - 8, ty))
        placed.append((tx, ty))
        text, chain = names[r]
        if dup[text] > 1:
            text += f"({chain})"
        labels.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="middle">{text}</text>')

    body = (f'<defs><clipPath id="site-clip"><rect width="{w}" height="{h}"/></clipPath></defs>'
            f'<g clip-path="url(#site-clip)">{"".join(s for _, s in parts)}'
            f'{_sticks(res_atoms, rq, sx, res_w)}'
            f'<path d="{"".join(dashes)}" stroke="{ink}" stroke-width="2" stroke-dasharray="5 4" '
            f'fill="none" opacity="0.85"/>'
            f'{_sticks(lk, lq, sx, lig_w)}'
            f'<g font-size="{label_size}" font-weight="700" fill="{ink}" stroke="{bg}" stroke-width="4" '
            f'paint-order="stroke" stroke-linejoin="round">{"".join(labels)}</g></g>')
    resid = sorted(pocket, key=lambda r: (names[r][1], int(re.sub(r"\D", "", names[r][0]) or 0)))
    info = {"ligand": focus, "residues": [names[r][0] + (f"({names[r][1]})" if dup[names[r][0]] > 1 else "") for r in resid],
            "polar": len(polar), "neighbours": sorted({k[2] for k in keep_insts if k != best}),
            "center": [round(sum(p[c] for p in focus_xyz) / len(focus_xyz), 2) for c in range(3)]}
    return body, info


def _sticks(atoms: list[dict], q: list[tuple], sx, width: float) -> str:
    """Molecules as sticks: bonds inferred from interatomic distances within each molecule
    (atoms sharing "inst"), each half-bond coloured by its atom, carbon in the atom's "ccol"."""
    if not atoms:
        return ""
    edge = 1.4 if width > 3 else 1.0
    by_inst: dict[tuple, list[int]] = defaultdict(list)
    for i, a in enumerate(atoms):
        by_inst[a["inst"]].append(i)

    def atom_col(i):
        el = atoms[i]["el"]
        return atoms[i]["ccol"] if el == "C" else ELEMENT_COLORS.get(el, "#8A8F98")

    groups = []
    for inst, idx in by_inst.items():
        bonds = []
        for x, i in enumerate(idx):
            ri = COVALENT_RADIUS.get(atoms[i]["el"], 1.3)
            for j in idx[x + 1:]:
                rj = COVALENT_RADIUS.get(atoms[j]["el"], 1.3)
                if 0.4 < math.dist(atoms[i]["xyz"], atoms[j]["xyz"]) <= ri + rj + 0.45:
                    bonds.append((i, j))
        bonded = {k for b in bonds for k in b}
        z = sum(q[i][2] for i in idx) / len(idx)
        outline, fill = [], []
        for i, j in sorted(bonds, key=lambda b: q[b[0]][2] + q[b[1]][2]):
            (ax, ay), (bx, by) = sx(q[i]), sx(q[j])
            mx, my = (ax + bx) / 2, (ay + by) / 2
            outline.append(f"M{ax:.1f},{ay:.1f}L{bx:.1f},{by:.1f}")
            fill.append((atom_col(i), f"M{ax:.1f},{ay:.1f}L{mx:.1f},{my:.1f}"))
            fill.append((atom_col(j), f"M{mx:.1f},{my:.1f}L{bx:.1f},{by:.1f}"))
        dark = _shade(atoms[idx[0]]["ccol"], 0.55)
        svg = []
        if outline:
            svg.append(f'<path d="{"".join(outline)}" stroke="{dark}" stroke-width="{width + 2 * edge:.1f}"/>')
            by_col: dict[str, list[str]] = defaultdict(list)
            for col, d in fill:
                by_col[col].append(d)
            svg += [f'<path d="{"".join(ds)}" stroke="{col}" stroke-width="{width:.1f}"/>' for col, ds in by_col.items()]
        for i in idx:  # lone atoms (ions, or atoms the distance rule left unbonded)
            if i not in bonded:
                cx, cy = sx(q[i])
                svg.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{width * 1.1:.1f}" fill="{atom_col(i)}" '
                           f'stroke="{dark}" stroke-width="{edge}"/>')
        groups.append((z, "".join(svg)))
    groups.sort(key=lambda g: g[0])
    return ('<g fill="none" stroke-linecap="round" stroke-linejoin="round">' +
            "".join(s for _, s in groups) + "</g>")
