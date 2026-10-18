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
import datetime
import html
import json
import re
import shutil
import sys
import unicodedata
from pathlib import Path

from . import brand, impact, pmcoa, render
from .common import ISSUES_DIR, ROOT, read_json
from .schema import CATEGORIES, FEATURE_AXES, FEATURE_MIN, LAB_NOTE_FIELDS, SUMMARY_FIELDS, SUMMARY_OPTIONAL

SITE_DIR = ROOT / "site"
TH = brand.THEME
DESCRIPTION = "新着の査読済みオープンアクセス論文から、構造生物学論文を、毎日紹介するサイト。"
TAGLINE = "構造生物学者のための情報収集サポートサイト"
LEAD = ("新着の査読済みオープンアクセス論文から、", "構造生物学の論文を、", "毎日ピックアップして紹介")
LATEST = {"date": None}   # the newest issue with articles (the menu's "Latest Article"), set by build()

CHART_NEW, CHART_HOT = "#2F5DA8", "#C8442A"   # 藍 / 朱: validated against the ground #FAFAF8 (dataviz validate_palette)
CHART_TRACK = "#D6DEEB"                        # the meter's track: a light step of the same blue
CHART_ISSUES = 30                              # the papers-per-issue chart shows the latest issues only

CSS = f"""
:root {{
  --ground:{TH.ground}; --paper:{TH.paper}; --ink:{TH.ink}; --ink2:{TH.ink2}; --mute:{TH.muted};
  --desc:{TH.desc}; --hair:{TH.hair}; --acc:{TH.accent}; --acc-ink:{TH.accent_ink};
  --serif:"Noto Serif JP","Hiragino Mincho ProN","Yu Mincho",YuMincho,"BIZ UDMincho",Georgia,serif;
  --latin:Georgia,"Times New Roman","Noto Serif JP",serif;
  --sans:"Noto Sans JP","Hiragino Kaku Gothic ProN","Hiragino Sans","Yu Gothic",Meiryo,sans-serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --col:240px; --side:320px; --pad:clamp(24px,6vw,96px); --head:106px;
  --sec:96px;   /* the space between sections, which replaces the rules between them */
  --rule:#B3BAC4;   /* the thin rule under a section heading */
}}
* {{ box-sizing:border-box; }}
html, body {{ margin:0; background:var(--ground); }}
body {{ color:var(--ink); font-family:var(--serif); font-size:16px; line-height:1.95;
        -webkit-font-smoothing:antialiased; }}
a {{ color:inherit; text-decoration:none; text-underline-offset:3px; }}
h2, h3, .headline, .subhead, .sub {{ word-break:auto-phrase; line-break:strict; }}
a:hover {{ color:var(--acc-ink); text-decoration:underline; text-decoration-color:var(--acc); }}
.kicker a {{ text-decoration:underline; text-decoration-color:var(--acc); }}
.wrap {{ max-width:1600px; margin:0 auto; padding:0 var(--pad); }}
.mk {{ color:var(--acc-ink); font-weight:700; }}
.label {{ margin:0; font-family:var(--mono); font-size:14px; letter-spacing:.12em; font-weight:700;
          color:var(--ink2); text-transform:uppercase; padding-top:6px; }}
.label a {{ text-decoration:none; }}

/* masthead: a heavy rule over a thin one, running the full width of the window */
.mast {{ padding-top:30px; }}
.mast-row {{ display:flex; align-items:center; justify-content:space-between; gap:24px; }}
.brand {{ display:flex; align-items:center; gap:26px; min-width:0; }}
.word {{ display:flex; align-items:center; gap:15px; text-decoration:none; flex:none; }}
.word img {{ width:46px; height:46px; display:block; }}
.word .name {{ font-family:var(--latin); font-size:42px; line-height:1; letter-spacing:-0.02em; font-weight:700; margin:0; }}
.tag {{ margin:0; padding-left:26px; border-left:1px solid var(--hair); font-size:13.5px; color:var(--desc); line-height:1.7; }}
.vol {{ font-size:14px; text-align:right; color:var(--ink2); line-height:1.7; }}
.vol a {{ text-decoration:underline; text-decoration-color:var(--acc); }}
.rules {{ margin-top:18px; border-top:4px solid var(--ink); }}
.rules::after {{ content:""; display:block; margin-top:3px; border-top:1px solid var(--ink); }}
.intro {{ color:var(--desc); font-size:14.5px; margin-top:18px; }}

/* shell: the main column and a sticky sidebar on the right */
.shell {{ display:grid; grid-template-columns:minmax(0,1fr) var(--side); gap:0 56px; align-items:stretch; }}
.main {{ min-width:0; padding-bottom:30px; }}
.side {{ border-left:1px solid var(--hair); padding-left:32px; padding-top:40px; padding-bottom:30px; }}
.sticky-side .side-in {{ position:sticky; top:calc(var(--headnow) + 16px); max-height:calc(100vh - var(--headnow) - 32px); overflow-y:auto; scrollbar-width:thin; }}
.sd {{ margin:0 0 28px; }}
.sd + .sd {{ padding-top:24px; border-top:1px solid var(--hair); }}
.sd .label {{ padding-top:0; margin-bottom:10px; }}
.sd p {{ margin:0 0 8px; font-size:13.5px; line-height:1.85; color:var(--desc); }}
.sd-meta {{ font-family:var(--mono); font-size:12px !important; color:var(--ink2) !important; line-height:1.8 !important; }}
.jl {{ list-style:none; margin:0; padding:0; font-size:14px; line-height:1.55; }}
.jl li {{ display:flex; justify-content:space-between; align-items:baseline; gap:14px; padding:5px 0;
          border-bottom:1px dotted var(--hair); }}
.jl .c {{ font-family:var(--mono); font-size:12px; color:var(--mute); white-space:nowrap; }}
.sd-toc a {{ display:grid; grid-template-columns:28px minmax(0,1fr); gap:6px; padding:5px 0; font-size:13.5px;
             line-height:1.6; color:var(--ink2); }}
.sd-toc a b {{ font-family:var(--mono); font-size:11.5px; color:var(--acc-ink); padding-top:1px; }}
.sd-toc h3 {{ margin:14px 0 4px; font-family:var(--mono); font-size:11px; letter-spacing:.12em; font-weight:500; color:var(--mute); }}
.sd-toc h3:first-of-type {{ margin-top:0; }}
.sd-up {{ display:inline-flex; gap:8px; margin:0 0 18px; padding:8px 14px; font-family:var(--mono); font-size:12.5px; letter-spacing:.04em;
          color:var(--ink); box-shadow:inset 0 0 0 1px var(--ink); transition:background-color .25s, color .25s; }}
.sd-up:hover {{ background:var(--ink); color:#fff; text-decoration:none; }}
.issue-top {{ padding-bottom:20px; }}
.issue-top + .shell .side {{ padding-top:58px; }}
.sd-nav {{ display:flex; flex-direction:column; gap:4px; font-family:var(--mono); font-size:12.5px; }}

/* contents: one table row per article: number, headline + supplement, TARGET / METHOD / RES., the real structure, PDB */
.toc {{ margin-top:var(--sec); }}
/* every section heading of the pages (Picked up Today's Structure, About FoldFeed, New Structure, Notable Structure, ...),
   all in English: one face, one size, and (as on Nature's pages) a thin rule under the title, the content below it.
   Sections are told apart by space, not rules; a sub-section inside one opens with a short bar above it */
.ix-h {{ display:flex; justify-content:space-between; align-items:baseline; gap:16px; padding-bottom:12px; margin-bottom:26px;
         border-bottom:1px solid var(--rule); }}
.rows + .toc-sub, .art, .about > .wrap > .about-sub {{ position:relative; }}
.rows + .toc-sub::before, .art::before, .about > .wrap > .about-sub::before {{
  content:""; position:absolute; left:0; top:-28px; width:36px; height:0; border-top:4px solid var(--ink); }}
.rows + .toc-sub {{ margin-top:72px; }}
.ix-h .label, .part h2 {{ padding-top:0; font-family:var(--latin); font-size:30px; letter-spacing:0; text-transform:none;
               font-weight:700; color:var(--ink); line-height:1.35; }}
.ix-h span {{ font-family:var(--mono); font-size:13px; letter-spacing:.08em; color:var(--mute); }}
.ix-h.hot {{ margin-top:var(--sec); }}
.rows {{ list-style:none; margin:0; padding:0; }}
/* a row of an index leads with its structure: number | the figure, large | headline and supplement | the facts */
.row {{ display:grid; grid-template-columns:60px 360px minmax(0,1fr) 250px; grid-template-areas:"no th rt rx";
        gap:0 36px; align-items:center; padding:26px 0; border-top:1px solid var(--hair); }}
.rows .row:first-child {{ border-top:0; }}
.row .no {{ grid-area:no; font-family:var(--latin); font-size:30px; line-height:1; letter-spacing:-0.02em; color:var(--acc-ink); }}
.row .rt {{ grid-area:rt; min-width:0; }}
.row .rx {{ grid-area:rx; min-width:0; }}
.row .thumb {{ grid-area:th; }}
.row .rp {{ margin-top:10px; }}
.rk {{ display:none; margin:0 0 3px; font-family:var(--mono); font-size:11.5px; letter-spacing:.04em; color:var(--mute); line-height:1.6; }}
.rk .n {{ color:var(--acc-ink); font-weight:700; margin-right:10px; }}
.row h3 {{ margin:0; font-family:var(--sans); font-size:22px; line-height:1.55; font-weight:700; color:var(--ink); text-wrap:balance; }}
.row .sub {{ margin:6px 0 0; font-family:var(--sans); font-size:14.5px; line-height:1.85; color:var(--desc); }}
.row .rm {{ margin:5px 0 0; font-family:var(--mono); font-size:11px; color:var(--mute); line-height:1.6; }}
.thumb {{ display:block; }}
.thumb img {{ display:block; width:100%; height:auto; }}
.thumb.none {{ aspect-ratio:3/2; display:flex; align-items:center; justify-content:center;
               font-family:var(--mono); font-size:11px; color:var(--mute); text-align:center; padding:8px; }}
.rf {{ margin:0; font-family:var(--mono); }}
.rf div {{ display:grid; grid-template-columns:62px minmax(0,1fr); gap:0 8px; padding:2px 0; }}
.rf dt {{ font-size:10.5px; letter-spacing:.1em; color:var(--mute); padding-top:2px; }}
.rf dd {{ margin:0; font-size:12px; line-height:1.6; color:var(--ink2); overflow-wrap:anywhere; }}
.rp {{ font-family:var(--mono); line-height:1.5; }}
.rp .pl {{ display:block; font-size:10.5px; letter-spacing:.1em; color:var(--mute); }}
.rp b {{ display:block; font-size:15px; font-weight:500; color:var(--ink); }}
.rp .mk {{ display:block; margin-top:4px; font-family:var(--serif); font-size:12px; }}
.more {{ margin:8px 0 0; font-family:var(--mono); font-size:13px; }}
.more a {{ text-decoration:underline; text-decoration-color:var(--acc); text-underline-offset:4px; }}
.past {{ margin-top:56px; }}
.past ol {{ list-style:none; margin:14px 0 0; padding:0; }}
.past li {{ display:grid; grid-template-columns:110px minmax(0,1fr); gap:14px; margin:0 0 14px; align-items:baseline; }}
.past .d {{ font-family:var(--mono); font-size:12.5px; }}
.past .t {{ font-size:14.5px; color:var(--desc); line-height:1.7; }}
.past .t b {{ font-family:var(--mono); font-size:11px; font-weight:500; color:var(--mute); margin-right:10px; }}
.part {{ margin-top:var(--sec); }}
.part h2 {{ margin:0; display:flex; align-items:baseline; gap:16px; padding-bottom:12px; border-bottom:1px solid var(--rule); }}
.part h2 {{ justify-content:space-between; }}
.ixm, .ix-h .ixm {{ margin-left:auto; text-align:right; font-family:var(--mono); font-size:14px; font-weight:400; letter-spacing:.04em;
        color:var(--ink2); white-space:nowrap; }}
.ixm b, .ix-h .ixm b {{ margin-left:18px; font-weight:700; color:var(--ink); }}
.part p {{ margin:0; color:var(--desc); font-size:14px; max-width:760px; }}
.part + .art {{ margin-top:8px; }}
.part + .art::before {{ display:none; }}

/* article: margin column for the data, text column for the story */
/* the articles under a New / Notable Structure heading are its sub-sections: each after the first opens with the bar */
.art {{ display:grid; grid-template-columns:var(--col) minmax(0,1fr); gap:28px; padding-top:44px; padding-bottom:24px;
        margin-top:72px; position:relative; }}
.art::before {{ top:0; }}
.margin {{ font-family:var(--mono); font-size:11.5px; line-height:1.7; color:var(--ink2); }}
.margin .no {{ font-family:var(--latin); font-size:78px; line-height:.9; color:var(--ink); letter-spacing:-0.04em;
               margin-bottom:16px; }}
.margin dl {{ margin:0; }}
.margin dt {{ margin-top:13px; color:var(--mute); letter-spacing:.1em; font-size:10px; text-transform:uppercase; }}
.margin dd {{ margin:2px 0 0; overflow-wrap:anywhere; }}
.margin .pdb {{ display:block; }}
.margin .first {{ margin-top:16px; }}
.margin .sticky {{ position:sticky; top:calc(var(--headnow) + 20px); }}
.kicker {{ font-family:var(--mono); font-size:11.5px; letter-spacing:.06em; color:var(--mute); margin:0 0 10px; line-height:1.7; }}
.kicker b {{ color:var(--ink); font-weight:600; }}
/* an article opens as Today's Structure does on the front page: headline, supplement, then the paper's own title */
.art .art-h {{ margin:0; font-size:30px; line-height:1.45; font-weight:700; color:var(--ink); text-wrap:balance; }}
.art .subhead {{ margin:10px 0 0; font-size:17.5px; line-height:1.75; color:var(--desc); }}
.ptitle {{ margin:14px 0 26px; font-family:var(--latin); font-size:16.5px; line-height:1.5; color:var(--ink2); }}
.ptitle .pl {{ margin-right:12px; font-family:var(--mono); font-size:10.5px; letter-spacing:.1em; text-transform:uppercase; color:var(--mute); }}
.art-4 {{ margin-top:22px; }}
.nopdb {{ margin:0 0 8px; padding:10px 16px; border-left:3px solid var(--acc); font-size:15px; line-height:1.8; color:var(--ink2); }}
.art .reads {{ margin-top:22px; }}

/* figures: transparent, on the page ground; the legend and a 3D viewer follow */
.v3d-view {{ position:relative; }}
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
.v3d .msp-layout-standard {{ border:0 !important; }}
.lead-3d .msp-viewport-controls {{ display:none; }}
.figtabs {{ display:flex; gap:22px; margin:0 0 8px; font-family:var(--mono); font-size:12.5px; }}
.figtabs button {{ font:inherit; letter-spacing:.04em; background:none; border:0; border-bottom:2px solid transparent; padding:2px 0 4px;
                   color:var(--mute); cursor:pointer; }}
.figtabs button:hover {{ color:var(--ink); }}
.figtabs button.on {{ color:var(--ink); border-bottom-color:var(--acc); }}
.fig.m {{ display:none; }}
.v3d .fig.cmp, .v3d.show-cmp .fig:not(.cmp), .v3d.show-cmp .v3d-bar {{ display:none; }}
.v3d.show-cmp .fig.cmp.d {{ display:block; }}
.cmpfig {{ margin:26px 0 0; }}
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
.sum {{ margin:0 0 12px; }}
.sum b {{ font-weight:700; margin-right:1em; }}
.art h3 {{ font-size:19px; line-height:1.5; font-weight:700; margin:44px 0 12px; }}
.hl {{ margin:0 0 26px; }}
.hl > p:first-child {{ margin:0; }}
.src {{ display:grid; grid-template-columns:auto minmax(0,1fr); gap:0 12px; margin:8px 0 0 1.4em; font-size:13px; line-height:1.7; color:var(--ink2); }}
.src b {{ font-family:var(--mono); font-size:10.5px; letter-spacing:.1em; color:var(--acc-ink); padding-top:2px; }}
.src .qs p {{ margin:0 0 4px; }}
.src i {{ font-family:var(--latin); }}
.src span {{ font-family:var(--mono); font-size:10.5px; color:var(--mute); margin-left:8px; }}
.lab {{ margin:0; font-size:14.5px; }}
.lab div {{ display:grid; grid-template-columns:112px 1fr; gap:14px; padding:6px 0; border-bottom:1px dotted var(--hair); }}
.lab dt {{ font-family:var(--mono); font-size:11px; color:var(--mute); padding-top:3px; }}
.lab dd {{ margin:0; }}
.lab-src {{ margin-top:10px; }}
.rest {{ margin-top:var(--sec); }}
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
.colo {{ margin-top:22px; border-top:4px solid var(--ink); }}
.colophon {{ padding-top:16px; padding-bottom:70px; font-size:12.5px; color:var(--mute); line-height:1.8; }}
.colophon p {{ margin:0 0 6px; max-width:760px; }}


/* the front page: the day's pick fills the first screen on the page ground, closed by a heavy rule */
.sec {{ margin-top:var(--sec); }}
.sec .lead {{ margin:14px 0 0; font-size:14.5px; color:var(--desc); }}
.ix-all {{ display:inline-flex; align-items:center; gap:12px; margin-left:22px; padding:11px 18px; background:var(--ink); color:#fff;
           font-family:var(--sans); font-size:14.5px; font-weight:700; transition:background-color .25s; }}
.ix-all:hover {{ background:var(--acc-ink); color:#fff; text-decoration:none; }}
.ix-all .arr {{ font-family:var(--mono); color:#fff; transition:transform .3s cubic-bezier(.2,.8,.2,1); }}
.ix-all:hover .arr {{ transform:translateX(5px); }}
.find {{ display:flex; gap:8px; margin:16px 0 0; }}
.find input {{ flex:1; min-width:0; font:inherit; font-family:var(--sans); font-size:14.5px; padding:10px 12px; color:var(--ink);
               border:1px solid var(--ink); background:transparent; border-radius:0; }}
.find input::placeholder {{ color:var(--mute); }}
.find button {{ font:inherit; font-family:var(--mono); font-size:13px; padding:10px 18px; border:1px solid var(--ink);
                background:var(--ink); color:#fff; cursor:pointer; border-radius:0; }}
.find button:hover {{ background:var(--acc-ink); border-color:var(--acc-ink); }}
.cats {{ list-style:none; margin:12px 0 0; padding:0; display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:0 40px; }}
.cats a {{ display:flex; justify-content:space-between; align-items:baseline; gap:12px; padding:10px 0; font-family:var(--sans);
           font-size:15px; border-bottom:1px solid var(--hair); }}
.cats .c {{ font-family:var(--mono); font-size:12px; color:var(--mute); }}
.mail-grid {{ display:grid; grid-template-columns:minmax(0,1.1fr) minmax(0,1fr); gap:48px; margin-top:16px; align-items:start; }}
.mail p {{ margin:0 0 8px; font-size:15px; color:var(--desc); }}
.mail ul {{ margin:6px 0 0; padding-left:1.3em; font-size:14.5px; color:var(--desc); }}
.mail .soon {{ font-family:var(--mono); font-size:12px; letter-spacing:.06em; color:var(--acc-ink); }}
.mail form {{ display:flex; gap:8px; }}
.mail input:disabled, .mail button:disabled {{ opacity:.5; cursor:not-allowed; }}


.row.hit {{ border-left:4px solid var(--acc); padding-left:18px; margin-left:-22px; }}
.row.hit .rt::before {{ content:"関心あり"; display:block; font-family:var(--mono); font-size:11px; letter-spacing:.1em; color:var(--acc-ink); font-weight:700; }}
.row[hidden], .pr[hidden], .pg[hidden] {{ display:none; }}

/* The header (wordmark, MENU, the double rule) slides away while the reader scrolls down and comes back as soon as
   they scroll up; on the first screen it always stays (HEAD_SCRIPT). Whatever sticks while scrolling (an article's
   margin, an issue's contents) sits under the header when it is there (--headnow). */
@media (prefers-reduced-motion:no-preference) {{ html {{ scroll-behavior:smooth; }} .mast {{ transition:transform .25s ease; }} }}
html {{ scroll-padding-top:16px; }}
body {{ --headnow:var(--head); }}
body.head-away {{ --headnow:0px; }}
.mast {{ position:sticky; top:0; z-index:1000; background:var(--ground); }}
.mast.away {{ transform:translateY(-100%); }}
.vol {{ display:flex; align-items:center; gap:22px; }}
.menu {{ position:relative; }}
.menu summary {{ list-style:none; display:flex; align-items:center; gap:14px; cursor:pointer; user-select:none;
                 font-family:var(--mono); font-size:13px; letter-spacing:.16em; color:var(--ink); }}
.menu summary::-webkit-details-marker {{ display:none; }}
.menu .mb {{ width:50px; height:50px; border-radius:50%; background:var(--ink); display:flex; flex-direction:column;
             align-items:center; justify-content:center; gap:5px; }}
.menu .mb {{ transition:background .2s, transform .25s cubic-bezier(.2,.8,.2,1); }}
.menu .mb i {{ display:block; width:20px; height:2px; background:#fff;
               transition:transform .32s cubic-bezier(.2,.8,.2,1), opacity .2s; }}
.menu.on .mb i:nth-child(1), html:not(.js) .menu[open] .mb i:nth-child(1) {{ transform:translateY(7px) rotate(45deg); }}
.menu.on .mb i:nth-child(2), html:not(.js) .menu[open] .mb i:nth-child(2) {{ opacity:0; transform:scaleX(.2); }}
.menu.on .mb i:nth-child(3), html:not(.js) .menu[open] .mb i:nth-child(3) {{ transform:translateY(-7px) rotate(-45deg); }}
.menu summary:hover .mb, .menu summary:focus-visible .mb {{ background:var(--acc-ink); }}
.menu summary:active .mb {{ transform:scale(.92); }}
.menu-list {{ position:absolute; right:0; top:calc(100% + 12px); z-index:30; min-width:230px; display:flex; flex-direction:column;
              background:var(--ground); border:1px solid var(--ink); padding:8px 0; text-align:right; }}
.menu-list a {{ position:relative; padding:9px 24px; font-family:var(--mono); font-size:14px; color:var(--ink); text-decoration:none;
               transition:color .2s; }}
.menu-list a::before {{ content:""; position:absolute; inset:0; z-index:-1; background:var(--ink); transform:scaleX(0);
                       transform-origin:right; transition:transform .28s cubic-bezier(.2,.8,.2,1); }}
.menu-list a:hover, .menu-list a:focus-visible {{ color:#fff; text-decoration:none; }}
.menu-list a:hover::before, .menu-list a:focus-visible::before {{ transform:scaleX(1); }}
.menu-list {{ isolation:isolate; }}
.js .menu-list {{ opacity:0; transform:translateY(-12px) scaleY(.94); transform-origin:top right;
                  transition:opacity .22s ease, transform .32s cubic-bezier(.2,.8,.2,1); }}
.js .menu.on .menu-list {{ opacity:1; transform:none; }}
.js .menu-list a {{ opacity:0; transform:translateX(14px);
                    transition:opacity .25s ease, transform .34s cubic-bezier(.2,.8,.2,1), color .2s; }}
.js .menu.on .menu-list a {{ opacity:1; transform:none; }}
.js .menu.on .menu-list a:nth-child(2) {{ transition-delay:40ms; }}
.js .menu.on .menu-list a:nth-child(3) {{ transition-delay:80ms; }}
.js .menu.on .menu-list a:nth-child(4) {{ transition-delay:120ms; }}
.js .menu.on .menu-list a:hover {{ transition-delay:0ms; }}

/* the front page's first screen: what the site is, the button down to Today's Structure, a large structure */
.lead {{ }}
.lead-grid {{ display:grid; grid-template-columns:minmax(0,44fr) minmax(0,56fr); gap:36px; align-items:center;
              min-height:min(560px, calc(100vh - 150px)); padding-top:24px; padding-bottom:24px; }}
.lead-h {{ margin:0; font-family:var(--serif); font-weight:400; font-size:clamp(18px, 1.72vw, 30px); line-height:1.8;
           color:var(--ink); letter-spacing:.02em; }}
.lead-h span {{ display:block; white-space:nowrap; }}
html, body {{ overflow-x:clip; }}   /* a guard: nothing may widen the page sideways (see docs/decisions.md) */
.lead-go {{ display:inline-flex; align-items:center; gap:12px; margin-top:34px; padding:14px 24px 14px 20px;
            background:var(--ink); color:#fff; font-family:var(--mono); font-size:14.5px; letter-spacing:.06em; }}
.lead-go .tri {{ font-size:13px; }}
.lead-btns {{ display:flex; flex-wrap:wrap; gap:12px 14px; margin-top:34px; }}
.lead-btns .lead-go {{ margin-top:0; }}
.lead-go.alt {{ background:transparent; color:var(--ink); box-shadow:inset 0 0 0 1px var(--ink); }}
.lead-go.alt:hover {{ background:var(--ink); color:#fff; }}

/* About FoldFeed, right under the first screen */
.about {{ padding:var(--sec) 0 0; }}
.about-grid {{ display:grid; grid-template-columns:minmax(0,64fr) minmax(0,36fr); gap:48px; margin-top:22px; align-items:start; }}
.about-dl {{ margin:0; }}
.about-dl div {{ display:grid; grid-template-columns:136px minmax(0,1fr); gap:16px; padding:10px 0; border-top:1px solid var(--hair); }}
.about-dl div:first-child {{ border-top:0; padding-top:0; }}
.about-dl dt {{ font-size:15px; font-weight:700; color:var(--ink2); padding-top:3px; }}
.about-dl dd {{ margin:0; font-size:16px; line-height:1.95; color:var(--ink2); }}
.about-sub {{ margin:0 0 14px; font-family:var(--sans); font-size:17px; font-weight:700; color:var(--ink); display:flex;
              justify-content:space-between; align-items:baseline; }}
.about-sub span {{ font-family:var(--mono); font-size:12.5px; font-weight:500; color:var(--mute); }}
.about > .wrap > .about-sub {{ margin-top:72px; }}
/* FoldFeed in Numbers: counts, a meter, a column chart (dataviz: thin marks, 4 px rounded data ends, 2 px gaps,
   hairline grid, legend for the two series, values in text tokens) */
.kpis {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:18px; }}
.kpi {{ display:flex; flex-direction:column; gap:2px; min-width:0; }}
.kl {{ font-family:var(--sans); font-size:13px; color:var(--ink2); }}
.kv {{ font-family:var(--sans); font-size:44px; line-height:1.1; font-weight:600; color:var(--ink); letter-spacing:-0.01em; }}
.kv small {{ font-size:15px; font-weight:500; color:var(--mute); letter-spacing:0; }}
.meter-row {{ display:grid; grid-template-columns:auto minmax(0,1fr) auto; gap:18px; align-items:end; margin-top:22px;
              padding-top:18px; border-top:1px solid var(--hair); }}
.meter {{ height:10px; margin-bottom:14px; background:{CHART_TRACK}; border-radius:5px; overflow:hidden; }}
.meter i {{ display:block; height:100%; background:{CHART_NEW}; border-radius:0 5px 5px 0; transform-origin:left;
            transform:scaleX(var(--v)); transition:transform 1.3s cubic-bezier(.2,.8,.2,1) .25s; }}
.mp {{ font-family:var(--sans); font-size:15px; font-weight:600; color:var(--ink2); margin-bottom:8px; }}
.ppi {{ position:relative; margin:22px 0 0; padding-top:18px; border-top:1px solid var(--hair); }}
.ppi figcaption {{ display:flex; flex-wrap:wrap; justify-content:space-between; align-items:baseline; gap:6px 16px;
                   font-family:var(--sans); font-size:14px; color:var(--ink2); }}
.ppi figcaption b {{ font-size:15px; color:var(--ink); }}
.ppi .lg {{ display:flex; align-items:center; gap:6px; font-size:12.5px; }}
.ppi .lg i {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-left:10px; }}
.ppi .lg i.n {{ background:{CHART_NEW}; }}
.ppi .lg i.h {{ background:{CHART_HOT}; }}
.plot {{ position:relative; height:190px; margin:14px 0 0 26px; }}
.yt span {{ position:absolute; left:-26px; right:0; bottom:calc(var(--y) * 100%); border-top:1px solid var(--hair);
            font-family:var(--mono); font-size:10.5px; line-height:0; color:var(--mute); }}
.yt span:first-child {{ border-top-color:var(--ink2); }}
.cols {{ position:absolute; inset:0; display:flex; align-items:stretch; }}
.col {{ position:relative; flex:1 1 0; min-width:0; display:flex; justify-content:center; text-decoration:none; outline:none; }}
.col .stack {{ position:absolute; bottom:0; width:min(18px, 60%); height:100%; display:flex; flex-direction:column;
               justify-content:flex-end; transform-origin:bottom; transition:transform .9s cubic-bezier(.2,.8,.2,1);
               transition-delay:calc(var(--i) * 45ms + 150ms); }}
.col .stack i {{ display:block; height:calc(var(--h) * 100%); background:{CHART_NEW}; }}
.col .stack i.h {{ background:{CHART_HOT}; margin-bottom:2px; }}
.col .stack i:first-child {{ border-radius:4px 4px 0 0; }}
.col:hover .stack i, .col:focus-visible .stack i {{ filter:brightness(1.18); }}
.col:focus-visible .stack {{ outline:2px solid var(--ink); outline-offset:3px; }}
.col .cap {{ position:absolute; bottom:calc(var(--h) * 100% + 4px); font-family:var(--sans); font-size:12.5px; font-weight:600;
             color:var(--ink); transition:opacity .4s ease 1.2s; }}
.col .x {{ position:absolute; top:calc(100% + 6px); font-family:var(--mono); font-size:10px; color:var(--mute); white-space:nowrap; }}
.col:nth-child(even) .x {{ visibility:hidden; }}
.col:nth-last-child(2) .x {{ visibility:hidden; }}
.col:last-child .x {{ visibility:visible; }}
.ppi .tip {{ position:absolute; z-index:5; pointer-events:none; padding:6px 10px; background:var(--ink); color:#fff;
             font-family:var(--sans); font-size:12.5px; white-space:nowrap; }}
.ppi .tip[hidden] {{ display:none; }}
.ppi {{ padding-bottom:26px; }}
.sr {{ position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }}
.about .jl {{ columns:3 260px; column-gap:44px; font-size:14.5px; }}
.about .jl li {{ break-inside:avoid; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:4px 14px; padding:7px 0 8px;
                 border-bottom:1px solid var(--hair); }}
.about .jl .bar {{ grid-column:1 / -1; height:6px; }}
.about .jl .bar i {{ display:block; height:100%; width:calc(var(--v) * 100%); min-width:6px; background:{CHART_NEW};
                     border-radius:0 3px 3px 0; transform-origin:left;
                     transition:transform .9s cubic-bezier(.2,.8,.2,1) calc(var(--i) * 30ms + 200ms); }}
.anim .about .jl:not(.in) .bar i, .anim .about-side:not(.in) .meter i {{ transform:scaleX(0); }}
.anim .about-side:not(.in) .stack {{ transform:scaleY(0); }}
.anim .about-side:not(.in) .cap {{ opacity:0; }}
.lead-go:hover {{ background:var(--acc-ink); color:#fff; text-decoration:none; }}

/* motion (all off for prefers-reduced-motion): blocks rise into place as they enter the view, the first screen comes
   in line by line, links and buttons answer the pointer */
.anim .rv {{ opacity:0; transform:translateY(22px);
             transition:opacity .7s cubic-bezier(.2,.7,.2,1) var(--d,0ms), transform .8s cubic-bezier(.2,.7,.2,1) var(--d,0ms); }}
.anim .rv.in {{ opacity:1; transform:none; }}
@keyframes rise {{ from {{ opacity:0; transform:translateY(18px); }} to {{ opacity:1; transform:none; }} }}
@keyframes fadein {{ from {{ opacity:0; }} to {{ opacity:1; }} }}
@keyframes pulse {{ 0%, 100% {{ opacity:.35; }} 50% {{ opacity:1; }} }}
.anim .lead-h span {{ animation:rise .9s cubic-bezier(.2,.7,.2,1) both; animation-delay:calc(var(--i) * 140ms + 80ms); }}
.anim .lead-btns {{ animation:rise .9s cubic-bezier(.2,.7,.2,1) .55s both; }}
.anim .lead-art {{ animation:fadein 1.2s ease .2s both; }}
.lead-wait {{ animation:pulse 1.6s ease-in-out infinite; }}
.v3d.loading .v3d-canvas, .v3d.on .v3d-canvas {{ transition:opacity .8s ease; }}
.v3d.loading .v3d-canvas {{ opacity:0; }}
.v3d.on .v3d-canvas {{ opacity:1; }}
a, .find button, .v3d-bar button, .figtabs button {{ transition:color .2s, background-color .2s, border-color .2s,
   text-decoration-color .2s; }}
.lead-go, .reads a {{ transition:background-color .25s, color .25s, box-shadow .25s; }}
.lead-go .tri {{ display:inline-block; transition:transform .3s cubic-bezier(.2,.8,.2,1); }}
.lead-go:hover .tri {{ transform:translateY(3px); }}
.reads .arr {{ display:inline-block; transition:transform .3s cubic-bezier(.2,.8,.2,1); }}
.read-art:hover .arr {{ transform:translateX(5px); }}
.read-paper:hover .arr {{ transform:translate(3px,-3px); }}
.thumb {{ overflow:hidden; }}
.thumb img {{ transition:transform .5s cubic-bezier(.2,.8,.2,1); }}
.row:hover .thumb img, .rel a:hover .thumb img {{ transform:scale(1.05); }}
.row h3 a, .today-h a {{ background:linear-gradient(currentColor,currentColor) left bottom / 0 1px no-repeat;
                         transition:background-size .35s cubic-bezier(.2,.8,.2,1), color .2s; }}
.row:hover h3 a, .today-h a:hover {{ background-size:100% 1px; text-decoration:none; }}
@media (prefers-reduced-motion:reduce) {{
  *, *::before, *::after {{ animation-duration:.01ms !important; animation-iteration-count:1 !important;
                            transition-duration:.01ms !important; transition-delay:0ms !important; }}
}}
.lead-art {{ display:block; margin-right:calc(-1 * var(--pad) + 24px); }}
.lead-art img {{ display:block; width:100%; height:auto; max-height:min(640px, calc(100vh - 170px)); object-fit:contain; }}
.lead-3d .v3d-view, .lead-3d.on .v3d-view, .lead-3d.loading .v3d-view {{ aspect-ratio:1000/780; max-height:min(640px, calc(100vh - 210px));
                                                                         margin:0 auto; }}
.lead-3d .fig {{ height:100%; object-fit:contain; }}
.lead-wait {{ display:none; position:absolute; inset:0; margin:0; align-items:center; justify-content:center;
              font-family:var(--mono); font-size:14px; letter-spacing:.12em; color:var(--mute); }}
@media (min-width:1121px) {{
  .js .lead-3d:not(.on):not(.failed) .fig {{ visibility:hidden; }}
  .js .lead-3d:not(.on):not(.failed) .lead-wait {{ display:flex; }}
}}

/* Today's Structure on the front page, in the order of the editor's layout: name, headline, figure, four lines, links */
.today {{ padding:var(--sec) 0 0; }}
.today .today-k {{ margin-top:18px; }}
.today-k {{ margin:0; display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 22px; }}
.today-k .tg {{ font-family:var(--sans); font-size:22px; font-weight:500; color:var(--ink); letter-spacing:.01em; line-height:1.4; }}
.today-k .dt {{ margin-left:auto; text-align:right; font-family:var(--mono); font-size:14px; letter-spacing:.04em; color:var(--ink2); }}
.today-k .dt b {{ margin-left:18px; font-weight:700; color:var(--ink); }}
.today-h {{ margin:12px 0 0; font-size:34px; line-height:1.42; font-weight:700; color:var(--ink); text-wrap:balance; }}
.today-sub {{ margin:10px 0 0; font-size:18px; line-height:1.75; color:var(--desc); }}
.today-fig {{ margin-top:22px; }}
.today-fig .v3d-bar {{ margin-top:10px; }}
.today-4 {{ margin:26px 0 0; max-width:1120px; }}
.today-4 div {{ display:grid; grid-template-columns:150px minmax(0,1fr); gap:16px; padding:7px 0; }}
.today-4 dt {{ font-size:15px; font-weight:700; color:var(--ink2); padding-top:3px; letter-spacing:.04em; }}
.today-4 dd {{ margin:0; font-size:16px; line-height:1.85; color:var(--ink2); }}
.reads {{ display:flex; flex-wrap:wrap; gap:14px 18px; margin-top:28px; }}
.reads a {{ display:block; padding:12px 22px 11px; min-width:260px; border:1px solid var(--ink); }}
.reads b {{ font-family:var(--sans); font-size:16px; font-weight:700; }}
.reads .arr {{ margin-left:12px; font-family:var(--mono); font-size:16px; }}
.reads small {{ display:block; margin-top:2px; font-family:var(--mono); font-size:11.5px; letter-spacing:.02em; }}
.read-art {{ background:var(--ink); color:#fff; }}
.read-art small {{ color:#C9D1DD; }}
.read-art:hover {{ background:var(--acc-ink); border-color:var(--acc-ink); color:#fff; text-decoration:none; }}
.read-paper {{ color:var(--ink); }}
.read-paper small {{ color:var(--mute); }}
.read-paper:hover {{ background:var(--ink); color:#fff; text-decoration:none; }}
.read-paper:hover small {{ color:#C9D1DD; }}
.rel {{ list-style:none; margin:0; padding:0; }}
.rel li {{ display:grid; grid-template-columns:minmax(0,1fr) 210px; gap:4px 20px; align-items:baseline; padding:9px 0; border-top:1px solid var(--hair); }}
.rel li:last-child {{ border-bottom:1px solid var(--hair); }}
.rel a {{ display:flex; flex-wrap:wrap; gap:0 14px; align-items:baseline; }}
.rel .rd {{ font-family:var(--mono); font-size:11.5px; color:var(--mute); }}
.rel b {{ font-family:var(--sans); font-size:15.5px; font-weight:700; }}
.rel .why {{ font-family:var(--mono); font-size:11.5px; color:var(--acc-ink); }}

/* an issue page's first screen: the issue (one date, large), the figures of the day and the bar of its articles */
.ihead {{ padding:40px 0 10px; }}
.ih-top {{ display:flex; flex-wrap:wrap; justify-content:space-between; align-items:flex-end; gap:20px 32px; }}
/* the neighbouring issues as buttons: arrow, what it is, its date; the missing one (no newer issue yet) stays, greyed */
.ih-nav, .issue-nav {{ display:flex; flex-wrap:wrap; gap:12px; }}
.ib {{ display:inline-flex; align-items:baseline; gap:10px; padding:12px 18px; font-family:var(--mono); font-size:13px;
       letter-spacing:.06em; color:var(--ink); box-shadow:inset 0 0 0 1px var(--ink); text-decoration:none;
       transition:background-color .25s, color .25s; }}
.ib b {{ font-weight:700; letter-spacing:.02em; }}
.ib .ar {{ display:inline-block; transition:transform .3s cubic-bezier(.2,.8,.2,1); }}
a.ib:hover {{ background:var(--ink); color:#fff; text-decoration:none; }}
a.ib.prev:hover .ar {{ transform:translateX(-4px); }}
a.ib.next:hover .ar {{ transform:translateX(4px); }}
.ib.off {{ color:var(--mute); box-shadow:inset 0 0 0 1px var(--hair); }}
.issue-nav {{ justify-content:space-between; margin-top:var(--sec); }}
.issue-nav .up {{ background:var(--ink); color:#fff; }}
.issue-nav a.up:hover {{ background:var(--acc-ink); }}
.ih-k {{ margin:0; font-family:var(--mono); font-size:14px; letter-spacing:.14em; text-transform:uppercase;
         color:var(--acc-ink); font-weight:700; }}
.ih-date {{ margin:4px 0 0; font-family:var(--latin); font-size:92px; line-height:1; letter-spacing:-.035em; font-weight:700; color:var(--ink); }}
.ih-src {{ margin:12px 0 0; font-size:14.5px; color:var(--desc); }}
.ih-fig {{ position:relative; margin-top:30px; padding-top:22px; border-top:1px solid var(--hair); }}
.ih-kpis {{ grid-template-columns:repeat(4,minmax(0,1fr)); }}
.kl i {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:7px; vertical-align:baseline; }}
.kl .sw-n {{ background:{CHART_NEW}; }}
.kl .sw-h {{ background:{CHART_HOT}; }}
.ih-bar {{ display:flex; gap:2px; height:14px; margin-top:24px; }}
.ih-bar a {{ flex:var(--w) 1 0; min-width:3px; background:{CHART_NEW}; transform-origin:left;
             transition:transform .7s cubic-bezier(.2,.8,.2,1) calc(var(--i) * 45ms + 250ms), filter .2s; }}
.ih-bar a.h {{ background:{CHART_HOT}; }}
.ih-bar a:first-child {{ border-radius:3px 0 0 3px; }}
.ih-bar a:last-child {{ border-radius:0 3px 3px 0; }}
.ih-bar a:hover, .ih-bar a:focus-visible {{ filter:brightness(1.2); outline:none; }}
.anim .ihead:not(.in) .ih-bar a {{ transform:scaleX(0); }}
.ih-scale {{ display:flex; justify-content:space-between; margin:6px 0 0; font-family:var(--mono); font-size:11px; color:var(--mute); }}
.ih-fig .tip {{ position:absolute; z-index:5; pointer-events:none; padding:6px 10px; background:var(--ink); color:#fff;
                font-family:var(--sans); font-size:12.5px; max-width:420px; }}
.ih-fig .tip[hidden] {{ display:none; }}
.ihead .lead-btns {{ margin-top:26px; }}
.lead-go b {{ margin-left:12px; font-weight:700; }}
.toc-sub {{ display:flex; flex-wrap:wrap; gap:12px; align-items:baseline; margin:22px 0 2px; font-family:var(--mono); font-size:13px;
            letter-spacing:.1em; text-transform:uppercase; color:var(--ink2); }}
.toc-sub span {{ color:var(--mute); }}
.part h2 .hd {{ margin-left:16px; font-family:var(--latin); font-weight:400; color:var(--mute); }}
.shell.solo {{ grid-template-columns:minmax(0,1fr); }}
/* an issue page: its contents sidebar sits close to the right edge of the window */
.wrap.sticky-side {{ max-width:none; padding-left:max(var(--pad), calc((100vw - 1600px) / 2 + var(--pad)));
                     padding-right:clamp(16px, 2vw, 32px); }}
.about-dl dd .s {{ display:block; text-wrap:balance; }}
.about-dl dd .w {{ white-space:nowrap; }}
.row.feat {{ border-left:4px solid var(--acc); padding-left:18px; margin-left:-22px; }}
.row.feat h3 {{ font-size:23px; }}
.feat-mark {{ display:block; text-transform:uppercase; margin:0 0 2px; font-family:var(--mono); font-size:11px; letter-spacing:.12em; color:var(--acc-ink); font-weight:700; }}

/* the article's four-line introduction: plain lines, one weight, no rules; the labels are quiet and sit beside the text */
.art-4 .open dd {{ color:var(--desc); font-size:15px; }}

/* proteins.html: browse by kind of protein, search by name or ID */
.pp-head h1 {{ margin:40px 0 4px; font-size:34px; line-height:1.4; }}
.pp-head p {{ margin:0; color:var(--desc); font-size:15px; }}
.pg {{ margin-top:40px; scroll-margin-top:20px; }}
.pr {{ display:grid; grid-template-columns:132px minmax(0,1fr) 250px; gap:6px 22px; padding:13px 0; border-top:1px solid var(--hair);
       align-items:baseline; }}
.pr .pd {{ font-family:var(--mono); font-size:12px; color:var(--mute); white-space:nowrap; }}
.pr .pt {{ font-family:var(--sans); font-size:17px; font-weight:700; line-height:1.55; }}
.pr .ps {{ display:block; margin-top:2px; font-family:var(--sans); font-size:13.5px; font-weight:400; color:var(--desc); }}
.pr .pm {{ font-family:var(--mono); font-size:11.5px; line-height:1.7; color:var(--ink2); overflow-wrap:anywhere; }}
.pr .pm i {{ color:var(--mute); font-style:normal; letter-spacing:.08em; margin-right:6px; }}
.pg-none {{ display:none; margin-top:30px; font-family:var(--mono); font-size:13px; color:var(--acc-ink); }}
.sd-cats a {{ display:flex; justify-content:space-between; gap:12px; padding:5px 0; font-family:var(--sans); font-size:14px; }}
.sd-cats .c {{ font-family:var(--mono); font-size:12px; color:var(--mute); }}

@media (min-width:1500px) {{
  body {{ font-size:17px; }}
}}
@media (max-width:1560px) {{
  :root {{ --col:200px; --side:290px; }}
  .shell {{ gap:0 40px; }}
  .side {{ padding-left:26px; }}
  .row {{ grid-template-columns:44px 300px minmax(0,1fr) 220px; gap:0 26px; }}
  .row .no {{ font-size:24px; }}
  .row h3 {{ font-size:20px; }}
  .margin .no {{ font-size:64px; }}
}}
@media (max-width:1120px) {{
  .lead-grid {{ grid-template-columns:minmax(0,1fr); min-height:0; gap:26px; padding-top:30px; }}
  .lead-art {{ margin-right:0; }}
  .shell {{ grid-template-columns:minmax(0,1fr); }}
  .row {{ grid-template-columns:44px 280px minmax(0,1fr); grid-template-areas:"no th rt" "no th rx"; gap:12px 24px; align-items:start; }}
  .wrap.sticky-side {{ padding-right:var(--pad); }}
  .side {{ border-left:0; padding-left:0; border-top:4px solid var(--ink); margin-top:20px; }}
  .sticky-side .side-in {{ position:static; max-height:none; overflow:visible; }}
  .side-only-issue {{ display:none; }}
  .side:not(:has(.sd:not(.side-only-issue))) {{ display:none; }}   /* nothing left in it on a narrow screen */
}}
@media (max-width:860px) {{
  :root {{ --pad:18px; --sec:64px; }}
  :root {{ --head:118px; }}
  .mast {{ padding-top:14px; }}
  .rules {{ margin-top:10px; }}
  .mast-row {{ position:relative; }}
  .vol {{ position:absolute; right:0; top:0; }}
  .menu .mb {{ width:44px; height:44px; }}
  .menu .mt {{ font-size:12px; }}
  .lead-h {{ font-size:min(17px, calc((100vw - 40px) / 21.8)); line-height:1.85; letter-spacing:0; }}
  .lead-h span {{ white-space:normal; }}
  .ix-h, .part h2 {{ flex-wrap:wrap; row-gap:4px; }}
  .ixm, .ix-h .ixm {{ font-size:12px; }}
  .ixm b, .ix-h .ixm b {{ margin-left:10px; }}
  .lead-go {{ margin-top:22px; font-size:13.5px; }}
  .ix-all {{ margin-left:0; flex-basis:100%; justify-content:space-between; }}
  .lead-btns {{ margin-top:22px; display:grid; grid-template-columns:minmax(0,1fr); gap:10px; }}
  .lead-btns .lead-go {{ justify-content:flex-start; }}
  .lead-go b {{ margin-left:auto; }}
  .about-grid {{ grid-template-columns:minmax(0,1fr); gap:30px; }}
  .about-dl div {{ grid-template-columns:minmax(0,1fr); gap:2px; }}
  .about-dl dd {{ font-size:15px; }}
  .lead-art img {{ max-height:none; }}
  .today {{ padding-top:var(--sec); }}
  .today-k .tg {{ font-size:18px; }}
  .ix-h .label, .part h2 {{ font-size:23px; }}
  .today-h {{ font-size:26px; }}
  .today-sub {{ font-size:15.5px; }}
  .today-4 div {{ grid-template-columns:minmax(0,1fr); gap:0; padding:6px 0; }}
  .today-4 dt {{ padding-top:0; font-size:13.5px; }}
  .today-4 dd {{ font-size:15px; }}
  .reads a {{ min-width:0; flex:1 1 100%; }}
  .word .name {{ font-size:32px; }}
  .brand {{ flex-wrap:wrap; gap:8px 0; }}
  .tag {{ padding-left:0; border-left:0; flex-basis:100%; font-size:13px; }}
  .mast-row {{ flex-wrap:wrap; align-items:flex-start; }}
  .vol {{ text-align:left; }}
  .art {{ grid-template-columns:minmax(0,1fr); gap:14px; }}
  .ih-date {{ font-size:56px; }}
  .ih-kpis {{ grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
  .ih-top {{ display:block; }}
  .ih-nav {{ margin-top:22px; }}
  .ih-nav, .issue-nav {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }}
  /* two lines: arrow and what it is, then the date */
  .ib {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:4px 8px; padding:11px 12px; font-size:11.5px; letter-spacing:.02em; }}
  .ib b {{ flex-basis:100%; font-size:13px; }}
  .ib.next {{ justify-content:flex-end; text-align:right; }}
  .ib.next .ar {{ order:1; }}
  .ib.next b {{ order:2; }}
  .issue-nav .up {{ grid-column:1 / -1; grid-row:2; justify-content:center; }}
  .issue-nav .up b {{ flex-basis:auto; }}
  .mail-grid {{ grid-template-columns:minmax(0,1fr); gap:20px; }}
  .pr {{ grid-template-columns:minmax(0,1fr); }}
  .row.feat, .row.hit {{ margin-left:0; padding-left:14px; }}
  .rel li {{ grid-template-columns:minmax(0,1fr); }}
  .row {{ grid-template-columns:minmax(0,1fr); grid-template-areas:"th" "rt" "rx"; gap:10px 0; align-items:start; padding:22px 0; }}
  .row .rp {{ margin-top:4px; }}
  .row .no {{ display:none; }}
  .rk {{ display:block; }}
  .row h3 {{ font-size:18px; }}
  .row .sub {{ font-size:13.5px; }}
  .rf div {{ grid-template-columns:54px minmax(0,1fr); }}
  .rp b, .rp .pl {{ display:inline; margin-right:8px; }}
  .past li {{ grid-template-columns:96px minmax(0,1fr); }}
  .margin .no {{ font-size:52px; margin-bottom:10px; }}
  .margin dl {{ display:grid; grid-template-columns:78px minmax(0,1fr); gap:3px 12px; }}
  .margin dt {{ margin-top:0; padding-top:2px; }}
  .margin .pdb {{ display:inline; margin-right:12px; }}
  .margin .first {{ grid-column:2; margin-top:4px; }}
  .margin .sticky {{ position:static; }}
  .art .art-h {{ font-size:23px; }}
  .art .subhead {{ font-size:15.5px; }}
  .ptitle {{ font-size:15px; }}
  .site dl {{ grid-template-columns:minmax(0,1fr); }}
  .lab div {{ grid-template-columns:92px 1fr; }}
  .legend {{ grid-template-columns:minmax(0,1fr); }}
  /* phone: the figures have a portrait version of their own (larger text, parts stacked) that fits the screen */
  .hasm .fig.d {{ display:none !important; }}
  .v3d.hasm:not(.show-cmp) .fig.m:not(.cmp), .cmpfig.hasm .fig.m, .v3d.hasm.show-cmp .fig.cmp.m {{ display:block; }}
  .v3d.on .v3d-view, .v3d.loading .v3d-view {{ aspect-ratio:1 / 1.15; }}
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


WEEKDAY_JA = "月火水木金土日"
WEEKDAY_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def date_parts(date: str) -> tuple[int, int, int, str]:
    y, m, d = (int(x) for x in date.split("-"))
    return y, m, d, WEEKDAY_JA[datetime.date(y, m, d).weekday()]


def doi_url(cand: dict) -> str:
    return f"https://doi.org/{cand['doi']}" if cand.get("doi") else \
        f"https://europepmc.org/article/PMC/{cand['pmcid']}"


EXTERNAL_LINK = re.compile(r'<a\s[^>]*href="https?://[^"]*"[^>]*>')


def new_tab(html_text: str) -> str:
    """Links to other sites (the paper, RCSB, UniProt, EMDB, ...) open in a new tab; links within the site do not."""
    def fix(m):
        tag = m.group(0)
        if "target=" in tag:
            return tag
        return tag[:-1] + ' target="_blank"' + ("" if "rel=" in tag else ' rel="noopener"') + ">"
    return EXTERNAL_LINK.sub(fix, html_text)


def page(title: str, body: str, depth: int, description: str = DESCRIPTION, noindex: bool = False,
         viewer: bool = False, script: str = "") -> str:
    up = "../" * depth
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<script>document.documentElement.className += " js";</script>'
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
            f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+JP:wght@400;700&family=Noto+Sans+JP:wght@400;500;700&display=swap">'
            f'<style>{CSS}</style></head><body><div class="page">{new_tab(body)}</div>'
            + (f'<script src="{up}assets/viewer.js" defer></script>' if viewer else "")
            + f'<script>{MENU_SCRIPT}{script}</script></body></html>')


def menu_html(up: str) -> str:
    """MENU: a round button that opens the four ways around the site (a <details>, so it works without JavaScript)."""
    latest = f'{up}issues/{LATEST["date"]}.html' if LATEST["date"] else f"{up}index.html"
    items = (("Top", f"{up}index.html"), ("Today’s Structure", f"{up}index.html#today"),
             ("Article Search", f"{up}proteins.html"), ("Latest Article", latest))
    links = "".join(f'<a href="{h}">{esc(t)}</a>' for t, h in items)
    return (f'<details class="menu"><summary><span class="mt">MENU</span>'
            f'<span class="mb" aria-hidden="true"><i></i><i></i><i></i></span></summary>'
            f'<nav class="menu-list" aria-label="サイト内のページ">{links}</nav></details>')


MENU_SCRIPT = r"""
(function () {
  /* The header: away while scrolling down, back on scrolling up, always there on the first screen. In-page links land
     at the top when the page goes down (the header slides away) and just under the header when it goes up. */
  var head = document.querySelector('.mast');
  if (!head) return;
  var last = window.scrollY, lead = document.querySelector('.lead');
  var jumped = false;   // after an in-page link down, the first-screen rule no longer holds the header (until back at the top)
  function firstScreen() {
    return (lead ? lead.getBoundingClientRect().bottom + window.scrollY : window.innerHeight) - head.offsetHeight;
  }
  function away(on) {
    if (on && document.querySelector('details.menu[open]')) return;
    head.classList.toggle('away', on);
    document.body.classList.toggle('head-away', on);
  }
  window.addEventListener('scroll', function () {
    var y = window.scrollY, d = y - last;
    if (y < head.offsetHeight) jumped = false;
    if (y < firstScreen() && !jumped) away(false);
    else if (d > 4) away(true);
    else if (d < -4) away(false);
    last = y;
  }, { passive: true });
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href^="#"]');
    var id = a && a.getAttribute('href').slice(1);
    var t = id && document.getElementById(id);
    if (!t) return;
    e.preventDefault();
    var y = t.getBoundingClientRect().top + window.scrollY;
    var down = y > window.scrollY;
    var top = down ? y - 16 : y - head.offsetHeight - 16;
    if (down) { jumped = true; away(true); }
    var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: Math.max(0, top), behavior: calm ? 'auto' : 'smooth' });
    if (history.replaceState) history.replaceState(null, '', '#' + id);
  });
})();
(function () {
  /* MENU: opens and closes with an animation (.on), so <details> is driven by hand; without JavaScript it is a
     plain <details>. Closes on a click elsewhere, on a link in it, and on Escape. */
  function toggle(d, on) {
    clearTimeout(d._t);
    if (on) {
      d.open = true;
      requestAnimationFrame(function () { requestAnimationFrame(function () { d.classList.add('on'); }); });
    } else {
      d.classList.remove('on');
      d._t = setTimeout(function () { if (!d.classList.contains('on')) d.open = false; }, 320);
    }
  }
  [].forEach.call(document.querySelectorAll('details.menu'), function (d) {
    d.querySelector('summary').addEventListener('click', function (e) { e.preventDefault(); toggle(d, !d.classList.contains('on')); });
  });
  document.addEventListener('click', function (e) {
    [].forEach.call(document.querySelectorAll('details.menu.on'), function (d) {
      if (!d.contains(e.target) || e.target.closest('.menu-list a')) toggle(d, false);
    });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') [].forEach.call(document.querySelectorAll('details.menu.on'), function (d) { toggle(d, false); });
  });
})();
(function () {
  /* Motion with a job: blocks come up as they enter the view (the reader sees where the next part starts), counts
     count up and the charts grow once they are in view. Only with JavaScript and when the reader has not asked for
     reduced motion; otherwise everything is simply there, final values included. */
  var root = document.documentElement;
  var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (calm || !('IntersectionObserver' in window)) return;
  root.classList.add('anim');
  var SEL = '.ix-h, .about-dl > div, .about-side, .about .jl, .about > .wrap > .about-sub, .today-k, .today-h, .today-sub, '
          + '.today-fig, .today-4 > div, .reads, .rows > li, .cats, .past li, .find, .mail-grid, .shell:not(.sticky-side) .side .sd, '
          + '.ihead, .part, .art, .rest, .pp-head, .pg .pr, .queue li';
  var els = [].slice.call(document.querySelectorAll(SEL));
  var seen = new Map();
  els.forEach(function (el) {                     // a short stagger among siblings that come in together
    var k = seen.get(el.parentNode) || 0;
    seen.set(el.parentNode, k + 1);
    el.style.setProperty('--d', Math.min(k, 6) * 70 + 'ms');
    el.classList.add('rv');
  });
  function countUp(el) {
    var to = +el.getAttribute('data-count'), t0 = null, dur = 1200;
    function f(t) {
      t0 = t0 || t;
      var p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(to * e);
      if (p < 1) requestAnimationFrame(f);
    }
    requestAnimationFrame(f);
  }
  var counts = [].slice.call(document.querySelectorAll('[data-count]'));
  counts.forEach(function (el) { el.textContent = '0'; });
  var io = new IntersectionObserver(function (es) {
    es.forEach(function (e) {
      if (!e.isIntersecting) return;
      e.target.classList.add('in');
      [].forEach.call(e.target.querySelectorAll('[data-count]'), countUp);
      io.unobserve(e.target);
    });
  }, { rootMargin: '0px 0px -6% 0px', threshold: 0 });
  els.forEach(function (el) { io.observe(el); });
  counts.forEach(function (el) { if (!el.closest('.rv')) countUp(el); });
})();
(function () {
  /* the papers-per-issue chart and an issue's bar of articles: one tooltip, on hover and on keyboard focus (the values
     are also in the page: the table, the article list) */
  [].forEach.call(document.querySelectorAll('.ppi, .ih-fig'), function (fig) {
    var tip = fig.querySelector('.tip');
    if (!tip) return;
    function show(c) {
      tip.textContent = c.getAttribute('data-tip');
      tip.hidden = false;
      var r = c.getBoundingClientRect(), f = fig.getBoundingClientRect();
      var x = Math.max(0, Math.min(f.width - tip.offsetWidth, r.left - f.left + r.width / 2 - tip.offsetWidth / 2));
      tip.style.left = x + 'px';
      tip.style.top = ((c.querySelector('.stack') || c).getBoundingClientRect().top - f.top - tip.offsetHeight - 10) + 'px';
    }
    function hide() { tip.hidden = true; }
    [].forEach.call(fig.querySelectorAll('[data-tip]'), function (c) {
      c.addEventListener('pointerenter', function () { show(c); });
      c.addEventListener('focus', function () { show(c); });
      c.addEventListener('pointerleave', hide);
      c.addEventListener('blur', hide);
    });
  });
})();
"""


def masthead(depth: int, right: str = "") -> str:
    """The wordmark row inside the page width, with MENU on the right; the double rule under it spans the whole window.
    The whole header stays at the top of the window while the page scrolls."""
    up = "../" * depth
    return (f'<header class="mast"><div class="wrap"><div class="mast-row">'
            f'<div class="brand"><a class="word" href="{up}index.html"><img src="{up}assets/logo-mark.svg" alt="">'
            f'<span class="name">{esc(brand.SITE_NAME)}</span></a>'
            f'<p class="tag">{esc(TAGLINE)}</p></div>'
            f'<div class="vol">{right}{menu_html(up)}</div></div></div><div class="rules"></div></header>')


def shell(main: str, side: str, sticky: bool = False) -> str:
    """Main column and right sidebar. Only the sidebar of an issue page (its contents) stays in view while scrolling.
    With no sidebar (the front page) the main column takes the whole width."""
    if not side:
        return f'<div class="wrap shell solo"><main class="main">{main}</main></div>'
    return (f'<div class="wrap shell{" sticky-side" if sticky else ""}"><main class="main">{main}</main>'
            f'<aside class="side"><div class="side-in">{side}</div></aside></div>')


def colophon(extra: str = "") -> str:
    return (f'<footer class="colo"><div class="wrap colophon"><p>{esc(brand.SITE_NAME)} — 構造生物学者のための毎日の新着構造。</p>'
            f'{extra}<p>β版</p></div></footer>')   # how the articles are made is told in About FoldFeed


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
    """Journals of every paper so far (a paper that came back in a later issue counts once), most papers first."""
    counts: dict[str, int] = {}
    seen: set[str] = set()
    for date in issues:
        _, picks, _ = load_issue(date, root)
        for cand, _ in picks:
            name = journal_name(cand)
            if name and cand["pmcid"] not in seen:
                seen.add(cand["pmcid"])
                counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))


# a paper whose protein (UniProt entry) had no structure in the PDB before it; the other new structures add a state, a
# ligand or a partner to a protein solved before (2026-10-18: "PDB 初構造" and "初めて解かれた構造" were both misread)
FIRST_LABEL = "初めて構造が解かれたタンパク質"
NOTABLE_NEW = "既知のタンパク質の新しい構造"   # the Notable Structure row of a new structure of a protein solved before


# ---------- one article ----------

def quotes(evidence: list) -> str:
    """The paper's own words behind one explanation: the label 原文 once, and beside it each quote with its section
    (a quote wraps under itself, never under the label)."""
    if not evidence:
        return ""
    qs = "".join(f'<p><i>“{esc(e["quote"])}”</i><span>{esc(e.get("section", ""))}</span></p>' for e in evidence)
    return f'<div class="src"><b>原文</b><div class="qs">{qs}</div></div>'


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
        pdb_dd = "—" if hot else "本文に記載なし"
    pdb_label = "EMDB" if not own and cand["kind"] == "new_map_only" else ("PDB（使用）" if hot else "PDB")
    prots = cand.get("proteins", [])
    uni = ", ".join(f'<a href="https://www.uniprot.org/uniprotkb/{esc(p["uniprot"])}">{esc(p.get("gene") or p["uniprot"])}</a>'
                    for p in prots[:4])
    orgs = sorted({o for s in rel for q in s.get("polymers", []) for o in q["organisms"]})
    rows = [("Target", esc(pick.get("target_en") or pick["target_label"])), ("Method", esc(method)), (pdb_label, pdb_dd),
            ("Ligand", esc(pick.get("ligand_label") or ("—" if hot else render.ligand_line(cand))))]
    if hot:
        rows.insert(0, ("Notable", esc(impact.TYPES.get(impact_type or "", "新しい構造以外"))))
    elif render.part_of(cand, pick) == "impact":
        rows.insert(0, ("Notable", NOTABLE_NEW))
    if uni:
        rows.append(("UniProt", uni))
    if orgs:
        rows.append(("Organism", "<i>" + esc(", ".join(orgs[:3])) + "</i>"))
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
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
    groups = ((f'<section><h4>Proteins &amp; Nucleic Acids</h4><ul>{prot}</ul></section>' if prot else "") +
              (f'<section><h4>Ligands</h4><ul>{lig}</ul></section>' if lig else ""))
    return f'<div class="legend">{groups}</div>' if groups else ""


def viewer_bar(info: dict, up: str, view: dict | None = None) -> tuple[str, str, str]:
    """Attributes for .v3d, the "loading" overlay and the button bar (3D viewer, loaded on demand by assets/viewer.js).
    The viewer starts from the figure's own view (info["view"], or `view`): the same side up, membrane proteins upright."""
    pdb = info.get("pdb_id")
    view = view or info.get("view")
    ents = [{"id": x["entity"], "color": x["color"]} for x in info.get("legend", []) if x.get("entity")]
    if not pdb or not ents:
        return "", "", ""
    ligs = [{"id": x["id"], "label": x["label"], "color": x["color"], "entity": x.get("entity", []),
             "sites": x.get("sites", [])} for x in info.get("ligands", [])]
    site = (info.get("site") or {}).get("ligand", "")
    attrs = (f' data-pdb="{esc(pdb)}" data-assets="{up}assets" data-site="{esc(site)}"'
             f' data-ents="{esc(json.dumps(ents))}" data-ligs="{esc(json.dumps(ligs, ensure_ascii=False))}"'
             + (f' data-view="{esc(json.dumps(view))}"' if view else ""))
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
           f'<span class="help on-only">ドラッグで回転 · クリックで拡大して周囲のアミノ酸を表示</span>'
           f'</div><p class="v3d-msg" hidden></p>')
    return attrs, wait, bar


SVG_SIZE = re.compile(r'<svg[^>]*?\swidth="([\d.]+)"\s+height="([\d.]+)"')


def wh(path: Path) -> str:
    """ width/height attributes from an SVG's own size, so the page keeps the figure's place before it has loaded
    (the figures load lazily; without a size, the page below them shifts and an anchor jump lands short)."""
    try:
        with open(path, encoding="utf-8") as f:
            m = SVG_SIZE.search(f.read(600))
    except OSError:
        return ""
    return f' width="{m.group(1)}" height="{m.group(2)}"' if m else ""


def figures_html(cand: dict, pick: dict, date: str, root: Path, fig_base: str, up: str = "../") -> str:
    pm = cand["pmcid"]
    fig_dir = root / date / "figures"
    if not (fig_dir / f"{pm}.svg").exists():
        return ""
    info = read_json(fig_dir / f"{pm}.json") if (fig_dir / f"{pm}.json").exists() else {}
    attrs, wait, bar = viewer_bar(info, up)
    has_m = (fig_dir / f"{pm}-m.svg").exists()   # the phone version of the figure (portrait, larger text)
    img = (f'<img class="fig d" src="{fig_base}/{date}/{esc(pm)}.svg"{wh(fig_dir / f"{pm}.svg")} '
           f'alt="構造図：{esc(pick["target_label"])}" loading="lazy">')
    if has_m:
        img += f'<img class="fig m" src="{fig_base}/{date}/{esc(pm)}-m.svg"{wh(fig_dir / f"{pm}-m.svg")} alt="" loading="lazy">'
    out = [f'<div class="v3d{" hasm" if has_m else ""}"{attrs}><div class="v3d-view">{img}<div class="v3d-canvas"></div>{wait}</div>{bar}</div>']
    if render.is_impact(pick) and info.get("pdb_id"):
        out.append(f'<p class="pdbnote">PDB {esc(info["pdb_id"])}（論文が使った既存の構造）</p>')
    out.append(legend_html(info))
    if info.get("compare") and (fig_dir / f"{pm}-compare.svg").exists():
        has_cm = (fig_dir / f"{pm}-compare-m.svg").exists()
        alt = f'構造の比較：{esc("と".join(x["title"] for x in info["compare"]["panels"]))}'
        out.append(f'<div class="cmpfig{" hasm" if has_cm else ""}"><img class="fig d" src="{fig_base}/{date}/{esc(pm)}-compare.svg"'
                   f'{wh(fig_dir / f"{pm}-compare.svg")} '
                   f'alt="{alt}" loading="lazy">'
                   + (f'<img class="fig m" src="{fig_base}/{date}/{esc(pm)}-compare-m.svg"{wh(fig_dir / f"{pm}-compare-m.svg")} '
                      f'alt="" loading="lazy">' if has_cm else "")
                   + '</div>')
    return "".join(out)


def intro_html(pick: dict) -> str:
    """The article's short introduction: four lines (question, approach, result, learned) and, when the paper states
    one, a fifth (what is still open)."""
    s = pick["summary"]
    if "question" in s:
        rows = "".join(f'<div class="{k}"><dt>{label}</dt><dd>{esc(s[k])}</dd></div>'
                       for k, label in {**SUMMARY_FIELDS, **SUMMARY_OPTIONAL}.items() if s.get(k))
    else:  # an issue written before the four-line introduction (e.g. an old pull request being previewed)
        rows = "".join(f'<div><dt>{label}</dt><dd>{esc(s.get(k, ""))}</dd></div>'
                       for k, label in (("background", "背景"), ("approach", "やったこと"), ("findings", "分かったこと")))
    return f'<dl class="today-4 art-4">{rows}</dl>'   # the look of Today's Structure on the front page


def related_html(items: list[dict], up: str) -> str:
    if not items:
        return ""
    lis = "".join(f'<li><a href="{up}issues/{x["row"]["date"]}.html#a{x["row"]["n"]}">'
                  f'<span class="rd">{x["row"]["date"][5:].replace("-", ".")} · {pad(x["row"]["n"])}</span>'
                  f'<b>{esc(x["row"]["pick"]["headline"])}</b></a><span class="why">{esc(x["why"])}</span></li>'
                  for x in items)
    return f'<h3>Related Articles</h3><ul class="rel">{lis}</ul>'


def no_pdb_note(cand: dict) -> str:
    """For a paper whose text names no PDB ID (it says the structures were deposited, but the IDs are elsewhere or not
    yet given): the article is written all the same, without a figure, and opens by saying so (the owner, 2026-10-18)."""
    if cand["kind"] != "new_structure_ids_unlisted" or cand.get("own_structures"):
        return ""
    return ('<p class="nopdb">論文の本文から PDB ID にたどり着けなかったため、構造を PDB と照合できず、'
            '構造図は載せていません。</p>')


def article_html(cand: dict, pick: dict, date: str, n: int, root: Path, fig_base: str,
                 impact_type: str | None = None, up: str = "../", related: list[dict] | None = None) -> str:
    lic = esc((cand.get("license") or "ライセンス不明").upper())
    hls = "".join(f'<div class="hl"><p>{esc(h["text"])}</p>{quotes(h.get("evidence"))}</div>'
                  for h in pick["highlights"])
    notes = pick.get("lab_notes") or {}
    filled = [(LAB_NOTE_FIELDS[k], notes[k]) for k in LAB_NOTE_FIELDS if notes.get(k)]
    lab = ""
    if filled:
        rows = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in filled)
        ev = quotes(notes.get("evidence"))
        lab = f'<h3>Lab Notes</h3><dl class="lab">{rows}</dl>' + (f'<div class="lab-src">{ev}</div>' if ev else "")
    text = (f'<div class="text">'
            f'<p class="kicker"><b>{esc(cand["journal"] or cand["journal_abbrev"])}</b> · {esc(cand["pub_date"])} · {lic}</p>'
            f'<h2 class="art-h">{esc(pick["headline"])}</h2>'
            f'<p class="subhead">{esc(pick.get("subhead") or "")}</p>'
            f'<p class="ptitle"><span class="pl">Paper</span><span lang="en">{esc(cand["title"])}</span></p>'
            f'{figures_html(cand, pick, date, root, fig_base, up) or no_pdb_note(cand)}'
            f'{intro_html(pick)}{paper_link(cand)}'
            f'<h3>Highlights</h3>{hls}{lab}{related_html(related or [], up)}</div>')
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
    "new": ("New Structure", "構造が初めて解かれたタンパク質。"),
    "impact": ("Notable Structure", "既に構造があるタンパク質の新しい構造（別の状態・リガンド・相手との複合体など）と、"
                            "既存の構造を使って仕組みの解明や創薬に迫った注目の研究。"),
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
           else "PDB ID が本文にない" if cand["kind"] == "new_structure_ids_unlisted"
           else "EMDB マップのみ" if cand["kind"] == "new_map_only" else "構造図なし")
    return f'<a class="thumb none" href="{href}">{why}</a>'


def facts(cand: dict, pick: dict) -> dict:
    """The few facts shown beside a headline: target, method, resolution, PDB (the structure of the figure),
    ligand and UniProt. All from the pick and PDB data."""
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
        pdb = "公開待ち" if own else ("—" if hot else "本文に記載なし")
    prots = cand.get("proteins", [])
    return {"target": pick.get("target_en") or pick["target_label"], "method": method or "—", "res": res or "—", "pdb": pdb,
            "pdb_id": shown["pdb_id"] if shown else None,
            "ligand": pick.get("ligand_label") or ("—" if hot else render.ligand_line(cand)),
            "uniprot": [(p["uniprot"], p.get("gene") or p["uniprot"]) for p in prots[:3]]}


def row_html(cand: dict, pick: dict, n: int, date: str, root: Path, fig_base: str, href_base: str,
             feat: bool = False) -> str:
    """One row of the index: number | structure (large) | headline and supplement | TARGET METHOD RES. and PDB."""
    href = f"{href_base}#a{n}"
    f = facts(cand, pick)
    dl = "".join(f"<div><dt>{k}</dt><dd>{esc(v)}</dd></div>"
                 for k, v in (("TARGET", f["target"]), ("METHOD", f["method"]), ("RES.", f["res"])))
    mark = '<span class="feat-mark">Today&#8217;s Structure</span>' if feat else ""
    cats = " ".join(pick.get("categories") or [])
    return (f'<li class="row{" feat" if feat else ""}" data-cats="{esc(cats)}"><span class="no">{pad(n)}</span><div class="rt">{mark}'
            f'<p class="rk"><span class="n">{pad(n)}</span></p>\n'
            f'<h3><a href="{href}">{esc(pick["headline"])}</a></h3>\n'
            f'<p class="sub">{esc(pick.get("subhead") or "")}</p>'
            f'<p class="rm">{esc(cand["journal_abbrev"] or cand["journal"])}</p></div>'
            f'{thumb_html(cand, pick, date, root, fig_base, href)}<div class="rx"><dl class="rf">{dl}</dl>'
            f'<div class="rp"><span class="pl">PDB</span><b>{esc(f["pdb_id"] or f["pdb"])}</b></div></div></li>')


HOME_PARTS = {"new": "Today’s New Structure", "impact": "Today’s Notable Structure"}


def contents_html(picks: list, date: str, root: Path, fig_base: str, href_base: str = "",
                  featured: str | None = None, mark: bool = False) -> str:
    """The index in two blocks, 新しい構造 and 注目の構造, each under a rule with its count.

    On the front page the article shown as 今日の一本 is not repeated here (it keeps its number, so the list may
    skip one); on an issue page (mark=True) it stays in the list, marked.
    """
    new, hot = render.split_sections(picks)
    number = {c["pmcid"]: n for n, (c, _) in enumerate(new + hot, 1)}
    out = ""
    for key, items, cls in (("new", new, ""), ("impact", hot, " hot")):
        body = "".join(row_html(c, pk, number[c["pmcid"]], date, root, fig_base, href_base,
                                feat=mark and c["pmcid"] == featured)
                       for c, pk in items if mark or c["pmcid"] != featured)
        if not body:
            continue
        if mark:   # an issue page: the contents, under one heading (the articles follow under New / Notable Structure)
            out += (f'<h3 class="toc-sub{cls}">{PARTS[key][0]}<span>{len(items)}</span></h3><ol class="rows">{body}</ol>')
        else:   # the front page: the way to the whole issue sits at the start of the lists, beside the first heading
            y, m, d, _ = date_parts(date)
            go = (f'<a class="ix-all" href="{href_base}">{y}.{m:02d}.{d:02d}のすべての解説記事一覧'
                  f'<span class="arr" aria-hidden="true">→</span></a>') if not out else ""
            out += (f'<div class="ix-h{cls}" id="{key}-list"><h2 class="label">{HOME_PARTS[key]}</h2>'
                    f'<span class="ixm"><b>{n_articles(len(items))}</b></span>{go}</div><ol class="rows">{body}</ol>')
    if mark and out:
        out = f'<div class="ix-h"><h2 class="label">Contents</h2><span class="ixm"><b>{n_articles(len(picks))}</b></span></div>' + out
    return out


def n_articles(n: int) -> str:
    return f"{n} {'Article' if n == 1 else 'Articles'}"


def feature_of(feature: dict | None, picks: list) -> tuple[int, dict, dict] | None:
    """(article number, cand, pick) of the day's Today's Structure, or None."""
    if not feature:
        return None
    for n, (cand, pk) in enumerate(picks, 1):
        if cand["pmcid"] == feature["pmcid"]:
            return n, cand, pk
    return None


def lead_html(feature: dict | None, picks: list, date: str, root: Path, fig_base: str) -> str:
    """The first screen of the front page: what the site is (three lines), the button down to Today's Structure, and
    on the right Today's Structure in the 3D viewer, opened by itself on a wide screen, with no text around it but
    "読み込み中" while it loads. On a phone (where it is not opened) and when it cannot load, the same structure is
    shown as a drawing from the same view (pipeline.render: <pmcid>-hero.svg)."""
    lines = "".join(f'<span style="--i:{i}">{esc(t)}</span>' for i, t in enumerate(LEAD))
    go = (('<a class="lead-go" href="#today"><span class="tri" aria-hidden="true">▽</span>'
           'Picked up: Today’s Structure</a>') if feature_of(feature, picks) else "")
    new, hot = render.split_sections(picks)
    more = "".join(f'<a class="lead-go alt" href="#{key}"><span class="tri" aria-hidden="true">▽</span>{label}<b>{n}</b></a>'
                   for key, label, n in (("new-list", "New Structure", len(new)), ("impact-list", "Notable Structure", len(hot))) if n)
    go = (f'<div class="lead-btns"><a class="lead-go alt" href="#about"><span class="tri" aria-hidden="true">▽</span>'
          f'About FoldFeed</a>{go}{more}</div>')   # in the order of the page
    hit = feature_of(feature, picks)
    art_html = ""
    if hit and (root / date / "figures" / f"{hit[1]['pmcid']}-hero.svg").exists():
        pm, pk = hit[1]["pmcid"], hit[2]
        fd = root / date / "figures"
        info = read_json(fd / f"{pm}.json") if (fd / f"{pm}.json").exists() else {}
        attrs, _, _ = viewer_bar(info, "", info.get("hero_view"))
        img = (f'<img class="fig" src="{fig_base}/{date}/{esc(pm)}-hero.svg"{wh(fd / f"{pm}-hero.svg")} '
               f'alt="構造図：{esc(pk.get("target_en") or pk["target_label"])}">')
        if attrs:   # opens by itself on a wide screen (assets/viewer.js). While it loads, only "読み込み中" shows; the
            # drawing is for a phone (where it does not open) and for a browser that cannot load it. No buttons, no help.
            art_html = (f'<div class="lead-art"><div class="v3d lead-3d" data-auto="wide"{attrs}><div class="v3d-view">{img}'
                        f'<div class="v3d-canvas"></div><p class="lead-wait" aria-live="polite">読み込み中</p></div></div></div>')
        else:
            art_html = f'<a class="lead-art" href="#today" tabindex="-1">{img}</a>'
    return (f'<section class="lead"><div class="wrap lead-grid"><div class="lead-text">'
            f'<h1 class="lead-h">{lines}</h1>{go}</div>{art_html}</div></section>')


def feature_figure(cand: dict, pk: dict, date: str, root: Path, fig_base: str, href: str, up: str = "") -> str:
    """Today's Structure's figure: the article's own figure (overview and binding site), with the comparison figure on
    a second tab when there is one, and the 3D viewer."""
    pm = cand["pmcid"]
    fd = root / date / "figures"
    big = fd / f"{pm}.svg"
    info = read_json(fd / f"{pm}.json") if (fd / f"{pm}.json").exists() else {}
    attrs, wait, bar = viewer_bar(info, up) if big.exists() else ("", "", "")
    has_cmp = bool(info.get("compare")) and (fd / f"{pm}-compare.svg").exists()
    tabs = cmp_img = ""
    if has_cmp:
        first_tab = "全体と結合部位" if info.get("site") else "全体"
        tabs = (f'<div class="figtabs"><button type="button" data-fig="main" class="on">{first_tab}</button>'
                f'<button type="button" data-fig="cmp">構造の比較</button></div>')
        cmp_img = (f'<img class="fig cmp d" src="{fig_base}/{date}/{esc(pm)}-compare.svg"{wh(fd / f"{pm}-compare.svg")} '
                   f'alt="構造の比較：{esc("と".join(x["title"] for x in info["compare"]["panels"]))}">')
        if (fd / f"{pm}-compare-m.svg").exists():
            cmp_img += f'<img class="fig cmp m" src="{fig_base}/{date}/{esc(pm)}-compare-m.svg"{wh(fd / f"{pm}-compare-m.svg")} alt="">'
    alt = f'構造図：{esc(pk.get("target_en") or pk["target_label"])}'
    if attrs:   # the structure can be turned in place (Mol*, loaded only when asked for)
        has_m = (fd / f"{pm}-m.svg").exists()
        main_m = f'<img class="fig m" src="{fig_base}/{date}/{esc(pm)}-m.svg"{wh(fd / f"{pm}-m.svg")} alt="">' if has_m else ""
        return (f'<div class="v3d{" hasm" if has_m else ""}"{attrs}>{tabs}<div class="v3d-view"><a href="{href}"><img class="fig d" '
                f'src="{fig_base}/{date}/{esc(pm)}.svg"{wh(big)} alt="{alt}">{main_m}</a>{cmp_img}'
                f'<div class="v3d-canvas"></div>{wait}</div>{bar}</div>')
    if big.exists():
        return f'<a class="thumb" href="{href}"><img src="{fig_base}/{date}/{esc(pm)}.svg"{wh(big)} alt="{alt}"></a>'
    return thumb_html(cand, pk, date, root, fig_base, href)


def paper_button(cand: dict) -> str:
    journal = cand.get("journal_abbrev") or cand.get("journal") or "掲載誌"
    return (f'<a class="read-paper" href="{doi_url(cand)}" rel="noopener"><b>原著論文を開く</b><span class="arr" aria-hidden="true">↗</span>'
            f'<small>{esc(journal)} · 英語 · 外部サイト</small></a>')


def paper_link(cand: dict) -> str:
    """An article's way to the paper, as the button Today's Structure has on the front page."""
    return f'<div class="reads">{paper_button(cand)}</div>'


def read_links(cand: dict, href: str) -> str:
    """The two ways on from a summary, told apart by who wrote it, in which language and where it lives: FoldFeed's
    article (Japanese, on this site) and the paper itself (English, on the journal's site)."""
    return (f'<div class="reads">'
            f'<a class="read-art" href="{href}"><b>解説記事を読む</b><span class="arr" aria-hidden="true">→</span>'
            f'<small>FoldFeed の日本語の解説（このサイト）</small></a>{paper_button(cand)}</div>')


def today_html(feature: dict | None, picks: list, date: str, root: Path, fig_base: str, href_base: str = "",
               up: str = "") -> str:
    """Today's Structure (今日の一本) on the front page: the English name of the target, the headline, the figure, the
    four-line introduction and the two links (the article, the paper). The reason it was picked is for the pull request."""
    hit = feature_of(feature, picks)
    if not hit:
        return ""
    n, cand, pk = hit
    href = f"{href_base}#a{n}"
    y, m, d, _ = date_parts(date)
    part = PARTS[render.part_of(cand, pk)][0]
    rows = "".join(f'<div><dt>{label}</dt><dd>{esc(pk["summary"][k])}</dd></div>'
                   for k, label in SUMMARY_FIELDS.items() if pk["summary"].get(k))
    return (f'<section class="today" id="today"><div class="wrap">'
            f'<div class="ix-h"><h2 class="label">Picked up Today’s Structure</h2></div>'
            f'<p class="today-k"><span class="tg">{esc(pk.get("target_en") or pk["target_label"])}</span>'
            f'<span class="dt">{part} · Issue of {y}.{m:02d}.{d:02d}<b>Number {pad(n)}</b></span></p>'
            f'<h3 class="today-h"><a href="{href}">{esc(pk["headline"])}</a></h3>'
            f'<p class="today-sub">{esc(pk.get("subhead") or "")}</p>'
            f'<div class="today-fig">{feature_figure(cand, pk, date, root, fig_base, href, up)}</div>'
            f'<dl class="today-4">{rows}</dl>{read_links(cand, href)}</div></section>')


def part_html(key: str, count: int, date: str) -> str:
    """An issue page's 'New Structure 2026.10.18' heading over its articles, with how many on the right."""
    title, note = PARTS[key]
    y, m, d, _ = date_parts(date)
    return (f'<section class="part" id="{key}"><h2>{title}<span class="hd">{y}.{m:02d}.{d:02d}</span>'
            f'<span class="ixm"><b>{n_articles(count)}</b></span></h2></section>')


def counts_text(n_new: int, n_hot: int, sep: str = "<br>") -> str:
    return f"New Structure {n_new} 本" + (f"{sep}Notable Structure {n_hot} 本" if n_hot else "")


def toc_side(picks: list, featured: str | None) -> str:
    """The sidebar of an issue page (beside the articles): the way back to the top of the issue, then every article,
    grouped, each a link to its anchor."""
    new, hot = render.split_sections(picks)
    number = {c["pmcid"]: n for n, (c, _) in enumerate(new + hot, 1)}
    out = ""
    for key, items in (("new", new), ("impact", hot)):
        if items:
            links = "".join(f'<a href="#a{number[c["pmcid"]]}"><b>{pad(number[c["pmcid"]])}</b>'
                            f'<span>{esc(pk["headline"])}</span></a>' for c, pk in items)
            out += f'<h3>{PARTS[key][0]}</h3>{links}'
    up = '<a class="sd-up" href="#issue-top"><span aria-hidden="true">↑</span> Top of This Issue</a>'
    return sd("In This Issue", f'{up}<div class="sd-toc">{out}</div>', "side-only-issue")


def index_span(index_date: str, index_from: str | None = None) -> str:
    """'10月13日' for one day, '10月11日〜14日' for a catch-up window of several days."""
    if not index_from or index_from == index_date:
        return jp_date_short(index_date)
    (_, m1, d1), (_, m2, d2) = ((int(x) for x in v.split("-")) for v in (index_from, index_date))
    return f"{m1}月{d1}日〜{d2}日" if m1 == m2 else f"{m1}月{d1}日〜{m2}月{d2}日"


READ_CHARS_PER_MIN = 500   # Japanese technical prose, read with care
FIGURE_MIN = 0.5           # time with each article's figures


def reading_minutes(pick: dict) -> float:
    """How long one article takes to read: its Japanese text (headline, supplement, the introduction, highlights,
    lab notes) at READ_CHARS_PER_MIN, plus FIGURE_MIN for its figures. The quoted English is not counted."""
    s = pick.get("summary") or {}
    parts = [pick.get("headline") or "", pick.get("subhead") or ""]
    parts += [s.get(k) or "" for k in (*SUMMARY_FIELDS, *SUMMARY_OPTIONAL)]
    parts += [h.get("text") or "" for h in pick.get("highlights") or []]
    parts += [v for k, v in (pick.get("lab_notes") or {}).items() if k in LAB_NOTE_FIELDS and v]
    return sum(len(x) for x in parts) / READ_CHARS_PER_MIN + FIGURE_MIN


def issue_nav(prev_date: str | None, next_date: str | None, cls: str, top: bool = False) -> str:
    """Previous / next issue as buttons, each saying what it is and its date (and, at the foot of a page, the way back
    to the top). With no newer issue (the latest), "Next issue" stays in its place, greyed, so the pair does not move."""
    def link(date, label, side):
        ar = f'<span class="ar" aria-hidden="true">{"←" if side == "prev" else "→"}</span>'
        if not date:
            return f'<span class="ib {side} off" aria-disabled="true"><span class="t">{label}</span>{ar}</span>' if side == "next" else ""
        y, m, d, _ = date_parts(date)
        body = f'<span class="t">{label}</span><b>{y}.{m:02d}.{d:02d}</b>'
        return f'<a class="ib {side}" href="{date}.html">' + (ar + body if side == "prev" else body + ar) + '</a>'
    mid = '<a class="ib up" href="../index.html"><span class="t">FoldFeed</span><b>Top</b></a>' if top else ""
    return (f'<nav class="{cls}" aria-label="ほかの号">{link(prev_date, "Previous issue", "prev")}{mid}'
            f'{link(next_date, "Next issue", "next")}</nav>')


def issue_head(date: str, number: int | None, picks: list, prev_date: str | None, next_date: str | None,
               index_date: str | None = None, index_from: str | None = None, nature: bool = False) -> str:
    """The first screen of an issue page: which issue it is (the date, once, large), how many articles, how many of them
    New and Notable Structure, and about how long reading them all takes, as figures that count up and a bar of the
    articles (one segment each, as long as its reading time, coloured by its part; each a link to it), with buttons
    down to the two parts and the neighbouring issues."""
    y, m, d, _ = date_parts(date)
    new, hot = render.split_sections(picks)
    mins = [reading_minutes(pk) for _, pk in picks]
    total = max(1, round(sum(mins))) if picks else 0
    kicker = (f"Issue No. {number}" if number else "Preview") + f" · {WEEKDAY_EN[datetime.date(y, m, d).weekday()]}"
    # (the papers not written up are listed at the foot of the page, under Not Covered, not counted here)
    src = (f'<p class="ih-src">{index_span(index_date, index_from)}に論文データベースへ登録された論文'
           + ('と、同じ日に Nature 系の雑誌で出版された論文' if nature else '') + 'から</p>') if index_date else ""
    tiles = [("Articles", len(picks), ""), ("New Structure", len(new), "n"), ("Notable Structure", len(hot), "h")]
    kpis = "".join(f'<div class="kpi"><span class="kl">{f"<i class=sw-{c}></i>" if c else ""}{k}</span>'
                   f'<span class="kv" data-count="{v}">{v}</span></div>' for k, v, c in tiles)
    kpis += (f'<div class="kpi"><span class="kl">Reading time</span><span class="kv"><small>約 </small>'
             f'<span data-count="{total}">{total}</span><small> 分</small></span></div>') if picks else ""
    segs = "".join(f'<a class="{"h" if render.part_of(c, pk) == "impact" else "n"}" href="#a{n}" style="--w:{t:.2f};--i:{n - 1}" '
                   f'data-tip="{pad(n)} · {esc(pk["headline"])} · 約\u00a0{max(1, round(t))}\u00a0分" aria-label="{pad(n)} {esc(pk["headline"])}"></a>'
                   for n, ((c, pk), t) in enumerate(zip(picks, mins), 1))
    bar = (f'<div class="ih-bar" role="list">{segs}</div>'
           f'<p class="ih-scale"><span>Article 01</span><span>{pad(len(picks))}</span></p><div class="tip" hidden></div>') if picks else ""
    btns = "".join(f'<a class="lead-go" href="#{key}"><span class="tri" aria-hidden="true">▽</span>{label}'
                   + (f'<b>{n}</b>' if key != "contents" else "") + '</a>'
                   for key, label, n in (("contents", "Contents", len(picks)), ("new", "New Structure", len(new)),
                                         ("impact", "Notable Structure", len(hot))) if n)
    nav = issue_nav(prev_date, next_date, "ih-nav") if prev_date or next_date else ""
    return (f'<header class="ihead" id="issue-top"><div class="ih-top"><div><p class="ih-k">{kicker}</p>'
            f'<h1 class="ih-date">{y}.{m:02d}.{d:02d}</h1>{src}</div>{nav}</div>'
            f'<div class="ih-fig"><div class="kpis ih-kpis">{kpis}</div>{bar}</div>'
            + (f'<div class="lead-btns">{btns}</div>' if btns else "") + '</header>')


def find_form(up: str = "") -> str:
    return (f'<form class="find" action="{up}proteins.html" method="get" role="search">'
            f'<input type="search" name="q" placeholder="UniProt ID・遺伝子名・タンパク質名・PDB ID" '
            f'aria-label="タンパク質を探す"><button type="submit">探す</button></form>')


def issue_page(date: str, prev_date: str | None, next_date: str | None,
               root: Path = ISSUES_DIR, preview: bool = False, number: int | None = None,
               related: dict | None = None) -> str:
    meta, picks, types = load_issue(date, root)
    n_hot = sum(render.part_of(c, pk) == "impact" for c, pk in picks)
    picked = {pk["pmcid"] for _, pk in picks}
    rest = render.not_covered(meta, picked)
    fig_base = "figures" if preview else "../figures"
    bar = ""
    if preview:
        pr = read_json(root / date / "pr.json") if (root / date / "pr.json").exists() else {}
        link = (f' · <a href="{esc(pr["url"])}">Pull Request #{pr["number"]} を開く（ここでマージ）</a>'
                if pr.get("url") else "")
        bar = f'<div class="preview-bar">プレビュー（未公開）{link} · <a href="index.html">確認待ちの一覧</a></div>'
    # the first screen and the contents run the full width; the sidebar starts with the articles
    top = [issue_head(date, number, picks, prev_date, next_date, meta.get("index_date"), meta.get("index_from"),
                      bool((meta.get("springer") or {}).get("read")))]
    feature = load_feature(date, root, picks) if picks else None
    if picks:
        # the issue page lists every article, today's pick included (marked); the front page is where it is shown large
        top.append(f'<section class="toc" id="contents">{contents_html(picks, date, root, fig_base, "", feature and feature["pmcid"], mark=True)}</section>')
    else:
        top.append('<section class="part"><h2>No Articles</h2></section>')
    main = []
    for n, (cand, pk) in enumerate(picks, 1):
        hot = render.part_of(cand, pk) == "impact"
        if n == 1 or hot != (render.part_of(*picks[n - 2]) == "impact"):
            main.append(part_html("impact" if hot else "new", n_hot if hot else len(picks) - n_hot, date))
        main.append(article_html(cand, pk, date, n, root, fig_base, types.get(cand["pmcid"]), "../",
                                 (related or {}).get((date, n))))
    if rest:
        items = "".join(f'<li><a href="{doi_url(x)}">{esc(x["title"])}</a> — <i>{esc(x["journal_abbrev"] or x["journal"])}</i>'
                        f'（{esc(render.KIND_JA[x["kind"]])}）</li>' for x in rest)
        main.append(f'<section class="rest"><div class="ix-h"><h2 class="label">Not Covered</h2></div><ul>{items}</ul></section>')
    main.append(issue_nav(prev_date, next_date, "issue-nav", top=True))
    side = toc_side(picks, feature and feature["pmcid"]) if picks else ""   # the issue links and the search are elsewhere
    head = masthead(1)
    from_nlm = meta.get("source") == pmcoa.SOURCE      # Europe PMC was not indexing: the papers came from NLM's PMC data
    foot = colophon(f'<p>{esc(pmcoa.CREDIT)}</p>' if from_nlm else "")   # what the issue covers is on its first screen
    desc = "、".join(pk["target_label"] for _, pk in picks[:6])
    title = f"{brand.SITE_NAME} {date.replace('-', '')}"   # "FoldFeed 20261018" (the owner's naming, 2026-10-18)
    body = f'<div class="wrap issue-top">{"".join(top)}</div>' + shell("".join(main), side, sticky=True)
    return page(("［プレビュー］" if preview else "") + title, bar + head + body + foot, depth=1,
                description=f"{jp_date(date)}の新しい構造：{desc}", noindex=preview, viewer=True)


ABOUT = (   # the front page's About FoldFeed, in polite Japanese (the owner's wording request, 2026-10-18)
    # One sentence per line. "|" marks where a sentence may wrap (between phrases), so a long sentence breaks into
    # even halves at a phrase boundary and no line is left with 「す。」 alone.
    ("どんなサイトか",
     "FoldFeed は、|構造生物学に携わる方のための|情報収集サポートサイトです。"
     "前日に|論文データベース（Europe PMC）へ|新しく登録された|査読済みの|オープンアクセス論文の中から、|"
     "構造生物学の論文を|毎日ピックアップして|ご紹介します。"),
    ("紹介する論文",
     "構造が初めて解かれた|タンパク質の論文は、|New Structure で|すべてご紹介します。"
     "既に構造がある|タンパク質の新しい構造|（別の状態や複合体など）の論文も、|Notable Structure で|すべてご紹介します。"
     "PDB ID を|本文から確かめられない論文も、|構造図なしで|ご紹介します。"
     "そのほか、|既存の構造を使って|仕組みの解明や創薬に迫った|注目の研究を、|Notable Structure で |1 日 3 本まで|ご紹介します。"),
    ("掲載のタイミング",
     "論文は、|論文データベースに登録された|翌日にご紹介します。"
     "データベースに|論文が届く時期は、|雑誌によって異なります。"
     "Nature 系の雑誌は|出版からおよそ 1 か月後に届くため、|掲載もそのころになります。"),
    ("記事のつくり",
     "各記事では、|論文が明らかにしたことを|日本語で短くまとめ、|構造図とリガンドの結合部位の拡大図、|実験条件のメモを|添えています。"
     "分解能・PDB ID・リガンドは、|PDB と UniProt から|取得しています。"
     "構造図は、|PDB の座標から|描いています。"
     "紹介文は AI が|論文本文をもとに作成し、|内容ごとに|論文本文の該当箇所を|引用したうえで、|編集者が確認してから|"
     "公開しています。"),
)


def numbers_html(issues: list[str], lib: list[dict]) -> str:
    """FoldFeed in Numbers: three counts that count up as they come into view, the share of papers that are a
    protein's first PDB structure as a meter, and the papers of each issue (New / Notable Structure, stacked) as
    columns that grow from the baseline; hovering or focusing a column tells its numbers. Every number is also in
    the page as text (the final values are written in; a table holds the chart's data for screen readers)."""
    papers = {r["cand"]["pmcid"]: r["cand"] for r in lib}
    picks_of = {r["cand"]["pmcid"]: r["pick"] for r in lib}
    n_first = sum(render.part_of(c, picks_of[pm]) == "new" for pm, c in papers.items())
    share = n_first / len(papers) if papers else 0
    tiles = [("発行した号", len(issues)), ("紹介した論文", len(papers)), ("掲載誌", len(journal_counts(issues)))]
    kpis = "".join(f'<div class="kpi"><span class="kl">{k}</span><span class="kv" data-count="{v}">{v}</span></div>'
                   for k, v in tiles)
    meter = (f'<div class="meter-row"><div class="kpi"><span class="kl">{FIRST_LABEL}を含む論文</span><span class="kv">'
             f'<span data-count="{n_first}">{n_first}</span><small> / {len(papers)} 本</small></span></div>'
             f'<div class="meter" role="img" aria-label="紹介した論文の {round(share * 100)}% が、{FIRST_LABEL}を含む">'
             f'<i style="--v:{share:.3f}"></i></div><span class="mp">{round(share * 100)}%</span></div>')
    rows = []
    for date in sorted(issues)[-CHART_ISSUES:]:
        _, picks, _ = load_issue(date, ISSUES_DIR)
        n_hot = sum(render.part_of(c, pk) == "impact" for c, pk in picks)
        rows.append((date, len(picks) - n_hot, n_hot))
    top = max([a + b for _, a, b in rows] + [1])
    step = 5 if top <= 25 else 10
    ymax = -(-top // step) * step
    ticks = "".join(f'<span style="--y:{t / ymax:.3f}">{t}</span>' for t in range(0, ymax + 1, step))
    cols = []
    for i, (date, a, b) in enumerate(rows):
        _, m, d, _ = date_parts(date)
        tip = f"{m}月{d}日号 · New Structure {a} 本" + (f" · Notable Structure {b} 本" if b else "")
        seg = (f'<i class="h" style="--h:{b / ymax:.4f}"></i>' if b else "") + (f'<i class="n" style="--h:{a / ymax:.4f}"></i>' if a else "")
        last = f'<b class="cap" style="--h:{(a + b) / ymax:.4f}">{a + b}</b>' if i == len(rows) - 1 else ""
        cols.append(f'<a class="col" href="issues/{date}.html" style="--i:{i}" data-tip="{esc(tip)}" aria-label="{esc(tip)}">'
                    f'<span class="stack">{seg}</span>{last}<span class="x">{m}.{d:02d}</span></a>')
    table = "".join(f"<tr><th>{date}</th><td>{a}</td><td>{b}</td></tr>" for date, a, b in rows)
    chart = (f'<figure class="ppi"><figcaption><b>Papers per Issue</b>'
             f'<span class="lg"><i class="n"></i>New Structure<i class="h"></i>Notable Structure</span></figcaption>'
             f'<div class="plot"><div class="yt">{ticks}</div><div class="cols">{"".join(cols)}</div></div>'
             f'<table class="sr"><caption>号ごとの紹介本数</caption><tr><th>号</th><th>New Structure</th>'
             f'<th>Notable Structure</th></tr>{table}</table><div class="tip" hidden></div></figure>')
    return f'<div class="kpis">{kpis}</div>{meter}{chart}'


# 掲載のタイミング once the Nature journals are read the day after publication (pipeline/springer.py)
TIMING_NATURE = ("掲載のタイミング",
                 "Nature Communications・|Nature Structural & |Molecular Biology などの|Nature 系の雑誌の論文は、|"
                 "出版の翌日に|ご紹介します。"
                 "そのほかの雑誌の論文は、|論文データベースに登録された|翌日にご紹介します。"
                 "データベースに|論文が届く時期は、|雑誌によって異なります。")


def about_html(issues: list[str], lib: list[dict]) -> str:
    """About FoldFeed, right under the first screen: what the site is (polite Japanese), FoldFeed in Numbers (an
    infographic, see numbers_html) and every journal the papers came from, as bars. All numbers are computed from the
    data; a paper that came back in a later issue counts once."""
    journals = journal_counts(issues)
    # one sentence per line; it may wrap only at the marked phrase boundaries, balanced over its lines
    def sentence(x: str) -> str:
        return "<wbr>".join(f'<span class=w>{esc(w)}</span>' for w in x.split("|"))
    nature = any((load_issue(d, ISSUES_DIR)[0].get("springer") or {}).get("read") for d in issues)
    about = [TIMING_NATURE if nature and k == "掲載のタイミング" else (k, v) for k, v in ABOUT]
    rows = "".join(f'<div><dt>{esc(k)}</dt><dd>{"".join(f"<span class=s>{sentence(x)}。</span>" for x in v.split("。") if x)}</dd></div>'
                   for k, v in about)
    most = max([c for _, c in journals] + [1])
    jl = "".join(f'<li style="--i:{i}"><span class="jn">{esc(n)}</span><span class="c">{c}</span>'
                 f'<span class="bar" aria-hidden="true"><i style="--v:{c / most:.3f}"></i></span></li>'
                 for i, (n, c) in enumerate(journals))
    return (f'<section class="about" id="about"><div class="wrap">'
            f'<div class="ix-h"><h2 class="label">About FoldFeed</h2></div>'
            f'<div class="about-grid"><dl class="about-dl">{rows}</dl>'
            f'<div class="about-side"><h3 class="about-sub">FoldFeed in Numbers</h3>{numbers_html(issues, lib)}</div></div>'
            f'<h3 class="about-sub">Journals<span>論文数</span></h3><ul class="jl">{jl}</ul></div></section>')


METHOD_WORDS = (("クライオ", "cryo-em cryoem"), ("X線", "x-ray crystallography"), ("NMR", "nmr"))


def method_kinds(method: str) -> list[str]:
    """'クライオ電顕 / X線結晶構造解析' -> ['クライオ電顕', 'X線結晶構造解析']; the page groups by these."""
    return [m.strip() for m in method.split("/") if m.strip() and m.strip() != "—"]


def library(issues: list[str], root: Path = ISSUES_DIR) -> list[dict]:
    """Every article of every issue (newest first) with what the browse-and-search page needs: protein names,
    UniProt accessions, gene names, PDB IDs, categories, and a lower-cased search string."""
    out = []
    for date in issues:
        _, picks, _ = load_issue(date, root)
        for n, (cand, pick) in enumerate(picks, 1):
            structs = cand.get("cited_structures", []) if render.is_impact(pick) else cand["own_structures"]
            released = [x for x in structs if x["status"] == "released"]
            names, ids = [], [p["uniprot"] for p in cand.get("proteins", [])]
            for x in released:
                for q in x.get("polymers", []):
                    if q.get("description"):
                        names.append(q["description"])
                    ids += q.get("uniprot_ids", [])
            genes = [p["gene"] for p in cand.get("proteins", []) if p.get("gene")]
            method = facts(cand, pick)["method"]
            row = {"date": date, "n": n, "cand": cand, "pick": pick, "cats": pick.get("categories") or ["other"],
                   "uniprot": sorted(set(ids)), "genes": sorted(set(genes)), "names": sorted(set(names)),
                   "pdbs": sorted({x["pdb_id"] for x in released}), "method": method,
                   "gene_of": {p["uniprot"]: p["gene"] for p in cand.get("proteins", []) if p.get("gene")}}
            text = " ".join([pick["target_label"], pick.get("target_en") or "", pick["headline"], pick.get("subhead") or "", cand["title"]]
                            + list(pick.get("legend_labels") or []) + row["genes"] + row["uniprot"] + row["names"] + row["pdbs"]
                            + [method] + [w for key, w in METHOD_WORDS if key in method])
            row["search"] = unicodedata.normalize("NFKC", text).lower()
            out.append(row)
    return out


BROAD_KINDS = {"enzyme", "nucleic", "other"}   # too wide to say two articles are related


def related_articles(lib: list[dict], limit: int = 3) -> dict[tuple[str, int], list[dict]]:
    """For every article, up to `limit` other articles of the site that share something checkable: first a UniProt
    accession (the same protein), then the same main kind of protein. Nothing here is a claim about the papers."""
    out = {}
    for r in lib:
        strong, weak = [], []
        for o in lib:
            if o is r:
                continue
            shared = sorted(set(r["uniprot"]) & set(o["uniprot"]))
            if shared:
                names = [r["gene_of"].get(a) or o["gene_of"].get(a) or a for a in shared]
                strong.append((len(shared), o["date"], o["n"], o, "同じタンパク質：" + "、".join(dict.fromkeys(names[:3]))))
            elif r["cats"][0] == o["cats"][0] and r["cats"][0] not in BROAD_KINDS:
                weak.append((o["date"], o["n"], o, f"同じ種類：{CATEGORIES.get(r['cats'][0], r['cats'][0])}"))
        strong.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)   # most shared proteins, then newest
        weak.sort(key=lambda t: (t[0], t[1]), reverse=True)
        picked = [{"row": t[3], "why": t[4]} for t in strong[:limit]]
        picked += [{"row": t[2], "why": t[3]} for t in weak[:max(0, limit - len(picked))]]
        out[(r["date"], r["n"])] = picked
    return out


def category_counts(lib: list[dict]) -> list[tuple[str, int]]:
    counts = {k: 0 for k in CATEGORIES}
    for r in lib:
        for c in set(r["cats"]):
            counts[c if c in counts else "other"] += 1
    return [(k, v) for k, v in counts.items() if v]


def cats_html(lib: list[dict], up: str = "") -> str:
    items = "".join(f'<li><a href="{up}proteins.html#c-{k}"><span>{esc(CATEGORIES[k])}</span><span class="c">{v}</span></a></li>'
                    for k, v in sorted(category_counts(lib), key=lambda kv: (-kv[1], list(CATEGORIES).index(kv[0]))))
    return f'<ul class="cats">{items}</ul>'


def mail_html() -> str:
    return ('<section class="sec mail" id="mail"><div class="ix-h"><h2 class="label">Newsletter</h2>'
            '<span class="soon">準備中</span></div><div class="mail-grid"><div>'
            '<ul><li>毎朝の号の知らせ（New Structure と Notable Structure）</li><li>週のまとめ</li>'
            '<li>気になるタンパク質のアラート（UniProt ID を登録）</li></ul></div><div>'
            '<form aria-disabled="true" onsubmit="return false"><input type="email" placeholder="メールアドレス" disabled '
            'aria-label="メールアドレス（準備中）"><button type="button" disabled>登録（準備中）</button></form>'
            '</div></div></section>')


def home(issues: list[str]) -> str:
    """The front page. First screen: what the site is, in three lines, a large structure, and the button down to
    Today's Structure (the day's one article, with its figure and four-line introduction), so a first-time visitor
    sees what the site does before what it shows. Then: the rest of the latest issue, the archive by kind of protein
    (and a search), the past issues (empty ones too, as "記事なし"), and (not yet working) a place for the mailing list."""
    latest = next((d for d in issues if load_issue(d, ISSUES_DIR)[1]), issues[0])  # newest issue that has articles
    past = [d for d in issues if d != latest]
    _, picks, _ = load_issue(latest, ISSUES_DIR)
    href = f"issues/{latest}.html"
    feature = load_feature(latest, ISSUES_DIR, picks)
    lib = library(issues)
    top = (lead_html(feature, picks, latest, ISSUES_DIR, "figures") + about_html(issues, lib)
           + today_html(feature, picks, latest, ISSUES_DIR, "figures", href, ""))
    main = [f'<section class="toc">{contents_html(picks, latest, ISSUES_DIR, "figures", href, feature and feature["pmcid"])}</section>',
            f'<section class="sec"><div class="ix-h"><h2 class="label">Browse by Protein Type</h2>'
            f'<a class="soon" href="proteins.html">一覧へ</a></div>'
            f'{find_form()}{cats_html(lib)}</section>']
    if past:
        items = []
        for date in past:
            _, ps, _ = load_issue(date, ISSUES_DIR)
            targets = "・".join(pk["target_label"].split("（")[0] for _, pk in ps) or "記事なし"
            items.append(f'<li><a class="d" href="issues/{date}.html">{jp_date_short(date)}号</a>'
                         f'<span class="t"><b>{len(ps)} 本</b>{esc(targets)}</span></li>')
        main.append(f'<section class="sec past"><div class="ix-h"><h2 class="label">Past Issues</h2><span>{len(past)} 号</span></div>'
                    f'<ol>{"".join(items)}</ol></section>')
    main.append(mail_html())
    side = ""   # no sidebar: the search is in Browse by Protein Type, the interests were dropped (2026-10-18)
    return page(brand.SITE_NAME,
                masthead(0) + top + shell("".join(main), side)
                + colophon(f'<p>{esc(pmcoa.CREDIT)}</p>' if load_issue(latest, ISSUES_DIR)[0].get("source") == pmcoa.SOURCE else ""),
                depth=0, viewer=True)


PROTEIN_SCRIPT = r"""
(function () {
  var q = document.getElementById('q'), rows = [].slice.call(document.querySelectorAll('.pr')),
      groups = [].slice.call(document.querySelectorAll('.pg')), none = document.getElementById('pg-none'),
      total = document.getElementById('pg-total');
  function norm(s) { return s.normalize('NFKC').toLowerCase(); }
  function apply() {
    var terms = norm(q.value).split(/\s+/).filter(Boolean), shown = 0, seen = {};
    rows.forEach(function (r) {
      var ok = terms.every(function (t) { return r.getAttribute('data-s').indexOf(t) >= 0; });
      var a = r.getAttribute('data-a');
      r.hidden = !ok; if (ok && !seen[a]) { seen[a] = 1; shown++; }
    });
    groups.forEach(function (g) { g.hidden = !g.querySelector('.pr:not([hidden])'); });
    none.style.display = shown ? 'none' : 'block';
    total.textContent = terms.length ? shown + ' 本が見つかった' : '';
  }
  var m = /[?&]q=([^&]*)/.exec(location.search);
  if (m) q.value = decodeURIComponent(m[1].replace(/\+/g, ' '));
  q.addEventListener('input', apply);
  q.form.addEventListener('submit', function (e) { e.preventDefault(); apply(); });
  apply();
})();
"""


def proteins_page(issues: list[str]) -> str:
    """proteins.html: every article grouped by kind of protein; a search box narrows them by UniProt ID, gene name,
    protein name, PDB ID or any word of the headline. Works without JavaScript as a plain grouped list."""
    lib = library(issues)
    counts = dict(category_counts(lib))
    groups = []
    for key in CATEGORIES:
        rows = [r for r in lib if key in r["cats"] or (key == "other" and not set(r["cats"]) & set(CATEGORIES))]
        if not rows:
            continue
        items = []
        for r in rows:
            y, m, d, _ = date_parts(r["date"])
            meta = []
            if r["uniprot"]:
                meta.append(f'<i>UNIPROT</i>{esc(", ".join(r["uniprot"][:4]))}{" …" if len(r["uniprot"]) > 4 else ""}')
            if r["genes"]:
                meta.append(f'<i>GENE</i>{esc(", ".join(r["genes"][:4]))}')
            if r["pdbs"]:
                meta.append(f'<i>PDB</i>{esc(", ".join(r["pdbs"][:4]))}{" …" if len(r["pdbs"]) > 4 else ""}')
            items.append(f'<li class="pr" data-a="{r["date"]}#{r["n"]}" data-s="{esc(r["search"])}"><span class="pd">{y}.{m:02d}.{d:02d} · {pad(r["n"])}</span>'
                         f'<a class="pt" href="issues/{r["date"]}.html#a{r["n"]}">{esc(r["pick"]["headline"])}'
                         f'<span class="ps">{esc(r["pick"].get("target_en") or r["pick"]["target_label"])}</span></a>'
                         f'<span class="pm">{"<br>".join(meta)}</span></li>')
        groups.append(f'<section class="pg" id="c-{key}"><div class="ix-h"><h2 class="label">{esc(CATEGORIES[key])}</h2>'
                      f'<span>{counts[key]} 本</span></div><ul class="rows" style="list-style:none;margin:0;padding:0">'
                      f'{"".join(items)}</ul></section>')
    head = (f'<header class="pp-head"><h1>Article Search</h1>'
            f'<form class="find" role="search"><input id="q" type="search" name="q" autocomplete="off" '
            f'placeholder="例：P72047 / kinase / 25IP / リボソーム" aria-label="タンパク質を探す"><button type="submit">探す</button></form>'
            f'<p id="pg-total" class="sd-meta"></p></header>')
    main = head + "".join(groups) + '<p id="pg-none" class="pg-none">該当する記事はありませんでした。</p>'
    side = sd("Categories", '<div class="sd-cats">' + "".join(
        f'<a href="#c-{k}"><span>{esc(CATEGORIES[k])}</span><span class="c">{counts[k]}</span></a>'
        for k in CATEGORIES if k in counts) + "</div>") + sd("Pages", '<div class="sd-nav"><a href="index.html">トップへ</a></div>')
    return page(f"タンパク質から探す · {brand.SITE_NAME}", masthead(0) + shell(main, side, sticky=True) + colophon(), depth=0,
                description="これまでに紹介した構造を、タンパク質の種類・UniProt ID・タンパク質名で探す。", script=PROTEIN_SCRIPT)


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
        n_hot = sum(render.part_of(c, pk) == "impact" for c, pk in picks)
        pr = read_json(root / date / "pr.json") if (root / date / "pr.json").exists() else {}
        merge = (f'<a class="go" href="{esc(pr["url"])}">Pull Request #{pr["number"]}（マージ）</a>'
                 if pr.get("url") else "")
        items.append(f'<li><div class="d">{jp_date(date)}号</div>'
                     f'<div class="c">New Structure {len(picks) - n_hot} 本 · Notable Structure {n_hot} 本</div>'
                     f'<div class="btns"><a href="{date}.html">見た目を確認</a>{merge}</div></li>')
    body = [masthead(1, "Awaiting Review"),
            '<div class="wrap"><p class="intro">マージ前の号のプレビュー。見た目を確認して、問題がなければ Pull Request を開いて'
            '「Merge pull request」→「Confirm merge」で公開する。このページはブックマークやホーム画面に追加しておける。</p>',
            f'<ul class="queue">{"".join(items)}</ul>' if items else '<p class="intro">いま確認待ちの号はない。</p>',
            '</div>', colophon()]
    return page(f"確認待ちの号 · {brand.SITE_NAME}", "".join(body), depth=1, noindex=True)


def build(preview_root: Path | None = None) -> int:
    issues = issue_dates(ISSUES_DIR, require_articles=False)
    if not issues:
        print("no built issues found")
        return 1
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    (SITE_DIR / "assets").mkdir(parents=True)
    (SITE_DIR / "issues").mkdir()
    for name in ("logo.svg", "logo-mark.svg", "viewer.js"):
        shutil.copy(ROOT / "assets" / name, SITE_DIR / "assets" / name)
    LATEST["date"] = next((d for d in issues if load_issue(d, ISSUES_DIR)[1]), None)
    shutil.copytree(ROOT / "assets" / "vendor", SITE_DIR / "assets" / "vendor")
    related = related_articles(library(issues))
    for i, date in enumerate(issues):
        prev_date = issues[i + 1] if i + 1 < len(issues) else None
        next_date = issues[i - 1] if i > 0 else None
        (SITE_DIR / "issues" / f"{date}.html").write_text(
            issue_page(date, prev_date, next_date, number=len(issues) - i, related=related), encoding="utf-8")
        src = ISSUES_DIR / date / "figures"
        if src.exists():
            shutil.copytree(src, SITE_DIR / "figures" / date, ignore=shutil.ignore_patterns("*.json"))
    (SITE_DIR / "index.html").write_text(home(issues), encoding="utf-8")
    (SITE_DIR / "proteins.html").write_text(proteins_page(issues), encoding="utf-8")
    print(f"built site/ with {len(issues)} issue(s): {', '.join(issues)}")
    previews = issue_dates(preview_root, require_articles=False) if preview_root else []
    out = SITE_DIR / "preview"
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(preview_index(previews, preview_root), encoding="utf-8")
    for date in previews:
        (out / f"{date}.html").write_text(issue_page(date, None, None, preview_root, preview=True, related=related), encoding="utf-8")
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
