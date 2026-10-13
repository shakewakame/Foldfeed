"""Render an issue: graphical abstracts (SVG) and the issue page for review (README.md).

    python3 -m pipeline.render 2026-10-07
"""

from __future__ import annotations

import html
import json
import re
import sys
import unicodedata

from . import brand, impact, pdb, structure_svg
from .common import issue_dir, read_json
from .schema import LAB_NOTE_FIELDS

SITE_NAME = brand.SITE_NAME
FONT = brand.FONT
W, H = 1200, 560
SITE_W, SITE_H = 760, 500  # binding-site close-up
CONTACT_NOTE = "4 Å 以内の残基"

METHOD_JA = {
    "X-RAY DIFFRACTION": "X線結晶構造解析",
    "ELECTRON MICROSCOPY": "クライオ電顕",
    "SOLUTION NMR": "溶液NMR",
    "SOLID-STATE NMR": "固体NMR",
    "ELECTRON CRYSTALLOGRAPHY": "電子線結晶構造解析",
    "NEUTRON DIFFRACTION": "中性子回折",
}
KIND_JA = {
    "new_structure": "新しい構造",
    "new_structure_ids_unlisted": "新しい構造（ID は補足資料）",
    "new_map_only": "新しいマップ（EMDB）",
}


def method_ja(m: str | None) -> str:
    return ", ".join(METHOD_JA.get(x.strip(), x.strip()) for x in (m or "").split(",") if x.strip())


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
    methods = sorted({method_ja(s["method"]) for s in rel})
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


def structure_figure(cand: dict, pick: dict, theme: brand.Theme | None = None) -> tuple[str, dict, str]:
    """The protein and nothing else, plus a close-up of the main ligand's binding site.

    Returns (svg, info, site_svg). info holds what the page prints next to the figures: the PDB ID,
    [{"color", "label"}] per polymer entity, [{"id", "label", "color"}] per ligand, and "site"
    ({"ligand", "label", "residues", "polar", "neighbours"}) when a close-up was drawn (site_svg != "").
    The close-up shows pick["site_ligand"] if given ("" for none), else a ligand the writer named in
    ligand_names, else the largest ligand.
    """
    th = theme or brand.THEME
    s = pick_structure(cand, pick)
    body, legend, lig_legend, site_body, site = "", [], [], "", None
    if s:
        coords = pdb.coordinates(s["pdb_id"])
        if coords:
            lig_ids = {l["id"] for l in s.get("ligands") or []} - pdb.COMMON_ADDITIVES
            genes = {p["uniprot"]: p["gene"] for p in cand.get("proteins", []) if p.get("gene")}
            body, legend, lig_legend = structure_svg.render(
                coords, lig_ids, 0, 0, W, H, genes, margin=34, entity_colors=list(th.entities),
                ligand_colors=structure_svg.LIGAND_PALETTE)
            if pick.get("legend_labels"):
                legend = [(col, lab) for (col, _), lab in zip(legend, pick["legend_labels"])]
            want = pick.get("site_ligand")
            if lig_legend and want != "":
                prefer = [want] if want else list(pick.get("ligand_names") or {})
                site_body, site = structure_svg.render_site(
                    coords, lig_ids, prefer, SITE_W, SITE_H, genes, entity_colors=list(th.entities),
                    ligand_colors=structure_svg.LIGAND_PALETTE, ink=th.ink)
    ground = th.paper
    if not body and is_impact(pick):
        return "", {"pdb_id": None, "legend": [], "ligands": []}, ""
    if not body:
        ground = th.soft
        msg, sub = _placeholder(cand, s)
        body = "".join(text_block([t], W / 2, H / 2 + dy, size_, th.muted, wt)
                       .replace("<text ", '<text text-anchor="middle" ', 1)
                       for t, dy, size_, wt in ((msg, -10, 28, 600), (sub, 34, 19, 400)) if t)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'font-family="{FONT}"><rect width="{W}" height="{H}" fill="{ground}"/>{body}</svg>\n')
    pdb_names = {l["id"]: l.get("name") for l in (s or {}).get("ligands") or []}
    info = {"pdb_id": s["pdb_id"] if s else None,
            "legend": [{"color": col, "label": lab} for col, lab in legend] if len(legend) > 1 else [],
            "ligands": [{"id": lid, "label": ligand_name(lid, pdb_names.get(lid), pick), "color": col}
                        for col, lid in lig_legend]}
    site_svg = ""
    if site_body:
        names = {x["id"]: x["label"] for x in info["ligands"]}
        info["site"] = dict(site, label=names.get(site["ligand"], site["ligand"]),
                            neighbours=[names.get(n, n) for n in site["neighbours"]])
        site_svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{SITE_W}" height="{SITE_H}" '
                    f'viewBox="0 0 {SITE_W} {SITE_H}" font-family="{FONT}">'
                    f'<rect width="{SITE_W}" height="{SITE_H}" fill="{th.paper}"/>{site_body}</svg>\n')
    return svg, info, site_svg


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
        rows.append(f"| [{s['pdb_id']}](https://www.rcsb.org/structure/{s['pdb_id']}) | {method_ja(s['method'])} | "
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
    if info and info.get("site"):
        st = info["site"]
        out += [f"![結合部位：{st['label']}](figures/{cand['pmcid']}-site.svg)", "",
                f"<sub>{st['label']}の結合部位。{CONTACT_NOTE}：{', '.join(st['residues'])}"
                + (f"。点線は 3.3 Å 以内の N・O の組（{st['polar']} 組）" if st["polar"] else "") + "</sub>", ""]
    out += [f"**{pick['headline']}**", "",
            f"- **背景**：{s['background']}",
            f"- **やったこと**：{s['approach']}",
            f"- **分かったこと**：{s['findings']}", "",
            "#### 読みどころ", ""]
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


def issue_md(meta: dict, picks: list[tuple[dict, dict]], infos: dict | None = None,
             impact_record: dict | None = None) -> str:
    c = meta["counts"]
    new_total = c["new_structure"] + c.get("new_structure_ids_unlisted", 0) + c.get("new_map_only", 0)
    picked = {p["pmcid"] for _, p in picks}
    rest = [x for x in meta["candidates"] if x["kind"] in KIND_JA and x["pmcid"] not in picked]
    covered = "すべて紹介する。" if not rest else f"うち {len(picks)} 本を紹介する（残りは末尾に一覧）。"
    out = ['<p align="center"><img src="../../../assets/logo.svg" alt="構造生物学デイリー" height="56"></p>', "",
           f"# {meta['issue_date']} 号", "",
           f"**今日の新しい構造：{new_total} 本**　Europe PMC に {meta['index_date']} に登録された"
           f"オープンアクセス論文 {c['parsed']} 本のうち、新しい構造を報告した論文は {new_total} 本。{covered}", ""]
    new, hot = split_sections(picks)
    if hot:
        out += [f"**注目の構造：{len(hot)} 本**（新しい構造ではないが、実験で決めた構造で確かめた注目の研究）", ""]
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
        svg, info, site_svg = structure_figure(cand, pick)
        fig_path = fig_dir / f"{cand['pmcid']}.svg"
        if svg:
            fig_path.write_text(svg, encoding="utf-8")
        elif fig_path.exists():
            fig_path.unlink()
        site_path = fig_dir / f"{cand['pmcid']}-site.svg"
        if site_svg:
            site_path.write_text(site_svg, encoding="utf-8")
        elif site_path.exists():
            site_path.unlink()
        (fig_dir / f"{cand['pmcid']}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1) + "\n",
                                                       encoding="utf-8")
        infos[cand["pmcid"]] = info
    (d / "README.md").write_text(issue_md(meta, picks, infos, load_impact(d)), encoding="utf-8")
    print(f"Rendered {len(picks)} articles to {d / 'README.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
