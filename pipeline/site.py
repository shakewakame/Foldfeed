"""Build the static website from the built issues.

    python3 -m pipeline.site            # writes site/ (index + one page per issue)

Reads every data/issues/<date>/ that has candidates.json and a picks/ folder and writes a
self-contained site under site/ (figures and logo copied in) for GitHub Pages.

Layout, settled with the owner in October 2026 (docs/design-feedback.md): a journal-like page
on a cool grey ground in indigo ink with a single vermilion accent. Each article has a margin
column for the data (number, target, method, PDB, ligand, UniProt, organism) and a text column:
the paper's title, the protein figure and its key, the binding-site close-up, the headline,
the three-part summary, 読みどころ with the original sentences always shown, and the lab notes.
"""

from __future__ import annotations

import html
import shutil
import sys

from . import brand, render
from .common import ISSUES_DIR, ROOT, read_json
from .schema import LAB_NOTE_FIELDS

SITE_DIR = ROOT / "site"
TH = brand.THEME
DESCRIPTION = "毎朝、査読済みのオープンアクセス論文から、新しいタンパク質の構造を報告した論文をすべて紹介する。"

CSS = f"""
:root {{
  --ground:{TH.ground}; --paper:{TH.paper}; --ink:{TH.ink}; --ink2:{TH.ink2}; --mute:{TH.muted};
  --hair:{TH.hair}; --acc:{TH.accent}; --acc-ink:{TH.accent_ink};
  --serif:"Noto Serif JP","Hiragino Mincho ProN","Yu Mincho",YuMincho,"BIZ UDMincho",Georgia,serif;
  --latin:Georgia,"Times New Roman","Noto Serif JP",serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
}}
* {{ box-sizing:border-box; }}
html, body {{ margin:0; background:var(--ground); }}
body {{ color:var(--ink); font-family:var(--serif); font-size:16px; line-height:1.95;
        -webkit-font-smoothing:antialiased; }}
a {{ color:inherit; text-decoration:none; text-underline-offset:3px; }}
a:hover {{ color:var(--acc-ink); text-decoration:underline; text-decoration-color:var(--acc); }}
.kicker a {{ text-decoration:underline; text-decoration-color:var(--acc); }}
.page > * {{ max-width:1120px; margin-left:auto; margin-right:auto; padding-left:28px; padding-right:28px; }}
.mk {{ color:var(--acc-ink); font-weight:700; }}

/* masthead: a heavy rule over a thin one */
.mast {{ padding-top:30px; }}
.mast-row {{ display:flex; align-items:flex-end; justify-content:space-between; gap:24px; }}
.word {{ display:flex; align-items:center; gap:15px; text-decoration:none; }}
.word img {{ width:46px; height:46px; display:block; }}
.word .name {{ font-family:var(--latin); font-size:42px; line-height:1; letter-spacing:-0.02em; font-weight:700; margin:0; }}
.word .sub {{ font-size:13px; letter-spacing:.12em; color:var(--mute); margin-top:7px; line-height:1.2; }}
.vol {{ font-size:14px; text-align:right; color:var(--ink2); line-height:1.7; }}
.rules {{ margin-top:18px; border-top:4px solid var(--ink); }}
.rules::after {{ content:""; display:block; margin-top:3px; border-top:1px solid var(--ink); }}
.intro {{ color:var(--ink2); font-size:14.5px; margin-top:18px; }}

/* contents */
.toc {{ margin-top:40px; margin-bottom:14px; display:grid; grid-template-columns:220px 1fr; gap:28px; }}
.label {{ margin:0; font-family:var(--mono); font-size:12px; letter-spacing:.14em; font-weight:500;
          color:var(--mute); text-transform:uppercase; padding-top:6px; }}
.label a {{ text-decoration:none; }}
.toc ol {{ list-style:none; margin:0; padding:0; }}
.toc li {{ display:grid; grid-template-columns:38px 1fr; align-items:baseline; padding:0 0 16px; }}
.toc .n {{ font-family:var(--mono); font-size:12px; color:var(--acc-ink); font-weight:700; }}
.toc .t {{ font-size:18px; line-height:1.5; }}
.toc .lead {{ grid-column:2; font-size:13.5px; color:var(--mute); line-height:1.65; }}
.home-issue {{ margin-top:34px; }}
.home-issue + .home-issue {{ margin-top:10px; }}

/* article: margin column for the data, text column for the story */
.art {{ display:grid; grid-template-columns:220px minmax(0,1fr); gap:28px; padding-top:60px; padding-bottom:24px;
        margin-top:56px; position:relative; }}
.art::before {{ content:""; position:absolute; left:28px; right:28px; top:0; border-top:1px solid var(--ink); }}
.margin {{ font-family:var(--mono); font-size:11.5px; line-height:1.7; color:var(--ink2); }}
.margin .no {{ font-family:var(--latin); font-size:78px; line-height:.9; color:var(--ink); letter-spacing:-0.04em;
               margin-bottom:16px; }}
.margin dl {{ margin:0; }}
.margin dt {{ margin-top:13px; color:var(--mute); letter-spacing:.1em; font-size:10px; text-transform:uppercase; }}
.margin dd {{ margin:2px 0 0; overflow-wrap:anywhere; }}
.margin .pdb {{ display:block; }}
.margin .first {{ margin-top:16px; }}
.margin .sticky {{ position:sticky; top:24px; }}
.kicker {{ font-family:var(--mono); font-size:11.5px; letter-spacing:.06em; color:var(--mute); margin:0 0 10px; line-height:1.7; }}
.kicker b {{ color:var(--ink); font-weight:600; }}
.art h2 {{ font-family:var(--latin); font-size:25px; line-height:1.38; letter-spacing:-0.005em; margin:0 0 24px;
           font-weight:700; text-wrap:balance; scroll-margin-top:20px; }}
.fig {{ display:block; width:100%; height:auto; border:1px solid var(--ink); background:var(--paper); margin:0 0 8px; }}
.key {{ display:flex; flex-wrap:wrap; gap:2px 20px; margin:0; font-family:var(--mono); font-size:11.5px;
        color:var(--ink2); line-height:1.9; }}
.key i {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:7px; }}
.key i.stick {{ width:15px; height:4px; border-radius:2px; transform:rotate(-30deg); vertical-align:2px; }}
.site {{ margin:26px 0 0; }}
.site img {{ display:block; width:100%; height:auto; border:1px solid var(--ink); background:var(--paper); }}
.site dl {{ display:grid; grid-template-columns:auto minmax(0,1fr) auto; gap:4px 26px; margin:8px 0 0;
            font-family:var(--mono); font-size:11.5px; line-height:1.7; color:var(--ink2); }}
.site dl div {{ min-width:0; }}
.site dt {{ color:var(--mute); letter-spacing:.1em; font-size:10px; }}
.site dd {{ margin:0; }}
.site .dash {{ display:inline-block; width:22px; border-top:2px dashed var(--ink); vertical-align:4px; margin-right:6px; }}
.art .headline {{ font-size:19px; font-weight:700; line-height:1.7; margin:34px 0 14px; text-wrap:balance; }}
.sum {{ margin:0 0 12px; }}
.sum b {{ font-weight:700; margin-right:1em; }}
.art h3 {{ font-size:19px; line-height:1.5; font-weight:700; margin:44px 0 12px; }}
.hl {{ margin:0 0 26px; }}
.hl > p:first-child {{ margin:0; }}
.src {{ margin:8px 0 0 1.4em; font-size:13px; line-height:1.7; color:var(--ink2); }}
.src b {{ font-family:var(--mono); font-size:10.5px; letter-spacing:.1em; color:var(--acc-ink); margin-right:10px; }}
.src i {{ font-family:var(--latin); }}
.src span {{ font-family:var(--mono); font-size:10.5px; color:var(--mute); margin-left:8px; }}
.lab {{ margin:0; font-size:14.5px; }}
.lab div {{ display:grid; grid-template-columns:112px 1fr; gap:14px; padding:6px 0; border-bottom:1px dotted var(--hair); }}
.lab dt {{ font-family:var(--mono); font-size:11px; color:var(--mute); padding-top:3px; }}
.lab dd {{ margin:0; }}
.lab-src {{ margin-top:10px; }}
.rest {{ margin-top:56px; position:relative; padding-top:30px; }}
.rest::before {{ content:""; position:absolute; left:28px; right:28px; top:0; border-top:1px solid var(--ink); }}
.rest ul {{ margin:10px 0 0; padding-left:1.2em; font-size:14.5px; }}

.issue-nav {{ display:flex; justify-content:space-between; gap:16px; margin-top:70px; font-family:var(--mono);
              font-size:12px; color:var(--ink2); }}
.colophon {{ margin-top:22px; padding-top:16px; padding-bottom:70px; position:relative; font-size:12.5px;
             color:var(--mute); line-height:1.8; }}
.colophon::before {{ content:""; position:absolute; left:28px; right:28px; top:0; border-top:4px solid var(--ink); }}
.colophon p {{ margin:0 0 6px; max-width:720px; }}

@media (max-width:860px) {{
  .page > * {{ padding-left:18px; padding-right:18px; }}
  .art::before, .colophon::before, .rest::before {{ left:18px; right:18px; }}
  .word .name {{ font-size:32px; }}
  .mast-row {{ flex-wrap:wrap; align-items:flex-start; }}
  .vol {{ text-align:left; }}
  .toc, .art {{ grid-template-columns:minmax(0,1fr); gap:14px; }}
  .toc li {{ grid-template-columns:30px minmax(0,1fr); }}
  .margin .no {{ font-size:52px; margin-bottom:10px; }}
  .margin dl {{ display:grid; grid-template-columns:78px minmax(0,1fr); gap:3px 12px; }}
  .margin dt {{ margin-top:0; padding-top:2px; }}
  .margin .pdb {{ display:inline; margin-right:12px; }}
  .margin .first {{ grid-column:2; margin-top:4px; }}
  .margin .sticky {{ position:static; }}
  .art h2 {{ font-size:21px; }}
  .art .headline {{ font-size:17px; }}
  .site dl {{ grid-template-columns:minmax(0,1fr); }}
  .lab div {{ grid-template-columns:92px 1fr; }}
}}
"""


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def pad(n: int) -> str:
    return f"{n:02d}"


def jp_date(date: str) -> str:
    y, m, d = (int(x) for x in date.split("-"))
    return f"{y}年{m}月{d}日"


def doi_url(cand: dict) -> str:
    return f"https://doi.org/{cand['doi']}" if cand.get("doi") else \
        f"https://europepmc.org/article/PMC/{cand['pmcid']}"


def page(title: str, body: str, depth: int, description: str = DESCRIPTION) -> str:
    up = "../" * depth
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{esc(title)}</title>'
            f'<meta name="description" content="{esc(description)}">'
            f'<meta property="og:title" content="{esc(title)}">'
            f'<meta property="og:description" content="{esc(description)}">'
            f'<meta property="og:type" content="website">'
            f'<link rel="icon" href="{up}assets/logo-mark.svg">'
            f'<link rel="preconnect" href="https://fonts.googleapis.com">'
            f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+JP:wght@400;700&display=swap">'
            f'<style>{CSS}</style></head><body><div class="page">{body}</div></body></html>')


def masthead(depth: int, right: str = "") -> str:
    up = "../" * depth
    return (f'<header class="mast"><div class="mast-row">'
            f'<a class="word" href="{up}index.html"><img src="{up}assets/logo-mark.svg" alt="">'
            f'<div><div class="name">{esc(brand.SITE_NAME)}</div><div class="sub">{esc(brand.SITE_NAME_EN)}</div></div></a>'
            f'<div class="vol">{right}</div></div><div class="rules"></div></header>')


def colophon(extra: str = "") -> str:
    return (f'<footer class="colophon"><p>{esc(brand.SITE_NAME)} — 構造生物学者のための毎日の新着構造。</p>'
            f'{extra}<p>要約は AI が論文本文から作成し、編集者が確認している。数値・ID・リガンドは PDB / UniProt から取り、'
            f'構造図は PDB の座標から描いている。「原文」は論文本文からの引用。</p><p>β版</p></footer>')


# ---------- one article ----------

def quotes(evidence: list) -> str:
    return "".join(f'<p class="src"><b>原文</b><i>“{esc(e["quote"])}”</i><span>{esc(e.get("section", ""))}</span></p>'
                   for e in evidence or [])


def margin_html(cand: dict, pick: dict, n: int) -> str:
    own = cand["own_structures"]
    rel = [s for s in own if s["status"] == "released"]
    method = render.method_line(cand)
    if pick.get("method_label") and not rel:
        method = pick["method_label"]
    if own:
        links = []
        for s in own[:6]:
            tail = f"  {s['resolution']:.2f} Å" if s.get("resolution") else ("" if s["status"] == "released" else "  (未公開)")
            links.append(f'<a class="pdb" href="https://www.rcsb.org/structure/{esc(s["pdb_id"])}">{esc(s["pdb_id"])}{tail}</a>')
        if len(own) > 6:
            links.append(f"<span>ほか {len(own) - 6} 件</span>")
        pdb_dd = "".join(links)
    elif cand["kind"] == "new_map_only" and cand.get("emdb_ids"):
        pdb_dd = "".join(f'<a class="pdb" href="https://www.ebi.ac.uk/emdb/{esc(e)}">{esc(e)}</a>' for e in cand["emdb_ids"][:6])
    else:
        pdb_dd = "補足資料に記載"
    pdb_label = "EMDB" if not own and cand["kind"] == "new_map_only" else "PDB"
    prots = cand.get("proteins", [])
    uni = ", ".join(f'<a href="https://www.uniprot.org/uniprotkb/{esc(p["uniprot"])}">{esc(p.get("gene") or p["uniprot"])}</a>'
                    for p in prots[:4])
    orgs = sorted({o for s in rel for q in s.get("polymers", []) for o in q["organisms"]})
    first = any(p.get("first_structure") for p in prots)
    rows = [("Target", esc(pick["target_label"])), ("Method", esc(method)), (pdb_label, pdb_dd),
            ("Ligand", esc(pick.get("ligand_label") or render.ligand_line(cand)))]
    if uni:
        rows.append(("UniProt", uni))
    if orgs:
        rows.append(("Organism", "<i>" + esc(", ".join(orgs[:3])) + "</i>"))
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    if first:
        dl += '<dd class="first"><span class="mk">PDB 初構造</span></dd>'
    return f'<aside class="margin"><div class="sticky"><div class="no">{pad(n)}</div><dl>{dl}</dl></div></aside>'


def figures_html(cand: dict, pick: dict, date: str) -> str:
    pm = cand["pmcid"]
    fig_dir = ISSUES_DIR / date / "figures"
    if not (fig_dir / f"{pm}.svg").exists():
        return ""
    info = read_json(fig_dir / f"{pm}.json") if (fig_dir / f"{pm}.json").exists() else {}
    out = [f'<img class="fig" src="../figures/{date}/{esc(pm)}.svg" alt="構造図：{esc(pick["target_label"])}" loading="lazy">']
    items = [f'<span><i style="background:{esc(x["color"])}"></i>{esc(x["label"])}</span>' for x in info.get("legend", [])]
    items += [f'<span><i class="stick" style="background:{esc(x["color"])}"></i>{esc(x["label"])}</span>'
              for x in info.get("ligands", [])]
    if items:
        out.append(f'<p class="key">{"".join(items)}</p>')
    st = info.get("site")
    if st and (fig_dir / f"{pm}-site.svg").exists():
        rows = [("結合部位", esc(st["label"]) + (f'（{esc("・".join(st["neighbours"]))} と隣接）' if st.get("neighbours") else "")),
                (f"{render.CONTACT_NOTE}（{len(st['residues'])}）", esc(" ".join(st["residues"])))]
        if st.get("polar"):
            rows.append(("極性接触", f'<span class="dash"></span>N・O が 3.3 Å 以内の組 {st["polar"]}'))
        dl = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in rows)
        out.append(f'<div class="site"><img src="../figures/{date}/{esc(pm)}-site.svg" '
                   f'alt="{esc(st["label"])}の結合部位" loading="lazy"><dl>{dl}</dl></div>')
    return "".join(out)


def article_html(cand: dict, pick: dict, date: str, n: int) -> str:
    s = pick["summary"]
    lic = esc((cand.get("license") or "ライセンス不明").upper())
    hls = "".join(f'<div class="hl"><p>{esc(h["text"])}</p>{quotes(h.get("evidence"))}</div>'
                  for h in pick["highlights"])
    notes = pick.get("lab_notes") or {}
    filled = [(LAB_NOTE_FIELDS[k], notes[k]) for k in LAB_NOTE_FIELDS if notes.get(k)]
    lab = ""
    if filled:
        rows = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in filled)
        ev = quotes(notes.get("evidence"))
        lab = f'<h3>実験メモ</h3><dl class="lab">{rows}</dl>' + (f'<div class="lab-src">{ev}</div>' if ev else "")
    text = (f'<div class="text">'
            f'<p class="kicker"><b>{esc(cand["journal"] or cand["journal_abbrev"])}</b> · {esc(cand["pub_date"])} · '
            f'{lic} · <a href="{doi_url(cand)}">論文へ</a></p>'
            f'<h2>{esc(cand["title"])}</h2>'
            f'{figures_html(cand, pick, date)}'
            f'<p class="headline">{esc(pick["headline"])}</p>'
            f'<p class="sum"><b>背景</b>{esc(s["background"])}</p>'
            f'<p class="sum"><b>やったこと</b>{esc(s["approach"])}</p>'
            f'<p class="sum"><b>分かったこと</b>{esc(s["findings"])}</p>'
            f'<h3>読みどころ</h3>{hls}{lab}</div>')
    return f'<article class="art" id="a{n}">{margin_html(cand, pick, n)}{text}</article>'


# ---------- pages ----------

def load_issue(date: str) -> tuple[dict, list[tuple[dict, dict]]]:
    d = ISSUES_DIR / date
    meta = read_json(d / "candidates.json")
    cands = {c["pmcid"]: c for c in meta["candidates"]}
    picks = [(cands[pk["pmcid"]], pk) for pk in (read_json(p) for p in sorted((d / "picks").glob("*.json")))]
    picks.sort(key=lambda cp: cp[1].get("rank", 99))
    return meta, picks


def toc_html(picks: list[tuple[dict, dict]], href: str = "", label: str = "Contents") -> str:
    items = "".join(f'<li><span class="n">{pad(n)}</span><a class="t" href="{href}#a{n}">{esc(pk["target_label"])}</a>'
                    f'<span class="lead">{esc(pk["headline"])}</span></li>'
                    for n, (_, pk) in enumerate(picks, 1))
    return f'<h2 class="label">{label}</h2><ol>{items}</ol>'


def issue_page(date: str, prev_date: str | None, next_date: str | None) -> str:
    meta, picks = load_issue(date)
    picked = {pk["pmcid"] for _, pk in picks}
    rest = [x for x in meta["candidates"] if x["kind"] in render.KIND_JA and x["pmcid"] not in picked]
    body = [masthead(1, f"{jp_date(date)}号<br>新しい構造 {len(picks) + len(rest)} 件"),
            f'<section class="toc">{toc_html(picks)}</section>']
    body += [article_html(cand, pk, date, n) for n, (cand, pk) in enumerate(picks, 1)]
    if rest:
        items = "".join(f'<li><a href="{doi_url(x)}">{esc(x["title"])}</a> — <i>{esc(x["journal_abbrev"] or x["journal"])}</i>'
                        f'（{esc(render.KIND_JA[x["kind"]])}）</li>' for x in rest)
        body.append(f'<section class="rest"><h2 class="label">記事にしていない新しい構造</h2><ul>{items}</ul></section>')
    prev_link = f'<a href="{prev_date}.html">← {jp_date(prev_date)}号</a>' if prev_date else "<span></span>"
    next_link = f'<a href="{next_date}.html">{jp_date(next_date)}号 →</a>' if next_date else "<span></span>"
    body.append(f'<nav class="issue-nav">{prev_link}<a href="../index.html">号の一覧</a>{next_link}</nav>')
    body.append(colophon(f'<p>この号の対象：Europe PMC に {esc(meta["index_date"])} に登録されたオープンアクセス論文 '
                         f'{meta["counts"]["parsed"]} 本のうち、新しい構造を報告した論文。</p>'))
    desc = "、".join(pk["target_label"] for _, pk in picks[:6])
    return page(f"{jp_date(date)}号 · {brand.SITE_NAME}", "".join(body), depth=1,
                description=f"{jp_date(date)}の新しい構造：{desc}")


def home(issues: list[str]) -> str:
    body = [masthead(0, "毎朝更新"), f'<p class="intro">{esc(DESCRIPTION)}</p>']
    for date in issues:
        _, picks = load_issue(date)
        link = f'<a href="issues/{date}.html">{date.replace("-", ".")}</a>'
        body.append(f'<section class="toc home-issue">{toc_html(picks, f"issues/{date}.html", link)}</section>')
    body.append(colophon())
    return page(f"{brand.SITE_NAME}（{brand.SITE_NAME_EN}）", "".join(body), depth=0)


def build() -> int:
    issues = sorted([d.name for d in ISSUES_DIR.iterdir()
                     if (d / "candidates.json").exists() and (d / "picks").exists()
                     and any((d / "picks").glob("*.json"))], reverse=True)
    if not issues:
        print("no built issues found")
        return 1
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    (SITE_DIR / "assets").mkdir(parents=True)
    (SITE_DIR / "issues").mkdir()
    for name in ("logo.svg", "logo-mark.svg"):
        shutil.copy(ROOT / "assets" / name, SITE_DIR / "assets" / name)
    for i, date in enumerate(issues):
        prev_date = issues[i + 1] if i + 1 < len(issues) else None
        next_date = issues[i - 1] if i > 0 else None
        (SITE_DIR / "issues" / f"{date}.html").write_text(issue_page(date, prev_date, next_date), encoding="utf-8")
        src = ISSUES_DIR / date / "figures"
        if src.exists():
            shutil.copytree(src, SITE_DIR / "figures" / date, ignore=shutil.ignore_patterns("*.json"))
    (SITE_DIR / "index.html").write_text(home(issues), encoding="utf-8")
    print(f"built site/ with {len(issues)} issue(s): {', '.join(issues)}")
    return 0


if __name__ == "__main__":
    sys.exit(build())
