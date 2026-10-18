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

CSS = f"""
:root {{
  --ground:{TH.ground}; --paper:{TH.paper}; --ink:{TH.ink}; --ink2:{TH.ink2}; --mute:{TH.muted};
  --desc:{TH.desc}; --hair:{TH.hair}; --acc:{TH.accent}; --acc-ink:{TH.accent_ink};
  --serif:"Noto Serif JP","Hiragino Mincho ProN","Yu Mincho",YuMincho,"BIZ UDMincho",Georgia,serif;
  --latin:Georgia,"Times New Roman","Noto Serif JP",serif;
  --sans:"Noto Sans JP","Hiragino Kaku Gothic ProN","Hiragino Sans","Yu Gothic",Meiryo,sans-serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --col:240px; --side:320px; --pad:clamp(24px,6vw,96px); --head:106px;
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
/* every section heading of the pages (Picked up Today's Structure, About FoldFeed, New Structure, 注目の構造, ...): one
   face, one size, a 2 px rule above */
.ix-h {{ display:flex; justify-content:space-between; align-items:baseline; gap:16px; border-top:2px solid var(--ink);
         padding-top:14px; margin-bottom:4px; }}
.ix-h .label, .part h2 {{ padding-top:0; font-family:var(--latin); font-size:30px; letter-spacing:0; text-transform:none;
               font-weight:700; color:var(--ink); line-height:1.35; }}
.ix-h span {{ font-family:var(--mono); font-size:13px; letter-spacing:.08em; color:var(--mute); }}
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
.row h3 {{ margin:0; font-family:var(--sans); font-size:21px; line-height:1.5; font-weight:700; color:var(--ink); text-wrap:balance; }}
.row .sub {{ margin:3px 0 0; font-family:var(--sans); font-size:14px; line-height:1.8; color:var(--desc); }}
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
.hero .label {{ color:var(--acc-ink); display:block; padding-top:0; margin-bottom:16px; }}
.hero .label span {{ color:var(--mute); }}
.hero-grid {{ display:grid; grid-template-columns:minmax(0,46fr) minmax(0,54fr); gap:52px; align-items:center; }}
.hero h3 {{ margin:0; font-size:42px; line-height:1.36; font-weight:700; color:var(--ink); text-wrap:balance; }}
.hero .sub {{ margin:12px 0 0; font-size:18px; line-height:1.75; color:var(--desc); }}
.hero .intro4 {{ margin-top:22px; }}
.hero .intro4 div {{ grid-template-columns:128px minmax(0,1fr); gap:12px; padding:5px 0; }}
.hero .intro4 dt {{ font-size:12px; padding-top:5px; }}
.hero .intro4 dd {{ font-size:15px; line-height:1.85; }}
.hf {{ display:grid; grid-template-columns:minmax(0,1.15fr) minmax(0,.9fr) minmax(0,1.5fr) minmax(0,1fr); gap:10px 18px; margin:20px 0 0; font-family:var(--mono); }}
.hf dt {{ font-size:10.5px; letter-spacing:.1em; color:var(--mute); }}
.hf dd {{ margin:0; font-size:12px; line-height:1.6; color:var(--ink2); overflow-wrap:anywhere; }}
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
.part h2 {{ margin:8px 0 4px; display:flex; align-items:baseline; gap:16px; }}
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
.margin .sticky {{ position:sticky; top:calc(var(--headnow) + 20px); }}
.kicker {{ font-family:var(--mono); font-size:11.5px; letter-spacing:.06em; color:var(--mute); margin:0 0 10px; line-height:1.7; }}
.kicker b {{ color:var(--ink); font-weight:600; }}
.art h2 {{ font-family:var(--latin); font-size:25px; line-height:1.38; letter-spacing:-0.005em; margin:0 0 24px;
           font-weight:700; text-wrap:balance; scroll-margin-top:20px; }}

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


/* the front page: the day's pick fills the first screen on the page ground, closed by a heavy rule */
.band {{ border-bottom:4px solid var(--ink); }}
.band .hero {{ margin-top:0; padding:38px 0 52px; }}
.sec {{ margin-top:56px; }}
.sec .lead {{ margin:14px 0 0; font-size:14.5px; color:var(--desc); }}
.sec-more {{ margin:6px 0 0; font-family:var(--mono); font-size:13px; }}
.sec-more a {{ text-decoration:underline; text-decoration-color:var(--acc); text-underline-offset:4px; }}
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
.mail .fine {{ margin-top:10px; font-size:12.5px; color:var(--mute); }}


.mix {{ display:flex; flex-wrap:wrap; gap:4px 28px; margin:34px 0 -16px; font-family:var(--mono); font-size:12.5px; color:var(--ink2); }}
.mix b {{ font-weight:500; letter-spacing:.1em; font-size:11px; color:var(--mute); margin-right:10px; }}
.issue-body .mix, .main > .mix:first-child {{ margin-top:30px; }}
.mine .chips {{ display:flex; flex-wrap:wrap; gap:6px; }}
.chip {{ font:inherit; font-family:var(--sans); font-size:12.5px; line-height:1.4; padding:3px 9px; color:var(--ink2); background:transparent;
         border:1px solid var(--hair); border-radius:0; cursor:pointer; }}
.chip:hover {{ border-color:var(--ink); color:var(--ink); }}
.chip.on {{ background:var(--ink); border-color:var(--ink); color:#fff; }}
.mine .only {{ display:block; margin-top:12px; font-family:var(--sans); font-size:13px; color:var(--ink2); cursor:pointer; }}
.mine-n {{ margin:6px 0 0; font-family:var(--mono); font-size:12px; color:var(--acc-ink); }}
.mine[hidden] {{ display:none; }}
.row.hit {{ border-left:4px solid var(--acc); padding-left:18px; margin-left:-22px; }}
.row.hit .rt::before {{ content:"関心あり"; display:block; font-family:var(--mono); font-size:11px; letter-spacing:.1em; color:var(--acc-ink); font-weight:700; }}
.row[hidden], .pr[hidden], .pg[hidden] {{ display:none; }}
.hero .v3d-bar {{ margin-top:10px; }}

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
.menu .mb i {{ display:block; width:20px; height:2px; background:#fff; transition:transform .15s; }}
.menu[open] .mb i:nth-child(1) {{ transform:translateY(7px) rotate(45deg); }}
.menu[open] .mb i:nth-child(2) {{ opacity:0; }}
.menu[open] .mb i:nth-child(3) {{ transform:translateY(-7px) rotate(-45deg); }}
.menu summary:hover .mb, .menu summary:focus-visible .mb {{ background:var(--acc-ink); }}
.menu-list {{ position:absolute; right:0; top:calc(100% + 12px); z-index:30; min-width:230px; display:flex; flex-direction:column;
              background:var(--ground); border:1px solid var(--ink); padding:8px 0; text-align:right; }}
.menu-list a {{ padding:8px 22px; font-family:var(--mono); font-size:14px; color:var(--ink); text-decoration:none; }}
.menu-list a:hover {{ background:var(--ink); color:#fff; text-decoration:none; }}

/* the front page's first screen: what the site is, the button down to Today's Structure, a large structure */
.lead {{ }}
.lead-grid {{ display:grid; grid-template-columns:minmax(0,44fr) minmax(0,56fr); gap:36px; align-items:center;
              min-height:min(560px, calc(100vh - 150px)); padding-top:24px; padding-bottom:24px; }}
.lead-h {{ margin:0; font-family:var(--serif); font-weight:400; font-size:clamp(18px, 1.72vw, 30px); line-height:1.8;
           color:var(--ink); letter-spacing:.02em; }}
.lead-h span {{ display:block; white-space:nowrap; }}
.lead-go {{ display:inline-flex; align-items:center; gap:12px; margin-top:34px; padding:14px 24px 14px 20px;
            background:var(--ink); color:#fff; font-family:var(--mono); font-size:14.5px; letter-spacing:.06em; }}
.lead-go .tri {{ font-size:13px; }}
.lead-btns {{ display:flex; flex-wrap:wrap; gap:12px 14px; margin-top:34px; }}
.lead-btns .lead-go {{ margin-top:0; }}
.lead-go.alt {{ background:transparent; color:var(--ink); box-shadow:inset 0 0 0 1px var(--ink); }}
.lead-go.alt:hover {{ background:var(--ink); color:#fff; }}

/* About FoldFeed, right under the first screen */
.about {{ padding:40px 0 30px; }}
.about-grid {{ display:grid; grid-template-columns:minmax(0,62fr) minmax(0,38fr); gap:56px; margin-top:22px; align-items:start; }}
.about-dl {{ margin:0; }}
.about-dl div {{ display:grid; grid-template-columns:150px minmax(0,1fr); gap:16px; padding:10px 0; border-top:1px solid var(--hair); }}
.about-dl div:first-child {{ border-top:0; padding-top:0; }}
.about-dl dt {{ font-size:15px; font-weight:700; color:var(--ink2); padding-top:3px; }}
.about-dl dd {{ margin:0; font-size:16px; line-height:1.95; color:var(--ink2); }}
.about-sub {{ margin:0 0 14px; font-family:var(--sans); font-size:17px; font-weight:700; color:var(--ink); display:flex;
              justify-content:space-between; align-items:baseline; }}
.about-sub span {{ font-family:var(--mono); font-size:12.5px; font-weight:500; color:var(--mute); }}
.about > .wrap > .about-sub {{ margin-top:38px; padding-top:18px; border-top:1px solid var(--hair); }}
.about .nums {{ gap:22px 24px; }}
.about .nums dt {{ font-size:13px; letter-spacing:.04em; font-family:var(--sans); color:var(--ink2); }}
.about .nums dd {{ font-size:40px; }}
.about .jl {{ columns:3 260px; column-gap:44px; font-size:14.5px; }}
.about .jl li {{ break-inside:avoid; }}
.lead-go:hover {{ background:var(--acc-ink); color:#fff; text-decoration:none; }}
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
.today {{ padding:40px 0 30px; }}
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

/* an issue page opens with its date, large */
.ihead {{ display:grid; grid-template-columns:var(--col) minmax(0,1fr); gap:28px; padding:44px 0 6px; align-items:end; }}
.idate {{ line-height:1; }}
.idate .y {{ display:block; font-family:var(--mono); font-size:15px; letter-spacing:.14em; color:var(--mute); }}
.idate .md {{ display:block; font-family:var(--latin); font-size:84px; letter-spacing:-.045em; font-weight:700; color:var(--ink);
              margin:8px 0 10px; white-space:nowrap; }}
.idate .wd {{ display:block; font-family:var(--mono); font-size:13px; letter-spacing:.12em; color:var(--acc-ink); }}
.ihead h1 {{ margin:0; font-size:34px; line-height:1.35; }}
.ihead .ic {{ margin:6px 0 0; font-family:var(--mono); font-size:13.5px; color:var(--ink2); }}
.ihead .in {{ margin:12px 0 0; display:flex; flex-wrap:wrap; gap:6px 24px; font-family:var(--mono); font-size:12.5px; }}
.ihead .in a {{ text-decoration:underline; text-decoration-color:var(--acc); text-underline-offset:4px; }}
.issue-body .toc {{ margin-top:30px; }}
.row.feat {{ border-left:4px solid var(--acc); padding-left:18px; margin-left:-22px; }}
.row.feat h3 {{ font-size:23px; }}
.feat-mark {{ display:block; text-transform:uppercase; margin:0 0 2px; font-family:var(--mono); font-size:11px; letter-spacing:.12em; color:var(--acc-ink); font-weight:700; }}

/* the article's four-line introduction: plain lines, one weight, no rules; the labels are quiet and sit beside the text */
.intro4 {{ margin:10px 0 0; max-width:46em; }}
.intro4 div {{ display:grid; grid-template-columns:138px minmax(0,1fr); gap:14px; padding:6px 0; }}
.intro4 dt {{ font-size:12.5px; letter-spacing:.04em; color:var(--mute); padding-top:6px; }}
.intro4 dd {{ margin:0; font-size:16px; line-height:1.9; color:var(--ink2); }}
.intro4 .open dd {{ color:var(--desc); font-size:15px; }}

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
  .hero-grid {{ gap:36px; }}
  .hero h3 {{ font-size:35px; }}
  .hero .sub {{ font-size:16.5px; }}
  .idate .md {{ font-size:64px; }}
  .row {{ grid-template-columns:44px minmax(0,1fr) 190px 132px 92px; gap:0 20px; }}
  .row .no {{ font-size:24px; }}
  .row h3 {{ font-size:20px; }}
  .margin .no {{ font-size:64px; }}
}}
@media (max-width:1120px) {{
  .lead-grid {{ grid-template-columns:minmax(0,1fr); min-height:0; gap:26px; padding-top:30px; }}
  .lead-art {{ margin-right:0; }}
  .shell {{ grid-template-columns:minmax(0,1fr); }}
  .side {{ border-left:0; padding-left:0; border-top:4px solid var(--ink); margin-top:20px; }}
  .sticky-side .side-in {{ position:static; max-height:none; overflow:visible; }}
  .side-only-issue {{ display:none; }}
  .hero-grid {{ grid-template-columns:minmax(0,1fr); gap:26px; }}
  .hf {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
}}
@media (max-width:860px) {{
  :root {{ --pad:18px; }}
  :root {{ --head:118px; }}
  .mast {{ padding-top:14px; }}
  .rules {{ margin-top:10px; }}
  .mast-row {{ position:relative; }}
  .vol {{ position:absolute; right:0; top:0; }}
  .menu .mb {{ width:44px; height:44px; }}
  .menu .mt {{ font-size:12px; }}
  .lead-h {{ font-size:17px; line-height:1.85; }}
  .lead-go {{ margin-top:22px; font-size:13.5px; }}
  .lead-btns {{ margin-top:22px; }}
  .about-grid {{ grid-template-columns:minmax(0,1fr); gap:30px; }}
  .about-dl div {{ grid-template-columns:minmax(0,1fr); gap:2px; }}
  .about-dl dd {{ font-size:15px; }}
  .about .nums dd {{ font-size:32px; }}
  .lead-art img {{ max-height:none; }}
  .today {{ padding:32px 0 40px; }}
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
  .art, .ihead {{ grid-template-columns:minmax(0,1fr); gap:14px; }}
  .hero h3 {{ font-size:28px; }}
  .band .hero {{ padding:24px 0 32px; }}
  .idate .md {{ font-size:58px; display:inline-block; margin:0 14px 0 0; }}
  .idate .y, .idate .wd {{ display:inline-block; }}
  .ihead h1 {{ font-size:26px; }}
  .intro4 div, .hero .intro4 div {{ grid-template-columns:minmax(0,1fr); gap:0; }}
  .mail-grid {{ grid-template-columns:minmax(0,1fr); gap:20px; }}
  .pr {{ grid-template-columns:minmax(0,1fr); }}
  .row.feat, .row.hit {{ margin-left:0; padding-left:14px; }}
  .rel li {{ grid-template-columns:minmax(0,1fr); }}
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
    if (y < firstScreen()) away(false);
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
    var top = y > window.scrollY ? y - 16 : y - head.offsetHeight - 16;
    var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: Math.max(0, top), behavior: calm ? 'auto' : 'smooth' });
    if (history.replaceState) history.replaceState(null, '', '#' + id);
  });
})();
document.addEventListener('click', function (e) {
  document.querySelectorAll('details.menu[open]').forEach(function (d) {
    if (!d.contains(e.target) || e.target.closest('.menu-list a')) d.removeAttribute('open');
  });
});
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
    rows = [("Target", esc(pick.get("target_en") or pick["target_label"])), ("Method", esc(method)), (pdb_label, pdb_dd),
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
    return f'<dl class="intro4">{rows}</dl>'


def related_html(items: list[dict], up: str) -> str:
    if not items:
        return ""
    lis = "".join(f'<li><a href="{up}issues/{x["row"]["date"]}.html#a{x["row"]["n"]}">'
                  f'<span class="rd">{x["row"]["date"][5:].replace("-", ".")} · {pad(x["row"]["n"])}</span>'
                  f'<b>{esc(x["row"]["pick"]["headline"])}</b></a><span class="why">{esc(x["why"])}</span></li>'
                  for x in items)
    return f'<h3>関連する記事</h3><ul class="rel">{lis}</ul>'


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
        lab = f'<h3>実験メモ</h3><dl class="lab">{rows}</dl>' + (f'<div class="lab-src">{ev}</div>' if ev else "")
    text = (f'<div class="text">'
            f'<p class="kicker"><b>{esc(cand["journal"] or cand["journal_abbrev"])}</b> · {esc(cand["pub_date"])} · '
            f'{lic} · <a href="{doi_url(cand)}" rel="noopener">原著論文（英語）↗</a></p>'
            f'<h2>{esc(cand["title"])}</h2>'
            f'{figures_html(cand, pick, date, root, fig_base, up)}'
            f'<p class="headline">{esc(pick["headline"])}</p>'
            f'<p class="subhead">{esc(pick.get("subhead") or "")}</p>'
            f'{intro_html(pick)}'
            f'<h3>読みどころ</h3>{hls}{lab}{related_html(related or [], up)}</div>')
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
    "new": ("New Structure", "この号で報告された、PDB / EMDB に寄託された新しい構造。"),
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
    return {"target": pick.get("target_en") or pick["target_label"], "method": method or "—", "res": res or "—", "pdb": pdb,
            "pdb_id": shown["pdb_id"] if shown else None,
            "ligand": pick.get("ligand_label") or ("—" if hot else render.ligand_line(cand)),
            "uniprot": [(p["uniprot"], p.get("gene") or p["uniprot"]) for p in prots[:3]],
            "first": any(p.get("first_structure") for p in prots)}


def row_html(cand: dict, pick: dict, n: int, date: str, root: Path, fig_base: str, href_base: str,
             feat: bool = False) -> str:
    """One row of the index: number | headline and supplement | TARGET METHOD RES. | structure | PDB."""
    href = f"{href_base}#a{n}"
    f = facts(cand, pick)
    dl = "".join(f"<div><dt>{k}</dt><dd>{esc(v)}</dd></div>"
                 for k, v in (("TARGET", f["target"]), ("METHOD", f["method"]), ("RES.", f["res"])))
    first = '<span class="mk">PDB 初構造</span>' if f["first"] else ""
    mark = '<span class="feat-mark">Today&#8217;s Structure</span>' if feat else ""
    cats = " ".join(pick.get("categories") or [])
    return (f'<li class="row{" feat" if feat else ""}" data-cats="{esc(cats)}"><span class="no">{pad(n)}</span><div class="rt">{mark}'
            f'<p class="rk"><span class="n">{pad(n)}</span></p>\n'
            f'<h3><a href="{href}">{esc(pick["headline"])}</a></h3>\n'
            f'<p class="sub">{esc(pick.get("subhead") or "")}</p>'
            f'<p class="rm">{esc(cand["journal_abbrev"] or cand["journal"])}</p></div>'
            f'<dl class="rf">{dl}</dl>{thumb_html(cand, pick, date, root, fig_base, href)}'
            f'<div class="rp"><span class="pl">PDB</span><b>{esc(f["pdb_id"] or f["pdb"])}</b>{first}</div></li>')


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
        if body:
            out += (f'<div class="ix-h{cls}"><h2 class="label">{PARTS[key][0]}</h2><span>{len(items)} 本</span></div>'
                    f'<ol class="rows">{body}</ol>')
    return out


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
    lines = "".join(f'<span>{esc(t)}</span>' for t in LEAD)
    go = (('<a class="lead-go" href="#today"><span class="tri" aria-hidden="true">▽</span>'
           'Picked up: Today’s Structure</a>') if feature_of(feature, picks) else "")
    go = (f'<div class="lead-btns">{go}<a class="lead-go alt" href="#about"><span class="tri" aria-hidden="true">▽</span>'
          f'About FoldFeed</a></div>')
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


def read_links(cand: dict, href: str) -> str:
    """The two ways on from a summary, told apart by who wrote it, in which language and where it lives: FoldFeed's
    article (Japanese, on this site) and the paper itself (English, on the journal's site)."""
    journal = cand.get("journal_abbrev") or cand.get("journal") or "掲載誌"
    return (f'<div class="reads">'
            f'<a class="read-art" href="{href}"><b>解説記事を読む</b><span class="arr" aria-hidden="true">→</span>'
            f'<small>FoldFeed の日本語の解説（このサイト）</small></a>'
            f'<a class="read-paper" href="{doi_url(cand)}" rel="noopener"><b>原著論文を開く</b><span class="arr" aria-hidden="true">↗</span>'
            f'<small>{esc(journal)} · 英語 · 外部サイト</small></a></div>')


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
    first = '<span class="mk">PDB 初構造</span>' if facts(cand, pk)["first"] else ""
    rows = "".join(f'<div><dt>{label}</dt><dd>{esc(pk["summary"][k])}</dd></div>'
                   for k, label in TODAY_FIELDS.items() if pk["summary"].get(k))
    return (f'<section class="today" id="today"><div class="wrap">'
            f'<div class="ix-h"><h2 class="label">Picked up Today’s Structure</h2></div>'
            f'<p class="today-k"><span class="tg">{esc(pk.get("target_en") or pk["target_label"])}</span>'
            f'<span class="dt">{(first + " · ") if first else ""}Issue of {y}.{m:02d}.{d:02d}<b>Number {pad(n)}</b></span></p>'
            f'<h3 class="today-h"><a href="{href}">{esc(pk["headline"])}</a></h3>'
            f'<p class="today-sub">{esc(pk.get("subhead") or "")}</p>'
            f'<div class="today-fig">{feature_figure(cand, pk, date, root, fig_base, href, up)}</div>'
            f'<dl class="today-4">{rows}</dl>{read_links(cand, href)}</div></section>')


TODAY_FIELDS = {"question": "疑問・課題", "approach": "アプローチ", "result": "結果", "learned": "わかったこと"}


def part_html(key: str, count: int) -> str:
    title, note = PARTS[key]
    return f'<section class="part" id="{key}"><h2>{title}<span>{count} 本</span></h2></section>'


def counts_text(n_new: int, n_hot: int, sep: str = "<br>") -> str:
    return f"New Structure {n_new} 本" + (f"{sep}注目の構造 {n_hot} 本" if n_hot else "")


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


def index_span(index_date: str, index_from: str | None = None) -> str:
    """'10月13日' for one day, '10月11日〜14日' for a catch-up window of several days."""
    if not index_from or index_from == index_date:
        return jp_date_short(index_date)
    (_, m1, d1), (_, m2, d2) = ((int(x) for x in v.split("-")) for v in (index_from, index_date))
    return f"{m1}月{d1}日〜{d2}日" if m1 == m2 else f"{m1}月{d1}日〜{m2}月{d2}日"


def issue_head(date: str, number: int | None, n_new: int, n_hot: int, prev_date: str | None, next_date: str | None,
               index_date: str | None = None, index_from: str | None = None) -> str:
    """The top of an issue page: its date, large (year, month.day, weekday and the issue's number), and the counts."""
    y, m, d, wd = date_parts(date)
    num = f" · 第 {number} 号" if number else " · プレビュー"
    links = "".join(x for x in (f'<a href="{prev_date}.html">← {jp_date_short(prev_date)}号</a>' if prev_date else "",
                                f'<a href="{next_date}.html">{jp_date_short(next_date)}号 →</a>' if next_date else "",
                                '<a href="../index.html">トップへ</a>'))
    return (f'<header class="ihead"><div class="idate"><span class="y">{y}</span><span class="md">{m:02d}.{d:02d}</span>'
            f'<span class="wd">{wd}曜日{num}</span></div><div><h1>{jp_date(date)}号</h1>'
            f'<p class="ic">{counts_text(n_new, n_hot, " · ")}</p>'
            + (f'<p class="ic">対象：{index_span(index_date, index_from)}登録分</p>' if index_date else "")
            + f'<p class="in">{links}</p></div></header>')


def find_form(up: str = "") -> str:
    return (f'<form class="find" action="{up}proteins.html" method="get" role="search">'
            f'<input type="search" name="q" placeholder="UniProt ID・遺伝子名・タンパク質名・PDB ID" '
            f'aria-label="タンパク質を探す"><button type="submit">探す</button></form>')


def issue_page(date: str, prev_date: str | None, next_date: str | None,
               root: Path = ISSUES_DIR, preview: bool = False, number: int | None = None,
               related: dict | None = None) -> str:
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
    main = [issue_head(date, number, len(picks) - n_hot + len(rest), n_hot, prev_date, next_date, meta.get("index_date"),
                       meta.get("index_from"))]
    feature = load_feature(date, root, picks) if picks else None
    if picks:
        # the issue page lists every article, today's pick included (marked); the front page is where it is shown large
        main.append(breakdown_html(picks))
        main.append(f'<section class="toc">{contents_html(picks, date, root, fig_base, "", feature and feature["pmcid"], mark=True)}</section>')
    else:
        c = meta["counts"]
        main.append('<section class="part"><h2>記事なし<span>0 本</span></h2></section>')
    for n, (cand, pk) in enumerate(picks, 1):
        hot = render.is_impact(pk)
        if n == 1 or hot != render.is_impact(picks[n - 2][1]):
            main.append(part_html("impact" if hot else "new", n_hot if hot else len(picks) - n_hot))
        main.append(article_html(cand, pk, date, n, root, fig_base, types.get(cand["pmcid"]), "../",
                                 (related or {}).get((date, n))))
    if rest:
        items = "".join(f'<li><a href="{doi_url(x)}">{esc(x["title"])}</a> — <i>{esc(x["journal_abbrev"] or x["journal"])}</i>'
                        f'（{esc(render.KIND_JA[x["kind"]])}）</li>' for x in rest)
        main.append(f'<section class="rest"><h2 class="label">記事にしていない新しい構造</h2><ul>{items}</ul></section>')
    prev_link = f'<a href="{prev_date}.html">← {jp_date(prev_date)}号</a>' if prev_date else "<span></span>"
    next_link = f'<a href="{next_date}.html">{jp_date(next_date)}号 →</a>' if next_date else "<span></span>"
    main.append(f'<nav class="issue-nav">{prev_link}<a href="../index.html">トップへ</a>{next_link}</nav>')
    nav = "".join(x for x in (f'<a href="{prev_date}.html">← {jp_date(prev_date)}号</a>' if prev_date else "",
                              f'<a href="{next_date}.html">{jp_date(next_date)}号 →</a>' if next_date else "",
                              '<a href="../index.html">トップへ</a>'))
    side = ((toc_side(picks, feature and feature["pmcid"]) if picks else "") + sd("号の移動", f'<div class="sd-nav">{nav}</div>')
            + sd("タンパク質を探す", find_form("../")))
    head = masthead(1)
    span = f'{meta["index_from"]}〜{meta["index_date"]}' if meta.get("index_from") else meta["index_date"]
    from_nlm = meta.get("source") == pmcoa.SOURCE      # Europe PMC was not indexing: the papers came from NLM's PMC data
    foot = colophon(f'<p>この号の対象：{"PMC" if from_nlm else "Europe PMC"} に {esc(span)} に登録されたオープンアクセス論文 '
                    f'{meta["counts"]["parsed"]} 本のうち、新しい構造を報告した論文。</p>'
                    + (f'<p>{esc(pmcoa.CREDIT)}</p>' if from_nlm else ""))
    desc = "、".join(pk["target_label"] for _, pk in picks[:6])
    title = f"{jp_date(date)}号 · {brand.SITE_NAME}"
    return page(("［プレビュー］" if preview else "") + title, bar + head + shell("".join(main), side, sticky=True) + foot, depth=1,
                description=f"{jp_date(date)}の新しい構造：{desc}", noindex=preview, viewer=True)


ABOUT = (   # the front page's About FoldFeed, in polite Japanese (the owner's wording request, 2026-10-18)
    ("どんなサイトか",
     "FoldFeed は、構造生物学に携わる方のための情報収集サポートサイトです。前日に論文データベース（Europe PMC）へ"
     "新しく登録された査読済みのオープンアクセス論文の中から、構造生物学の論文を毎日ピックアップしてご紹介します。"),
    ("紹介する論文",
     "新しい構造（PDB・EMDB に登録された構造）を報告した論文は、すべてご紹介します。そのほか、既存の構造を使って"
     "仕組みの解明や創薬に迫った注目の研究を、1 日 5 本までご紹介します。記事が 3 本以上ある日は、その中の 1 本を"
     "Today’s Structure として大きく取り上げます。"),
    ("記事のつくり",
     "各記事では、論文が明らかにしたことを日本語で短くまとめ、構造図と結合部位の拡大図、実験条件のメモを添えています。"
     "分解能・PDB ID・リガンドは PDB と UniProt から取得し、構造図は PDB の座標から描いています。紹介文は AI が論文本文を"
     "もとに作成し、内容ごとに論文本文の該当箇所を引用したうえで、編集者が確認してから公開しています。"),
)


def about_html(issues: list[str], lib: list[dict]) -> str:
    """About FoldFeed, right under the first screen: what the site is (polite Japanese), the running totals
    (これまでの紹介実績) and every journal the papers came from. All numbers are computed from the data; a paper that
    came back in a later issue counts once."""
    papers = {r["cand"]["pmcid"]: r["cand"] for r in lib}
    n_first = sum(any(p.get("first_structure") for p in c.get("proteins", [])) for c in papers.values())
    journals = journal_counts(issues)
    rows = "".join(f'<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>' for k, v in ABOUT)
    cells = [("発行した号", len(issues)), ("紹介した論文", len(papers)), ("PDB 初構造", n_first), ("掲載誌", len(journals))]
    nums = "".join(f'<div><dt>{k}</dt><dd>{v}</dd></div>' for k, v in cells)
    jl = "".join(f'<li><span>{esc(n)}</span><span class="c">{c}</span></li>' for n, c in journals)
    return (f'<section class="about" id="about"><div class="wrap">'
            f'<div class="ix-h"><h2 class="label">About FoldFeed</h2></div>'
            f'<div class="about-grid"><dl class="about-dl">{rows}</dl>'
            f'<div class="about-side"><h3 class="about-sub">これまでの紹介実績</h3><dl class="nums">{nums}</dl></div></div>'
            f'<h3 class="about-sub">掲載誌一覧<span>論文数</span></h3><ul class="jl">{jl}</ul></div></section>')


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


def breakdown_html(picks: list) -> str:
    """One line on what the issue holds, counted from the data: methods, kinds of protein, first structures."""
    methods, kinds, first = {}, {}, 0
    for cand, pk in picks:
        for m in method_kinds(facts(cand, pk)["method"]):
            methods[m] = methods.get(m, 0) + 1
        for c in dict.fromkeys(pk.get("categories") or ["other"]):
            kinds[c] = kinds.get(c, 0) + 1
        first += any(p.get("first_structure") for p in cand.get("proteins", []))
    if not picks:
        return ""
    top = lambda d, label: "・".join(f"{label(k)} {v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:5])
    bits = [f"<b>手法</b>{esc(top(methods, lambda k: k))}", f"<b>種類</b>{esc(top(kinds, lambda k: CATEGORIES.get(k, k)))}"]
    if first:
        bits.append(f"<b>PDB 初構造</b>{first} 本")
    return f'<p class="mix">{"".join(f"<span>{b}</span>" for b in bits)}</p>'


def cats_html(lib: list[dict], up: str = "") -> str:
    items = "".join(f'<li><a href="{up}proteins.html#c-{k}"><span>{esc(CATEGORIES[k])}</span><span class="c">{v}</span></a></li>'
                    for k, v in sorted(category_counts(lib), key=lambda kv: (-kv[1], list(CATEGORIES).index(kv[0]))))
    return f'<ul class="cats">{items}</ul>'


MINE_SCRIPT = r"""
(function () {
  var box = document.getElementById('mine'); if (!box) return;
  var KEY = 'ff-interest', ONLY = 'ff-interest-only';
  function read(k, d) { try { var v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } }
  function write(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode: still works for this visit */ } }
  var mine = read(KEY, []), only = read(ONLY, false);
  var chips = [].slice.call(box.querySelectorAll('.chip')), rows = [].slice.call(document.querySelectorAll('.row[data-cats]')),
      check = document.getElementById('mine-only'), count = document.getElementById('mine-n');
  function apply() {
    var hit = 0;
    chips.forEach(function (c) { c.classList.toggle('on', mine.indexOf(c.getAttribute('data-c')) >= 0); c.setAttribute('aria-pressed', mine.indexOf(c.getAttribute('data-c')) >= 0); });
    rows.forEach(function (r) {
      var cats = r.getAttribute('data-cats').split(' '), m = mine.length && cats.some(function (c) { return mine.indexOf(c) >= 0; });
      r.classList.toggle('hit', !!m); r.hidden = !!(only && mine.length && !m); if (m) hit++;
    });
    check.checked = only; count.textContent = mine.length ? '関心に合う記事 ' + hit + ' 本' : '';
  }
  chips.forEach(function (c) { c.addEventListener('click', function () {
    var k = c.getAttribute('data-c'), i = mine.indexOf(k); if (i >= 0) mine.splice(i, 1); else mine.push(k); write(KEY, mine); apply(); }); });
  check.addEventListener('change', function () { only = check.checked; write(ONLY, only); apply(); });
  box.hidden = false; apply();
})();
"""


def mine_html(lib: list[dict]) -> str:
    """Saved interests (a block of the front page's sidebar): kinds of protein the reader picks, kept in this browser
    only (localStorage). Rows of the new issue that match are marked, or the others hidden. Hidden until the script
    runs, so it never shows empty. (It sat under Today's Structure before; a control that comes before the content
    it filters is in the way of a first-time reader, so it lives beside the search instead.)"""
    chips = "".join(f'<button type="button" class="chip" data-c="{k}" aria-pressed="false">{esc(CATEGORIES[k])}</button>'
                    for k, _ in sorted(category_counts(lib), key=lambda kv: list(CATEGORIES).index(kv[0])))
    block = sd("自分の関心", f'<div class="chips">{chips}</div>'
                           f'<label class="only"><input type="checkbox" id="mine-only"> 関心のある記事だけ表示</label>'
                           f'<p class="mine-n" id="mine-n"></p>', "mine")
    return block.replace('<section class="sd mine">', '<section class="sd mine" id="mine" hidden>', 1)


def mail_html() -> str:
    return ('<section class="sec mail" id="mail"><div class="ix-h"><h2 class="label">メールで受け取る</h2>'
            '<span class="soon">準備中</span></div><div class="mail-grid"><div>'
            '<ul><li>毎朝の号の知らせ（New Structure と注目の構造）</li><li>週のまとめ</li>'
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
            f'<p class="sec-more"><a href="{href}">{jp_date(latest)}号のすべての記事を読む →</a></p>',
            f'<section class="sec"><div class="ix-h"><h2 class="label">タンパク質の種類から探す</h2>'
            f'<a class="soon" href="proteins.html">一覧へ</a></div>'
            f'{find_form()}{cats_html(lib)}</section>']
    if past:
        items = []
        for date in past:
            _, ps, _ = load_issue(date, ISSUES_DIR)
            targets = "・".join(pk["target_label"].split("（")[0] for _, pk in ps) or "記事なし"
            items.append(f'<li><a class="d" href="issues/{date}.html">{jp_date_short(date)}号</a>'
                         f'<span class="t"><b>{len(ps)} 本</b>{esc(targets)}</span></li>')
        main.append(f'<section class="sec past"><div class="ix-h"><h2 class="label">過去の号</h2><span>{len(past)} 号</span></div>'
                    f'<ol>{"".join(items)}</ol></section>')
    main.append(mail_html())
    side = sd("タンパク質を探す", find_form()) + mine_html(lib)
    return page(f"{brand.SITE_NAME}（{brand.SITE_NAME_EN}）",
                masthead(0) + top + shell("".join(main), side)
                + colophon(f'<p>{esc(pmcoa.CREDIT)}</p>' if load_issue(latest, ISSUES_DIR)[0].get("source") == pmcoa.SOURCE else ""),
                depth=0, viewer=True, script=MINE_SCRIPT)


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
    head = (f'<header class="pp-head"><h1>タンパク質から探す</h1>'
            f'<form class="find" role="search"><input id="q" type="search" name="q" autocomplete="off" '
            f'placeholder="例：P72047 / kinase / 25IP / リボソーム" aria-label="タンパク質を探す"><button type="submit">探す</button></form>'
            f'<p id="pg-total" class="sd-meta"></p></header>')
    main = head + "".join(groups) + '<p id="pg-none" class="pg-none">該当する記事はありませんでした。</p>'
    side = sd("種類", '<div class="sd-cats">' + "".join(
        f'<a href="#c-{k}"><span>{esc(CATEGORIES[k])}</span><span class="c">{counts[k]}</span></a>'
        for k in CATEGORIES if k in counts) + "</div>") + sd("ほかのページ", '<div class="sd-nav"><a href="index.html">トップへ</a></div>')
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
        n_hot = sum(render.is_impact(pk) for _, pk in picks)
        pr = read_json(root / date / "pr.json") if (root / date / "pr.json").exists() else {}
        merge = (f'<a class="go" href="{esc(pr["url"])}">Pull Request #{pr["number"]}（マージ）</a>'
                 if pr.get("url") else "")
        items.append(f'<li><div class="d">{jp_date(date)}号</div>'
                     f'<div class="c">New Structure {len(picks) - n_hot} 本 · 注目の構造 {n_hot} 本</div>'
                     f'<div class="btns"><a href="{date}.html">見た目を確認</a>{merge}</div></li>')
    body = [masthead(1, "確認待ちの号"),
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
