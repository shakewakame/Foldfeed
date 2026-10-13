"""Build the static website from the built issues.

    python3 -m pipeline.site                         # writes site/ (index + one page per issue)
    python3 -m pipeline.site --preview DIR           # also preview pages for DIR/<date>/ (open PRs)

Reads every data/issues/<date>/ that has candidates.json and a picks/ folder and writes a
self-contained site under site/ (figures and logo copied in) for GitHub Pages. With --preview,
issues not yet merged (copied out of open pull requests by the Pages workflow) are rendered to
site/preview/<date>.html, unlisted and marked as previews, so the editor can see them before merging.

Layout, settled with the owner in October 2026 (docs/design-feedback.md): a journal-like page
on a cool grey ground in indigo ink with a single vermilion accent. Each article has a margin
column for the data (number, target, method, PDB, ligand, UniProt, organism) and a text column:
the paper's title, the protein figure and its key, the binding-site close-up, the headline,
the three-part summary, 読みどころ with the original sentences always shown, and the lab notes.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
from pathlib import Path

from . import brand, impact, render
from .common import ISSUES_DIR, ROOT, read_json
from .schema import FEATURE_AXES, FEATURE_MIN, LAB_NOTE_FIELDS

SITE_DIR = ROOT / "site"
TH = brand.THEME
DESCRIPTION = "新着の査読済みオープンアクセス論文から、構造生物学論文を、毎日紹介するサイト。"

CSS = f"""
:root {{
  --ground:{TH.ground}; --paper:{TH.paper}; --ink:{TH.ink}; --ink2:{TH.ink2}; --mute:{TH.muted};
  --desc:{TH.desc}; --hair:{TH.hair}; --acc:{TH.accent}; --acc-ink:{TH.accent_ink};
  --serif:"Noto Serif JP","Hiragino Mincho ProN","Yu Mincho",YuMincho,"BIZ UDMincho",Georgia,serif;
  --latin:Georgia,"Times New Roman","Noto Serif JP",serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --col:240px; --side:320px; --pad:48px;
}}
* {{ box-sizing:border-box; }}
html, body {{ margin:0; background:var(--ground); }}
body {{ color:var(--ink); font-family:var(--serif); font-size:16px; line-height:1.95;
        -webkit-font-smoothing:antialiased; }}
a {{ color:inherit; text-decoration:none; text-underline-offset:3px; }}
h2, h3, .headline, .subhead, .sub {{ word-break:auto-phrase; line-break:strict; }}
a:hover {{ color:var(--acc-ink); text-decoration:underline; text-decoration-color:var(--acc); }}
.kicker a {{ text-decoration:underline; text-decoration-color:var(--acc); }}
.wrap {{ max-width:1800px; margin:0 auto; padding:0 var(--pad); }}
.mk {{ color:var(--acc-ink); font-weight:700; }}
.label {{ margin:0; font-family:var(--mono); font-size:12px; letter-spacing:.14em; font-weight:500;
          color:var(--mute); text-transform:uppercase; padding-top:6px; }}
.label a {{ text-decoration:none; }}

/* masthead: a heavy rule over a thin one, running the full width of the window */
.mast {{ padding-top:30px; }}
.mast-row {{ display:flex; align-items:flex-end; justify-content:space-between; gap:24px; }}
.word {{ display:flex; align-items:center; gap:15px; text-decoration:none; }}
.word img {{ width:46px; height:46px; display:block; }}
.word .name {{ font-family:var(--latin); font-size:42px; line-height:1; letter-spacing:-0.02em; font-weight:700; margin:0; }}
.word .sub {{ font-size:13px; letter-spacing:.12em; color:var(--mute); margin-top:7px; line-height:1.2; }}
.word .tag {{ font-size:13px; color:var(--desc); margin-top:6px; line-height:1.5; }}
.vol {{ font-size:14px; text-align:right; color:var(--ink2); line-height:1.7; }}
.vol a {{ text-decoration:underline; text-decoration-color:var(--acc); }}
.rules {{ margin-top:18px; border-top:4px solid var(--ink); }}
.rules::after {{ content:""; display:block; margin-top:3px; border-top:1px solid var(--ink); }}
.intro {{ color:var(--desc); font-size:14.5px; margin-top:18px; }}

/* shell: the main column and a sticky sidebar on the right */
.shell {{ display:grid; grid-template-columns:minmax(0,1fr) var(--side); gap:0 56px; align-items:stretch; }}
.main {{ min-width:0; padding-bottom:30px; }}
.side {{ border-left:1px solid var(--hair); padding-left:32px; padding-top:40px; padding-bottom:30px; }}
.sticky-side .side-in {{ position:sticky; top:20px; max-height:calc(100vh - 40px); overflow-y:auto; scrollbar-width:thin; }}
.sd {{ margin:0 0 28px; }}
.sd + .sd {{ padding-top:24px; border-top:1px solid var(--hair); }}
.sd .label {{ padding-top:0; margin-bottom:10px; }}
.sd p {{ margin:0 0 8px; font-size:13.5px; line-height:1.85; color:var(--desc); }}
.sd-issue {{ display:block; font-size:19px; font-weight:700; line-height:1.5; }}
.sd-meta {{ font-family:var(--mono); font-size:12px !important; color:var(--ink2) !important; line-height:1.8 !important; }}
.jl {{ list-style:none; margin:0; padding:0; font-size:14px; line-height:1.55; }}
.jl li {{ display:flex; justify-content:space-between; align-items:baseline; gap:14px; padding:5px 0;
          border-bottom:1px dotted var(--hair); }}
.jl .c {{ font-family:var(--mono); font-size:12px; color:var(--mute); white-space:nowrap; }}
.jl-more {{ font-family:var(--mono); font-size:11.5px; color:var(--mute); margin-top:8px; }}
.nums {{ display:grid; grid-template-columns:1fr 1fr; gap:16px 18px; margin:0; }}
.nums div {{ min-width:0; }}
.nums dt {{ font-family:var(--mono); font-size:10.5px; letter-spacing:.1em; color:var(--mute); }}
.nums dd {{ margin:2px 0 0; font-family:var(--latin); font-size:30px; line-height:1.1; letter-spacing:-0.02em; }}
.sd-toc a {{ display:grid; grid-template-columns:28px minmax(0,1fr); gap:6px; padding:5px 0; font-size:13.5px;
             line-height:1.6; color:var(--ink2); }}
.sd-toc a b {{ font-family:var(--mono); font-size:11.5px; color:var(--acc-ink); padding-top:1px; }}
.sd-toc h3 {{ margin:14px 0 4px; font-family:var(--mono); font-size:11px; letter-spacing:.12em; font-weight:500; color:var(--mute); }}
.sd-toc h3:first-of-type {{ margin-top:0; }}
.sd-nav {{ display:flex; flex-direction:column; gap:4px; font-family:var(--mono); font-size:12.5px; }}

/* contents: one table row per article: number, headline + supplement, TARGET / METHOD / RES., the real structure, PDB */
.toc {{ margin-top:48px; }}
.ix-h {{ display:flex; justify-content:space-between; align-items:baseline; gap:16px; border-top:1px solid var(--ink);
         padding-top:12px; }}
.ix-h .label {{ padding-top:0; }}
.ix-h span {{ font-family:var(--mono); font-size:12px; letter-spacing:.08em; color:var(--mute); }}
.ix-h.hot {{ margin-top:44px; }}
.rows {{ list-style:none; margin:0; padding:0; }}
.row {{ display:grid; grid-template-columns:60px minmax(0,1fr) 220px 168px 112px; grid-template-areas:"no rt rf th rp";
        gap:0 26px; align-items:center; padding:20px 0; border-top:1px solid var(--hair); }}
.rows .row:first-child {{ border-top:0; }}
.row .no {{ grid-area:no; font-family:var(--latin); font-size:30px; line-height:1; letter-spacing:-0.02em; color:var(--acc-ink); }}
.row .rt {{ grid-area:rt; min-width:0; }}
.row .rf {{ grid-area:rf; }}
.row .thumb {{ grid-area:th; }}
.row .rp {{ grid-area:rp; }}
.rk {{ display:none; margin:0 0 3px; font-family:var(--mono); font-size:11.5px; letter-spacing:.04em; color:var(--mute); line-height:1.6; }}
.rk .n {{ color:var(--acc-ink); font-weight:700; margin-right:10px; }}
.row h3 {{ margin:0; font-size:22px; line-height:1.5; font-weight:700; color:var(--ink); text-wrap:balance; }}
.row .sub {{ margin:3px 0 0; font-size:15px; line-height:1.75; color:var(--desc); }}
.row .rm {{ margin:5px 0 0; font-family:var(--mono); font-size:11px; color:var(--mute); line-height:1.6; }}
.thumb {{ display:block; border:1px solid var(--hair); }}
.thumb img {{ display:block; width:100%; height:auto; }}
.thumb.none {{ aspect-ratio:3/2; display:flex; align-items:center; justify-content:center;
               font-family:var(--mono); font-size:11px; color:var(--mute); text-align:center; padding:8px; }}
.rf, .hf {{ margin:0; font-family:var(--mono); }}
.rf div {{ display:grid; grid-template-columns:62px minmax(0,1fr); gap:0 8px; padding:2px 0; }}
.rf dt, .hf dt {{ font-size:10.5px; letter-spacing:.1em; color:var(--mute); padding-top:2px; }}
.rf dd, .hf dd {{ margin:0; font-size:12px; line-height:1.6; color:var(--ink2); overflow-wrap:anywhere; }}
.rp {{ font-family:var(--mono); line-height:1.5; }}
.rp .pl {{ display:block; font-size:10.5px; letter-spacing:.1em; color:var(--mute); }}
.rp b {{ display:block; font-size:15px; font-weight:500; color:var(--ink); }}
.rp .mk {{ display:block; margin-top:4px; font-family:var(--serif); font-size:12px; }}
.more {{ margin:8px 0 0; font-family:var(--mono); font-size:13px; }}
.more a {{ text-decoration:underline; text-decoration-color:var(--acc); text-underline-offset:4px; }}
.hero {{ margin-top:40px; }}
.hero > .label {{ color:var(--acc-ink); display:block; padding-top:0; margin-bottom:16px; }}
.hero > .label span {{ color:var(--mute); }}
.hero-grid {{ display:grid; grid-template-columns:minmax(0,40fr) minmax(0,60fr); gap:44px; align-items:center; }}
.hero h3 {{ margin:0; font-size:38px; line-height:1.38; font-weight:700; color:var(--ink); text-wrap:balance; }}
.hero .sub {{ margin:14px 0 0; font-size:18px; line-height:1.75; color:var(--desc); }}
.hf {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px 22px; margin:22px 0 0; padding-top:16px;
       border-top:1px solid var(--hair); }}
.hf dd {{ font-size:12.5px; }}
.hf a {{ text-decoration:underline; text-decoration-color:var(--acc); }}
.hero .go {{ margin:20px 0 0; font-family:var(--mono); font-size:13px; }}
.hero .go a {{ text-decoration:underline; text-decoration-color:var(--acc); text-underline-offset:4px; }}
.hero .thumb img {{ width:100%; }}
.past {{ margin-top:56px; }}
.past ol {{ list-style:none; margin:14px 0 0; padding:0; }}
.past li {{ display:grid; grid-template-columns:110px minmax(0,1fr); gap:14px; margin:0 0 14px; align-items:baseline; }}
.past .d {{ font-family:var(--mono); font-size:12.5px; }}
.past .t {{ font-size:14.5px; color:var(--desc); line-height:1.7; }}
.past .t b {{ font-family:var(--mono); font-size:11px; font-weight:500; color:var(--mute); margin-right:10px; }}
.part {{ margin-top:80px; position:relative; padding-top:20px; }}
.part::before {{ content:""; position:absolute; left:0; right:0; top:0; border-top:4px solid var(--ink); }}
.part::after {{ content:""; position:absolute; left:0; right:0; top:7px; border-top:1px solid var(--ink); }}
.part h2 {{ font-size:27px; line-height:1.4; margin:8px 0 4px; display:flex; align-items:baseline; gap:16px; }}
.part h2 span {{ font-family:var(--mono); font-size:12px; font-weight:500; color:var(--mute); letter-spacing:.08em; }}
.part p {{ margin:0; color:var(--desc); font-size:14px; max-width:760px; }}
.part + .art {{ margin-top:18px; }}
.part + .art::before {{ display:none; }}

/* article: margin column for the data, text column for the story */
.art {{ display:grid; grid-template-columns:var(--col) minmax(0,1fr); gap:28px; padding-top:60px; padding-bottom:24px;
        margin-top:56px; position:relative; }}
.art::before {{ content:""; position:absolute; left:0; right:0; top:0; border-top:1px solid var(--ink); }}
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

/* figures: transparent, on the page ground; the legend and a 3D viewer follow */
.v3d-view {{ position:relative; border-top:1px solid var(--hair); border-bottom:1px solid var(--hair); }}
.fig {{ display:block; width:100%; height:auto; margin:0; }}
.v3d-canvas {{ display:none; position:absolute; inset:0; }}
.v3d.on .v3d-canvas {{ display:block; }}
.v3d.on .v3d-view {{ aspect-ratio:1200/560; }}
.v3d.on .fig {{ visibility:hidden; height:100%; object-fit:contain; }}
.v3d-bar {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px 12px; margin:10px 0 0; font-family:var(--mono);
            font-size:12px; color:var(--ink2); }}
.v3d-bar button {{ font:inherit; letter-spacing:.02em; background:transparent; color:var(--ink); border:1px solid var(--ink);
                   padding:6px 13px; cursor:pointer; line-height:1.4; }}
.v3d-bar button:hover {{ color:var(--acc-ink); border-color:var(--acc); }}
.v3d-bar button.go {{ background:var(--ink); color:#fff; }}
.v3d-bar button.go:hover {{ background:var(--acc-ink); border-color:var(--acc-ink); color:#fff; }}
.v3d-bar a {{ text-decoration:underline; text-decoration-color:var(--acc); }}
.v3d-bar .help {{ color:var(--mute); flex-basis:100%; line-height:1.7; }}
.v3d:not(.on) .on-only, .v3d.on .off-only {{ display:none; }}
.v3d-now {{ color:var(--acc-ink); }}
.v3d-wait {{ display:none; position:absolute; inset:0; z-index:2; flex-direction:column; align-items:center;
             justify-content:center; gap:8px; padding:16px; text-align:center; font-family:var(--mono); font-size:13px;
             line-height:1.7; color:var(--ink); background:rgba(240,241,239,.92); }}
.v3d-wait b {{ font-weight:600; }}
.v3d-wait .secs {{ color:var(--mute); }}
.v3d.loading .v3d-wait {{ display:flex; }}
.v3d.loading .v3d-view {{ aspect-ratio:1200/560; }}
.v3d.loading .v3d-canvas {{ display:block; visibility:hidden; }}
.v3d.loading .fig {{ height:100%; object-fit:contain; }}
.v3d:not(.loading) .loading-only, .v3d.loading .off-only, .v3d.loading .on-only {{ display:none; }}
.v3d-msg {{ margin:8px 0 0; font-family:var(--mono); font-size:12px; color:var(--acc-ink); }}
.v3d .msp-state-snapshot-viewport-controls {{ display:none; }}
.legend {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:16px 44px; margin:18px 0 0; }}
.legend h4 {{ margin:0 0 4px; font-family:var(--mono); font-size:11px; letter-spacing:.12em; font-weight:500; color:var(--mute); }}
.legend ul {{ list-style:none; margin:0; padding:0; }}
.legend li {{ display:grid; grid-template-columns:24px minmax(0,1fr); gap:0 8px; align-items:baseline; padding:3px 0;
              line-height:1.65; }}
.legend i {{ display:block; width:14px; height:10px; align-self:center; }}
.legend i.stick {{ height:4px; width:18px; border-radius:2px; transform:rotate(-24deg); }}
.legend b {{ font-size:14.5px; font-weight:700; }}
.legend b code {{ font-family:var(--mono); font-size:11px; font-weight:500; color:var(--mute); margin-left:8px; }}
.legend .en {{ display:block; font-family:var(--mono); font-size:11.5px; color:var(--ink2); overflow-wrap:anywhere; }}
.pdbnote {{ margin:8px 0 0; font-family:var(--mono); font-size:11.5px; color:var(--ink2); }}
.site {{ margin:34px 0 0; }}
.site img {{ display:block; width:100%; height:auto; border-top:1px solid var(--hair); border-bottom:1px solid var(--hair); }}
.site dl {{ display:grid; grid-template-columns:auto minmax(0,1fr) auto; gap:4px 26px; margin:8px 0 0;
            font-family:var(--mono); font-size:11.5px; line-height:1.7; color:var(--ink2); }}
.site dl div {{ min-width:0; }}
.site dt {{ color:var(--mute); letter-spacing:.1em; font-size:10px; }}
.site dd {{ margin:0; }}
.site .dash {{ display:inline-block; width:22px; border-top:2px dashed var(--ink); vertical-align:4px; margin-right:6px; }}
.art .headline {{ font-size:22px; font-weight:700; line-height:1.55; margin:34px 0 4px; text-wrap:balance; }}
.art .subhead {{ font-size:16px; line-height:1.8; color:var(--desc); margin:0 0 18px; }}
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
.rest::before {{ content:""; position:absolute; left:0; right:0; top:0; border-top:1px solid var(--ink); }}
.rest ul {{ margin:10px 0 0; padding-left:1.2em; font-size:14.5px; }}

.preview-bar {{ background:var(--acc); color:#fff; font-family:var(--mono); font-size:12.5px;
                letter-spacing:.04em; padding:9px 28px; text-align:center; }}
.preview-bar a {{ color:#fff; text-decoration:underline; }}
.queue {{ list-style:none; margin:30px 0 0; padding:0; }}
.queue li {{ padding:18px 0; border-bottom:1px solid var(--hair); }}
.queue .d {{ font-size:20px; font-weight:700; }}
.queue .c {{ color:var(--desc); font-size:14px; margin:2px 0 10px; }}
.queue .btns {{ display:flex; flex-wrap:wrap; gap:10px; }}
.queue .btns a {{ display:inline-block; padding:9px 16px; border:1px solid var(--ink); font-family:var(--mono); font-size:13px; }}
.queue .btns a.go {{ background:var(--ink); color:#fff; }}
.issue-nav {{ display:flex; justify-content:space-between; gap:16px; margin-top:70px; font-family:var(--mono);
              font-size:12px; color:var(--ink2); }}
.colo {{ margin-top:22px; border-top:4px solid var(--ink); }}
.colophon {{ padding-top:16px; padding-bottom:70px; font-size:12.5px; color:var(--mute); line-height:1.8; }}
.colophon p {{ margin:0 0 6px; max-width:760px; }}

@media (min-width:1500px) {{
  body {{ font-size:17px; }}
}}
@media (max-width:1560px) {{
  :root {{ --col:200px; --side:290px; --pad:36px; }}
  .shell {{ gap:0 40px; }}
  .side {{ padding-left:26px; }}
  .hero-grid {{ grid-template-columns:minmax(0,46fr) minmax(0,54fr); gap:32px; }}
  .hero h3 {{ font-size:30px; }}
  .hero .sub {{ font-size:16.5px; }}
  .hf {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
  .row {{ grid-template-columns:44px minmax(0,1fr) 190px 132px 92px; gap:0 20px; }}
  .row .no {{ font-size:24px; }}
  .row h3 {{ font-size:20px; }}
  .margin .no {{ font-size:64px; }}
}}
@media (max-width:1120px) {{
  .shell {{ grid-template-columns:minmax(0,1fr); }}
  .side {{ border-left:0; padding-left:0; border-top:4px solid var(--ink); margin-top:20px; }}
  .sticky-side .side-in {{ position:static; max-height:none; overflow:visible; }}
  .side-only-issue {{ display:none; }}
}}
@media (max-width:860px) {{
  :root {{ --pad:18px; }}
  .part h2 {{ font-size:23px; }}
  .word .name {{ font-size:32px; }}
  .mast-row {{ flex-wrap:wrap; align-items:flex-start; }}
  .vol {{ text-align:left; }}
  .art {{ grid-template-columns:minmax(0,1fr); gap:14px; }}
  .row {{ grid-template-columns:116px minmax(0,1fr); grid-template-areas:"th rt" "th rf" "th rp"; gap:4px 14px;
          align-items:start; padding:16px 0; }}
  .row .no {{ display:none; }}
  .rk {{ display:block; }}
  .row h3 {{ font-size:18px; }}
  .row .sub {{ font-size:13.5px; }}
  .rf div {{ grid-template-columns:54px minmax(0,1fr); }}
  .rp b, .rp .pl {{ display:inline; margin-right:8px; }}
  .hero-grid {{ grid-template-columns:minmax(0,1fr); gap:16px; }}
  .hf {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
  .hero h3 {{ font-size:26px; }}
  .hero .sub {{ font-size:15.5px; }}
  .past li {{ grid-template-columns:96px minmax(0,1fr); }}
  .margin .no {{ font-size:52px; margin-bottom:10px; }}
  .margin dl {{ display:grid; grid-template-columns:78px minmax(0,1fr); gap:3px 12px; }}
  .margin dt {{ margin-top:0; padding-top:2px; }}
  .margin .pdb {{ display:inline; margin-right:12px; }}
  .margin .first {{ grid-column:2; margin-top:4px; }}
  .margin .sticky {{ position:static; }}
  .art h2 {{ font-size:21px; }}
  .art .headline {{ font-size:19px; }}
  .art .subhead {{ font-size:15px; }}
  .site dl {{ grid-template-columns:minmax(0,1fr); }}
  .lab div {{ grid-template-columns:92px 1fr; }}
  .legend {{ grid-template-columns:minmax(0,1fr); }}
}}
"""


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def pad(n: int) -> str:
    return f"{n:02d}"


def jp_date_short(date: str) -> str:
    _, m, d = (int(x) for x in date.split("-"))
    return f"{m}月{d}日"


def jp_date(date: str) -> str:
    y, m, d = (int(x) for x in date.split("-"))
    return f"{y}年{m}月{d}日"


def doi_url(cand: dict) -> str:
    return f"https://doi.org/{cand['doi']}" if cand.get("doi") else \
        f"https://europepmc.org/article/PMC/{cand['pmcid']}"


def page(title: str, body: str, depth: int, description: str = DESCRIPTION, noindex: bool = False,
         viewer: bool = False) -> str:
    up = "../" * depth
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{esc(title)}</title>'
            + ('<meta name="robots" content="noindex">' if noindex else "") +
            f'<meta name="description" content="{esc(description)}">'
            f'<meta property="og:title" content="{esc(title)}">'
            f'<meta property="og:description" content="{esc(description)}">'
            f'<meta property="og:type" content="website">'
            f'<link rel="icon" href="{up}assets/logo-mark.svg">'
            f'<link rel="preconnect" href="https://fonts.googleapis.com">'
            f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+JP:wght@400;700&display=swap">'
            f'<style>{CSS}</style></head><body><div class="page">{body}</div>'
            + (f'<script src="{up}assets/viewer.js" defer></script>' if viewer else "") + '</body></html>')


def masthead(depth: int, right: str = "") -> str:
    """The wordmark row inside the page width; the double rule under it spans the whole window."""
    up = "../" * depth
    return (f'<header class="mast"><div class="wrap"><div class="mast-row">'
            f'<a class="word" href="{up}index.html"><img src="{up}assets/logo-mark.svg" alt="">'
            f'<div><div class="name">{esc(brand.SITE_NAME)}</div><div class="sub">{esc(brand.SITE_NAME_EN)}</div>'
            f'<div class="tag">{esc(DESCRIPTION)}</div></div></a>'
            f'<div class="vol">{right}</div></div></div><div class="rules"></div></header>')


def shell(main: str, side: str, sticky: bool = False) -> str:
    """Main column and right sidebar. Only the sidebar of an issue page (its contents) stays in view while scrolling."""
    return (f'<div class="wrap shell{" sticky-side" if sticky else ""}"><main class="main">{main}</main>'
            f'<aside class="side"><div class="side-in">{side}</div></aside></div>')


def colophon(extra: str = "") -> str:
    return (f'<footer class="colo"><div class="wrap colophon"><p>{esc(brand.SITE_NAME)} — 構造生物学者のための毎日の新着構造。</p>'
            f'{extra}<p>要約は AI が論文本文から作成し、編集者が確認している。数値・ID・リガンドは PDB / UniProt から取り、'
            f'構造図は PDB の座標から描いている。「原文」は論文本文からの引用。</p><p>β版</p></div></footer>')


def sd(label: str, inner: str, cls: str = "") -> str:
    """One block of the sidebar: a small label, then plain content (no cards)."""
    return f'<section class="sd {cls}"><h2 class="label">{label}</h2>{inner}</section>'


SMALL_WORDS = {"of", "and", "the", "in", "for", "on", "to", "a"}


def journal_name(cand: dict) -> str:
    """The journal as printed in the sidebar (Europe PMC writes some in lower case: 'Science translational medicine')."""
    name = (cand.get("journal") or cand.get("journal_abbrev") or "").strip()
    words = name.split(" ")
    if len(words) > 1 and all(w[:1].islower() for w in words[1:] if w.lower() not in SMALL_WORDS):
        words = [words[0]] + [w if w.lower() in SMALL_WORDS else w.capitalize() for w in words[1:]]
    return " ".join(words)


def journal_counts(issues: list[str], root: Path = ISSUES_DIR) -> list[tuple[str, int]]:
    """Journals of every article so far, most articles first."""
    counts: dict[str, int] = {}
    for date in issues:
        _, picks, _ = load_issue(date, root)
        for cand, _ in picks:
            name = journal_name(cand)
            if name:
                counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))


JOURNAL_LIMIT = 16


def journals_html(counts: list[tuple[str, int]]) -> str:
    if not counts:
        return ""
    items = "".join(f'<li><span>{esc(n)}</span><span class="c">{c}</span></li>' for n, c in counts[:JOURNAL_LIMIT])
    more = (f'<p class="jl-more">ほか {len(counts) - JOURNAL_LIMIT} 誌</p>' if len(counts) > JOURNAL_LIMIT else "")
    return sd("掲載ジャーナル", f'<p>これまでに記事にした論文の掲載誌（記事数順）。</p><ul class="jl">{items}</ul>{more}')


# ---------- one article ----------

def quotes(evidence: list) -> str:
    return "".join(f'<p class="src"><b>原文</b><i>“{esc(e["quote"])}”</i><span>{esc(e.get("section", ""))}</span></p>'
                   for e in evidence or [])


def margin_html(cand: dict, pick: dict, n: int, impact_type: str | None = None) -> str:
    hot = render.is_impact(pick)
    own = cand["own_structures"] if not hot else cand.get("cited_structures", [])
    rel = [s for s in own if s["status"] == "released"]
    method = render.method_line(cand) if not hot else "—"
    if pick.get("method_label") and (hot or not rel):
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
        pdb_dd = "—" if hot else "補足資料に記載"
    pdb_label = "EMDB" if not own and cand["kind"] == "new_map_only" else ("PDB（使用）" if hot else "PDB")
    prots = cand.get("proteins", [])
    uni = ", ".join(f'<a href="https://www.uniprot.org/uniprotkb/{esc(p["uniprot"])}">{esc(p.get("gene") or p["uniprot"])}</a>'
                    for p in prots[:4])
    orgs = sorted({o for s in rel for q in s.get("polymers", []) for o in q["organisms"]})
    first = any(p.get("first_structure") for p in prots)
    rows = [("Target", esc(pick["target_label"])), ("Method", esc(method)), (pdb_label, pdb_dd),
            ("Ligand", esc(pick.get("ligand_label") or ("—" if hot else render.ligand_line(cand))))]
    if hot:
        rows.insert(0, ("注目の構造", esc(impact.TYPES.get(impact_type or "", "新しい構造以外"))))
    if uni:
        rows.append(("UniProt", uni))
    if orgs:
        rows.append(("Organism", "<i>" + esc(", ".join(orgs[:3])) + "</i>"))
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    if first:
        dl += '<dd class="first"><span class="mk">PDB 初構造</span></dd>'
    return f'<aside class="margin"><div class="sticky"><div class="no">{pad(n)}</div><dl>{dl}</dl></div></aside>'


def legend_html(info: dict) -> str:
    """What each colour is, for readers who cannot tell the proteins apart by shape: polymers, then ligands."""
    def item(swatch: str, label: str, code: str, desc: str) -> str:
        tail = f'<code>{esc(code)}</code>' if code else ""
        sub = f'<span class="en">{esc(desc)}</span>' if desc and desc != label else ""
        return f'<li>{swatch}<span><b>{esc(label)}{tail}</b>{sub}</span></li>'

    prot = "".join(item(f'<i style="background:{esc(x["color"])}"></i>', x["label"], "", x.get("desc", ""))
                   for x in info.get("legend", []))
    lig = "".join(item(f'<i class="stick" style="background:{esc(x["color"])}"></i>', x["label"],
                       "" if x["label"] == x["id"] else x["id"], x.get("desc", ""))
                  for x in info.get("ligands", []))
    groups = ((f'<section><h4>タンパク質・核酸</h4><ul>{prot}</ul></section>' if prot else "") +
              (f'<section><h4>リガンド</h4><ul>{lig}</ul></section>' if lig else ""))
    return f'<div class="legend">{groups}</div>' if groups else ""


def viewer_bar(info: dict, up: str) -> tuple[str, str, str]:
    """Attributes for .v3d, the "loading" overlay and the button bar (3D viewer, loaded on demand by assets/viewer.js)."""
    pdb = info.get("pdb_id")
    ents = [{"id": x["entity"], "color": x["color"]} for x in info.get("legend", []) if x.get("entity")]
    if not pdb or not ents:
        return "", "", ""
    ligs = [{"id": x["id"], "label": x["label"], "color": x["color"], "entity": x.get("entity", []),
             "sites": x.get("sites", [])} for x in info.get("ligands", [])]
    site = (info.get("site") or {}).get("ligand", "")
    attrs = (f' data-pdb="{esc(pdb)}" data-assets="{up}assets" data-site="{esc(site)}"'
             f' data-ents="{esc(json.dumps(ents))}" data-ligs="{esc(json.dumps(ligs, ensure_ascii=False))}"')
    wait = ('<div class="v3d-wait" role="status" aria-live="polite"><b>3D を読み込んでいます</b>'
            '<span class="step"></span><span class="secs"></span></div>')
    focus = ('<button type="button" data-v3d="site" class="on-only">結合部位へ（押すたびに次へ）</button>'
             if any(x["sites"] for x in ligs) else "")
    bar = (f'<div class="v3d-bar">'
           f'<button type="button" class="go off-only" data-v3d="open">3D で動かす</button>'
           f'<button type="button" class="loading-only" data-v3d="close">読み込みを中止</button>'
           f'{focus}<button type="button" data-v3d="reset" class="on-only">全体に戻す</button>'
           f'<button type="button" data-v3d="close" class="on-only">静止画に戻す</button>'
           f'<span class="v3d-now on-only"></span>'
           f'<a href="https://www.rcsb.org/3d-view/{esc(pdb)}">RCSB で開く ↗</a>'
           f'<span class="help off-only">PDB {esc(pdb)} を RCSB から読み込み、ドラッグで回転・クリックで拡大して周囲のアミノ酸を表示できる。</span>'
           f'<span class="help on-only">ドラッグで回転 · ホイールで拡大縮小 · 残基をクリックで拡大し、周囲のアミノ酸をスティックで表示。</span>'
           f'</div><p class="v3d-msg" hidden></p>')
    return attrs, wait, bar


def figures_html(cand: dict, pick: dict, date: str, root: Path, fig_base: str, up: str = "../") -> str:
    pm = cand["pmcid"]
    fig_dir = root / date / "figures"
    if not (fig_dir / f"{pm}.svg").exists():
        return ""
    info = read_json(fig_dir / f"{pm}.json") if (fig_dir / f"{pm}.json").exists() else {}
    attrs, wait, bar = viewer_bar(info, up)
    img = f'<img class="fig" src="{fig_base}/{date}/{esc(pm)}.svg" alt="構造図：{esc(pick["target_label"])}" loading="lazy">'
    out = [f'<div class="v3d"{attrs}><div class="v3d-view">{img}<div class="v3d-canvas"></div>{wait}</div>{bar}</div>']
    if render.is_impact(pick) and info.get("pdb_id"):
        out.append(f'<p class="pdbnote">PDB {esc(info["pdb_id"])}（論文が使った既存の構造）</p>')
    out.append(legend_html(info))
    st = info.get("site")
    if st and (fig_dir / f"{pm}-site.svg").exists():
        rows = [("結合部位", esc(st["label"]) + (f'（{esc("・".join(st["neighbours"]))} と隣接）' if st.get("neighbours") else "")),
                (f"{render.CONTACT_NOTE}（{len(st['residues'])}）", esc(" ".join(st["residues"])))]
        if st.get("polar"):
            rows.append(("極性接触", f'<span class="dash"></span>N・O が 3.3 Å 以内の組 {st["polar"]}'))
        dl = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in rows)
        out.append(f'<div class="site"><img src="{fig_base}/{date}/{esc(pm)}-site.svg" '
                   f'alt="{esc(st["label"])}の結合部位" loading="lazy"><dl>{dl}</dl></div>')
    return "".join(out)


def article_html(cand: dict, pick: dict, date: str, n: int, root: Path, fig_base: str,
                 impact_type: str | None = None, up: str = "../") -> str:
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
            f'{figures_html(cand, pick, date, root, fig_base, up)}'
            f'<p class="headline">{esc(pick["headline"])}</p>'
            f'<p class="subhead">{esc(pick.get("subhead") or "")}</p>'
            f'<p class="sum"><b>背景</b>{esc(s["background"])}</p>'
            f'<p class="sum"><b>やったこと</b>{esc(s["approach"])}</p>'
            f'<p class="sum"><b>分かったこと</b>{esc(s["findings"])}</p>'
            f'<h3>読みどころ</h3>{hls}{lab}</div>')
    return f'<article class="art" id="a{n}">{margin_html(cand, pick, n, impact_type)}{text}</article>'


# ---------- pages ----------

def load_issue(date: str, root: Path = ISSUES_DIR) -> tuple[dict, list[tuple[dict, dict]], dict[str, str]]:
    """Meta, the articles in page order (new structures, then 注目の構造), and each 注目の構造's type."""
    d = root / date
    meta = read_json(d / "candidates.json")
    cands = {c["pmcid"]: c for c in meta["candidates"]}
    picks = [(cands[pk["pmcid"]], pk) for pk in (read_json(p) for p in sorted((d / "picks").glob("*.json")))
             if pk.get("pmcid") in cands]
    picks.sort(key=lambda cp: cp[1].get("rank", 99))
    new, hot = render.split_sections(picks)
    types = {s["pmcid"]: s.get("type") for s in render.load_impact(d).get("scored", [])}
    return meta, new + hot, types


PARTS = {
    "new": ("新しい構造", "この号で報告された、PDB / EMDB に寄託された新しい構造。"),
    "impact": ("注目の構造", "新しい構造ではないが、実験で決めた構造で確かめた注目の研究"
                            "（既存構造による機構の解明・予測やデザインの実証・新しい手法・構造に基づく創薬）。"),
}


def load_feature(date: str, root: Path, picks: list) -> dict | None:
    """feature.json (今日の一本), when the issue has enough articles and the pick is among them."""
    path = root / date / "feature.json"
    if not path.exists() or len(picks) < FEATURE_MIN:
        return None
    f = read_json(path)
    return f if any(c["pmcid"] == f.get("pmcid") for c, _ in picks) else None


def thumb_html(cand: dict, pick: dict, date: str, root: Path, fig_base: str, href: str) -> str:
    """The structure thumbnail (a real PDB structure, drawn by pipeline.render) as a link; never invented art."""
    pm = cand["pmcid"]
    if (root / date / "figures" / f"{pm}-thumb.svg").exists():
        return (f'<a class="thumb" href="{href}"><img src="{fig_base}/{date}/{esc(pm)}-thumb.svg" width="600" height="400" '
                f'alt="構造図：{esc(pick["target_label"])}" loading="lazy"></a>')
    own = cand["own_structures"]
    why = ("PDB 公開待ち" if own and not any(x["status"] == "released" for x in own)
           else "PDB ID は補足資料" if cand["kind"] == "new_structure_ids_unlisted"
           else "EMDB マップのみ" if cand["kind"] == "new_map_only" else "構造図なし")
    return f'<a class="thumb none" href="{href}">{why}</a>'


def facts(cand: dict, pick: dict) -> dict:
    """The few facts shown beside a headline: target, method, resolution, PDB (the structure of the figure),
    ligand, UniProt, and whether it is the protein's first structure. All from the pick and PDB data."""
    hot = render.is_impact(pick)
    own = cand["own_structures"] if not hot else cand.get("cited_structures", [])
    rel = [x for x in own if x["status"] == "released"]
    line = render.method_line(cand) if not hot else "—"
    if pick.get("method_label") and (hot or not rel):
        line = pick["method_label"]
    method, _, res = line.partition("・")
    shown = render.pick_structure(cand, pick)
    if shown:
        pdb = shown["pdb_id"] + (f"（ほか {len(rel) - 1} 件）" if len(rel) > 1 else "")
    elif cand["kind"] == "new_map_only" and cand.get("emdb_ids"):
        pdb = cand["emdb_ids"][0]
    else:
        pdb = "公開待ち" if own else ("—" if hot else "補足資料")
    prots = cand.get("proteins", [])
    return {"target": pick["target_label"], "method": method or "—", "res": res or "—", "pdb": pdb,
            "pdb_id": shown["pdb_id"] if shown else None,
            "ligand": pick.get("ligand_label") or ("—" if hot else render.ligand_line(cand)),
            "uniprot": [(p["uniprot"], p.get("gene") or p["uniprot"]) for p in prots[:3]],
            "first": any(p.get("first_structure") for p in prots)}


def row_html(cand: dict, pick: dict, n: int, date: str, root: Path, fig_base: str, href_base: str) -> str:
    """One row of the index: number | headline and supplement | TARGET METHOD RES. | structure | PDB."""
    href = f"{href_base}#a{n}"
    f = facts(cand, pick)
    dl = "".join(f"<div><dt>{k}</dt><dd>{esc(v)}</dd></div>"
                 for k, v in (("TARGET", f["target"]), ("METHOD", f["method"]), ("RES.", f["res"])))
    first = '<span class="mk">PDB 初構造</span>' if f["first"] else ""
    return (f'<li class="row"><span class="no">{pad(n)}</span><div class="rt">'
            f'<p class="rk"><span class="n">{pad(n)}</span></p>\n'
            f'<h3><a href="{href}">{esc(pick["headline"])}</a></h3>\n'
            f'<p class="sub">{esc(pick.get("subhead") or "")}</p>'
            f'<p class="rm">{esc(cand["journal_abbrev"] or cand["journal"])}</p></div>'
            f'<dl class="rf">{dl}</dl>{thumb_html(cand, pick, date, root, fig_base, href)}'
            f'<div class="rp"><span class="pl">PDB</span><b>{esc(f["pdb_id"] or f["pdb"])}</b>{first}</div></li>')


def contents_html(picks: list, date: str, root: Path, fig_base: str, href_base: str = "",
                  featured: str | None = None) -> str:
    """The index in two blocks, 新しい構造 and 注目の構造, each under a rule with its count.

    The article shown as 今日の一本 is not repeated here (it keeps its number, so the list may skip one).
    """
    new, hot = render.split_sections(picks)
    number = {c["pmcid"]: n for n, (c, _) in enumerate(new + hot, 1)}
    out = ""
    for key, items, cls in (("new", new, ""), ("impact", hot, " hot")):
        body = "".join(row_html(c, pk, number[c["pmcid"]], date, root, fig_base, href_base)
                       for c, pk in items if c["pmcid"] != featured)
        if body:
            out += (f'<div class="ix-h{cls}"><h2 class="label">{PARTS[key][0]}</h2><span>{len(items)} 本</span></div>'
                    f'<ol class="rows">{body}</ol>')
    return out


def hero_html(feature: dict | None, picks: list, date: str, root: Path, fig_base: str, href_base: str = "") -> str:
    """今日の一本: the day's one article, large, with its facts. The reason it was picked is for the pull request."""
    if not feature:
        return ""
    for n, (cand, pk) in enumerate(picks, 1):
        if cand["pmcid"] == feature["pmcid"]:
            break
    else:
        return ""
    href = f"{href_base}#a{n}"
    f = facts(cand, pk)
    uni = ", ".join(f'<a href="https://www.uniprot.org/uniprotkb/{esc(a)}">{esc(a)}</a>' for a, _ in f["uniprot"]) or "—"
    cells = [("TARGET", esc(f["target"])), ("METHOD", esc(f["method"])), ("RES.", esc(f["res"])),
             ("PDB", esc(f["pdb"])), ("LIGAND", esc(f["ligand"])), ("UNIPROT", uni)]
    dl = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in cells)
    big = root / date / "figures" / f"{cand['pmcid']}.svg"   # the article's own figure, larger than the list thumbnail
    fig = (f'<a class="thumb" href="{href}"><img src="{fig_base}/{date}/{esc(cand["pmcid"])}.svg" '
           f'alt="構造図：{esc(pk["target_label"])}"></a>') if big.exists() else thumb_html(cand, pk, date, root, fig_base, href)
    first = ' · <span class="mk">PDB 初構造</span>' if f["first"] else ""
    return (f'<section class="hero"><h2 class="label">今日の一本 <span>/ {pad(n)}{first}</span></h2><div class="hero-grid"><div>'
            f'<h3><a href="{href}">{esc(pk["headline"])}</a></h3>'
            f'<p class="sub">{esc(pk.get("subhead") or "")}</p>'
            f'<dl class="hf">{dl}</dl>'
            f'<p class="go"><a href="{href}">記事を読む →</a></p></div>{fig}</div></section>')


def part_html(key: str, count: int) -> str:
    title, note = PARTS[key]
    return (f'<section class="part" id="{key}"><h2>{title}<span>{count} 本</span></h2>'
            f'<p>{esc(note)}</p></section>')


def counts_text(n_new: int, n_hot: int, sep: str = "<br>") -> str:
    return f"新しい構造 {n_new} 本" + (f"{sep}注目の構造 {n_hot} 本" if n_hot else "")


def toc_side(picks: list, featured: str | None) -> str:
    """The sidebar of an issue page: every article of the issue, grouped, each a link to its anchor."""
    new, hot = render.split_sections(picks)
    number = {c["pmcid"]: n for n, (c, _) in enumerate(new + hot, 1)}
    out = ""
    for key, items in (("new", new), ("impact", hot)):
        if items:
            links = "".join(f'<a href="#a{number[c["pmcid"]]}"><b>{pad(number[c["pmcid"]])}</b>'
                            f'<span>{esc(pk["headline"])}</span></a>' for c, pk in items)
            out += f'<h3>{PARTS[key][0]}</h3>{links}'
    return sd("この号の記事", f'<div class="sd-toc">{out}</div>', "side-only-issue")


def issue_page(date: str, prev_date: str | None, next_date: str | None,
               root: Path = ISSUES_DIR, preview: bool = False) -> str:
    meta, picks, types = load_issue(date, root)
    n_hot = sum(render.is_impact(pk) for _, pk in picks)
    picked = {pk["pmcid"] for _, pk in picks}
    rest = [x for x in meta["candidates"] if x["kind"] in render.KIND_JA and x["pmcid"] not in picked]
    fig_base = "figures" if preview else "../figures"
    bar = ""
    if preview:
        pr = read_json(root / date / "pr.json") if (root / date / "pr.json").exists() else {}
        link = (f' · <a href="{esc(pr["url"])}">Pull Request #{pr["number"]} を開く（ここでマージ）</a>'
                if pr.get("url") else "")
        bar = f'<div class="preview-bar">プレビュー（未公開）{link} · <a href="index.html">確認待ちの一覧</a></div>'
    main = []
    feature = load_feature(date, root, picks) if picks else None
    if picks:
        main.append(hero_html(feature, picks, date, root, fig_base))
        main.append(f'<section class="toc">{contents_html(picks, date, root, fig_base, "", feature and feature["pmcid"])}</section>')
    else:
        c = meta["counts"]
        main.append(f'<section class="part"><h2>記事なし<span>0 本</span></h2><p>Europe PMC に {esc(meta["index_date"])} に'
                    f'登録された論文 {c.get("parsed", 0)} 本に、新しい構造も注目の構造もなかった。</p></section>')
    for n, (cand, pk) in enumerate(picks, 1):
        hot = render.is_impact(pk)
        if n == 1 or hot != render.is_impact(picks[n - 2][1]):
            main.append(part_html("impact" if hot else "new", n_hot if hot else len(picks) - n_hot))
        main.append(article_html(cand, pk, date, n, root, fig_base, types.get(cand["pmcid"]), "../"))
    if rest:
        items = "".join(f'<li><a href="{doi_url(x)}">{esc(x["title"])}</a> — <i>{esc(x["journal_abbrev"] or x["journal"])}</i>'
                        f'（{esc(render.KIND_JA[x["kind"]])}）</li>' for x in rest)
        main.append(f'<section class="rest"><h2 class="label">記事にしていない新しい構造</h2><ul>{items}</ul></section>')
    prev_link = f'<a href="{prev_date}.html">← {jp_date(prev_date)}号</a>' if prev_date else "<span></span>"
    next_link = f'<a href="{next_date}.html">{jp_date(next_date)}号 →</a>' if next_date else "<span></span>"
    main.append(f'<nav class="issue-nav">{prev_link}<a href="../index.html">号の一覧</a>{next_link}</nav>')
    nav = "".join(x for x in (f'<a href="{prev_date}.html">← {jp_date(prev_date)}号</a>' if prev_date else "",
                              f'<a href="{next_date}.html">{jp_date(next_date)}号 →</a>' if next_date else "",
                              '<a href="../index.html">号の一覧</a>'))
    side = (toc_side(picks, feature and feature["pmcid"]) if picks else "") + sd("号の移動", f'<div class="sd-nav">{nav}</div>')
    counts = counts_text(len(picks) - n_hot + len(rest), n_hot)
    head = masthead(1, f"{jp_date(date)}号<br>{counts}")
    foot = colophon(f'<p>この号の対象：Europe PMC に {esc(meta["index_date"])} に登録されたオープンアクセス論文 '
                    f'{meta["counts"]["parsed"]} 本のうち、新しい構造を報告した論文。</p>')
    desc = "、".join(pk["target_label"] for _, pk in picks[:6])
    title = f"{jp_date(date)}号 · {brand.SITE_NAME}"
    return page(("［プレビュー］" if preview else "") + title, bar + head + shell("".join(main), side, sticky=True) + foot, depth=1,
                description=f"{jp_date(date)}の新しい構造：{desc}", noindex=preview, viewer=True)


ABOUT = ("Europe PMC に前日登録されたオープンアクセスの査読済み論文から、構造生物学の論文を毎日紹介する。"
         "新しい構造（PDB / EMDB に寄託）は全件、新しい構造ではない注目の構造は 1 日 5 本まで、基準を決めて選ぶ。"
         "構造図は PDB の座標から描いている。")


def numbers_html(issues: list[str]) -> str:
    """Running totals over every issue, all computed from the data."""
    n_art = n_first = 0
    for date in issues:
        _, picks, _ = load_issue(date, ISSUES_DIR)
        n_art += len(picks)
        n_first += sum(any(p.get("first_structure") for p in c.get("proteins", [])) for c, _ in picks)
    cells = [("号", len(issues)), ("記事", n_art), ("PDB 初構造", n_first), ("掲載誌", len(journal_counts(issues)))]
    dl = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in cells)
    return sd("これまでの数字", f'<dl class="nums">{dl}</dl>')


def home(issues: list[str]) -> str:
    """The front page: the latest issue (今日の一本 and every other article with its structure), a sidebar
    (the latest issue, the journals, running totals, about) and a compact list of past issues."""
    latest, past = issues[0], issues[1:]
    meta, picks, _ = load_issue(latest, ISSUES_DIR)
    href = f"issues/{latest}.html"
    n_hot = sum(render.is_impact(pk) for _, pk in picks)
    feature = load_feature(latest, ISSUES_DIR, picks)
    main = [hero_html(feature, picks, latest, ISSUES_DIR, "figures", href),
            f'<section class="toc">{contents_html(picks, latest, ISSUES_DIR, "figures", href, feature and feature["pmcid"])}'
            f'</section>',
            f'<p class="more"><a href="{href}">この号のすべての記事を読む →</a></p>']
    if past:
        items = []
        for date in past:
            _, ps, _ = load_issue(date, ISSUES_DIR)
            targets = "・".join(pk["target_label"].split("（")[0] for _, pk in ps)
            items.append(f'<li><a class="d" href="issues/{date}.html">{jp_date_short(date)}号</a>'
                         f'<span class="t"><b>{len(ps)} 本</b>{esc(targets)}</span></li>')
        main.append(f'<section class="past"><div class="ix-h"><h2 class="label">過去の号</h2><span>{len(past)} 号</span></div>'
                    f'<ol>{"".join(items)}</ol></section>')
    side = (sd("最新号", f'<a class="sd-issue" href="{href}">{jp_date(latest)}号</a>'
                         f'<p class="sd-meta">{counts_text(len(picks) - n_hot, n_hot)}</p>')
            + journals_html(journal_counts(issues)) + numbers_html(issues) + sd("このサイトについて", f"<p>{esc(ABOUT)}</p>"))
    head = masthead(0, f'<a href="{href}">{jp_date(latest)}号</a><br>{counts_text(len(picks) - n_hot, n_hot, " · ")}')
    return page(f"{brand.SITE_NAME}（{brand.SITE_NAME_EN}）", head + shell("".join(main), side) + colophon(), depth=0)


def issue_dates(root: Path, require_articles: bool = True) -> list[str]:
    if not root or not root.exists():
        return []
    return sorted([d.name for d in root.iterdir() if (d / "candidates.json").exists() and
                   (not require_articles or any((d / "picks").glob("*.json")))], reverse=True)


def preview_index(previews: list[str], root: Path | None) -> str:
    """/preview/: the issues waiting for the editor, each with its preview and its pull request."""
    items = []
    for date in previews:
        _, picks, _ = load_issue(date, root)
        n_hot = sum(render.is_impact(pk) for _, pk in picks)
        pr = read_json(root / date / "pr.json") if (root / date / "pr.json").exists() else {}
        merge = (f'<a class="go" href="{esc(pr["url"])}">Pull Request #{pr["number"]}（マージ）</a>'
                 if pr.get("url") else "")
        items.append(f'<li><div class="d">{jp_date(date)}号</div>'
                     f'<div class="c">新しい構造 {len(picks) - n_hot} 本 · 注目の構造 {n_hot} 本</div>'
                     f'<div class="btns"><a href="{date}.html">見た目を確認</a>{merge}</div></li>')
    body = [masthead(1, "確認待ちの号"),
            '<div class="wrap"><p class="intro">マージ前の号のプレビュー。見た目を確認して、問題がなければ Pull Request を開いて'
            '「Merge pull request」→「Confirm merge」で公開する。このページはブックマークやホーム画面に追加しておける。</p>',
            f'<ul class="queue">{"".join(items)}</ul>' if items else '<p class="intro">いま確認待ちの号はない。</p>',
            '</div>', colophon()]
    return page(f"確認待ちの号 · {brand.SITE_NAME}", "".join(body), depth=1, noindex=True)


def build(preview_root: Path | None = None) -> int:
    issues = issue_dates(ISSUES_DIR)
    if not issues:
        print("no built issues found")
        return 1
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    (SITE_DIR / "assets").mkdir(parents=True)
    (SITE_DIR / "issues").mkdir()
    for name in ("logo.svg", "logo-mark.svg", "viewer.js"):
        shutil.copy(ROOT / "assets" / name, SITE_DIR / "assets" / name)
    shutil.copytree(ROOT / "assets" / "vendor", SITE_DIR / "assets" / "vendor")
    for i, date in enumerate(issues):
        prev_date = issues[i + 1] if i + 1 < len(issues) else None
        next_date = issues[i - 1] if i > 0 else None
        (SITE_DIR / "issues" / f"{date}.html").write_text(issue_page(date, prev_date, next_date), encoding="utf-8")
        src = ISSUES_DIR / date / "figures"
        if src.exists():
            shutil.copytree(src, SITE_DIR / "figures" / date, ignore=shutil.ignore_patterns("*.json"))
    (SITE_DIR / "index.html").write_text(home(issues), encoding="utf-8")
    print(f"built site/ with {len(issues)} issue(s): {', '.join(issues)}")
    previews = issue_dates(preview_root, require_articles=False) if preview_root else []
    out = SITE_DIR / "preview"
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(preview_index(previews, preview_root), encoding="utf-8")
    for date in previews:
        (out / f"{date}.html").write_text(issue_page(date, None, None, preview_root, preview=True), encoding="utf-8")
        src = preview_root / date / "figures"
        if src.exists():
            shutil.copytree(src, out / "figures" / date, ignore=shutil.ignore_patterns("*.json"))
    if previews:
        print(f"previews: {', '.join(previews)}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the static site into site/.")
    ap.add_argument("--preview", type=Path, help="folder of unmerged issues (<date>/candidates.json, picks/, figures/)")
    sys.exit(build(ap.parse_args().preview))
