"""Render an issue: graphical abstracts (SVG) and the issue page for review (README.md).

    python3 -m pipeline.render 2026-10-07
"""

from __future__ import annotations

import html
import json
import math
import re
import sys
import unicodedata

from . import brand, impact, jats, pdb, pmcoa, structure_svg, uniprot
from .common import issue_dir, read_json
from .schema import CATEGORIES, FEATURE_MIN, LAB_NOTE_FIELDS, SUMMARY_FIELDS, SUMMARY_OPTIONAL

SITE_NAME = brand.SITE_NAME
FONT = brand.FONT
W, H = 1200, 560
INSET_W, INSET_H = 410, 400  # the binding site, zoomed, beside the overview
MAX_LABELS = 8  # leader-line labels on one figure (entities); ligands get their own few
M_W, M_OVERVIEW_H, M_INSET_H, M_COMPARE_H, M_BAND, M_FONT = 640, 640, 380, 460, 190, 1.3  # the phone version
THUMB_W, THUMB_H = 600, 400  # list thumbnail (3:2); same PDB structure, thinned to THUMB_POINTS residues
HERO_W, HERO_H = 1000, 780   # Today's Structure drawn large, without labels, for the front page's first screen
THUMB_POINTS = 900

METHOD_LABEL = {   # the METHOD column: short English names, as structural biologists write them
    "X-RAY DIFFRACTION": "X-ray",
    "ELECTRON MICROSCOPY": "Cryo-EM",
    "SOLUTION NMR": "Solution NMR",
    "SOLID-STATE NMR": "Solid-state NMR",
    "ELECTRON CRYSTALLOGRAPHY": "MicroED",
    "NEUTRON DIFFRACTION": "Neutron",
}
TOMOGRAPHY = "SUBTOMOGRAM AVERAGING"   # an EM entry made by cryo-electron tomography: shown as Cryo-ET
def not_covered(meta: dict, picked: set) -> list[dict]:
    """The candidates that may report a new structure but have no article, for the list at the foot of an issue.
    A preprint that slipped in before the collector dropped them (Research Square in PMC) is left out: the site covers
    peer-reviewed papers only."""
    return [x for x in meta["candidates"] if x["kind"] in KIND_JA and x["pmcid"] not in picked
            and not (x.get("doi") or "").lower().startswith(jats.PREPRINT_DOI_PREFIXES)]


KIND_JA = {
    "new_structure": "新しい構造",
    "new_structure_ids_unlisted": "新しい構造（ID は補足資料）",
    "new_map_only": "新しいマップ（EMDB）",
}


def method_name(m: str | None, em_reconstruction: str | None = None) -> str:
    """'X-ray', 'Cryo-EM', 'Cryo-ET' (subtomogram averaging), 'Solution NMR', ... for the PDB's method(s)."""
    names = []
    for x in (m or "").split(","):
        x = x.strip()
        if x:
            names.append("Cryo-ET" if x == "ELECTRON MICROSCOPY" and em_reconstruction == TOMOGRAPHY
                         else METHOD_LABEL.get(x, x))
    return ", ".join(names)


def structure_method(s: dict) -> str:
    return method_name(s.get("method"), s.get("em_reconstruction"))


def esc(s: str) -> str:
    return html.escape(s or "", quote=True)


# ---------- text layout for SVG (no automatic wrapping in SVG) ----------

def char_width(ch: str) -> float:
    return 1.0 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 0.56


def wrap(text: str, size: float, max_width: float, max_lines: int) -> list[str]:
    units = re.findall(r"[A-Za-z0-9.,:;/+\-–()'%Åα-ωΑ-Ω]+ ?|.", text)
    lines, cur, cur_w = [], "", 0.0
    for u in units:
        uw = sum(char_width(c) for c in u) * size
        if cur and cur_w + uw > max_width:
            # Keep closing punctuation on the previous line.
            if u[0] in "、。）」』，．・":
                cur += u
                u, uw = "", 0.0
            lines.append(cur.rstrip())
            cur, cur_w = u.lstrip(), uw
        else:
            cur += u
            cur_w += uw
    if cur.strip():
        lines.append(cur.rstrip())
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def text_block(lines, x, y, size, color, weight=400, line_height=1.35) -> str:
    out = []
    for i, ln in enumerate(lines):
        out.append(f'<text x="{x}" y="{y + i * size * line_height:.1f}" font-size="{size}" '
                   f'font-weight="{weight}" fill="{color}">{esc(ln)}</text>')
    return "".join(out)


# ---------- graphical abstract ----------

def is_impact(pick: dict) -> bool:
    return pick.get("section") == "impact"


def split_sections(picks: list) -> tuple[list, list]:
    """(new-structure articles, 注目の構造 articles), each in rank order."""
    return [p for p in picks if not is_impact(p[1])], [p for p in picks if is_impact(p[1])]


def load_impact(d) -> dict:
    """data/issues/<date>/impact.json (the writer's scores for 注目の構造), or an empty record."""
    path = d / "impact.json"
    return read_json(path) if path.exists() else {"screened": 0, "scored": []}


def pick_structure(cand: dict, pick: dict) -> dict | None:
    released = [s for s in cand["own_structures"] if s["status"] == "released"]
    if not released and is_impact(pick):  # 注目の構造: draw a structure the paper used
        released = list(cand.get("cited_structures", []))
        if not pick.get("ga_pdb_id"):
            return released[0] if released else None
    if pick.get("ga_pdb_id"):
        for s in released:
            if s["pdb_id"] == pick["ga_pdb_id"].upper():
                return s
    with_res = [s for s in released if s.get("resolution")]
    if with_res:
        return min(with_res, key=lambda s: s["resolution"])
    return released[0] if released else None


def method_line(cand: dict) -> str:
    rel = [s for s in cand["own_structures"] if s["status"] == "released"]
    if not rel:
        return "PDB 公開待ち" if cand["own_structures"] else "—"
    methods = sorted({structure_method(s) for s in rel})
    res = [s["resolution"] for s in rel if s.get("resolution")]
    res_txt = (f"{min(res):.2f} Å" if len(res) == 1 or min(res) == max(res)
               else f"{min(res):.2f}–{max(res):.2f} Å") if res else ""
    n = f"（{len(rel)} 構造）" if len(rel) > 1 else ""
    return " / ".join(methods) + (f"・{res_txt}" if res_txt else "") + n


def ligand_line(cand: dict) -> str:
    seen = {}
    for s in cand["own_structures"]:
        for l in s.get("ligands") or []:
            seen.setdefault(l["id"], l.get("name") or l["id"])
    if not seen:
        return "なし（または未公開）"
    names = [f"{k}" for k in list(seen)[:4]]
    return ", ".join(names) + (" ほか" if len(seen) > 4 else "")


def _placeholder(cand: dict, s: dict | None) -> tuple[str, str]:
    if s:
        return "構造図は省略", f"PDB {s['pdb_id']}（座標ファイルが大きすぎるため）"
    if cand["own_structures"]:
        return "構造データは PDB 公開待ち", ", ".join(x["pdb_id"] for x in cand["own_structures"][:4])
    if cand["kind"] == "new_map_only":
        return "EMDB マップのみ（原子モデルなし）", ", ".join(cand.get("emdb_ids", [])[:4])
    return "PDB ID は論文の補足資料に記載", "（座標を自動取得できないため構造図なし）"


# Short names for common ligands; anything else uses the pick's ligand_names, then the PDB name.
COMMON_LIGAND_NAMES = {
    "ANP": "AMP-PNP", "ACP": "AMP-PCP", "AGS": "ATPγS", "GNP": "GppNHp", "GSP": "GTPγS", "GCP": "GppCp",
    "NAD": "NAD+", "NAI": "NADH", "NAP": "NADP+", "NDP": "NADPH", "FAD": "FAD", "FMN": "FMN",
    "SAM": "SAM", "SAH": "SAH", "HEM": "ヘム", "HEC": "ヘム c", "COA": "CoA", "ACO": "アセチル CoA",
    "PLP": "PLP", "TPP": "TPP", "BTN": "ビオチン", "RET": "レチナール", "CLA": "クロロフィル a",
    "SF4": "[4Fe-4S]", "FES": "[2Fe-2S]", "UDP": "UDP", "UTP": "UTP", "CTP": "CTP", "CDP": "CDP",
    "BEF": "BeF3", "ALF": "AlF4", "AF3": "AlF3", "MGF": "MgF3", "VO4": "バナジン酸",
}


def ligand_name(lig_id: str, pdb_name: str | None, pick: dict) -> str:
    """Display name of a ligand: the writer's ligand_names, a common abbreviation, the PDB name, or the ID."""
    own = (pick.get("ligand_names") or {}).get(lig_id)
    if own:
        return own
    if lig_id in COMMON_LIGAND_NAMES:
        return COMMON_LIGAND_NAMES[lig_id]
    if lig_id in {"ATP", "ADP", "AMP", "GTP", "GDP", "GMP"}:
        return lig_id
    name = (pdb_name or "").strip()
    if name.isupper():
        name = name.lower()
    return name if name and len(name) <= 24 else lig_id


def thumbnail(cand: dict, pick: dict, theme: brand.Theme | None = None) -> str:
    """The same real PDB structure as the article figure, drawn small for the lists (3:2, thinned for
    large models). Empty when the figure has no structure behind it: a list never shows invented art."""
    th = theme or brand.THEME
    s = pick_structure(cand, pick)
    coords = pdb.coordinates(s["pdb_id"]) if s else None
    if not coords:
        return ""
    genes = {p["uniprot"]: p["gene"] for p in cand.get("proteins", []) if p.get("gene")}
    body, _, _ = structure_svg.render(coords, {l["id"] for l in s.get("ligands") or []} - pdb.COMMON_ADDITIVES,
                                      0, 0, THUMB_W, THUMB_H, genes, margin=26,
                                      entity_colors=list(th.entities), ligand_colors=structure_svg.LIGAND_PALETTE,
                                      max_points=THUMB_POINTS, bg=th.ground, topology=uniprot.topology)
    if not body:
        return ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{THUMB_W}" height="{THUMB_H}" '
            f'viewBox="0 0 {THUMB_W} {THUMB_H}">{body}</svg>\n')


def hero_figure(cand: dict, pick: dict, theme: brand.Theme | None = None) -> tuple[str, dict | None]:
    """Today's Structure for the front page's first screen: the same PDB structure as the article's figure, large, with
    its ligands but no labels or close-up (the article's figure follows right below), and the 3D viewer's starting
    camera for the same view (the front page opens the 3D viewer on it). ("", None) without a structure."""
    th = theme or brand.THEME
    s = pick_structure(cand, pick)
    coords = pdb.coordinates(s["pdb_id"]) if s else None
    if not coords:
        return "", None
    genes = {p["uniprot"]: p["gene"] for p in cand.get("proteins", []) if p.get("gene")}
    probe: dict = {}
    body, _, _ = structure_svg.render(coords, {l["id"] for l in s.get("ligands") or []} - pdb.COMMON_ADDITIVES,
                                      0, 0, HERO_W, HERO_H, genes, margin=18, entity_colors=list(th.entities),
                                      ligand_colors=structure_svg.LIGAND_PALETTE, bg=th.ground, topology=uniprot.topology,
                                      probe=probe)
    if not body:
        return "", None
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{HERO_W}" height="{HERO_H}" '
            f'viewBox="0 0 {HERO_W} {HERO_H}">{body}</svg>\n'), probe.get("view")


def chem_name(name: str | None) -> str:
    """The PDB's chemical name of a ligand, in readable case (the PDB writes most in capitals)."""
    name = (name or "").strip()
    return name.title() if name.isupper() else name


def split_label(text: str) -> tuple[str, str]:
    """'Wzm（膜貫通サブユニット）' -> ('Wzm', '膜貫通サブユニット'): the name and, smaller beneath it, what it does."""
    m = re.match(r"^(.*?)[（(](.*)[）)]\s*$", text.strip())
    return (m.group(1).strip(), m.group(2).strip()) if m else (text.strip(), "")


def figure_notes(pick: dict) -> list[dict]:
    """pick["figure_notes"] as the renderer takes them (entity numbers in the pick are 1-based, in legend order)."""
    out = []
    for n in pick.get("figure_notes") or []:
        if n.get("range") and n.get("title"):
            out.append({"entity": int(n.get("entity", 1)) - 1, "range": [int(x) for x in n["range"]],
                        "title": n["title"], "sub": n.get("sub", "")})
    return out


def _annotation_inputs(pick: dict, lig_ids: set, pdb_names: dict, site: dict | None, prefer: list) -> tuple:
    """What the leader lines say: (entity labels the writer gave, up to 4 ligand labels with the zoomed one first,
    figure_notes)."""
    names = [split_label(t) for t in (pick.get("legend_labels") or [])[:MAX_LABELS]]
    lig_order = ([site["ligand"]] if site else []) + [x for x in prefer if x in lig_ids and not (site and x == site["ligand"])]
    lig_order += [x for x in sorted(lig_ids) if x not in lig_order]
    lig_labels = {}
    for lid in lig_order[:4]:
        nm, role = split_label(ligand_name(lid, pdb_names.get(lid), pick))
        lig_labels[lid] = (nm, " · ".join(x for x in (role, lid if nm != lid else "") if x))
    return names, lig_labels, figure_notes(pick)


def structure_figure(cand: dict, pick: dict, theme: brand.Theme | None = None) -> tuple[str, dict]:
    """The protein and nothing else, with a close-up of the main ligand's binding site beside it.

    The figure labels what is what with leader lines (each polymer entity the writer named in legend_labels, the
    ligands, and the stretches pick["figure_notes"] points at), and when a ligand's binding site is drawn it is shown
    in a frame beside the overview, circled on it, with its probable hydrogen bonds and π interactions. (The close-up
    is drawn only there: a second, larger copy below the figure only repeated it.)

    Returns (svg, info). info holds what the page prints next to the figures: the PDB ID,
    [{"color", "label", "desc", "entity"}] per polymer entity (label: the writer's legend_labels if given, else the
    gene name or PDB description; desc: the PDB description; entity: its mmCIF entity id, for the 3D viewer),
    [{"id", "label", "color", "desc", "entity", "sites"}] per ligand (entity: its mmCIF entity ids; sites:
    [asym id, residue number] of each copy, for the 3D viewer's "next binding site"), and "site"
    ({"ligand", "label", "residues", "hbonds", "pi", "neighbours"}) when a close-up was drawn.
    The close-up shows pick["site_ligand"] if given ("" for none), else a ligand the writer named in
    ligand_names, else the largest ligand.
    """
    th = theme or brand.THEME
    s = pick_structure(cand, pick)
    body, legend, lig_legend, site, probe = "", [], [], None, {}
    if s:
        coords = pdb.coordinates(s["pdb_id"])
        if coords:
            lig_ids = {l["id"] for l in s.get("ligands") or []} - pdb.COMMON_ADDITIVES
            genes = {p["uniprot"]: p["gene"] for p in cand.get("proteins", []) if p.get("gene")}
            pdb_names = {l["id"]: l.get("name") for l in s.get("ligands") or []}
            want = pick.get("site_ligand")
            prefer = [want] if want else list(pick.get("ligand_names") or {})
            ent_colors = list(th.entities)
            inset_body, inset = "", None
            if lig_ids and want != "":
                inset_body, inset = structure_svg.render_site(
                    coords, lig_ids, prefer, INSET_W, INSET_H, genes, entity_colors=ent_colors,
                    ligand_colors=structure_svg.LIGAND_PALETTE, ink=th.ink, bg=th.ground, label_size=17)
                site = inset
            names, lig_labels, notes = _annotation_inputs(pick, lig_ids, pdb_names, site, prefer)
            band = 220 if (names or lig_labels or notes) else 0   # the most a side may give to its labels
            box_w = W - (INSET_W + 30) if inset_body else W
            body, legend, lig_legend = structure_svg.render(
                coords, lig_ids, 0, 0, box_w, H, genes, margin=26, entity_colors=ent_colors,
                ligand_colors=structure_svg.LIGAND_PALETTE, bg=th.ground, notes=notes,
                labels={"entities": names, "ligands": lig_labels}, bands=(band, band),
                site_xyz=tuple(inset["center"]) if inset else None, ink=th.ink, hl_color=th.accent, probe=probe,
                topology=uniprot.topology)
            if inset_body:
                body += _inset(inset_body, inset, probe.get("site"), pick, th, ligand_name(
                    site["ligand"], pdb_names.get(site["ligand"]), pick))
            labels = pick.get("legend_labels") or []
            legend = [(col, labels[i] if i < len(labels) else lab, desc, ent)
                      for i, (col, lab, desc, ent) in enumerate(legend)]
    ground = None  # the figure is transparent: it sits on the page ground
    if not body and is_impact(pick):
        return "", {"pdb_id": None, "legend": [], "ligands": []}
    if not body:
        ground = th.soft
        msg, sub = _placeholder(cand, s)
        body = "".join(text_block([t], W / 2, H / 2 + dy, size_, th.muted, wt)
                       .replace("<text ", '<text text-anchor="middle" ', 1)
                       for t, dy, size_, wt in ((msg, -10, 28, 600), (sub, 34, 19, 400)) if t)
    base = f'<rect width="{W}" height="{H}" fill="{ground}"/>' if ground else ""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'font-family="{FONT}">{base}{body}</svg>\n')
    pdb_names = {l["id"]: l.get("name") for l in (s or {}).get("ligands") or []}
    info = {"pdb_id": s["pdb_id"] if s else None, **({"view": probe["view"]} if body and s and probe.get("view") else {}),
            "legend": [{"color": col, "label": lab, "desc": desc if desc != lab else "", "entity": ent}
                       for col, lab, desc, ent in legend],
            "ligands": [{"id": lid, "label": ligand_name(lid, pdb_names.get(lid), pick), "color": col,
                         "desc": chem_name(pdb_names.get(lid)), "entity": ents, "sites": sites}
                        for col, lid, ents, sites in lig_legend]}
    if site:
        names_ = {x["id"]: x["label"] for x in info["ligands"]}
        info["site"] = {k: v for k, v in dict(site, label=names_.get(site["ligand"], site["ligand"]),
                                              neighbours=[names_.get(n, n) for n in site["neighbours"]]).items()
                        if k != "center"}
    return svg, info


def interaction_key(site: dict, right: float, y: float, size: float, ink: str) -> str:
    """What the dotted lines in a binding-site close-up are, right-aligned at (right, y); only the kinds drawn."""
    kinds = [(structure_svg.HBOND_COLOR, "水素結合")] if site.get("hbonds") else []
    kinds += [(structure_svg.PI_COLOR, "π相互作用")] if site.get("pi") else []
    out, x = [], right
    dot, line = max(2.6, size * 0.2), size * 2.2
    for col, text in reversed(kinds):
        tw = sum(char_width(ch) for ch in text) * size
        x -= tw
        out.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size:.1f}" fill="{ink}" opacity=".8">{text}</text>')
        x -= line + size * 0.5
        out.append(f'<path d="M{x:.1f},{y - size * 0.34:.1f}h{line:.1f}" stroke="{col}" stroke-width="{dot:.1f}" '
                   f'stroke-dasharray="0.1 {2.3 * dot:.1f}" stroke-linecap="round"/>')
        x -= size * 1.2
    return "".join(out)


def _inset(body: str, site: dict, circle: tuple | None, pick: dict, th: brand.Theme, name: str) -> str:
    """The zoomed binding site in a frame on the right of the overview, joined to the circle drawn on the overview,
    with the key to its dotted lines below the frame."""
    x, y = W - INSET_W - 6, (H - INSET_H) / 2 + 14
    frame = (f'<rect x="{x}" y="{y}" width="{INSET_W}" height="{INSET_H}" fill="none" stroke="{th.ink}" '
             f'stroke-width="1.2"/>')
    title = (f'<text x="{x}" y="{y - 12}" font-size="19" font-weight="700" fill="{th.ink}">結合部位の拡大'
             f'<tspan font-weight="400" font-size="16" dx="10" opacity=".75">{esc(name)}</tspan></text>')
    lines = ""
    if circle:
        cx, cy, r = circle
        for tx, ty in ((x, y), (x, y + INSET_H)):
            d = math.hypot(tx - cx, ty - cy) or 1
            lines += (f'<path d="M{cx + (tx - cx) / d * r:.1f},{cy + (ty - cy) / d * r:.1f}L{tx},{ty}" '
                      f'stroke="{th.ink}" stroke-width="1.1" opacity=".5" fill="none"/>')
    key = interaction_key(site, x + INSET_W, y + INSET_H + 26, 15, th.ink)
    return f'{lines}{frame}{title}<g transform="translate({x},{y})">{body}</g>{key}'


def structure_figure_mobile(cand: dict, pick: dict, theme: brand.Theme | None = None) -> str:
    """The same figure drawn for a phone: portrait, the overview on top and the binding-site zoom below it, with the text
    scaled up so it stays readable when the picture is shown 350 px wide. "" when the pick has no structure."""
    th = theme or brand.THEME
    s = pick_structure(cand, pick)
    coords = pdb.coordinates(s["pdb_id"]) if s else None
    if not coords:
        return ""
    lig_ids = {l["id"] for l in s.get("ligands") or []} - pdb.COMMON_ADDITIVES
    genes = {p["uniprot"]: p["gene"] for p in cand.get("proteins", []) if p.get("gene")}
    pdb_names = {l["id"]: l.get("name") for l in s.get("ligands") or []}
    want = pick.get("site_ligand")
    prefer = [want] if want else list(pick.get("ligand_names") or {})
    ent_colors = list(th.entities)
    inset_body, inset, site = "", None, None
    if lig_ids and want != "":
        inset_body, inset = structure_svg.render_site(
            coords, lig_ids, prefer, M_W, M_INSET_H, genes, entity_colors=ent_colors,
            ligand_colors=structure_svg.LIGAND_PALETTE, ink=th.ink, bg=th.ground, label_size=23)
        site = inset
    names, lig_labels, notes = _annotation_inputs(pick, lig_ids, pdb_names, site, prefer)
    oh = M_OVERVIEW_H if inset_body else M_OVERVIEW_H + 80
    probe: dict = {}
    body, _, _ = structure_svg.render(
        coords, lig_ids, 0, 0, M_W, oh, genes, margin=16, entity_colors=ent_colors,
        ligand_colors=structure_svg.LIGAND_PALETTE, bg=th.ground, notes=notes,
        labels={"entities": names, "ligands": lig_labels}, bands=(M_BAND, M_BAND), band_mode="topbottom",
        site_xyz=tuple(inset["center"]) if inset else None, ink=th.ink, hl_color=th.accent, probe=probe, font=M_FONT,
        topology=uniprot.topology)
    total = oh
    if inset_body:
        y = oh + 46
        circle = probe.get("site")
        lines = ""
        if circle:
            cx, cy, r = circle
            for tx in (0, M_W):
                d = math.hypot(tx - cx, y - cy) or 1
                lines += (f'<path d="M{cx + (tx - cx) / d * r:.1f},{cy + (y - cy) / d * r:.1f}L{tx},{y}" '
                          f'stroke="{th.ink}" stroke-width="1.4" opacity=".5" fill="none"/>')
        name = ligand_name(site["ligand"], pdb_names.get(site["ligand"]), pick)
        body += (f'{lines}<rect x="0.6" y="{y}" width="{M_W - 1.2}" height="{M_INSET_H}" fill="none" stroke="{th.ink}" '
                 f'stroke-width="1.6"/><text x="0" y="{y - 12}" font-size="26" font-weight="700" fill="{th.ink}">'
                 f'結合部位の拡大<tspan font-weight="400" font-size="22" dx="12" opacity=".75">{esc(name)}</tspan></text>'
                 f'<g transform="translate(0,{y})">{inset_body}</g>')
        total = y + M_INSET_H + 4
        if site.get("hbonds") or site.get("pi"):
            body += interaction_key(site, M_W, y + M_INSET_H + 34, 21, th.ink)
            total += 44
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{M_W}" height="{total}" viewBox="0 0 {M_W} {total}" '
            f'font-family="{FONT}">{body}</svg>\n')


def _compare_inputs(cand: dict, pick: dict):
    """(panels for structure_svg.compare, focus, mark, genes, entity names, align) from pick["compare"]; None if the
    pick has no usable comparison."""
    spec = pick.get("compare")
    if not spec:
        return None
    known = {x["pdb_id"]: x for x in cand.get("own_structures", []) + cand.get("cited_structures", [])}
    panels = []
    for pn in spec["panels"]:
        pid = pn["pdb"].upper()
        coords = pdb.coordinates(pid)
        if not coords:
            return None
        lig = {l["id"] for l in (known.get(pid) or {}).get("ligands") or []} - pdb.COMMON_ADDITIVES
        panels.append({"cif": coords, "ligands": lig, "title": pn["title"], "sub": f"PDB {pid}"})
    focus = spec.get("focus") or {}
    if not focus and figure_notes(pick):
        focus = figure_notes(pick)[0]
    else:
        focus = {"entity": int(focus.get("entity", 1)) - 1, "range": [int(x) for x in focus.get("range", [])],
                 "title": focus.get("title", "")}
    if not focus.get("range"):
        return None
    mark = spec.get("mark")
    if mark:
        mark = {"entity": int(mark.get("entity", 1)) - 1, "residue": int(mark["residue"]), "text": mark.get("text", "")}
    genes = {p["uniprot"]: p["gene"] for p in cand.get("proteins", []) if p.get("gene")}
    names = [split_label(t)[0] for t in (pick.get("legend_labels") or [])[:MAX_LABELS]]
    return panels, focus, mark, genes, names, int(spec.get("align", 1)) - 1


def compare_figure_mobile(cand: dict, pick: dict, theme: brand.Theme | None = None) -> str:
    """The comparison figure for a phone: the panels stacked, the movement running left to right, larger text."""
    th = theme or brand.THEME
    inp = _compare_inputs(cand, pick)
    if not inp:
        return ""
    panels, focus, mark, genes, names, align = inp
    h = M_COMPARE_H * len(panels) + 30 * (len(panels) - 1)
    body, _ = structure_svg.compare(
        panels, 0, 0, M_W, h, align, focus, mark, genes, entity_colors=list(th.entities),
        ligand_colors=structure_svg.LIGAND_PALETTE, bg=th.ground, ink=th.ink, hl_color=th.accent, names=None,
        font=M_FONT, vertical=True, gap=30, topology=uniprot.topology)
    if not body:
        return ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{M_W}" height="{h}" viewBox="0 0 {M_W} {h}" '
            f'font-family="{FONT}">{body}</svg>\n')


def compare_figure(cand: dict, pick: dict, theme: brand.Theme | None = None) -> tuple[str, dict]:
    """The figure that shows what the paper found: pick["compare"]'s structures, superposed and side by side,
    with the stretch the paper is about in the accent colour. ("", {}) when the pick has none or the
    structures cannot be superposed."""
    th = theme or brand.THEME
    inp = _compare_inputs(cand, pick)
    if not inp:
        return "", {}
    panels, focus, mark, genes, names, align = inp
    body, info = structure_svg.compare(
        panels, 0, 0, W, H, align, focus, mark, genes, entity_colors=list(th.entities),
        ligand_colors=structure_svg.LIGAND_PALETTE, bg=th.ground, ink=th.ink, hl_color=th.accent, names=names,
        topology=uniprot.topology)
    if not body:
        return "", {}
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'font-family="{FONT}">{body}</svg>\n')
    return svg, {"panels": [{"pdb": pn["pdb"].upper(), "title": pn["title"]} for pn in pick["compare"]["panels"]],
                 "distance": info.get("distance")}


# ---------- issue page ----------

def structure_table(cand: dict) -> list[str]:
    own = cand["own_structures"]
    if not own and cand.get("cited_structures"):  # 注目の構造: the structures it used
        own = cand["cited_structures"]
    elif not own and cand["kind"] in ("uses_existing", "no_ids"):
        return ["論文が使った PDB 構造はない。", ""]
    if not own:
        if cand["kind"] == "new_map_only" and cand.get("emdb_ids"):
            links = ", ".join(f"[{e}](https://www.ebi.ac.uk/emdb/{e})" for e in cand["emdb_ids"][:12])
            return [f"原子モデルのない cryo-EM マップ（EMDB）：{links}", ""]
        return ["寄託の記載はあるが、ID は補足資料にのみ記載されている。", ""]
    rows = ["| PDB | 手法 | 分解能 | 公開 | 生物種 | リガンド |", "|---|---|---|---|---|---|"]
    for s in own:
        if s["status"] != "released":
            rows.append(f"| {s['pdb_id']} | — | — | 公開待ち | — | — |")
            continue
        orgs = sorted({o for p in s.get("polymers", []) for o in p["organisms"]})
        ligs = ", ".join(l["id"] for l in s.get("ligands") or []) or "—"
        res = f"{s['resolution']:.2f} Å" if s.get("resolution") else "—"
        rows.append(f"| [{s['pdb_id']}](https://www.rcsb.org/structure/{s['pdb_id']}) | {structure_method(s)} | "
                    f"{res} | {s.get('release_date') or '—'} | {', '.join(orgs) or '—'} | {ligs} |")
    return rows + [""]


def protein_lines(cand: dict) -> list[str]:
    out = []
    for p in cand.get("proteins", []):
        flag = "（PDB で初めての構造）" if p.get("first_structure") else f"（既存の PDB 構造 {p.get('prior_pdb_count', 0)} 件）"
        out.append(f"- [{p['uniprot']}](https://www.uniprot.org/uniprotkb/{p['uniprot']}) "
                   f"{p.get('gene') or ''} {p.get('name') or ''} — *{p.get('organism') or ''}* {flag}")
    return out + ([""] if out else [])


def quote_block(evidence: list) -> list[str]:
    out = ["<details><summary>根拠（論文本文）</summary>", ""]
    for e in evidence:
        out.append(f"> {e['quote']}  ")
        out.append(f"> — {e.get('section', '')}")
        out.append("")
    return out + ["</details>", ""]


def article_md(cand: dict, pick: dict, n: int, info: dict | None = None) -> list[str]:
    doi = f"https://doi.org/{cand['doi']}" if cand.get("doi") else f"https://europepmc.org/article/PMC/{cand['pmcid']}"
    s = pick["summary"]
    keys = []
    if info and info.get("legend"):
        keys.append(f"色（順に）：{' / '.join(x['label'] for x in info['legend'])}")
    if info and info.get("ligands"):
        keys.append("スティック：" + "、".join(x["label"] if x["label"] == x["id"] else f"{x['label']}（{x['id']}）"
                                          for x in info["ligands"]))
    key = "　".join(keys)
    out = [f"## {n}｜{pick['target_label']}", "",
           f"### {cand['title']}", "",
           f"*{cand['journal']}*（{cand['pub_date']}） · [論文]({doi}) · ライセンス: {cand.get('license') or '不明'}", ""]
    if info is None or info.get("pdb_id") or not is_impact(pick):
        used = "（論文が使った既存の構造）" if is_impact(pick) else ""
        out += [f"![構造図：{pick['target_label']}](figures/{cand['pmcid']}.svg)", ""] + ([f"<sub>PDB {info['pdb_id']}{used}</sub>", ""] if used and info else [])
    if key:
        out += [f"<sub>{key}</sub>", ""]
    if info and info.get("compare"):
        cp = info["compare"]
        out += [f"![構造の比較：{'と'.join(x['title'] for x in cp['panels'])}](figures/{cand['pmcid']}-compare.svg)", ""]
        if pick.get("figure_evidence"):
            out += ["<details><summary>この比較図の根拠（論文本文）</summary>", ""]
            for e in pick["figure_evidence"]:
                out += [f"> {e['quote']}  ", f"> — {e.get('section', '')}", ""]
            out += ["</details>", ""]
    out += [f"**{pick['headline']}**", "", pick.get("subhead") or "", "",
            ] + [f"- **{label}**：{s[k]}" for k, label in {**SUMMARY_FIELDS, **SUMMARY_OPTIONAL}.items() if s.get(k)] + [""]
    sev = [(label, e) for k, label in {**SUMMARY_FIELDS, **SUMMARY_OPTIONAL}.items() for e in (s.get("evidence") or {}).get(k, [])
           if s.get(k)]
    if sev:
        out += ["<details><summary>この紹介文の根拠（論文本文）</summary>", ""]
        for label, e in sev:
            out += [f"> {e['quote']}  ", f"> — {label}／{e.get('section', '')}", ""]
        out += ["</details>", ""]
    if pick.get("categories"):
        out += [f"<sub>分類：{'・'.join(CATEGORIES.get(c, c) for c in pick['categories'])}</sub>", ""]
    out += ["#### 読みどころ", ""]
    for h in pick["highlights"]:
        out += [h["text"], ""] + quote_block(h.get("evidence", []))
    out += ["#### 構造データ", ""] + structure_table(cand) + protein_lines(cand)
    notes = pick.get("lab_notes") or {}
    filled = [(LAB_NOTE_FIELDS[k], notes[k]) for k in LAB_NOTE_FIELDS if notes.get(k)]
    if filled:
        out += ["#### 実験メモ", ""] + [f"- **{label}**：{v}" for label, v in filled] + [""]
        if notes.get("evidence"):
            out += quote_block(notes["evidence"])
    if pick.get("editor_notes"):
        out += [f"> 📝 編集者への確認事項：{pick['editor_notes']}", ""]
    return out + ["---", ""]


def contents_table(picks: list[tuple[dict, dict]]) -> list[str]:
    rows = ["| # | 標的 | 手法・分解能 | 見出し |", "|---|---|---|---|"]
    for n, (cand, pick) in enumerate(picks, 1):
        method = method_line(cand)
        if pick.get("method_label") and not any(x["status"] == "released" for x in cand["own_structures"]):
            method = pick["method_label"]
        rows.append(f"| {n} | {pick['target_label']} | {method} | {pick['headline']} |")
    return rows + [""]


def impact_md(record: dict, cands: dict) -> list[str]:
    """The writer's scores for 注目の構造, for the editor to check the choice."""
    if not record.get("scored"):
        return []
    names = {k: v[1].split("。")[0] for k, v in impact.RUBRIC.items()}
    out = [f"## 注目の構造の採点（{record.get('screened', 0)} 本を確認し、{len(record['scored'])} 本を採点）", "",
           f"基準は `pipeline/impact.py`（{impact.MAX_TOTAL} 点満点、{impact.THRESHOLD} 点以上から最大 {impact.MAX_PER_DAY} 本）。", "",
           "| 選定 | 論文 | 種類 | " + " | ".join(impact.RUBRIC) + " | 計 | 理由 |",
           "|---|---|---|" + "---|" * len(impact.RUBRIC) + "---|---|"]
    for s in sorted(record["scored"], key=lambda s: -sum(s["scores"].values())):
        c = cands.get(s["pmcid"], {})
        out.append(f"| {'◯' if s.get('selected') else ''} | {s['pmcid']} {c.get('title', '')[:60]} | "
                   f"{impact.TYPES.get(s.get('type'), s.get('type'))} | "
                   + " | ".join(str(s["scores"].get(k, 0)) for k in impact.RUBRIC)
                   + f" | {sum(s['scores'].values())} | {s.get('reason', '')} |")
    return out + ["", f"<sub>{' / '.join(f'{k}：{v}' for k, v in names.items())}</sub>", ""]


def feature_md(feature: dict | None, picks: list[tuple[dict, dict]]) -> list[str]:
    """今日の一本 for the review README: which article, why, what the picker weighed."""
    if not feature:
        return []
    by_id = {c["pmcid"]: (n, c, pk) for n, (c, pk) in enumerate(split_sections(picks)[0] + split_sections(picks)[1], 1)}
    if feature.get("pmcid") not in by_id:
        return []
    n, cand, pick = by_id[feature["pmcid"]]
    axes = "・".join({"structure": "構造生物学", "biology": "生命科学", "drug": "創薬"}.get(a, a)
                    for a in feature.get("axes", []))
    out = ["## Today's Structure（今日の一本）", "", f"**{n}｜{pick['headline']}**（{pick['target_label']}）", "",
           f"選んだ理由（{axes}）：{feature['reason']}", ""]
    for e in feature.get("evidence", []):
        out.append(f"> {e['quote']}  ")
        out.append(f"> — {e.get('section', '')}")
        out.append("")
    for c in feature.get("considered", []):
        other = by_id.get(c.get("pmcid"))
        out.append(f"- 迷った論文：{other[2]['target_label'] if other else c.get('pmcid')} — {c.get('note', '')}")
    return out + [""]


def issue_md(meta: dict, picks: list[tuple[dict, dict]], infos: dict | None = None,
             impact_record: dict | None = None, feature: dict | None = None) -> str:
    c = meta["counts"]
    new_total = c["new_structure"] + c.get("new_structure_ids_unlisted", 0) + c.get("new_map_only", 0)
    picked = {p["pmcid"] for _, p in picks}
    rest = not_covered(meta, picked)
    covered = "すべて紹介する。" if not rest else f"うち {len(picks)} 本を紹介する（残りは末尾に一覧）。"
    out = ['<p align="center"><img src="../../../assets/logo.svg" alt="構造生物学デイリー" height="56"></p>', "",
           f"# {meta['issue_date']} 号", "",
           f"**今日の新しい構造：{new_total} 本**　{'PMC' if meta.get('source') == 'pmc-aws' else 'Europe PMC'} に "
           f"{meta['index_from'] + '〜' if meta.get('index_from') else ''}{meta['index_date']} に登録された"
           f"オープンアクセス論文 {c['parsed']} 本のうち、新しい構造を報告した論文は {new_total} 本。{covered}", ""]
    if meta.get("source") == "pmc-aws":   # Europe PMC was not indexing; see pipeline/pmcoa.py
        out += [f"出典：{pmcoa.CREDIT}", ""]
    new, hot = split_sections(picks)
    if hot:
        out += [f"**注目の構造：{len(hot)} 本**（新しい構造ではないが、実験で決めた構造で確かめた注目の研究）", ""]
    out += feature_md(feature, picks)
    if picks:
        out += contents_table(new + hot)
    out += ["> この号は AI が論文本文から作成した**レビュー用の下書き**です。"
            "数値・ID・リガンドは PDB / UniProt から取得し、AI の記述には本文からの引用を付けています。", "", "---", ""]
    if hot and new:
        out += ["# 新しい構造", ""]
    for n, (cand, pick) in enumerate(new, 1):
        out += article_md(cand, pick, n, (infos or {}).get(cand["pmcid"]))
    if hot:
        out += ["# 注目の構造（新しい構造以外。実験で決めた構造で確かめた研究）", ""]
        for n, (cand, pick) in enumerate(hot, len(new) + 1):
            out += article_md(cand, pick, n, (infos or {}).get(cand["pmcid"]))
    out += impact_md(impact_record or {}, {c["pmcid"]: c for c in meta["candidates"]})
    if rest:
        out += ["## 記事にしていない新しい構造", ""]
        for x in rest:
            doi = f"https://doi.org/{x['doi']}" if x.get("doi") else f"https://europepmc.org/article/PMC/{x['pmcid']}"
            ids = ", ".join(s["pdb_id"] for s in x["own_structures"][:4])
            out.append(f"- [{x['title']}]({doi}) — *{x['journal_abbrev'] or x['journal']}*"
                       f"（{KIND_JA[x['kind']]}{'：' + ids if ids else ''}）")
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print(__doc__)
        return 2
    d = issue_dir(argv[0])
    meta = read_json(d / "candidates.json")
    cands = {c["pmcid"]: c for c in meta["candidates"]}
    picks = []
    for path in sorted((d / "picks").glob("*.json")):
        pick = read_json(path)
        picks.append((cands[pick["pmcid"]], pick))
    picks.sort(key=lambda cp: cp[1].get("rank", 99))
    fig_dir = d / "figures"
    fig_dir.mkdir(exist_ok=True)
    infos = {}
    for cand, pick in picks:
        svg, info = structure_figure(cand, pick)
        fig_path = fig_dir / f"{cand['pmcid']}.svg"
        if svg:
            fig_path.write_text(svg, encoding="utf-8")
        elif fig_path.exists():
            fig_path.unlink()
        site_path = fig_dir / f"{cand['pmcid']}-site.svg"   # the separate close-up of earlier renders
        if site_path.exists():
            site_path.unlink()
        cmp_svg, cmp_info = compare_figure(cand, pick) if svg else ("", {})
        cmp_path = fig_dir / f"{cand['pmcid']}-compare.svg"
        if cmp_svg:
            cmp_path.write_text(cmp_svg, encoding="utf-8")
            info["compare"] = cmp_info
            mk = (pick.get("compare") or {}).get("mark") or {}
            m_ = re.search(r"(\d+(?:\.\d+)?)\s*Å", mk.get("text", ""))
            if m_ and cmp_info.get("distance") and abs(cmp_info["distance"] - float(m_.group(1))) > 0.25 * float(m_.group(1)):
                print(f"warning: {cand['pmcid']}: the arrow says {m_.group(0)} but the superposed structures give "
                      f"{cmp_info['distance']} Å (check the residue, the chain and the entity numbers)")
        elif cmp_path.exists():
            cmp_path.unlink()
        for suffix, maker in (("-m", structure_figure_mobile), ("-compare-m", compare_figure_mobile)):
            mob_path = fig_dir / f"{cand['pmcid']}{suffix}.svg"
            mob = maker(cand, pick) if svg and (suffix == "-m" or cmp_svg) else ""
            if mob:
                mob_path.write_text(mob, encoding="utf-8")
            elif mob_path.exists():
                mob_path.unlink()
        thumb = thumbnail(cand, pick) if svg else ""
        thumb_path = fig_dir / f"{cand['pmcid']}-thumb.svg"
        if thumb:
            thumb_path.write_text(thumb, encoding="utf-8")
        elif thumb_path.exists():
            thumb_path.unlink()
        (fig_dir / f"{cand['pmcid']}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1) + "\n",
                                                       encoding="utf-8")
        infos[cand["pmcid"]] = info
    feature = read_json(d / "feature.json") if (d / "feature.json").exists() and len(picks) >= FEATURE_MIN else None
    for cand, pick in picks:   # the front page's large picture: Today's Structure only
        hero_path = fig_dir / f"{cand['pmcid']}-hero.svg"
        hero, view = hero_figure(cand, pick) if feature and feature.get("pmcid") == cand["pmcid"] else ("", None)
        if hero:
            hero_path.write_text(hero, encoding="utf-8")
            if view:
                infos[cand["pmcid"]]["hero_view"] = view
                (fig_dir / f"{cand['pmcid']}.json").write_text(json.dumps(infos[cand["pmcid"]], ensure_ascii=False, indent=1)
                                                               + "\n", encoding="utf-8")
        elif hero_path.exists():
            hero_path.unlink()
    (d / "README.md").write_text(issue_md(meta, picks, infos, load_impact(d), feature), encoding="utf-8")
    print(f"Rendered {len(picks)} articles to {d / 'README.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
