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
import itertools
import json
import math
import re
import shutil
import sys
import unicodedata
from pathlib import Path

from . import brand, cards, impact, jats, pmcoa, render
from .common import ISSUES_DIR, ROOT, read_json
from .schema import CATEGORIES, FEATURE_AXES, FEATURE_MIN, LAB_NOTE_FIELDS, SUMMARY_FIELDS, SUMMARY_OPTIONAL, TOPICS

SITE_DIR = ROOT / "site"
TH = brand.THEME
DESCRIPTION = "新着の査読済みオープンアクセス論文から、構造生物学論文を、毎日紹介するサイト。"
TAGLINE = "構造生物学者のための情報収集サポートサイト"
LEAD = ("新着の査読済み|オープンアクセス論文から、", "構造生物学の論文を、", "毎日ピックアップして紹介")   # "|": a phone's line break
LATEST = {"date": None}   # the newest issue with articles (the global navigation's "Latest Issue"), set by build()
NAV_INFO = {"groups": [], "articles": 0, "recent": []}   # what the navigation's dropdowns list, set by build()
THUMBS_PER_ISSUE = 8   # structures shown for each issue in a list of issues
MORE_THUMBS = 6   # the issue's other structures under Picked up Today's Structure on the front page
CAROUSEL_PAGE = 6   # structures at a time on an issue's first screen (3 x 2); more turn over every CAROUSEL_SECONDS
CAROUSEL_SECONDS = 8
BROWSE_THUMBS = 3   # structures on each Browse tile, the newest
BROWSE_GROUPS = (("part", "Part"), ("protein", "Protein Type"), ("research", "Research Type"), ("method", "Method"))
FACETS = {"cats": [], "topics": [], "methods": [], "parts": []}   # the header search's suggestions with counts, set by build()

CHART_NEW, CHART_UPD, CHART_HOT = "#2F5DA8", "#1F8A70", "#C8442A"   # 藍 / 青緑 / 朱 for New Structure, Structure Update,
# Structure-Based Study: validated together against the ground #FAFAF8 (dataviz validate_palette, all pairs)
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
  --col:240px; --side:320px; --pad:clamp(24px,8.5vw,168px); --head:156px; --navh:52px;
  --fig-shadow:drop-shadow(1.89px 1.89px 4px rgba(0,0,0,.40));
  --fig-shadow-up:drop-shadow(4.5px 8px 7px rgba(0,0,0,.30));   /* lifted under the pointer: farther, softer */
  --sec:112px;   /* the space between sections, which replaces the rules between them */
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
.wrap {{ max-width:1680px; margin:0 auto; padding:0 var(--pad); }}
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

/* contents: the articles as cards (cards, below) */
.toc {{ margin-top:var(--sec); }}
/* every section heading of the pages (Picked up Today's Structure, About FoldFeed, New Structure, Structure Update, ...),
   all in English: one face, one size, and (as on Nature's pages) a thin rule under the title, the content below it.
   Sections are told apart by space, not rules; a sub-section inside one opens with a short bar above it */
.ix-h {{ display:flex; justify-content:space-between; align-items:baseline; gap:16px; padding-bottom:12px; margin-bottom:26px;
         border-bottom:1px solid var(--rule); }}
.cards + .toc-sub, .art, .about > .wrap > .about-sub {{ position:relative; }}
.cards + .toc-sub::before, .art::before, .about > .wrap > .about-sub::before {{
  content:""; position:absolute; left:0; top:-28px; width:36px; height:0; border-top:4px solid var(--ink); }}
.cards + .toc-sub {{ margin-top:72px; }}
.ix-h .label, .part h2 {{ padding-top:0; font-family:var(--latin); font-size:30px; letter-spacing:0; text-transform:none;
               font-weight:700; color:var(--ink); line-height:1.35; }}
.ix-h span {{ font-family:var(--mono); font-size:13px; letter-spacing:.08em; color:var(--mute); }}
.ix-h.hot {{ margin-top:var(--sec); }}
.rows {{ list-style:none; margin:0; padding:0; }}
/* a row of an index leads with its structure: number | the figure, large | headline and supplement | the facts */
        gap:0 36px; align-items:center; padding:26px 0; border-top:1px solid var(--hair); }}
.thumb {{ display:block; }}
.thumb img {{ display:block; width:100%; height:auto; }}
.thumb.none {{ aspect-ratio:3/2; display:flex; align-items:center; justify-content:center;
               font-family:var(--mono); font-size:11px; color:var(--mute); text-align:center; padding:8px; }}
.more {{ margin:8px 0 0; font-family:var(--mono); font-size:13px; }}
.more a {{ text-decoration:underline; text-decoration-color:var(--acc); text-underline-offset:4px; }}
.past {{ margin-top:56px; }}
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
/* the articles under a part's heading (New Structure, ...) are its sub-sections: each after the first opens with the bar */
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
/* the paper's title, then who it comes from: labels in one column, so the three lines read as one block */
.pmeta {{ margin:14px 0 26px; }}
.pmeta p {{ display:grid; grid-template-columns:118px minmax(0,1fr); margin:0; font-family:var(--latin); line-height:1.5; }}
.pmeta p + p {{ margin-top:5px; }}
.ptitle {{ font-size:16.5px; color:var(--ink2); }}
.pmeta .pl {{ padding-top:.35em; font-family:var(--mono); font-size:10.5px; line-height:1.4; letter-spacing:.1em; text-transform:uppercase; color:var(--mute); }}
.ppl {{ font-size:15px; color:var(--ink2); }}
.ppl .pa {{ font-size:13.5px; color:var(--mute); }}
.ppl .ja {{ font-family:var(--sans); }}
.pj {{ font-size:14.5px; color:var(--ink2); }}
.pj a {{ color:var(--ink2); text-decoration:underline; text-decoration-color:var(--hair); text-underline-offset:3px; }}
.pj a:hover {{ color:var(--acc-ink); text-decoration-color:var(--acc); }}
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
.legend b {{ font-size:14.5px; font-weight:700; overflow-wrap:anywhere; }}   /* a PDB entity name can be a whole RNA sequence */
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
.rest {{ margin-top:var(--sec); }}
.rest .bl {{ margin-top:10px; font-size:14.5px; }}

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
/* the footer: the name and what the site is on the left, the places on the right; under a hairline one quiet line */
.colophon {{ padding-top:30px; padding-bottom:64px; font-size:12.5px; color:var(--mute); line-height:1.8; }}
.co-top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:28px 48px; flex-wrap:wrap; padding-bottom:26px; }}
.co-word {{ font-family:var(--latin); font-size:24px; font-weight:700; line-height:1.1; letter-spacing:-.01em; color:var(--ink);
            text-decoration:none; }}
.co-word:hover {{ color:var(--acc-ink); text-decoration:none; }}
.co-brand p {{ margin:8px 0 0; font-size:12.5px; color:var(--desc); }}
.co-nav {{ display:flex; flex-wrap:wrap; gap:10px 30px; padding-top:6px; }}
.co-nav a {{ font-family:var(--mono); font-size:11.5px; letter-spacing:.1em; text-transform:uppercase; color:var(--ink2);
             text-decoration:none; }}
.co-nav a:hover {{ color:var(--acc-ink); }}
.co-bot {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 26px; padding-top:14px; border-top:1px solid var(--hair); }}
.co-bot p {{ margin:0; }}
.co-bot a {{ color:var(--desc); }}
.co-bot a:hover {{ color:var(--acc-ink); }}
.co-beta {{ font-family:var(--mono); font-size:11.5px; letter-spacing:.06em; }}
/* a page of text (Privacy Policy, 特定商取引法に基づく表示): one readable column */
.doc .doc-lead, .doc .doc-sec, .doc .doc-date {{ max-width:760px; }}   /* left, under the heading, as the other pages */
.doc-lead {{ margin:0 0 8px; font-size:16px; line-height:1.9; color:var(--ink); }}
.doc-sec {{ margin-top:40px; }}
.doc-sec h2 {{ margin:0 0 12px; font-family:var(--sans); font-size:17px; font-weight:700; color:var(--ink); }}
.doc-sec p {{ margin:0; font-size:15px; line-height:1.9; color:var(--ink2); }}
.doc-sec p + p, .doc-sec .bl + p {{ margin-top:12px; }}
.doc-sec .bl {{ margin:12px 0 0; font-size:15px; line-height:1.8; color:var(--ink2); }}
.doc-dl {{ margin:0; }}
.doc-dl div {{ display:grid; grid-template-columns:200px minmax(0,1fr); gap:6px 24px; padding:14px 0; border-top:1px solid var(--hair); }}
.doc-dl div:last-child {{ border-bottom:1px solid var(--hair); }}
.doc-dl dt {{ font-size:14.5px; font-weight:700; color:var(--ink2); }}
.doc-dl dd {{ margin:0; font-size:14.5px; line-height:1.8; color:var(--ink2); }}
.doc-dl .todo {{ color:var(--mute); }}
.doc-date {{ margin:48px 0 0; font-size:13px; color:var(--mute); }}


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
.mail-grid {{ display:grid; grid-template-columns:minmax(0,1.1fr) minmax(0,1fr); gap:48px; margin-top:16px; align-items:start; }}
.mail p {{ margin:0 0 8px; font-size:15px; color:var(--desc); }}
.mail .bl {{ margin:6px 0 0; font-size:14.5px; line-height:1.65; color:var(--desc); }}
.mail .bl li + li {{ margin-top:12px; }}
.mail .soon {{ font-family:var(--mono); font-size:12px; letter-spacing:.06em; color:var(--acc-ink); }}
.mail form {{ display:flex; gap:8px; }}
.mail input {{ flex:1; min-width:0; font:inherit; font-family:var(--sans); font-size:14.5px; padding:10px 12px; border:1px solid var(--ink);
               background:transparent; border-radius:0; }}
.mail button {{ font:inherit; font-family:var(--mono); font-size:13px; padding:10px 18px; border:1px solid var(--ink); background:var(--ink);
                color:#fff; border-radius:0; }}
.list-page.mail {{ min-height:52vh; }}
.mail input:disabled, .mail button:disabled {{ opacity:.5; cursor:not-allowed; }}


.pr[hidden], .pg[hidden] {{ display:none; }}

/* The header (wordmark, MENU, the double rule) slides away while the reader scrolls down and comes back as soon as
   they scroll up; on the first screen it always stays (HEAD_SCRIPT). Whatever sticks while scrolling (an article's
   margin, an issue's contents) sits under the header when it is there (--headnow). */
@media (prefers-reduced-motion:no-preference) {{ html {{ scroll-behavior:smooth; }} }}
html {{ scroll-padding-top:calc(var(--navh) + 16px); }}
body {{ --headnow:var(--navh); }}
.mast {{ background:var(--ground); }}
.gnav {{ position:sticky; top:0; z-index:1000; background:var(--ground); }}
.vol {{ display:flex; align-items:center; gap:22px; }}
.menu {{ position:relative; }}
.menu summary {{ list-style:none; display:flex; align-items:center; gap:14px; cursor:pointer; user-select:none;
                 font-family:var(--mono); font-size:13px; letter-spacing:.16em; color:var(--ink); }}
.menu summary::-webkit-details-marker {{ display:none; }}
.menu-dim {{ display:none; }}   /* a phone: the page dims behind the open MENU; a tap on it closes the MENU */
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
.menu-list small {{ display:block; margin-top:1px; font-family:var(--sans); font-size:11.5px; color:var(--mute); }}
.menu-list a:hover small, .menu-list a:focus-visible small {{ color:#C9D1DD; }}
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
              min-height:min(560px, calc(100vh - var(--head) - 48px)); padding-top:24px; padding-bottom:24px; }}
.lead-h {{ margin:0; font-family:var(--serif); font-weight:400; font-size:clamp(18px, 1.72vw, 30px); line-height:1.8;
           color:var(--ink); letter-spacing:.02em; }}
.lead-h span {{ display:block; white-space:nowrap; }}
.lead-h .sp {{ display:none; }}
html, body {{ overflow-x:clip; }}   /* a guard: nothing may widen the page sideways (see docs/decisions.md) */
.lead-go {{ display:inline-flex; align-items:center; gap:12px; margin-top:34px; padding:14px 24px 14px 20px;
            background:var(--ink); color:#fff; font-family:var(--mono); font-size:14.5px; letter-spacing:.06em; }}
.lead-go .tri {{ font-size:13px; }}
.lead-mark {{ display:none; }}   /* a phone's first screen only */
.upd {{ display:none; }}   /* a phone's header only */
.totop {{ display:none; }}   /* a phone only */
.lead-upd {{ margin:8px 0 0; text-align:center; font-family:var(--mono); font-size:11.5px; letter-spacing:.06em; color:var(--mute); }}
.lead-upd span {{ margin-left:12px; }}
.lead-text .lead-upd {{ margin-top:22px; text-align:left; }}
/* under the first screen's structure: what it is, small and quiet */
.lead-cap {{ display:flex; flex-wrap:wrap; justify-content:center; align-items:baseline; gap:2px 12px; margin:10px 0 0; padding:0 24px; text-decoration:none;
             color:var(--ink2); }}
.lead-cap .k {{ font-family:var(--mono); font-size:11.5px; letter-spacing:.1em; text-transform:uppercase; color:var(--acc-ink); }}
.lead-cap .h {{ font-family:var(--sans); font-size:14.5px; line-height:1.6; }}
.lead-cap:hover {{ text-decoration:none; }}
.lead-cap:hover .h {{ color:var(--acc-ink); }}
.lead-btns {{ display:flex; flex-wrap:wrap; gap:12px 14px; margin-top:34px; }}
.lead-btns .lead-go {{ margin-top:0; }}
.lead-go.alt {{ background:transparent; color:var(--ink); box-shadow:inset 0 0 0 1px var(--ink); }}
.lead-go.alt:hover {{ background:var(--ink); color:#fff; }}
.lead .lead-go {{ font-family:var(--sans); font-size:15.5px; font-weight:700; letter-spacing:.02em; padding:15px 26px 15px 24px; }}
.lead .lead-go:not(.alt) {{ font-size:16.5px; padding:16px 28px 16px 26px; }}
.lead .lead-go .arr {{ font-family:var(--mono); transition:transform .3s cubic-bezier(.2,.8,.2,1); }}
.lead .lead-go:hover .arr {{ transform:translateX(5px); }}
.lead-parts {{ display:flex; flex-wrap:wrap; align-items:center; gap:6px 22px; margin:24px 0 0; font-size:14px; }}
.lead-parts .t {{ font-family:var(--mono); font-size:11.5px; letter-spacing:.1em; color:var(--mute); }}
.lead-parts a {{ display:inline-flex; align-items:center; color:var(--ink2); text-decoration:underline; text-decoration-color:var(--hair);
                 text-underline-offset:4px; }}
.lead-parts a:hover {{ color:var(--acc-ink); text-decoration-color:var(--acc); }}
.lead-parts b {{ margin-left:7px; font-family:var(--mono); color:var(--ink); }}
.lead-about {{ display:inline-flex; gap:8px; margin-top:16px; font-family:var(--mono); font-size:12.5px; letter-spacing:.04em; color:var(--ink2);
               text-decoration:underline; text-decoration-color:var(--hair); text-underline-offset:4px; }}
.lead-about:hover {{ color:var(--acc-ink); text-decoration-color:var(--acc); }}

/* About FoldFeed, right under the first screen */
.about {{ padding:var(--sec) 0 0; }}
.about-page .about {{ padding-top:40px; padding-bottom:var(--sec); }}
.about-grid {{ display:grid; grid-template-columns:minmax(0,64fr) minmax(0,36fr); gap:48px; margin-top:22px; align-items:start; }}
.about-dl {{ margin:0; }}
/* spacing outside the articles (the owner's memo, 2026-10-22): the lines of one sentence close together, sentences and
   items clearly apart, so each reads as one piece */
.about-dl div {{ display:grid; grid-template-columns:136px minmax(0,1fr); gap:16px; padding:24px 0; border-top:1px solid var(--hair); }}
.about-dl div:first-child {{ border-top:0; padding-top:0; }}
.about-dl dt {{ font-size:15px; font-weight:700; color:var(--ink2); padding-top:3px; }}
.about-dl dd {{ margin:0; font-size:16px; line-height:1.7; color:var(--ink2); }}
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
.ppi .lg i.u {{ background:{CHART_UPD}; }}
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
.col .stack i.u {{ background:{CHART_UPD}; margin-bottom:2px; }}
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
.sr {{ position:absolute; left:0; top:0; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); clip-path:inset(50%); }}
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
@keyframes comein {{ from {{ opacity:0; transform:translate3d(7%, 9%, 0) rotate(-10deg) scale(.84); }}
                    to {{ opacity:1; transform:translate3d(1.5%, 0, 0) rotate(-3deg); }} }}
@keyframes drift {{ from {{ transform:translate3d(1.5%, 0, 0) rotate(-3deg); }}
                  to {{ transform:translate3d(-2.5%, 2%, 0) rotate(3.5deg) scale(1.06); }} }}
@keyframes slidein {{ from {{ opacity:0; transform:translateX(-18px); }} to {{ opacity:1; transform:none; }} }}
@media (max-width:860px) {{   /* the phone's first screen comes in line by line, then the structure (it swings
                                  in, then sways, never a full turn; the scroll turns it a little more: LEAD_SCRIPT) and
                                  the Picked up mark */
  .anim .lead-mark {{ animation:slidein .8s cubic-bezier(.2,.7,.2,1) 1.1s both; }}
  .anim .lead-art .fig {{ animation:comein 1.6s cubic-bezier(.2,.7,.2,1) .3s both, drift 6.5s ease-in-out 1.9s infinite alternate;
                          will-change:transform; }}
}}
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
.rel a:hover .thumb img {{ transform:scale(1.04); }}
/* under the pointer a structure lifts off the page: up a little, its shadow longer and softer */
/* every structure casts a soft shadow down and to the right (the owner's choice, 2026-10-20: PowerPoint's black 40%,
   blur 3 pt, 45°, distance 2 pt). The labelled figures carry it in the file, on the molecule only (pipeline.render);
   the pictures without labels get it here, the same in pixels whatever their size */
.thumb, .pk-fig, .ihc-grid .cfig, .bt-th, .iss-th, .lp-fig {{ overflow:visible; }}
.thumb img, .ihc-grid img, .bt-th img, .iss-th img, .mi-th img, .lp-fig img, .lead-art img {{ filter:var(--fig-shadow); }}
.card .thumb img, .pk-fig img, .ihc-grid img, .bt-th img {{
     transition:transform .45s cubic-bezier(.2,.8,.2,1), filter .45s cubic-bezier(.2,.8,.2,1); }}
.card:hover .thumb img, .pickup:hover .pk-fig img, .ihc-grid a:hover img, .bt a:hover img {{ transform:translateY(-6px); }}
.card:hover .thumb img, .ihc-grid a:hover img, .bt a:hover img {{ filter:var(--fig-shadow-up); }}
.card h3 a, .pk-h a {{ background:linear-gradient(currentColor,currentColor) left bottom / 0 1px no-repeat;
                         transition:background-size .35s cubic-bezier(.2,.8,.2,1), color .2s; }}
.card:hover h3 a, .pickup:hover .pk-h a {{ background-size:100% 1px; text-decoration:none; }}
@media (prefers-reduced-motion:reduce) {{
  *, *::before, *::after {{ animation-duration:.01ms !important; animation-iteration-count:1 !important;
                            transition-duration:.01ms !important; transition-delay:0ms !important; }}
}}
.lead-art {{ display:block; margin-right:calc(-1 * var(--pad) + 24px); }}
.lead-art img {{ display:block; width:100%; height:auto; max-height:min(620px, calc(100vh - var(--head) - 90px)); object-fit:contain; }}
.lead-3d .v3d-view, .lead-3d.on .v3d-view, .lead-3d.loading .v3d-view {{ aspect-ratio:1000/780; max-height:min(620px, calc(100vh - var(--head) - 110px));
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
.ihead {{ padding:32px 0 10px; }}
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
.ih-date {{ margin:4px 0 0; font-family:var(--latin); font-size:clamp(56px, 5.2vw, 84px); line-height:1; letter-spacing:-.035em; font-weight:700; color:var(--ink); }}
.ih-src {{ margin:12px 0 0; font-size:14.5px; color:var(--desc); }}
.kl i {{ display:inline-block; flex:none; width:10px; height:10px; border-radius:2px; margin-right:8px; }}
.kl .sw-n {{ background:{CHART_NEW}; }}
.kl .sw-u {{ background:{CHART_UPD}; }}
.kl .sw-h {{ background:{CHART_HOT}; }}
/* an issue's first screen: how much (left), what (right) */
.ih-grid {{ display:grid; grid-template-columns:minmax(0,34fr) minmax(0,66fr); gap:0 56px; align-items:start; }}
.ih-big {{ display:flex; gap:40px; margin-top:28px; padding-top:22px; border-top:1px solid var(--hair); }}
.ih-big .kv {{ font-size:60px; font-weight:700; }}
.ih-big .kv small {{ font-size:18px; margin:0 3px; }}
.ih-big .kl {{ font-size:14px; }}
.ih-parts {{ display:flex; flex-direction:column; gap:2px; margin:18px 0 0; max-width:300px; }}
.ih-parts a {{ display:flex; align-items:center; padding:3px 0; color:var(--ink2); text-decoration:none; }}
.ih-parts a:hover {{ color:var(--acc-ink); }}
.ih-parts b {{ margin-left:auto; padding-left:16px; font-family:var(--mono); color:var(--ink); }}
.ih-left .ih-nav {{ margin-top:26px; }}
.ih-left .ib {{ padding:9px 14px; font-size:12px; }}
.ihc-head {{ display:flex; align-items:center; gap:18px; padding-bottom:10px; margin-bottom:16px; border-bottom:1px solid var(--rule); }}
.ihc-t {{ margin:0; font-family:var(--latin); font-size:22px; font-weight:700; color:var(--ink); }}
.ihc-ctl {{ display:none; align-items:center; gap:10px; font-family:var(--mono); font-size:13px; color:var(--ink2); }}
.ihc.paged .ihc-ctl {{ display:flex; }}
.ihc-ctl button {{ width:34px; height:30px; font:inherit; color:var(--ink); background:transparent; border:1px solid var(--ink);
                   border-radius:0; cursor:pointer; transition:background-color .2s, color .2s; }}
.ihc-ctl button:hover {{ background:var(--ink); color:#fff; }}
.ihc-toc {{ margin-left:auto; display:inline-flex; align-items:center; gap:10px; padding:8px 14px; background:var(--ink); color:#fff;
            font-family:var(--mono); font-size:12.5px; letter-spacing:.06em; text-decoration:none; transition:background-color .25s; }}
.ihc-toc:hover {{ background:var(--acc-ink); color:#fff; text-decoration:none; }}
.ihc-grid {{ list-style:none; margin:0; padding:0; display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px 24px; }}
.ihc-grid > li[hidden] {{ display:none; }}
.ihc-grid a {{ display:block; text-decoration:none; color:var(--ink); }}
.ihc-grid .thumb.none {{ border:1px solid var(--hair); }}
.ihc-grid img {{ display:block; }}
.ihc-grid img, .ihc-grid .thumb.none {{ width:100%; height:auto; aspect-ratio:3/2; transition:transform .4s cubic-bezier(.2,.8,.2,1); }}
.ihc-grid .cno {{ font-size:24px; }}
.ihc-grid .ctg {{ margin-top:6px; font-size:15.5px; }}
.ihc-grid .ct {{ display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; min-height:2.8em; }}
.ihc-grid a:hover .ct b {{ color:var(--acc-ink); }}
.ihc-bar {{ display:none; height:2px; margin-top:14px; background:var(--hair); }}
.ihc.paged:not(.manual) .ihc-bar {{ display:block; }}
.ihc-bar i {{ display:block; height:100%; background:var(--ink); transform-origin:left; transform:scaleX(0); }}
.ihc-bar.run i {{ animation:ihc-run {CAROUSEL_SECONDS}s linear forwards; }}
.ihc.hold .ihc-bar i {{ animation-play-state:paused; }}
@keyframes ihc-run {{ to {{ transform:scaleX(1); }} }}
.lead-go b {{ margin-left:12px; font-weight:700; }}
.toc-sub {{ display:flex; flex-wrap:wrap; gap:12px; align-items:baseline; margin:22px 0 2px; font-family:var(--mono); font-size:13px;
            letter-spacing:.1em; text-transform:uppercase; color:var(--ink2); }}
.toc-sub span {{ color:var(--mute); }}
.part h2 .hd {{ margin-left:16px; font-family:var(--latin); font-weight:400; color:var(--mute); }}
.shell.solo {{ grid-template-columns:minmax(0,1fr); }}
/* an issue page: its contents sidebar sits close to the right edge of the window */
.wrap.sticky-side {{ max-width:none; padding-left:max(var(--pad), calc((100vw - 1680px) / 2 + var(--pad)));
                     padding-right:clamp(16px, 2vw, 32px); }}
.about-dl dd .s {{ display:block; text-wrap:balance; }}
.about-dl dd .s + .s {{ margin-top:.75em; }}
.about-dl dd .w {{ white-space:nowrap; }}
.about-dl .ab-lead {{ margin:0; color:var(--ink); }}
/* a list (About, Newsletters & Alerts, Not Covered): a short dash hung in the margin, at the first line's middle; the
   lines of an item are kept close, the items a little apart */
.bl {{ list-style:none; margin:14px 0 0; padding:0; }}
.bl li {{ position:relative; padding-left:1.5em; }}
.bl li + li {{ margin-top:.6em; }}
.bl li::before {{ content:""; position:absolute; left:.15em; top:.82em; top:calc(.5lh - .5px); width:.7em; border-top:1px solid var(--mute); }}
.about-dl .bl {{ font-size:15px; line-height:1.75; }}
/* the parts and what goes in each: the part's name with its colour, its text beside it (under it on a phone) */
.bl.bl-parts {{ display:grid; grid-template-columns:max-content minmax(0,1fr); gap:.6em 1.4em; align-items:baseline; margin:18px 0; }}
.bl-parts li {{ display:contents; }}
.bl-parts li::before {{ display:none; }}
.bl-parts b {{ font-family:var(--latin); font-weight:700; color:var(--ink); white-space:nowrap; }}
.bl-parts i {{ display:inline-block; width:9px; height:9px; margin-right:9px; border-radius:2px; vertical-align:.05em; }}
.bl-parts .sw-n {{ background:{CHART_NEW}; }}
.bl-parts .sw-u {{ background:{CHART_UPD}; }}
.bl-parts .sw-h {{ background:{CHART_HOT}; }}

/* the article's four-line introduction: plain lines, one weight, no rules; the labels are quiet and sit beside the text */
.art-4 .open dd {{ color:var(--desc); font-size:15px; }}

/* search.html: the articles found, grouped by kind of protein */
.pr {{ display:grid; grid-template-columns:132px minmax(0,1fr) 250px; gap:6px 22px; padding:13px 0; align-items:baseline; }}
.pr:not([hidden]) ~ .pr {{ border-top:1px solid var(--hair); }}   /* the first row shown sits on the heading's rule */
.pr .pd {{ font-family:var(--mono); font-size:12px; color:var(--mute); white-space:nowrap; }}
.pr .pt {{ font-family:var(--sans); font-size:17px; font-weight:700; line-height:1.55; }}
.pr .ps {{ display:block; margin-top:2px; font-family:var(--sans); font-size:13.5px; font-weight:400; color:var(--desc); }}
.pr .pm {{ font-family:var(--mono); font-size:11.5px; line-height:1.7; color:var(--ink2); overflow-wrap:anywhere; }}
.pr .pm i {{ color:var(--mute); font-style:normal; letter-spacing:.08em; margin-right:6px; }}
.pg-none {{ display:none; margin-top:30px; font-family:var(--mono); font-size:13px; color:var(--acc-ink); }}
.pg-list {{ list-style:none; margin:10px 0 0; padding:0; border-top:1px solid var(--rule); }}
.pg-list .pr:first-child {{ border-top:0; }}
.pg-more {{ display:block; margin:26px 0 0; padding:11px 18px; font:inherit; font-family:var(--mono); font-size:12.5px;
            color:var(--ink); background:transparent; border:1px solid var(--ink); cursor:pointer; }}
.pg-more:hover {{ background:var(--ink); color:#fff; }}
.pg-more[hidden] {{ display:none; }}

/* the global navigation: one row of the main places under the header's double rule, small capitals; the current page
   in vermilion. A phone has the same places in the MENU, and only there is the MENU */
.gnav-in {{ display:flex; align-items:center; gap:28px; padding:8px 0; border-bottom:1px solid var(--hair); }}
.gnav-links {{ flex:none; display:flex; gap:34px; min-width:0; white-space:nowrap; }}
.gnav-links::-webkit-scrollbar {{ display:none; }}
.gnav .hs {{ flex:0 1 340px; max-width:340px; }}
.gnav .hs-q {{ padding:6px 12px 6px 34px; font-size:13px; line-height:1.5; }}
.gnav .hs-pop {{ top:calc(100% + 9px); }}   /* hangs from the navigation's rule */
.gn-item {{ position:relative; }}
.gn-a {{ display:block; padding:6px 0; font-family:var(--mono); font-size:12.5px; letter-spacing:.1em; text-transform:uppercase;
         color:var(--ink2); text-decoration:none; }}
.gn-item:hover .gn-a, .gn-a:focus-visible {{ color:var(--acc-ink); text-decoration:none; }}
.gn-a[aria-current] {{ color:var(--acc-ink); font-weight:700; }}
/* a line on the navigation's rule: grows under the current place as the page opens, and under a place being opened */
.gnav-links {{ align-self:stretch; }}
.gn-item {{ display:flex; align-items:center; }}
.gn-item::after {{ content:""; position:absolute; left:0; right:0; bottom:-9px; height:2px; background:var(--ink2); transform:scaleX(0);
                   transform-origin:left; transition:transform .35s cubic-bezier(.2,.8,.2,1); }}
.gn-item:hover::after, .gn-item:focus-within::after {{ transform:scaleX(1); }}
.gn-item:has(.gn-a[aria-current])::after {{ background:var(--acc-ink); transform:scaleX(1); }}
.anim .gn-item:has(.gn-a[aria-current])::after {{ animation:grow .7s cubic-bezier(.2,.8,.2,1) .25s both; }}
@keyframes grow {{ from {{ transform:scaleX(0); }} to {{ transform:scaleX(1); }} }}
/* the chevron that says a place opens; it turns up while its dropdown is open */
.gn-a {{ display:flex; align-items:center; gap:8px; }}
.gn-v {{ width:6px; height:6px; margin-top:-3px; border-right:1.5px solid currentColor; border-bottom:1.5px solid currentColor;
         transform:rotate(45deg); transition:transform .28s cubic-bezier(.2,.8,.2,1), margin .28s cubic-bezier(.2,.8,.2,1); opacity:.7; }}
.gn-item:hover .gn-v, .gn-item:focus-within .gn-v {{ transform:rotate(-135deg); margin-top:3px; opacity:1; }}
/* a place's dropdown: what it is (Japanese, the first line), then what it holds; hangs from the navigation's rule */
.gn-pop {{ position:absolute; left:-22px; top:calc(100% + 8px); z-index:1100; width:340px; box-sizing:border-box; padding:16px 22px 14px;
           background:var(--ground); border:1px solid var(--ink); border-top:0; white-space:normal;
           opacity:0; visibility:hidden; transform:translateY(-6px);
           transition:opacity .18s ease, transform .18s ease, visibility 0s linear .18s; }}
.gn-item:hover .gn-pop, .gn-item:focus-within .gn-pop {{ opacity:1; visibility:visible; transform:none;
           transition:opacity .2s ease .08s, transform .2s cubic-bezier(.2,.8,.2,1) .08s, visibility 0s linear .08s; }}
.gn-item:last-child .gn-pop {{ left:auto; right:-22px; }}
.gn-d {{ margin:0 0 10px; padding-bottom:10px; border-bottom:1px solid var(--hair); font-family:var(--sans); font-size:13.5px;
         line-height:1.75; color:var(--ink); }}
.gn-pop ul {{ list-style:none; margin:0; padding:0; }}
.gn-pop li a {{ display:flex; justify-content:space-between; gap:16px; padding:5px 0; font-family:var(--sans); font-size:14px;
                line-height:1.5; color:var(--ink2); text-decoration:none; }}
.gn-pop li a:hover, .gn-pop li a:focus-visible {{ color:var(--acc-ink); text-decoration:none; }}
.gn-pop li span {{ font-family:var(--mono); font-size:12px; color:var(--mute); }}
@media (min-width:861px) {{ .menu {{ display:none; }} }}

/* cards: the structure leads, then number and target, headline, the one-line supplement. No frame, no shadow: the
   figures stand on the page ground, the grid and the space keep them apart. Three sizes: the pick, New Structure,
   the other two parts */
.cards {{ list-style:none; margin:0; padding:0; display:grid; gap:80px 48px; }}
.cards.md {{ grid-template-columns:repeat(3, minmax(0,1fr)); }}
.cards.sm {{ grid-template-columns:repeat(4, minmax(0,1fr)); gap:68px 32px; }}
.card {{ min-width:0; }}
.card .thumb.none {{ border:1px solid var(--hair); }}
.cfig {{ position:relative; }}
/* the article's number, large, at the figure's top left (the same face as the number beside each article) */
.cno {{ position:absolute; left:0; top:0; padding:0 10px 4px 0; background:var(--ground); font-family:var(--latin);
        font-size:26px; line-height:1; letter-spacing:-0.01em; color:var(--acc-ink); pointer-events:none; }}
.card.md .cno {{ font-size:32px; }}
.pickup .cno {{ font-size:44px; }}
/* the target, the line a reader scans for: the protein's name bold, the organism lighter */
.ctg {{ display:flex; align-items:flex-start; gap:12px; margin:12px 0 0; font-family:var(--latin); font-size:15px; line-height:1.4;
        color:var(--ink2); }}
.ctg .ct {{ min-width:0; overflow-wrap:anywhere; }}
.ctg b {{ font-weight:700; }}
.ctg .org {{ display:inline-block; color:var(--mute); }}   /* the parenthesis moves to the next line whole */
.card.md .ctg {{ font-size:16px; }}
/* the target (Georgia, smaller, a lighter ink) and the headline under it (Noto Sans, larger, the darkest ink) */
.crow {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin:14px 0 0; }}
.crow .later.sm {{ flex:none; margin:0; }}
.cpart {{ display:flex; align-items:center; margin:0; font-family:var(--mono); font-size:11.5px; letter-spacing:.06em;
          color:var(--ink2); }}
.cpart b {{ color:var(--acc-ink); }}
.cpart .sep {{ margin:0 7px; color:var(--mute); }}
.crow + .ctg {{ margin-top:8px; }}
.toc-cards h3, .toc-cards .cs {{ display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
.cact {{ margin:12px 0 0; }}
.cact .later.sm {{ margin:0; }}
.ck {{ display:flex; align-items:center; gap:10px; margin:0; font-family:var(--mono); font-size:13px; line-height:1.6;
       color:var(--ink2); letter-spacing:.04em; }}
.ck .later.sm {{ margin:0 0 0 auto; flex:none; }}
.card h3 {{ margin:5px 0 0; font-family:var(--sans); font-size:16.5px; line-height:1.55; font-weight:700; color:var(--ink); text-wrap:pretty; }}
.card.md h3 {{ font-size:19px; }}
.cs {{ margin:5px 0 0; font-family:var(--sans); font-size:13.5px; line-height:1.75; color:var(--desc); }}
.card.md .cs {{ font-size:14.5px; }}
.cards + .ix-h {{ margin-top:var(--sec); }}
/* the pick: the largest card, its own figure on the left, the words and the ways on to the right */
.pickup {{ display:grid; grid-template-columns:minmax(0,7fr) minmax(0,5fr); gap:0 52px; align-items:start; }}
.pk-fig {{ display:block; overflow:hidden; }}
.pk-fig img {{ display:block; width:100%; height:auto; transition:transform .5s cubic-bezier(.2,.8,.2,1); }}
.pickup .pk-tg {{ margin:12px 0 0; font-size:22px; }}
.pk-h {{ margin:12px 0 0; font-size:32px; line-height:1.42; font-weight:700; color:var(--ink); text-wrap:balance; }}
.pk-s {{ margin:12px 0 0; font-size:17px; line-height:1.8; color:var(--desc); }}
.pickup .reads {{ margin-top:26px; gap:12px; }}
.pickup .reads a {{ min-width:0; flex:1 1 220px; }}
/* Concept, right under the first screen: the site in its words, and the day in numbers (thousands of new papers ->
   the structural biology ones read whole -> the articles, by part) */
.concept-grid {{ display:grid; grid-template-columns:minmax(0,5fr) minmax(0,7fr); gap:40px 72px; align-items:start; }}
.concept-t {{ margin:0; font-size:17px; line-height:1.7; color:var(--ink2); }}
.concept-t .s {{ display:block; text-wrap:balance; }}
.concept-t .s + .s {{ margin-top:.75em; }}
.concept-t .w {{ white-space:nowrap; }}
.flow {{ margin:0; }}
.flow ol {{ list-style:none; margin:0; padding:0; display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:0 40px; }}
.flow li {{ position:relative; min-width:0; }}
.flow li + li::before {{ content:"→"; position:absolute; left:-30px; top:14px; font-family:var(--mono); font-size:17px; color:var(--mute); }}
.flow li > b {{ display:block; font-family:var(--latin); font-size:clamp(40px, 3.4vw, 58px); line-height:1; font-weight:700; color:var(--ink);
          font-variant-numeric:tabular-nums; }}
.flow-bar {{ display:flex; gap:2px; width:calc(var(--w) * 100%); height:6px; margin:14px 0 0; }}
.flow-bar i {{ display:block; flex:1; background:var(--ink); }}
.flow-bar i.n {{ background:{CHART_NEW}; }}
.flow-bar i.u {{ background:{CHART_UPD}; }}
.flow-bar i.h {{ background:{CHART_HOT}; }}
.flow-t {{ display:block; margin-top:12px; font-size:13.5px; line-height:1.6; color:var(--desc); }}
.flow-parts {{ display:block; margin-top:8px; font-size:12.5px; line-height:1.5; color:var(--desc); }}
.flow-parts > a {{ display:flex; align-items:center; width:max-content; white-space:nowrap; color:var(--desc); text-decoration:none; }}
.flow-parts > a + a {{ margin-top:3px; }}
.flow-parts > a:hover {{ color:var(--acc-ink); }}
.flow-parts b {{ margin-left:8px; font-family:var(--mono); color:var(--ink); }}
.flow figcaption {{ margin-top:26px; font-size:13.5px; }}
.flow figcaption a {{ color:var(--ink2); text-decoration:underline; text-decoration-color:var(--hair); text-underline-offset:4px; }}
.flow figcaption a:hover {{ color:var(--acc-ink); text-decoration-color:var(--acc); }}
.flow figcaption .arr {{ margin-left:8px; font-family:var(--mono); }}
.concept-go {{ display:none; margin:0; }}
.anim .flow-bar {{ transform-origin:left; transform:scaleX(0); transition:transform 1s cubic-bezier(.2,.8,.2,1) .25s; }}
.anim .flow li.in .flow-bar, .anim .flow.in .flow-bar {{ transform:none; }}
/* the pick's four lines on the front page (question, approach, result, learned) */
.pk-4 {{ margin:22px 0 0; }}
.pk-4 div {{ grid-template-columns:96px minmax(0,1fr); gap:12px; padding:6px 0; }}
.pk-4 dt {{ font-size:13.5px; }}
.pk-4 dd {{ font-size:15px; line-height:1.75; }}

/* the front page's way into the issue: its other structures, small, each a link to its article */
.mi-th {{ list-style:none; margin:0; padding:0; display:grid; grid-template-columns:repeat({MORE_THUMBS}, minmax(0,1fr)); gap:28px; }}
.mi-th.more {{ grid-template-columns:repeat({MORE_THUMBS}, minmax(0,1fr)) 48px; }}
.mi-th a {{ position:relative; display:block; color:var(--ink); text-decoration:none; }}
.mi-th img {{ display:block; width:100%; height:auto; transition:transform .4s cubic-bezier(.2,.8,.2,1), filter .4s; }}
.mi-th a:hover img {{ transform:translateY(-4px); filter:var(--fig-shadow-up); }}
.mi-th .no {{ aspect-ratio:3/2; display:flex; align-items:center; justify-content:center; padding:8px; border:1px solid var(--hair);
              font-family:var(--mono); font-size:11px; color:var(--mute); text-align:center; }}
.mi-no {{ position:absolute; left:0; top:0; padding:0 8px 3px 0; background:var(--ground); font-family:var(--latin); font-size:22px;
          line-height:1; color:var(--ink); }}   /* as a card's number: on the figure's top-left */
.mi-more a {{ height:100%; box-sizing:border-box; padding-bottom:var(--th-tg); display:flex; align-items:center; font-family:var(--mono);
              font-size:15px; color:var(--mute); }}   /* level with the figures, not with figure and target */
/* the target under a small figure (This Issue, Back Issues): the name only, two lines at most (three on a phone); the
   whole target is the figure's title */
.th-tg {{ display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; height:2.7em; margin-top:7px;
          font-family:var(--latin); font-size:13px; line-height:1.35; color:var(--ink2); overflow-wrap:anywhere; }}
.mi-th {{ --th-tg:calc(7px + 2.7 * 13px); }}
.mi-th a:hover .th-tg b {{ color:var(--acc-ink); }}
.mi-more a:hover {{ color:var(--acc-ink); }}

/* Search, while nothing is asked: a tile for each way in, the newest structures of it on top */
.find-page .find {{ max-width:760px; margin:0 0 40px; }}
.lp-lead .s {{ text-wrap:balance; }}
.lp-lead .w {{ white-space:nowrap; }}
.find-page .find input {{ padding:13px 14px; font-size:15.5px; }}
.find-page .bt-sec:first-child {{ margin-top:72px; }}
.fp-ways[hidden] {{ display:none; }}
.js .fp-results:not(.on) {{ display:none; }}   /* without JavaScript the tiles, then every article */
.bt-sec + .bt-sec, .lp-lead + .bt-sec {{ margin-top:88px; }}
.bt-h {{ position:relative; margin:0 0 22px; font-family:var(--latin); font-size:22px; font-weight:700; color:var(--ink); }}
.bt-h::before {{ content:""; position:absolute; left:0; top:-26px; width:36px; border-top:4px solid var(--ink); }}
.bt-grid {{ list-style:none; margin:0; padding:0; display:grid; gap:56px 36px; }}
.bt-grid.lg {{ grid-template-columns:repeat(3, minmax(0,1fr)); }}
.bt-grid.sm {{ grid-template-columns:repeat(4, minmax(0,1fr)); gap:48px 32px; }}
.bt a {{ display:block; color:var(--ink); text-decoration:none; }}
.bt-th {{ display:grid; grid-template-columns:repeat({BROWSE_THUMBS}, minmax(0,1fr)); gap:6px; }}
.bt-th img {{ display:block; width:100%; height:auto; transition:transform .4s cubic-bezier(.2,.8,.2,1); }}
.bt-n {{ display:flex; justify-content:space-between; align-items:baseline; gap:12px; margin-top:12px; font-family:var(--sans);
         font-size:16px; font-weight:700; line-height:1.5; }}
.bt-grid.lg .bt-n {{ font-family:var(--latin); font-size:21px; }}
.bt a:hover .bt-n {{ color:var(--acc-ink); }}
.bt-c {{ flex:none; font-family:var(--mono); font-size:12px; font-weight:400; color:var(--mute); }}
.bt-d {{ display:block; margin-top:6px; font-size:14px; line-height:1.6; color:var(--desc); }}

/* a list of issues (Back Issues, on its page and on the front page): the date, the parts with their counts, a strip of the
   structures with their targets */
.iss-list {{ list-style:none; margin:0; padding:0; }}
.iss {{ display:grid; grid-template-columns:200px 250px minmax(0,1fr); gap:0 36px; align-items:center; padding:18px 0;
        border-top:1px solid var(--hair); }}
.iss:last-child {{ border-bottom:1px solid var(--hair); }}
.ix-h + .iss-list > .iss:first-child {{ border-top:0; }}   /* the heading's rule is the first line */
.iss-d {{ display:block; text-decoration:none; }}
.iss-d:hover {{ text-decoration:none; }}
.iss-d .k {{ display:block; font-family:var(--mono); font-size:11.5px; letter-spacing:.06em; color:var(--acc-ink); }}
.iss-d b {{ display:block; margin-top:2px; font-family:var(--latin); font-size:30px; line-height:1.2; letter-spacing:-0.01em; color:var(--ink); }}
.iss-d:hover b {{ color:var(--acc-ink); }}
.iss-n {{ margin:0; display:flex; flex-direction:column; gap:3px; font-family:var(--mono); font-size:12px; }}
.iss-n span {{ display:flex; align-items:center; }}
.iss-n b {{ margin-left:auto; padding-left:12px; color:var(--ink); }}
.iss-th {{ --th-tg:calc(6px + 2.7 * 11.5px); display:grid; grid-template-columns:repeat({THUMBS_PER_ISSUE}, minmax(0,1fr)) 44px;
           gap:8px; align-items:start; color:var(--ink); text-decoration:none; }}
.iss-th:hover {{ text-decoration:none; }}
.iss-th .it {{ display:block; min-width:0; }}
.iss-th .th-tg {{ margin-top:6px; font-size:11.5px; }}
.iss-th:hover .th-tg b {{ color:var(--acc-ink); }}
.iss-th img {{ display:block; width:100%; height:auto; transition:transform .4s cubic-bezier(.2,.8,.2,1); }}
.iss-th:hover img {{ transform:scale(1.06); }}
.iss-th .no {{ display:block; aspect-ratio:3/2; border:1px solid var(--hair); }}
.iss-th .more {{ align-self:stretch; display:flex; align-items:center; padding-bottom:var(--th-tg); font-family:var(--mono);
                 font-size:12px; color:var(--mute); }}
.past .ix-h .ix-all {{ margin-left:auto; }}
.list-page {{ padding-top:40px; padding-bottom:var(--sec); }}
.lp-lead {{ margin:-6px 0 34px; font-size:15.5px; line-height:1.7; color:var(--desc); }}
.list-page .toc-sub {{ margin-top:0; margin-bottom:18px; }}
.list-page .cards + .toc-sub {{ margin-top:72px; }}
.toc-sub a {{ color:var(--ink); }}

/* the header search: one plain box; focused, a panel drops down with the ways in (kinds of protein, kinds of research,
   methods, parts) and, once something is typed, the matching articles. No shadow: an ink frame on the page ground. */
.hs {{ position:relative; flex:1 1 300px; max-width:480px; min-width:0; margin:0 0 0 auto; }}
.hs-q {{ display:block; width:100%; box-sizing:border-box; font:inherit; font-family:var(--sans); font-size:14.5px; padding:11px 14px;
         color:var(--ink); background:transparent; border:1px solid var(--ink); border-radius:0; }}
.hs-q::placeholder {{ color:var(--mute); }}
.hs-box {{ position:relative; display:block; }}
.hs-box .ic {{ position:absolute; left:12px; top:50%; transform:translateY(-50%); color:var(--mute); pointer-events:none; }}
.hs-box .hs-q {{ padding-left:36px; }}
.hs-open .ic {{ flex:none; margin-right:8px; }}
.hs-q:focus {{ outline:2px solid var(--acc); outline-offset:1px; }}
.hs-pop {{ position:absolute; z-index:1100; top:calc(100% + 26px); right:0; width:max(100%, 640px); box-sizing:border-box;
           max-height:calc(100vh - var(--head) - 24px); overflow-y:auto; padding:20px 22px 18px; background:var(--ground);
           border:1px solid var(--ink); border-top:0; }}   /* hangs from the header's double rule */
.hs-pop:not([hidden]) {{ animation:drop .24s cubic-bezier(.2,.8,.2,1); }}
@keyframes drop {{ from {{ opacity:0; transform:translateY(-8px); }} to {{ opacity:1; transform:none; }} }}
.hs-g[hidden], .hs-c a[hidden], .hs-res[hidden] {{ display:none; }}
.hs-g + .hs-g {{ margin-top:14px; }}
.hs-h {{ display:flex; justify-content:space-between; margin:0 0 8px; font-family:var(--mono); font-size:11px; letter-spacing:.12em;
         color:var(--mute); line-height:1.6; text-transform:uppercase; }}
.hs-c {{ display:flex; flex-wrap:wrap; gap:7px; }}
.hs-c a {{ display:inline-flex; align-items:baseline; gap:8px; padding:4px 11px; font-family:var(--sans); font-size:13.5px; line-height:1.6;
           color:var(--ink); border:1px solid var(--hair); }}
.hs-c a span {{ font-family:var(--mono); font-size:11px; color:var(--mute); }}
.hs-c a:hover, .hs-c a:focus-visible {{ background:var(--ink); border-color:var(--ink); color:#fff; text-decoration:none; }}
.hs-c a:hover span, .hs-c a:focus-visible span {{ color:#C9D1DD; }}
.hs-res {{ margin:0 0 18px; }}
.hs-res a {{ display:block; padding:8px 0; border-top:1px solid var(--hair); line-height:1.5; }}
.hs-res a:hover {{ text-decoration:none; }}
.hs-res a:hover b {{ color:var(--acc-ink); }}
.hs-res b {{ display:block; font-family:var(--sans); font-size:14.5px; font-weight:700; color:var(--ink); }}
.hs-res a span {{ display:block; margin-top:2px; font-family:var(--mono); font-size:11.5px; color:var(--mute); }}
.hs-none {{ margin:0; padding:8px 0; border-top:1px solid var(--hair); font-family:var(--mono); font-size:12.5px; color:var(--acc-ink); }}
.hs-all {{ display:flex; justify-content:space-between; gap:12px; margin-top:18px; padding-top:12px; border-top:1px solid var(--ink);
           font-family:var(--sans); font-size:14px; font-weight:700; color:var(--ink); }}
.hs-all span {{ font-family:var(--mono); transition:transform .3s cubic-bezier(.2,.8,.2,1); }}
.hs-all:hover {{ text-decoration:none; color:var(--acc-ink); }}
.hs-all:hover span {{ transform:translateX(5px); }}
.hs-open {{ display:none; font:inherit; font-family:var(--mono); font-size:12px; letter-spacing:.08em; padding:7px 11px; color:var(--ink);
            background:transparent; border:1px solid var(--ink); border-radius:0; cursor:pointer; }}
.hs-open[aria-expanded="true"] {{ background:var(--ink); color:#fff; }}

/* Read Later: the header link (shown once something is saved, with how many) and the button on each article.
   The button needs JavaScript (the list is kept in the browser), so without it there is none. */
.gnav .rl {{ flex:none; display:inline-flex; align-items:center; gap:9px; font-family:var(--mono); font-size:12.5px; letter-spacing:.08em;
            color:var(--ink); text-decoration:none; white-space:nowrap; }}
.lp-row.off {{ opacity:.45; }}
.lp-row {{ transition:opacity .3s; }}
.gnav .rl[hidden] {{ display:none; }}
.gnav .rl:hover {{ color:var(--acc-ink); text-decoration:none; }}
.rl-n {{ display:inline-flex; align-items:center; justify-content:center; min-width:24px; height:24px; box-sizing:border-box; padding:0 7px;
         border-radius:12px; background:var(--acc-ink); color:#fff; font-size:12px; font-weight:700; letter-spacing:0; }}
.rl-n:empty {{ display:none; }}
.menu-list .rl-n {{ margin-left:12px; vertical-align:1px; }}
.rl.pop .rl-n, .later.pop .lt, .menu summary.pop .mb {{ animation:tick .4s cubic-bezier(.2,.8,.2,1); }}
@keyframes arrive {{ 12%, 55% {{ color:var(--acc-ink); }} }}
.arrive {{ animation:arrive 1.9s ease; }}
.fly-dot {{ position:fixed; z-index:2000; width:12px; height:12px; border-radius:50%; background:var(--acc-ink); pointer-events:none; }}
@keyframes tick {{ 0% {{ transform:scale(.8); }} 55% {{ transform:scale(1.12); }} 100% {{ transform:none; }} }}
.later {{ display:none; align-items:center; font:inherit; font-family:var(--sans); font-size:13.5px; font-weight:700; line-height:1.5;
          padding:7px 14px; color:var(--ink); background:transparent; border:1px solid var(--ink); border-radius:0; cursor:pointer;
          white-space:nowrap; transition:background-color .2s, color .2s, border-color .2s; }}
.js .later {{ display:inline-flex; }}
.later .lt {{ display:inline-block; }}
.later.on {{ background:var(--acc-ink); border-color:var(--acc-ink); color:#fff; }}
@media (hover:hover) {{   /* not on touch screens, where a tapped button would keep the hover colour */
  .later:hover {{ background:var(--ink); color:#fff; }}
  .later.on:hover {{ background:var(--ink); border-color:var(--ink); }}
}}
.later.sm {{ margin-top:14px; padding:4px 11px; font-size:12.5px; }}
.art-top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:10px 24px; margin:0; }}
.art-top .later {{ flex:none; margin-top:6px; }}

/* later.html */
.later-page {{ padding-top:40px; padding-bottom:var(--sec); }}
.lp-msg {{ margin:18px 0 0; font-family:var(--mono); font-size:13px; color:var(--acc-ink); }}
.lp-list {{ list-style:none; margin:22px 0 0; padding:0; }}
.lp-row {{ display:grid; grid-template-columns:220px minmax(0,1fr) auto; gap:0 30px; align-items:center; padding:18px 0;
           border-top:1px solid var(--hair); }}
.lp-row:last-child {{ border-bottom:1px solid var(--hair); }}
.lp-fig {{ display:block; overflow:hidden; }}
.lp-fig img {{ display:block; width:100%; height:auto; transition:transform .5s cubic-bezier(.2,.8,.2,1); }}
.lp-fig:hover img {{ transform:scale(1.05); }}
.lp-k {{ margin:0 0 3px; font-family:var(--mono); font-size:11.5px; letter-spacing:.04em; color:var(--mute); line-height:1.6; }}
.lp-h {{ display:block; font-family:var(--sans); font-size:19px; font-weight:700; line-height:1.55; color:var(--ink); text-wrap:balance; }}
.lp-s {{ margin:5px 0 0; font-family:var(--mono); font-size:12px; line-height:1.6; color:var(--ink2); }}
.lp-empty {{ margin:24px 0 0; font-size:15.5px; color:var(--desc); }}
.lp-empty[hidden], .lp-tools[hidden], .lp-msg[hidden] {{ display:none; }}
.lp-tools {{ display:flex; flex-wrap:wrap; gap:12px; margin-top:28px; }}
.lp-tools button {{ font:inherit; font-family:var(--sans); font-size:14px; font-weight:700; padding:10px 18px; color:var(--ink);
                    background:transparent; border:1px solid var(--ink); border-radius:0; cursor:pointer;
                    transition:background-color .2s, color .2s, border-color .2s; }}
.lp-tools button:first-child {{ background:var(--ink); color:#fff; }}
.lp-tools button:hover {{ background:var(--acc-ink); border-color:var(--acc-ink); color:#fff; }}
.lp-note {{ margin:18px 0 0; max-width:780px; font-size:13.5px; line-height:1.85; color:var(--desc); }}
.pg-filter {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:8px 16px; margin:14px 0 6px; }}
.pg-filter[hidden] {{ display:none; }}
.pg-filter span {{ padding:4px 12px; background:var(--ink); color:#fff; font-family:var(--sans); font-size:14px; font-weight:700; }}
.pg-filter a {{ font-family:var(--mono); font-size:12.5px; }}

@media (min-width:1500px) {{
  body {{ font-size:17px; }}
}}
@media (max-width:1560px) {{
  :root {{ --col:200px; --side:290px; }}
  .shell {{ gap:0 40px; }}
  .side {{ padding-left:26px; }}
  .margin .no {{ font-size:64px; }}
}}
@media (min-width:861px) and (max-width:1000px) {{
  .gnav .gnav-in {{ gap:14px; }}
  .gnav .gnav-in .hs {{ min-width:90px; }}
  .gnav .gnav-links {{ gap:14px; }}
  .gn-a {{ font-size:11px; letter-spacing:.03em; }}
}}
@media (max-width:1120px) {{
  .gnav-in {{ gap:20px; }}
  .gnav-links {{ gap:18px; }}
  .gn-a {{ font-size:11.5px; letter-spacing:.06em; }}
  .gnav .rl-t {{ position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }}   /* the count alone, its name read out */
  .gnav .hs {{ flex:1 1 220px; min-width:120px; }}
  /* an issue's first screen in one column: how much, then what */
  .ih-grid {{ grid-template-columns:minmax(0,1fr); gap:30px; }}
  .ih-parts {{ flex-direction:row; flex-wrap:wrap; gap:4px 22px; max-width:none; }}
  .ih-parts b {{ padding-left:8px; }}
  .cards.md {{ grid-template-columns:repeat(2, minmax(0,1fr)); }}
  .cards.sm {{ grid-template-columns:repeat(3, minmax(0,1fr)); }}
  .bt-grid.lg {{ grid-template-columns:repeat(2, minmax(0,1fr)); }}
  .bt-grid.sm {{ grid-template-columns:repeat(3, minmax(0,1fr)); }}
  .pickup {{ grid-template-columns:minmax(0,1fr); gap:24px; }}
  .iss {{ grid-template-columns:170px 220px minmax(0,1fr); gap:0 24px; }}
  .lead-grid {{ grid-template-columns:minmax(0,1fr); min-height:0; gap:26px; padding-top:30px; }}
  .lead-art {{ margin-right:0; }}
  .shell {{ grid-template-columns:minmax(0,1fr); }}
  .wrap.sticky-side {{ padding-right:var(--pad); }}
  .side {{ border-left:0; padding-left:0; border-top:4px solid var(--ink); margin-top:20px; }}
  .sticky-side .side-in {{ position:static; max-height:none; overflow:visible; }}
  .side-only-issue {{ display:none; }}
  .side:not(:has(.sd:not(.side-only-issue))) {{ display:none; }}   /* nothing left in it on a narrow screen */
}}
@media (max-width:860px) {{
  :root {{ --pad:22px; --sec:76px; }}
  /* a phone: the header is one slim bar of the site's ink, after the page the owner sent (2026-10-24), made FoldFeed's:
     the logo and FoldFeed in white on the left; on the right 探す (the mark and the word, no box) and MENU as a 朱
     square. No double rule under it: the bar is the edge */
  :root {{ --head:52px; }}
  .mast {{ padding-top:0; background:var(--ink); }}
  .rules {{ display:none; }}
  .mast-row {{ position:relative; flex-wrap:nowrap; align-items:center; gap:12px; height:var(--head); }}
  .vol {{ position:static; flex:none; gap:6px; margin-right:-10px; }}
  /* back to the top: an ink square like MENU (which is 朱), at the bottom right under MENU's edge, once the first
     screen is past */
  .totop {{ display:flex; align-items:center; justify-content:center; position:fixed; z-index:900; width:40px; height:40px;
            right:calc(var(--pad) - 10px); bottom:calc(16px + env(safe-area-inset-bottom)); border-radius:3px;
            background:var(--ink); color:#fff; opacity:0; transform:translateY(10px); pointer-events:none;
            transition:opacity .3s ease, transform .3s cubic-bezier(.2,.8,.2,1), background-color .2s; }}
  .totop.on {{ opacity:.92; transform:none; pointer-events:auto; }}
  .totop:hover, .totop:active {{ background:var(--acc-ink); color:#fff; }}
  .totop:active {{ transform:scale(.94); }}   /* MENU a little nearer the edge (the owner, 2026-10-26) */
  .menu .mb {{ width:40px; height:40px; gap:4px; border-radius:3px; background:var(--acc); }}
  .menu summary:hover .mb, .menu summary:focus-visible .mb {{ background:var(--acc-ink); }}
  .menu .mb i {{ width:17px; }}
  .menu.on .mb i:nth-child(1), html:not(.js) .menu[open] .mb i:nth-child(1) {{ transform:translateY(6px) rotate(45deg); }}
  .menu.on .mb i:nth-child(3), html:not(.js) .menu[open] .mb i:nth-child(3) {{ transform:translateY(-6px) rotate(-45deg); }}
  .menu .mt {{ position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }}
  .word {{ gap:9px; color:#fff; }}
  .word img {{ width:28px; height:28px; border-radius:24%; outline:1.5px solid rgba(255,255,255,.85); outline-offset:1px; }}
  .word .name {{ font-size:22px; color:#fff; }}
  .word .nm {{ display:flex; flex-direction:column; }}
  .word .name {{ line-height:1.05; }}
  .upd {{ display:block; margin-top:3px; font-family:var(--mono); font-size:9px; line-height:1; letter-spacing:.12em;
          text-transform:uppercase; color:rgba(255,255,255,.62); }}
  .upd b {{ margin-left:3px; font-weight:500; letter-spacing:.06em; color:#fff; }}
  .tag {{ display:none; }}
  /* a phone's first screen (the owner, 2026-10-24): the introduction top left in the serif of the first design, the
     issue's date top right, the structure large and past the right edge, the Picked up mark at the bottom left. The
     breakdown is in Concept on a phone; the way to About is not needed (Concept follows) */
  .lead {{ position:relative; overflow:clip; }}
  .lead-grid {{ position:relative; min-height:0; padding-top:46px; padding-bottom:4px; gap:0; align-content:start; }}
  .lead-text {{ display:contents; }}
  .lead-h, .lead-mark {{ position:relative; z-index:1; }}
  .lead-h {{ order:1; padding-left:10px; font-size:clamp(19px, 5.4vw, 24px); line-height:1.6; letter-spacing:.01em; }}   /* the owner's B, 2026-10-26 */
  .lead-h span {{ white-space:normal; word-break:keep-all; }}
  .lead-h .sp {{ display:inline; }}
  .lead-art {{ order:2; position:relative; z-index:0; margin:-22px calc(-1 * var(--pad) - 36vw) 0 calc(-1 * var(--pad) + 2vw);
               pointer-events:none; }}
  .lead-art .lead-cap, .lead-art .lead-upd, .lead-text .lead-upd {{ display:none; }}
  /* the structure a little paler, so the words over and beside it read clearly (the owner, 2026-10-25); a soft halo of
     the ground keeps them off its lines */
  .lead-art .v3d-view, .lead-art .lead-pic {{ opacity:.55; }}
  .lead-h, .lead-mark {{ text-shadow:0 0 3px var(--ground), 0 0 10px var(--ground), 0 0 18px var(--ground); }}
  .lead-mark {{ order:3; display:block; justify-self:start; max-width:100%; margin-top:-64px; padding-left:10px; color:var(--ink); text-decoration:none; }}
  .lead-mark:hover {{ text-decoration:none; }}
  .lead-mark .k {{ display:block; font-family:var(--mono); font-size:11px; font-weight:700; letter-spacing:.16em; text-transform:uppercase; color:var(--acc-ink); }}
  .lead-mark b {{ display:block; margin-top:5px; font-family:var(--latin); font-size:clamp(24px, 7.4vw, 30px); line-height:1.05;
                  letter-spacing:-.01em; white-space:nowrap; }}
  .lead-mark .t {{ display:block; margin-top:8px; font-size:13px; line-height:1.5; color:var(--desc); }}
  .lead .lead-btns, .lead-parts, .lead-about {{ display:none; }}
  .ix-h, .part h2 {{ flex-wrap:wrap; row-gap:4px; }}
  .ixm, .ix-h .ixm {{ font-size:12px; }}
  .ixm b, .ix-h .ixm b {{ margin-left:10px; }}
  .lead-go {{ margin-top:22px; font-size:13.5px; }}
  .ix-all {{ margin-left:0; flex-basis:100%; justify-content:space-between; }}
  .lead-btns {{ margin-top:22px; display:grid; grid-template-columns:minmax(0,1fr); gap:10px; }}
  .lead-btns .lead-go {{ justify-content:flex-start; }}
  .lead-go b {{ margin-left:auto; }}
  .about-grid {{ grid-template-columns:minmax(0,1fr); gap:30px; }}
  .about-dl div {{ grid-template-columns:minmax(0,1fr); gap:8px; }}
  .about-dl dd {{ font-size:15px; }}
  .about-dl .bl {{ font-size:14.5px; }}
  .co-nav {{ display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:14px 20px; width:100%; padding-top:0; }}
  .co-bot p {{ flex-basis:100%; }}
  .doc-dl div {{ grid-template-columns:minmax(0,1fr); gap:2px; }}
  /* the way from Concept to the pick on the same page (the owner, 2026-10-26) */
  .concept-go {{ display:block; margin:40px 0 0; }}
  .concept-go .ix-all {{ display:flex; justify-content:space-between; }}
  .bl.bl-parts {{ grid-template-columns:minmax(0,1fr); gap:0; }}
  .bl-parts li + li b {{ margin-top:.9em; }}
  .bl-parts .s {{ padding-left:18px; }}   /* under the name, past its colour */
  .lead-art img {{ max-height:none; }}
  /* a phone: the figures run the screen's full width (the owner's memo, 2026-10-22: wide margins beside a picture
     make a phone's screen feel cramped); a card's number moves in with the text */
  .cards.md .cfig {{ margin-inline:calc(-1 * var(--pad)); }}
  .art .v3d-view, .art .cmpfig, .pickup .cfig {{ margin-inline:calc(6px - var(--pad)); }}   /* labels keep off the very edge */
  .pickup .cno, .cards.md .cno {{ left:var(--pad); }}
  .lead-art .v3d-view {{ max-height:none; }}
  .today {{ padding-top:var(--sec); }}
  .ix-h .label, .part h2 {{ font-size:23px; }}
  .today-4 div {{ grid-template-columns:minmax(0,1fr); gap:0; padding:6px 0; }}
  .today-4 dt {{ padding-top:0; font-size:13.5px; }}
  .today-4 dd {{ font-size:15px; }}
  .reads a {{ min-width:0; flex:1 1 100%; }}
  /* a phone: the header stays at the top and goes while scrolling down; the navigation's places are in the MENU, and
     its search opens from the header's button as a sheet under the header. Read Later is in the MENU */
  html {{ scroll-padding-top:16px; }}
  body {{ --headnow:var(--head); }}
  body.head-away {{ --headnow:0px; }}
  .mast {{ position:sticky; top:0; z-index:1000; }}
  /* past the top only MENU stays (the owner, 2026-10-23): the rest of the header fades out and lets the page through;
     MENU brings it back */
  .mast .brand, .mast .hs-open {{ transition:opacity .25s ease, transform .3s cubic-bezier(.2,.8,.2,1); }}
  .mast .rules {{ transition:opacity .25s ease; }}
  .mast.away {{ background:transparent; pointer-events:none; }}
  .mast.away .brand, .mast.away .hs-open {{ opacity:0; transform:translateY(-10px); }}
  .mast.away .rules {{ opacity:0; }}
  .mast.away .menu {{ pointer-events:auto; }}
  /* MENU pulls the header down: a panel the width of the screen comes down from under the header's row */
  .menu {{ position:static; }}
  .menu-list {{ position:fixed; left:0; right:0; top:var(--head); z-index:-1; min-width:0; max-height:calc(100vh - var(--head));
               overflow-y:auto; box-sizing:border-box; padding:4px var(--pad) 22px; border:0; border-bottom:1px solid var(--ink); text-align:left; }}
  .menu-list a {{ padding:13px 0; font-size:16px; letter-spacing:.04em; border-top:1px solid var(--hair); }}
  .menu-list a:first-child {{ border-top:0; }}
  .menu-list a::before {{ display:none; }}
  .menu-list a:hover, .menu-list a:focus-visible {{ color:var(--acc-ink); }}
  .menu-list a:hover small, .menu-list a:focus-visible small {{ color:var(--mute); }}
  .menu-list small {{ font-size:12px; margin-top:3px; }}
  .js .menu-list {{ opacity:1; transform:none; clip-path:inset(0 0 100% 0); transition:clip-path .55s cubic-bezier(.65,0,.25,1); }}
  .js .menu.on .menu-list {{ clip-path:inset(0 0 0 0); }}
  .menu-dim {{ display:block; position:fixed; left:0; right:0; top:var(--head); bottom:0; z-index:-2; background:rgba(27,38,56,.38);
               opacity:0; pointer-events:none; transition:opacity .45s ease; }}
  .menu.on .menu-dim {{ opacity:1; pointer-events:auto; }}
  .menu:not([open]) .menu-list {{ display:none; }}   /* a closed <details> would hold the panel in its own small box */
  .js .menu-list a {{ transform:translateY(-14px); transition:opacity .35s ease, transform .5s cubic-bezier(.2,.8,.2,1), color .2s; }}
  .js .menu.on .menu-list a:nth-child(1) {{ transition-delay:.12s; }}
  .js .menu.on .menu-list a:nth-child(2) {{ transition-delay:.16s; }}
  .js .menu.on .menu-list a:nth-child(3) {{ transition-delay:.2s; }}
  .js .menu.on .menu-list a:nth-child(4) {{ transition-delay:.24s; }}
  .js .menu.on .menu-list a:nth-child(5) {{ transition-delay:.28s; }}
  .js .menu.on .menu-list a:nth-child(6) {{ transition-delay:.32s; }}
  .js .menu.on .menu-list a:nth-child(7) {{ transition-delay:.36s; }}
  /* a tap answers: buttons and the menu's places sink a little under the finger */
  .reads a, .ix-all, .hs-open, .later, .mi-th a {{ transition-property:transform, color, background-color, opacity; }}
  .reads a:active, .ix-all:active, .hs-open:active, .later:active, .mi-th a:active {{ transform:scale(.97); }}
  .js .gnav {{ display:none; }}
  .gnav {{ position:static; }}
  .gnav-in {{ flex-wrap:wrap; gap:10px; }}
  .gnav .hs {{ flex:1 1 100%; max-width:none; }}
  .gnav .hs-q {{ padding:11px 14px 11px 38px; font-size:16px; }}
  .js .mast.hs-on + .gnav {{ display:block; position:fixed; left:0; right:0; top:var(--headnow); z-index:999;
                             max-height:calc(100vh - var(--headnow)); overflow-y:auto; border-bottom:1px solid var(--ink); }}
  .js .mast.hs-on + .gnav .gnav-links {{ display:none; }}
  .js .hs-open {{ display:inline-flex; }}
  .hs-open {{ align-items:center; height:40px; padding:0 10px; font-family:var(--sans); font-size:13px; font-weight:500;
              letter-spacing:.06em; color:#fff; border:0; border-radius:3px; }}   /* the mark and the word, white on the bar */
  .hs-open .ic {{ margin-right:6px; }}
  .hs-open[aria-expanded="true"] {{ background:rgba(255,255,255,.14); color:#fff; }}
  .hs-pop {{ position:static; width:auto; padding:16px; }}   /* opens right under the box */
  .cards.md {{ grid-template-columns:minmax(0,1fr); gap:60px; }}
  .cards.sm {{ grid-template-columns:repeat(2, minmax(0,1fr)); gap:48px 16px; }}
  .bt-grid.lg {{ grid-template-columns:minmax(0,1fr); gap:44px; }}
  .bt-grid.sm {{ grid-template-columns:repeat(2, minmax(0,1fr)); gap:36px 16px; }}
  .bt-n {{ font-size:14.5px; }}
  .card h3 {{ font-size:15px; }}
  .card.md h3 {{ font-size:18px; }}
  .card.sm .cs {{ font-size:12.5px; line-height:1.7; }}
  /* a small card on a phone: Read Later over the target */
  .card.sm .ctg {{ font-size:14px; }}
  .card.sm .cact .later.sm {{ padding:3px 9px; font-size:11.5px; }}
  .card.sm .cno {{ font-size:22px; }}
  .card.md .ctg {{ font-size:15.5px; }}
  .toc-cards .thumb img, .toc-cards .thumb.none {{ max-height:210px; object-fit:contain; }}
  .pk-h {{ font-size:24px; }}
  .pickup .pk-tg {{ font-size:19px; }}
  :root {{ --fig-shadow:none; --fig-shadow-up:none; }}   /* no shadows on a phone (the owner's choice, 2026-10-20) */
  .pk-s {{ font-size:15.5px; }}
  /* an issue in the list: the structures lead (the owner, 2026-10-26). One quiet line over them, the date (still the
     largest text) on the left, the number and weekday small and grey on the right; the structures larger, in a row
     that runs off the right edge and scrolls sideways */
  .iss {{ grid-template-columns:minmax(0,1fr); gap:12px; padding:20px 0 22px; }}
  .iss-d {{ display:flex; align-items:baseline; justify-content:space-between; gap:12px; }}
  .iss-d b {{ order:-1; margin:0; font-size:20px; }}
  .iss-d .k {{ font-size:11px; letter-spacing:.04em; color:var(--mute); }}
  .iss-th {{ display:flex; gap:14px; overflow-x:auto; overscroll-behavior-x:contain; scroll-snap-type:x mandatory;
             margin-right:calc(-1 * var(--pad)); padding-right:var(--pad); scrollbar-width:none; }}
  .iss-th::-webkit-scrollbar {{ display:none; }}
  .iss-th .it {{ flex:0 0 37%; scroll-snap-align:start; }}
  .iss-th .more {{ flex:none; align-self:stretch; padding:0 6px var(--th-tg); }}
  .iss-n {{ display:none; }}   /* the parts' counts are on the issue page (the owner, 2026-10-26) */
  .mi-th, .mi-th.more {{ grid-template-columns:repeat(3, minmax(0,1fr)); gap:22px 14px; }}
  .concept-grid {{ grid-template-columns:minmax(0,1fr); gap:36px; }}
  .concept-t {{ font-size:16px; }}
  .flow ol {{ grid-template-columns:minmax(0,1fr); gap:34px; }}
  .flow li + li::before {{ content:"↓"; position:static; display:block; margin:0 0 10px; }}
  .flow li > b {{ font-size:44px; }}
  .mi-no {{ font-size:17px; }}
  .mi-more {{ grid-column:1 / -1; margin-top:-8px; }}   /* "+5" on a line of its own under the figures */
  .mi-more a {{ padding-bottom:0; }}
  .th-tg {{ -webkit-line-clamp:3; height:4.05em; font-size:12px; }}
  .iss-th {{ --th-tg:calc(6px + 2.7 * 12px); }}
  .iss-th .th-tg {{ -webkit-line-clamp:2; height:2.7em; font-size:12px; }}
  .lp-row {{ grid-template-columns:110px minmax(0,1fr); gap:6px 16px; align-items:start; }}
  .lp-row .later {{ grid-column:2; justify-self:start; }}
  .lp-h {{ font-size:16px; }}
  /* an article on a phone, in one column in the owner's order (2026-10-26): the number with Read Later beside it, the
     headline and its supplement, the facts (Target ... Organism), the paper (title, authors, journal, date, licence),
     then the rest. The margin and text columns give up their boxes so their parts can be put in that order */
  .art {{ grid-template-columns:minmax(0,1fr) auto; gap:0; align-items:start; }}
  .art .margin, .art .margin .sticky, .art .text, .art .art-top {{ display:contents; }}
  .art .text > *, .art .art-top > * {{ grid-column:1 / -1; order:10; }}
  .art .margin .no {{ grid-column:1; order:1; align-self:center; margin-bottom:0; }}
  .art .art-top .later {{ grid-column:2; order:2; align-self:center; margin:0; }}
  .art .art-h {{ order:3; margin-top:18px; }}
  .art .subhead {{ order:4; }}
  .art .margin dl {{ grid-column:1 / -1; order:5; margin-top:22px; }}
  .art .pmeta {{ order:6; margin-top:20px; }}
  .ih-date {{ font-size:56px; }}
  .ih-big .kv {{ font-size:48px; }}
  .ihc-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px 16px; }}
  .ih-left .ih-nav {{ display:none; }}   /* the same buttons are at the foot of the page */
  .ih-big {{ margin-top:22px; padding-top:18px; }}
  .ihc-head {{ flex-wrap:wrap; gap:10px 14px; }}
  .ihc-t {{ font-size:19px; }}
  .ihc-toc {{ padding:6px 10px; font-size:12px; }}
  .ihc-ctl {{ order:3; flex-basis:100%; }}
  .ihc-grid .ctg {{ font-size:14px; }}
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
  .rel li {{ grid-template-columns:minmax(0,1fr); }}
  .margin .no {{ font-size:52px; }}
  .margin dl {{ display:grid; grid-template-columns:78px minmax(0,1fr); gap:3px 12px; }}
  .margin dt {{ margin-top:0; padding-top:2px; }}
  .margin .pdb {{ display:inline; margin-right:12px; }}
  .margin .first {{ grid-column:2; margin-top:4px; }}
  .margin .sticky {{ position:static; }}
  .art .art-h {{ font-size:23px; }}
  .art .subhead {{ font-size:15.5px; }}
  .ptitle {{ font-size:15px; }}
  .pmeta p {{ display:block; }}
  .pmeta p + p {{ margin-top:10px; }}
  .pmeta .pl {{ display:block; padding:0; margin-bottom:1px; }}
  .ppl {{ font-size:14px; }}
  .ppl .pa {{ font-size:12.5px; }}
  .site dl {{ grid-template-columns:minmax(0,1fr); }}
  .lab div {{ grid-template-columns:92px 1fr; }}
  .legend {{ grid-template-columns:minmax(0,1fr); }}
  /* phone: the figures have a portrait version of their own (larger text, parts stacked) that fits the screen */
  .hasm .fig.d {{ display:none !important; }}
  .v3d.hasm:not(.show-cmp) .fig.m:not(.cmp), .cmpfig.hasm .fig.m, .v3d.hasm.show-cmp .fig.cmp.m {{ display:block; }}
  .v3d.on .v3d-view, .v3d.loading .v3d-view {{ aspect-ratio:1 / 1.15; }}
}}
@media (max-width:360px) {{   /* the wordmark, 探す and MENU still on one line */
  .word .name {{ font-size:22px; }}
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


SITE_URL = (read_json(ROOT / "config" / "editorial.json").get("site_url") or "").rstrip("/") + "/"
OG = {"default": None}   # the link preview of pages that have none of their own: the newest issue's (set by build)


def og_image(date: str | None, root: Path = ISSUES_DIR, base: str = "figures") -> str | None:
    """The address of an issue's link-preview image: its lead article's card (pipeline.cards), when there is one.
    A preview's issue is read from its own folder (`root`) and its figures are served under preview/ (`base`)."""
    pm = date and cards.lead_of(date, root)
    return f"{SITE_URL}{base}/{date}/{pm}-card.png" if pm and cards.card_path(date, pm, root=root).exists() else None


def page(title: str, body: str, depth: int, description: str = DESCRIPTION, noindex: bool = False,
         viewer: bool = False, script: str = "", image: str | None = None) -> str:
    up = "../" * depth
    image = image or OG["default"]
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<script>document.documentElement.className += " js";</script>'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{esc(title)}</title>'
            + ('<meta name="robots" content="noindex">' if noindex else "") +
            f'<meta name="description" content="{esc(description)}">'
            f'<meta property="og:title" content="{esc(title)}">'
            f'<meta property="og:description" content="{esc(description)}">'
            f'<meta property="og:type" content="website">'
            + (f'<meta property="og:image" content="{esc(image)}"><meta property="og:image:width" content="1200">'
               f'<meta property="og:image:height" content="630"><meta name="twitter:card" content="summary_large_image">'
               if image else '<meta name="twitter:card" content="summary">') +
            f'<link rel="icon" href="{up}assets/logo-mark.svg">'
            f'<link rel="preconnect" href="https://fonts.googleapis.com">'
            f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+JP:wght@400;700&family=Noto+Sans+JP:wght@400;500;700&display=swap">'
            f'<style>{CSS}</style></head><body><div class="page">{new_tab(body)}</div>'
            f'<a class="totop" href="#" aria-label="ページの先頭へ"><svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">'
            f'<path d="M10 16V4M4.5 9.5 10 4l5.5 5.5" fill="none" stroke="currentColor" stroke-width="2"/></svg></a>'
            + (f'<script src="{up}assets/viewer.js" defer></script>' if viewer else "")
            + f'<script>{MENU_SCRIPT}{SEARCH_SCRIPT}{LATER_SCRIPT}{script}</script></body></html>')


def menu_html(up: str) -> str:
    """MENU, on a phone (where the global navigation does not fit): a round button that opens the same places, the top
    and Read Later (a <details>, so it works without JavaScript)."""
    items = (("Top", f"{up}index.html", ""), *((t, h, NAV_NOTE[k][1]) for (t, h, _), (k, _) in zip(nav_links(up), NAV)),
             ("Read Later", f"{up}later.html", "保存した記事"))
    links = "".join(f'<a href="{h}">{esc(t)}{"<b class=rl-n></b>" if h.endswith("later.html") else ""}'
                    + (f'<small>{esc(note)}</small>' if note else "") + '</a>' for t, h, note in items)
    return (f'<details class="menu"><summary><span class="mt">MENU</span>'
            f'<span class="mb" aria-hidden="true"><i></i><i></i><i></i></span><span class="menu-dim" aria-hidden="true"></span></summary>'
            f'<nav class="menu-list" aria-label="サイト内のページ">{links}</nav></details>')


MENU_SCRIPT = r"""
(function () {
  /* The header: away while scrolling down, back on scrolling up, always there on the first screen. In-page links land
     at the top when the page goes down (the header slides away) and just under the header when it goes up. On a phone
     the header is away whenever the page is past its top, but for MENU, which stays (the owner, 2026-10-23). */
  var head = document.querySelector('.mast');
  if (!head) return;
  var phone = window.matchMedia ? window.matchMedia('(max-width: 860px)') : { matches: false };
  var last = window.scrollY, lead = document.querySelector('.lead');
  var jumped = false;   // after an in-page link down, the first-screen rule no longer holds the header (until back at the top)
  function firstScreen() {
    return (lead ? lead.getBoundingClientRect().bottom + window.scrollY : window.innerHeight) - head.offsetHeight;
  }
  function away(on) {
    if (on && document.querySelector('details.menu[open], .hs.on')) return;
    head.classList.toggle('away', on);
    document.body.classList.toggle('head-away', on);
  }
  var totop = document.querySelector('.totop');   // a phone: back to the top, once the first screen is past
  if (totop) totop.addEventListener('click', function (e) {
    e.preventDefault();
    var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: 0, behavior: calm ? 'auto' : 'smooth' });
  });
  window.addEventListener('scroll', function () {
    var y = window.scrollY, d = y - last;
    if (totop) totop.classList.toggle('on', y > window.innerHeight * 0.8);
    if (phone.matches) { away(y > head.offsetHeight); last = y; return; }
    if (y < head.offsetHeight && d < 0) jumped = false;   // back at the top by scrolling up (not the first step of a jump down)
    if (y < firstScreen() && !jumped) away(false);
    else if (d > 4) away(true);
    else if (d < -4) away(false);
    last = y;
  }, { passive: true });
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href*="#"]');   // "#x", or this page's own address with "#x"
    if (!a || a.pathname !== location.pathname || a.host !== location.host) return;
    var id = a.hash.slice(1);
    var t = id && document.getElementById(id);
    if (!t) return;
    e.preventDefault();
    var y = 0;   // where it is laid out (offsetTop): a block still rising into place (.rv) is drawn lower than that
    for (var el = t; el; el = el.offsetParent) y += el.offsetTop;
    var down = y > window.scrollY;
    var nav = document.querySelector('.gnav'), pinned = nav && getComputedStyle(nav).position === 'sticky' ? nav.offsetHeight : 0;
    var top = pinned ? y - pinned - 16 : down || phone.matches ? y - 16 : y - head.offsetHeight - 16;   // a wide screen: under the navigation
    if (down) { jumped = true; away(true); }
    var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: Math.max(0, top), behavior: calm ? 'auto' : 'smooth' });
    if (history.replaceState) history.replaceState(null, '', '#' + id);
    whenStill(function () { arrive(t); });
  });
  function whenStill(f) {   // once the page has stopped scrolling
    var y = -1, same = 0;
    (function tick() {
      if (window.scrollY === y) same++; else { same = 0; y = window.scrollY; }
      if (same > 5) f(); else requestAnimationFrame(tick);
    })();
  }
  function arrive(t) {   // light what was arrived at for a moment: an article's number, else the heading
    var el = t.matches('.art') ? t.querySelector('.margin .no') : t.querySelector('h1, h2, .label') || t;
    if (!el) return;
    el.classList.remove('arrive'); void el.offsetWidth; el.classList.add('arrive');
    setTimeout(function () { el.classList.remove('arrive'); }, 2000);
  }
  window.addEventListener('load', function () {   // from another page with "#x": under the navigation, then lit
    var t = location.hash && document.getElementById(location.hash.slice(1));
    if (!t) return;
    var y = 0, nav = document.querySelector('.gnav'), pinned = nav && getComputedStyle(nav).position === 'sticky' ? nav.offsetHeight : 0;
    for (var el = t; el; el = el.offsetParent) y += el.offsetTop;
    window.scrollTo({ top: Math.max(0, y - pinned - 16), behavior: 'instant' });
    if (y > head.offsetHeight) { jumped = true; away(true); }
    whenStill(function () { arrive(t); });
  });
})();
(function () {
  /* MENU: opens and closes with an animation (.on), so <details> is driven by hand; without JavaScript it is a
     plain <details>. Closes on a click elsewhere, on a link in it, and on Escape. */
  function toggle(d, on) {
    clearTimeout(d._t);
    var head = d.closest('.mast');
    if (on) {
      if (head) { head.classList.remove('away'); document.body.classList.remove('head-away'); }   // MENU brings the header back
      d.open = true;
      requestAnimationFrame(function () { requestAnimationFrame(function () { d.classList.add('on'); }); });
    } else {
      d.classList.remove('on');
      d._t = setTimeout(function () {
        if (d.classList.contains('on')) return;
        d.open = false;
        window.dispatchEvent(new Event('scroll'));   // past the top again: only MENU
      }, 560);
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
  var SEL = '.ix-h, .about-dl > div, .about-side, .about .jl, .about > .wrap > .about-sub, .pickup, .concept-t, .flow li, '
          + '.today-4 > div, .reads, .cards > li, .mi-th > li, .bt, .iss-list > li, .find, .mail-grid, .shell:not(.sticky-side) .side .sd, '
          + '.ihead, .part, .art, .rest, .queue li';
  var els = [].slice.call(document.querySelectorAll(SEL));
  var seen = new Map();
  els.forEach(function (el) {                     // a short stagger among siblings that come in together
    var k = seen.get(el.parentNode) || 0;
    seen.set(el.parentNode, k + 1);
    el.style.setProperty('--d', Math.min(k, 6) * 70 + 'ms');
    el.classList.add('rv');
  });
  function countUp(el) {
    // an issue's first screen: after its block has risen into place, and slower, so the count is seen
    var issue = !!el.closest('.ihead'), to = +el.getAttribute('data-count'), t0 = null, dur = issue ? 1700 : 1200;
    function f(t) {
      t0 = t0 || t;
      var p = Math.min(1, (t - t0) / dur), e = issue ? (p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2) : 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(to * e).toLocaleString('en-US');
      if (p < 1) requestAnimationFrame(f);
    }
    setTimeout(function () { requestAnimationFrame(f); }, issue ? 450 : 0);
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
  [].forEach.call(document.querySelectorAll('.ppi'), function (fig) {
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


# Search words for the suggestions beyond their Japanese labels (so "kinase" or "de novo" finds them too)
CATEGORY_EN = {"kinase": "kinase", "protease": "protease", "receptor": "receptor gpcr", "channel": "ion channel",
               "transporter": "transporter", "enzyme": "enzyme", "nucleic": "dna rna nucleic", "chromatin": "chromatin histone nucleosome",
               "translation": "ribosome translation folding chaperone", "regulation": "transcription signaling",
               "immune": "immune antibody mhc tcr", "pathogen": "virus pathogen infection", "photosynthesis": "photosynthesis",
               "other": "other"}
TOPIC_EN = {"design": "de novo design designed protein", "drug": "drug discovery inhibitor", "antibody": "antibody nanobody",
            "ai": "ai alphafold structure prediction machine learning", "membrane": "membrane protein",
            "dynamics": "conformational change intermediate dynamics", "insitu": "in situ cryo-et tomography",
            "method": "method development"}
METHOD_FACETS = ("Cryo-EM", "X-ray", "NMR", "Cryo-ET")


MAGNIFIER = ('<svg class="ic" viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" focusable="false">'
             '<circle cx="6.8" cy="6.8" r="5" fill="none" stroke="currentColor" stroke-width="1.6"/>'
             '<path d="M10.5 10.5 14.5 14.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>')


def search_form(up: str) -> str:
    """The header search: one box; focused, it drops down the ways in (kind of protein, kind of research, method, part)
    with how many articles each has, so the first screen stays plain until it is wanted. Typing narrows the suggestions
    and lists matching articles (search.json). Without JavaScript it is a plain search sent to the search page."""
    def group(title, items):
        links = "".join(f'<a href="{up}search.html?{k}={esc(v)}" data-k="{esc(unicodedata.normalize("NFKC", key).lower())}">'
                        f'{esc(label)}<span>{n}</span></a>' for k, v, label, n, key in items)
        return f'<div class="hs-g"><p class="hs-h">{title}</p><div class="hs-c">{links}</div></div>' if items else ""
    f = FACETS
    groups = (group("Protein Type", [("cat", k, CATEGORIES[k], n, f"{CATEGORIES[k]} {CATEGORY_EN.get(k, k)}") for k, n in f["cats"]])
              + group("Research Type", [("topic", k, TOPICS[k], n, f"{TOPICS[k]} {TOPIC_EN.get(k, k)}") for k, n in f["topics"]])
              + group("Method", [("method", m, m, n, m) for m, n in f["methods"]])
              + group("Part", [("part", k, PARTS[k][0], n, f"{PARTS[k][0]} {PARTS[k][1]}") for k, n in f["parts"]]))
    return (f'<form class="hs" action="{up}search.html" method="get" role="search" data-up="{up}">'
            f'<span class="hs-box">{MAGNIFIER}<input class="hs-q" type="search" name="q" autocomplete="off" '
            f'placeholder="タンパク質・PDB ID・研究の種類で探す" aria-label="記事を探す" aria-expanded="false" aria-controls="hs-pop"></span>'
            f'<div class="hs-pop" id="hs-pop" hidden><div class="hs-res" hidden></div>{groups}'
            f'<a class="hs-all" href="{up}search.html">すべての記事から探す<span aria-hidden="true">→</span></a></div></form>')


SEARCH_SCRIPT = r"""
(function () {
  /* The header search: the suggestions drop down on focus; typing narrows them and lists the articles that match
     (search.json, loaded on first use). Enter goes to the search page; Escape or a click elsewhere closes it. */
  var f = document.querySelector('.hs');
  if (!f) return;
  var q = f.querySelector('.hs-q'), pop = f.querySelector('.hs-pop'), res = f.querySelector('.hs-res'),
      all = f.querySelector('.hs-all'), up = f.getAttribute('data-up') || '', data = null, asked = false;
  var chips = [].slice.call(pop.querySelectorAll('.hs-c a')), groups = [].slice.call(pop.querySelectorAll('.hs-g'));
  var mast = document.querySelector('.mast'), btn = document.querySelector('.hs-open');
  function norm(s) { return s.normalize('NFKC').toLowerCase(); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function open(on) {
    if (!on === pop.hidden) return;
    pop.hidden = !on;
    q.setAttribute('aria-expanded', on ? 'true' : 'false');
    f.classList.toggle('on', on);
    if (on && !asked) {
      asked = true;
      fetch(up + 'search.json').then(function (r) { return r.json(); }).then(function (d) { data = d; draw(); }).catch(function () {});
    }
  }
  function draw() {
    var terms = norm(q.value.trim()).split(/\s+/).filter(Boolean);
    chips.forEach(function (c) {
      var k = c.getAttribute('data-k');
      c.hidden = terms.length > 0 && !terms.every(function (t) { return k.indexOf(t) >= 0; });
    });
    groups.forEach(function (g) { g.hidden = !g.querySelector('.hs-c a:not([hidden])'); });
    all.firstChild.textContent = terms.length ? '「' + q.value.trim() + '」ですべての記事を探す' : 'すべての記事から探す';
    all.href = up + 'search.html' + (terms.length ? '?q=' + encodeURIComponent(q.value.trim()) : '');
    if (!terms.length || !data) { res.hidden = true; return; }
    var hits = data.filter(function (a) { return terms.every(function (t) { return a.s.indexOf(t) >= 0; }); });
    res.innerHTML = '<p class="hs-h">Articles<span>' + hits.length + '</span></p>' + (hits.length ? hits.slice(0, 6).map(function (a) {
      return '<a href="' + up + 'issues/' + a.d + '.html#a' + a.n + '"><b>' + esc(a.h) + '</b><span>' + a.d.replace(/-/g, '.') +
             ' · ' + esc(a.t) + '</span></a>';
    }).join('') : '<p class="hs-none">見つかりませんでした</p>');
    res.hidden = false;
  }
  q.addEventListener('focus', function () { open(true); });
  q.addEventListener('input', function () { open(true); draw(); });
  document.addEventListener('click', function (e) {
    if (f.contains(e.target) || (btn && btn.contains(e.target))) return;
    open(false);
    if (mast) mast.classList.remove('hs-on');
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !pop.hidden) { open(false); q.blur(); if (mast) mast.classList.remove('hs-on'); }
  });
  if (btn) btn.addEventListener('click', function () {           // a phone: the box opens under the wordmark
    var on = !mast.classList.contains('hs-on');
    mast.classList.toggle('hs-on', on);
    btn.setAttribute('aria-expanded', on ? 'true' : 'false');
    if (on) {
      var sheet = f.closest('.gnav');
      if (sheet) sheet.style.top = Math.max(0, mast.getBoundingClientRect().bottom) + 'px';
      q.focus();
    } else open(false);
  });
})();
"""

LATER_SCRIPT = r"""
(function () {
  /* Read Later: the articles a reader saves, kept in this browser only (localStorage; nothing is sent anywhere).
     Buttons [data-id] toggle an article; "Read Later N" in the header and later.html show the list. */
  var KEY = 'foldfeed.later.v1';
  function load() { try { var v = JSON.parse(localStorage.getItem(KEY) || '[]'); return Array.isArray(v) ? v : []; } catch (e) { return []; } }
  function store(v) { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) {} }
  var ids = load();
  function paint() {
    [].forEach.call(document.querySelectorAll('.later[data-id]'), function (b) {
      var on = ids.indexOf(b.getAttribute('data-id')) >= 0;
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      b.querySelector('.lt').textContent = on ? '✓ 後で読む' : '＋ 後で読む';
    });
    [].forEach.call(document.querySelectorAll('.rl'), function (a) { a.hidden = !ids.length; });
    [].forEach.call(document.querySelectorAll('.rl-n'), function (n) { n.textContent = ids.length || ''; });
    document.dispatchEvent(new CustomEvent('ff-later'));
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.later[data-id]');
    if (!b) return;
    e.preventDefault();
    var id = b.getAttribute('data-id');
    ids = ids.indexOf(id) >= 0 ? ids.filter(function (x) { return x !== id; }) : [id].concat(ids);
    store(ids);
    paint();
    b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop');
    var saved = ids.indexOf(id) >= 0;
    function bump() {
      [].forEach.call(document.querySelectorAll('.rl, .menu summary'), function (a) { a.classList.remove('pop'); void a.offsetWidth; a.classList.add('pop'); });
    }
    if (saved) fly(b, bump); else bump();
  });
  function fly(from, done) {   // a small dot from the button to Read Later in the navigation (the MENU on a phone)
    var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var to = [].filter.call(document.querySelectorAll('.gnav .rl, .menu summary'), function (el) { return el.offsetParent; })[0];
    var r = to && to.getBoundingClientRect(), s = from.getBoundingClientRect();
    if (calm || !r || r.bottom < 0 || r.top > innerHeight || !document.body.animate) return done();
    var dot = document.createElement('span');
    dot.className = 'fly-dot';
    dot.style.left = (s.left + s.width / 2 - 6) + 'px';
    dot.style.top = (s.top + s.height / 2 - 6) + 'px';
    document.body.appendChild(dot);
    var dx = r.left + r.width - 14 - (s.left + s.width / 2), dy = r.top + r.height / 2 - (s.top + s.height / 2);
    dot.animate([{ transform: 'translate(0,0) scale(1)', opacity: 1 },
                 { transform: 'translate(' + dx * .5 + 'px,' + (dy * .5 - 60) + 'px) scale(1.15)', opacity: 1, offset: .5 },
                 { transform: 'translate(' + dx + 'px,' + dy + 'px) scale(.5)', opacity: .9 }],
                { duration: 620, easing: 'cubic-bezier(.4,0,.2,1)' }).onfinish = function () { dot.remove(); done(); };
  }
  window.addEventListener('storage', function (e) { if (e.key === KEY) { ids = load(); paint(); } });
  window.FFLater = { get: function () { return ids.slice(); }, set: function (v) { ids = v; store(ids); paint(); } };
  paint();
})();
"""


# the parts (New Structure, ...) are the first row of Search: eight places and the search do not fit in one row
NAV = (("latest", "Latest Issue"), ("back", "Back Issues"), ("browse", "Search"), ("news", "Newsletters & Alerts"),
       ("about", "About"))


def nav_links(up: str, current: str | None = None) -> list[tuple[str, str, bool]]:
    """(label, href, is the current page) of the global navigation, also the MENU on a phone."""
    latest = f'{up}issues/{LATEST["date"]}.html' if LATEST["date"] else f"{up}index.html"
    href = {"latest": latest, "browse": f"{up}search.html", "back": f"{up}issues.html", "news": f"{up}newsletter.html",
            "about": f"{up}about.html"}
    return [(label, href[k], k == current) for k, label in NAV]


# what each place of the navigation is, in Japanese (the labels are English): the first line of its dropdown, and a
# short form under it in the phone's MENU
NAV_NOTE = {
    "latest": ("最新号（{date} 号・{n} 本）の解説記事を読めます。", "最新号の解説記事"),
    "browse": ("これまでの記事を、キーワードや、部・タンパク質の種類・研究の種類・手法から探せます。", "キーワードや分類から記事を探す"),
    "back": ("これまでに出た号を、新しい順に一覧できます。", "これまでの号"),
    "news": ("新しい号のお知らせや、気になるタンパク質の新着をメールで受け取れます（準備中）。", "メールのお知らせ（準備中）"),
    "about": ("FoldFeed がどんなサイトか、記事の作り方や掲載のタイミングをご紹介します。", "このサイトについて"),
}


def nav_items(key: str, up: str) -> list[tuple[str, str, str]]:
    """(label, href, count) of a place's dropdown."""
    latest = f'{up}issues/{LATEST["date"]}.html' if LATEST["date"] else f"{up}index.html"
    if key == "latest":
        return [("Contents", f"{latest}#contents", str(NAV_INFO["articles"]))] + [
            (PICK_TITLE if k == "pick" else PARTS[k][0], f"{latest}#{k}", str(n)) for k, n in NAV_INFO["groups"]]
    if key == "browse":
        return ([("キーワードで探す", f"{up}search.html", "")] + [(PARTS[k][0], f"{up}{PART_PAGES[k]}", "") for k in PARTS]
                + [(t, f"{up}search.html#{a}", "") for a, t in BROWSE_GROUPS[1:]])
    if key == "back":
        return [(f"{y}.{m:02d}.{d:02d}", f"{up}issues/{date}.html", n_articles(n))
                for date, n in NAV_INFO["recent"] for y, m, d, _ in [date_parts(date)]] + [("すべての号一覧", f"{up}issues.html", "")]
    if key == "news":
        return [("毎朝の号のお知らせ", f"{up}newsletter.html", ""), ("週のまとめ", f"{up}newsletter.html", ""),
                ("タンパク質アラート", f"{up}newsletter.html", "")]
    return [("About FoldFeed", f"{up}about.html#about", ""), ("FoldFeed in Numbers", f"{up}about.html#numbers", ""),
            ("Journals", f"{up}about.html#journals", "")]


def nav_note(key: str) -> str:
    date = LATEST["date"] or ""
    return NAV_NOTE[key][0].format(date=date.replace("-", "."), n=NAV_INFO["articles"])


def global_nav(up: str, current: str | None = None, find: str = "") -> str:
    """The global navigation: one row of the site's main places under the header's double rule (small capitals, the
    current page in vermilion) with the search at its right end. Under the pointer or the keyboard a place drops down
    what it holds, its first line saying in Japanese what the place is (the labels are English; NAV_NOTE). While the page scrolls this row stays at the top of
    the window and the header above it goes. On a phone the places are in the MENU and the search opens from its
    button (the row is not shown)."""
    links = ""
    for (t, h, cur), (key, _) in zip(nav_links(up, current), NAV):
        items = "".join(f'<li><a href="{ih}">{esc(it)}' + (f'<span>{esc(c)}</span>' if c else "") + '</a></li>'
                        for it, ih, c in nav_items(key, up))
        links += (f'<div class="gn-item"><a class="gn-a" href="{h}"{" aria-current=page" if cur else ""}>{esc(t)}'
                  f'<span class="gn-v" aria-hidden="true"></span></a>'
                  f'<div class="gn-pop"><p class="gn-d">{esc(nav_note(key))}</p><ul>{items}</ul></div></div>')
    return (f'<nav class="gnav" aria-label="サイトの主な場所"><div class="wrap"><div class="gnav-in">'
            f'<div class="gnav-links">{links}</div>{find}'
            f'<a class="rl" href="{up}later.html" title="Read Later" hidden><span class="rl-t">Read Later</span><b class="rl-n"></b></a>'
            f'</div></div></nav>')


def masthead(depth: int, right: str = "", search: bool = True, current: str | None = None) -> str:
    """The wordmark row inside the page width, MENU (a phone only) on the right; the double rule under it spans the
    whole window; under the rule the global navigation with the search and Read Later (global_nav, search_form). The
    whole header stays at the top of the window while the page scrolls. On a phone the header is one slim ink bar (the
    owner, 2026-10-24): the logo and FoldFeed in white on the left; on the right 探す (opens the search under the bar)
    and MENU, a 朱 square. "Read Later N" shows once the reader has saved an article."""
    up = "../" * depth
    find = search_form(up) if search else ""
    open_btn = f'<button class="hs-open" type="button" aria-label="記事を探す">{MAGNIFIER}探す</button>' if search else ""
    return (f'<header class="mast"><div class="wrap"><div class="mast-row">'
            f'<div class="brand"><a class="word" href="{up}index.html"><img src="{up}assets/logo-mark.svg" alt="">'
            f'<span class="nm"><span class="name">{esc(brand.SITE_NAME)}</span>{update_html()}</span></a>'
            f'<p class="tag">{esc(TAGLINE)}</p></div>'
            f'<div class="vol">{right}{open_btn}{menu_html(up)}</div></div></div><div class="rules"></div></header>'
            f'{global_nav(up, current, find)}')


def update_html() -> str:
    """Update yy.mm.dd (the newest issue with articles) under FoldFeed in a phone's header bar (the owner, 2026-10-26:
    it was a badge on the front page's first screen)."""
    if not LATEST["date"]:
        return ""
    y, m, d, _ = date_parts(LATEST["date"])
    return f'<span class="upd">Update <b>{y % 100:02d}.{m:02d}.{d:02d}</b></span>'


def later_button(pmcid: str, small: bool = False) -> str:
    """Read Later: saves the article in this browser (LATER_SCRIPT, localStorage); shown only with JavaScript."""
    return (f'<button class="later{" sm" if small else ""}" type="button" data-id="{esc(pmcid)}" aria-pressed="false">'
            f'<span class="lt">＋ 後で読む</span></button>')


def shell(main: str, side: str, sticky: bool = False) -> str:
    """Main column and right sidebar. Only the sidebar of an issue page (its contents) stays in view while scrolling.
    With no sidebar (the front page) the main column takes the whole width."""
    if not side:
        return f'<div class="wrap shell solo"><main class="main">{main}</main></div>'
    return (f'<div class="wrap shell{" sticky-side" if sticky else ""}"><main class="main">{main}</main>'
            f'<aside class="side"><div class="side-in">{side}</div></aside></div>')


def colophon(extra: str = "", up: str = "") -> str:
    """The footer: the name and what the site is (as in the header), the places of the navigation and Read Later, then
    one quiet line: β版, the ways to 著作権と出典, the privacy policy and 特定商取引法に基づく表示 and, on a page made from
    PMC's data, its credit. How the articles are made
    is told in About FoldFeed."""
    links = "".join(f'<a href="{h}">{esc(t)}</a>' for t, h, _ in nav_links(up)) + f'<a href="{up}later.html">Read Later</a>'
    return (f'<footer class="colo"><div class="wrap colophon"><div class="co-top">'
            f'<div class="co-brand"><a class="co-word" href="{up}index.html">{esc(brand.SITE_NAME)}</a>'
            f'<p>{esc(TAGLINE)}</p></div><nav class="co-nav" aria-label="サイト内のページ">{links}</nav></div>'
            f'<div class="co-bot"><span class="co-beta">β版</span><a href="{up}about.html#rights">著作権と出典</a>'
            f'<a href="{up}privacy.html">プライバシーポリシー</a><a href="{up}tokushoho.html">特定商取引法に基づく表示</a>{extra}</div>'
            f'</div></footer>')


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


# ---------- one article ----------

def quotes(evidence: list) -> str:
    """The paper's own words behind one highlight, for reference: its shortest quote only, after the label 原文, with
    its section (a quote wraps under itself, never under the label). A reader who wants the paper's words reads the
    paper; every quote stays in the data and is still checked against the full text (pipeline.verify). The owner's
    choice, 2026-10-22, also for the rights: the article's own text is the main part, the quotes are few
    (docs/rights.md)."""
    if not evidence:
        return ""
    e = min(evidence, key=lambda e: len(e["quote"]))
    return (f'<div class="src"><b>原文</b><div class="qs"><p><i>“{esc(e["quote"])}”</i>'
            f'<span>{esc(e.get("section", ""))}</span></p></div></div>')


UNIPROT_SHOWN = 8   # UniProt entries named in an article's margin (a nucleosome alone has four histones)


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
    # the proteins the article is about first (their gene in the target or the headline: PARP2 before the four
    # histones of its nucleosome), then the rest; up to UNIPROT_SHOWN, the number of the others after them
    about = f'{pick.get("target_en") or ""} {pick["target_label"]} {pick["headline"]}'.lower()
    named = lambda p: bool(p.get("gene")) and re.search(rf"(?<![a-z0-9]){re.escape(p['gene'].lower())}(?![a-z0-9])", about)
    prots = sorted(prots, key=lambda p: not named(p))
    uni = ", ".join(f'<a href="https://www.uniprot.org/uniprotkb/{esc(p["uniprot"])}">{esc(p.get("gene") or p["uniprot"])}</a>'
                    for p in prots[:UNIPROT_SHOWN])
    if len(prots) > UNIPROT_SHOWN:
        uni += f" <span>ほか {len(prots) - UNIPROT_SHOWN} 件</span>"
    orgs = sorted({o for s in rel for q in s.get("polymers", []) for o in q["organisms"]})
    rows = [("Target", esc(pick.get("target_en") or pick["target_label"])), ("Method", esc(method)), (pdb_label, pdb_dd),
            ("Ligand", esc(pick.get("ligand_label") or ("—" if hot else render.ligand_line(cand))))]
    if hot:
        rows.insert(0, ("Study", esc(impact.TYPES.get(impact_type or "", "新しい構造以外"))))
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


def intro_html(pick: dict, open_line: bool = True, cls: str = "art-4") -> str:
    """The article's short introduction: four lines (question, approach, result, learned) and, when the paper states
    one and open_line is set, a fifth (what is still open)."""
    s = pick["summary"]
    if "question" in s:
        rows = "".join(f'<div class="{k}"><dt>{label}</dt><dd>{esc(s[k])}</dd></div>'
                       for k, label in {**SUMMARY_FIELDS, **(SUMMARY_OPTIONAL if open_line else {})}.items() if s.get(k))
    else:  # an issue written before the four-line introduction (e.g. an old pull request being previewed)
        rows = "".join(f'<div><dt>{label}</dt><dd>{esc(s.get(k, ""))}</dd></div>'
                       for k, label in (("background", "背景"), ("approach", "やったこと"), ("findings", "分かったこと")))
    return f'<dl class="today-4 {cls}">{rows}</dl>'   # the look of Today's Structure on the front page


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


AUTHOR_LABEL = {"first": "First author", "corresp": "Corresponding"}


def authors_html(cand: dict) -> str:
    """The first and corresponding authors under the paper's title, so the reader sees which group it comes from."""
    rows = []
    for key, groups, more in render.author_rows(cand):
        who = ", ".join(f'<span class="pn">{esc(", ".join(names))}</span>' + (f' <span class="pa">({esc(aff)})</span>' if aff else "")
                        for names, aff in groups) or '<span class="pa ja">筆頭著者と同じ</span>'
        if more:
            who += f' <span class="pa ja">ほか {more} 名</span>'
        rows.append(f'<p class="ppl"><span class="pl">{AUTHOR_LABEL[key]}</span><span lang="en">{who}</span></p>')
    return "".join(rows)


def journal_rows(cand: dict) -> str:
    """After the authors: the journal, the paper's publication date and its licence (the owner, 2026-10-26: they were a
    line over the headline)."""
    url = cand.get("license_url")   # a Creative Commons licence: its name and version, linked to its text
    lic = (f'<a href="{esc(url)}" rel="license">{esc(jats.license_label(url))}</a>' if url
           else esc((cand.get("license") or "ライセンス不明").upper()))
    when = esc((cand.get("pub_date") or "").replace("-", "."))
    return (f'<p class="pj"><span class="pl">Journal</span><span lang="en">{esc(journal_name(cand))}</span></p>'
            + (f'<p class="pj"><span class="pl">Published</span><span>{when}</span></p>' if when else "")
            + f'<p class="pj"><span class="pl">License</span><span>{lic}</span></p>')


def article_html(cand: dict, pick: dict, date: str, n: int, root: Path, fig_base: str,
                 impact_type: str | None = None, up: str = "../", related: list[dict] | None = None) -> str:
    hls = "".join(f'<div class="hl"><p>{esc(h["text"])}</p>{quotes(h.get("evidence"))}</div>'
                  for h in pick["highlights"])
    notes = pick.get("lab_notes") or {}
    filled = [(LAB_NOTE_FIELDS[k], notes[k]) for k in LAB_NOTE_FIELDS if notes.get(k)]
    lab = ""
    if filled:
        rows = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in filled)
        lab = f'<h3>Lab Notes</h3><dl class="lab">{rows}</dl>'   # its quotes are in the data, not on the page
    text = (f'<div class="text">'
            f'<div class="art-top"><h2 class="art-h">{esc(pick["headline"])}</h2>{later_button(cand["pmcid"])}</div>'
            f'<p class="subhead">{esc(pick.get("subhead") or "")}</p>'
            f'<div class="pmeta"><p class="ptitle"><span class="pl">Paper</span><span lang="en">{esc(cand["title"])}</span></p>'
            f'{authors_html(cand)}{journal_rows(cand)}</div>'
            f'{figures_html(cand, pick, date, root, fig_base, up) or no_pdb_note(cand)}'
            f'{intro_html(pick)}{paper_link(cand)}'
            f'<h3>Highlights</h3>{hls}{lab}{related_html(related or [], up)}</div>')
    return f'<article class="art" id="a{n}">{margin_html(cand, pick, n, impact_type)}{text}</article>'


# ---------- pages ----------

def load_issue(date: str, root: Path = ISSUES_DIR) -> tuple[dict, list[tuple[dict, dict]], dict[str, str]]:
    """Meta, the articles in page order (Today's Structure first, then part by part; render.ordered), and each
    Structure-Based Study's type. An article's number on every page is its place in this order."""
    d = root / date
    meta = read_json(d / "candidates.json")
    cands = {c["pmcid"]: c for c in meta["candidates"]}
    picks = [(cands[pk["pmcid"]], pk) for pk in (read_json(p) for p in sorted((d / "picks").glob("*.json")))
             if pk.get("pmcid") in cands]
    picks.sort(key=lambda cp: cp[1].get("rank", 99))
    types = {s["pmcid"]: s.get("type") for s in render.load_impact(d).get("scored", [])}
    fpath = d / "feature.json"
    featured = read_json(fpath).get("pmcid") if fpath.exists() and len(picks) >= FEATURE_MIN else None
    return meta, render.ordered(picks, featured), types


PARTS = {
    "new": ("New Structure", "構造が初めて解かれたタンパク質。"),
    "update": ("Structure Update", "既に構造があるタンパク質の新しい構造（別の状態・リガンド・相手との複合体など）。"),
    "impact": ("Structure-Based Study", "既存の構造を使って仕組みの解明や創薬に迫った注目の研究。"),
}
PART_CLS = {"new": "n", "update": "u", "impact": "h"}   # the colour of a part in the charts, the bar and the swatches


PICK_TITLE = "Picked up Today’s Structure"


def page_groups(picks: list, featured: str | None) -> list[tuple[str, list]]:
    """The articles in their groups, in page order: the pick ("pick", No. 01) on its own, then each part without it.
    Empty groups are left out."""
    groups = [("pick", [p for p in picks if p[0]["pmcid"] == featured])]
    groups += [(k, [p for p in items if p[0]["pmcid"] != featured]) for k, items in render.sections(picks).items()]
    return [(k, v) for k, v in groups if v]


def numbers(picks: list) -> dict[str, int]:
    """Each article's number (its place in the page order load_issue gives)."""
    return {c["pmcid"]: n for n, (c, _) in enumerate(picks, 1)}


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
    return f'<a class="thumb none" href="{href}">{no_figure(cand)}</a>'


def no_figure(cand: dict) -> str:
    """Why an article has no structure figure, in the place of its figure."""
    own = cand["own_structures"]
    return ("PDB 公開待ち" if own and not any(x["status"] == "released" for x in own)
            else "PDB ID が本文にない" if cand["kind"] == "new_structure_ids_unlisted"
            else "EMDB マップのみ" if cand["kind"] == "new_map_only" else "構造図なし")


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


CARD_SIZE = {"new": "md", "update": "sm", "impact": "sm"}   # New Structure a size up from the other two parts


TRAILING_NOTE = re.compile(r"^(.*\S)\s*(\([^()]*\))$")


def target_name(pick: dict) -> str:
    """The target's name alone, without the closing parenthesis (the organism, mostly): under a small figure."""
    target = pick.get("target_en") or pick["target_label"]
    m = TRAILING_NOTE.match(target)
    return f"<b>{esc(m.group(1) if m else target)}</b>"


def target_html(pick: dict) -> str:
    """The target's English name, its name in bold and a closing parenthesis (the organism, mostly) in a lighter tone,
    so the protein's name is what the eye catches."""
    target = pick.get("target_en") or pick["target_label"]
    m = TRAILING_NOTE.match(target)
    return f'<b>{esc(m.group(1))}</b> <span class="org">{esc(m.group(2))}</span>' if m else f"<b>{esc(target)}</b>"


def card_html(cand: dict, pick: dict, n: int, date: str, root: Path, fig_base: str, href: str, size: str = "sm",
              label: str = "") -> str:
    """A card of an index: the structure (the figure leads) with the article's number large at its top left, the
    target (it is often the name that tells a reader "this is mine") on a line of its own, the headline and its one-line
    supplement, and Read Later: at the right end of the group line when the card has one (an issue's contents), else
    right under the supplement. Nothing else: the facts and the four lines are in the article. The cards are kept
    apart by space alone: little inside a card, much more between rows (the owner wants no rules)."""
    later = later_button(cand["pmcid"], True)
    head = f'<div class="crow">{label}{later}</div>' if label else ""
    foot = "" if label else f'<p class="cact">{later}</p>'
    return (f'<li class="card {size}"><div class="cfig">{thumb_html(cand, pick, date, root, fig_base, href)}'
            f'<span class="cno" aria-hidden="true">{pad(n)}</span></div>{head}'
            f'<p class="ctg"><span class="ct">{target_html(pick)}</span></p>'
            f'<h3><a href="{href}">{esc(pick["headline"])}</a></h3>'
            f'<p class="cs">{esc(pick.get("subhead") or "")}</p>{foot}</li>')




def part_label(cand: dict, pick: dict, featured: str | None) -> str:
    """Which group an article is in, over its card: its part's colour and name, "Picked up" first for the pick."""
    key = render.part_of(cand, pick)
    pick_mark = '<b>Picked up</b><span class="sep">·</span>' if cand["pmcid"] == featured else ""
    return f'<p class="cpart kl"><i class="sw-{PART_CLS[key]}"></i>{pick_mark}{PARTS[key][0]}</p>'


def contents_html(picks: list, date: str, root: Path, fig_base: str, featured: str | None = None) -> str:
    """An issue page's Contents: every article as a card in one even grid, in the issue's order, each card saying which
    group it is in (Picked up, New Structure, ...), its headline and supplement two lines at most (the owner's request,
    2026-10-19). The numbers run 01, 02, ... without a gap."""
    number = numbers(picks)
    cards = "".join(card_html(c, pk, number[c["pmcid"]], date, root, fig_base, f'#a{number[c["pmcid"]]}', "md",
                              part_label(c, pk, featured)) for c, pk in picks)
    return (f'<div class="ix-h"><h2 class="label">Contents</h2><span class="ixm"><b>{n_articles(len(picks))}</b></span></div>'
            f'<ol class="cards md toc-cards">{cards}</ol>') if picks else ""


def concept_html(meta: dict, picks: list, href: str, featured: str | None = None) -> str:
    """Concept, right under the front page's first screen (the owner, 2026-10-23): what FoldFeed is, in its own words
    (ABOUT's first passage) and as a figure of the latest issue's day in numbers: the open-access papers the database
    took in that day, the ones read whole as structural biology, the ones written up (by part). The first number is
    left out when the issue came from PMC's data (Europe PMC was not counting). Each part under the last number is a
    way to that part of the issue page (on a phone the first screen has no breakdown: this is it, 2026-10-24)."""
    shown = {k for k, items in page_groups(picks, featured) if items}
    _, m, d, _ = date_parts(meta["index_date"][:10])
    _, im, idd, _ = date_parts(meta["issue_date"])
    fresh = (meta.get("health") or {}).get("fresh_oa") if meta.get("source") != pmcoa.SOURCE else None
    sections = [(k, len(v)) for k, v in render.sections(picks).items() if v]
    steps = ([(fresh, f"{m}月{d}日に Europe PMC へ|登録されたオープンアクセス論文")] if fresh else []) + [
        (len(meta["candidates"]), "構造生物学の論文として|全文を読んで確かめた"),
        (len(picks), "記事にした")]
    # under each number a bar, its length on a log scale (from thousands to ten); the last one in the parts' colours
    top = math.log10(max(n for n, _ in steps) + 1)
    items = ""
    for i, (n, t) in enumerate(steps):
        w = math.log10(n + 1) / top if top else 1
        last = i == len(steps) - 1 and sections
        bar = ("".join(f'<i class="{PART_CLS[k]}" style="flex:{c}"></i>' for k, c in sections) if last else "<i></i>")
        legend = ('<span class="flow-parts kl">' + "".join(f'<a href="{href}#{k if k in shown else "pick"}"><i class="sw-{PART_CLS[k]}"></i>'
                                                           f'{PARTS[k][0]}<b>{c}</b></a>' for k, c in sections)
                  + '</span>' if last else "")
        items += (f'<li><b data-count="{n}">{n:,}</b><span class="flow-bar" style="--w:{w:.3f}" aria-hidden="true">{bar}</span>'
                  f'<span class="flow-t">{"<br>".join(esc(x) for x in t.split("|"))}</span>{legend}</li>')
    text = next(lead for k, lead, _ in ABOUT if k == "Concept")
    return (f'<section class="sec concept" id="concept"><div class="ix-h"><h2 class="label">Concept</h2></div>'
            f'<div class="concept-grid"><p class="concept-t">{sentences(text)}</p>'
            f'<figure class="flow"><ol>{items}</ol><figcaption><a href="{href}">{m}.{d:02d} の新着から {im}.{idd:02d} 号へ'
            f'<span class="arr" aria-hidden="true">→</span></a></figcaption></figure></div>'
            f'<p class="concept-go"><a class="ix-all" href="#today">{PICK_TITLE}<span class="arr" aria-hidden="true">↓</span></a></p>'
            f'</section>')


def more_html(picks: list, date: str, root: Path, fig_base: str, href: str, featured: str | None) -> str:
    """Under Picked up Today's Structure on the front page, the way into the issue (the owner's request, 2026-10-21: the
    front page keeps the pick as a taste, what else came out is read on the issue page): the issue's other structures
    as a strip of figures with their targets, each a link to its article, and the button to the whole issue."""
    number = numbers(picks)
    rest = [(c, pk) for c, pk in picks if c["pmcid"] != featured]
    if not rest:
        return ""
    thumbs = ""
    for c, pk in rest[:MORE_THUMBS]:
        name = esc(pk.get("target_en") or pk["target_label"])
        n = number[c["pmcid"]]
        fig = (f'<img src="{fig_base}/{date}/{esc(c["pmcid"])}-thumb.svg" width="600" height="400" alt="{name}" loading="lazy">'
               if (root / date / "figures" / f'{c["pmcid"]}-thumb.svg').exists() else f'<span class="no">{esc(no_figure(c))}</span>')
        thumbs += (f'<li><a href="{href}#a{n}" title="{name}">{fig}<span class="mi-no" aria-hidden="true">{pad(n)}</span>'
                   f'<span class="th-tg">{target_name(pk)}</span></a></li>')
    more = len(rest) - MORE_THUMBS
    if more > 0:
        thumbs += f'<li class="mi-more"><a href="{href}">+{more}</a></li>'
    _, m, d, _ = date_parts(date)
    title = "More in This Issue" if featured else "This Issue"
    return (f'<section class="sec more-iss"><div class="ix-h"><h2 class="label">{title}</h2>'
            f'<a class="ix-all" href="{href}">{m}.{d} 号の全 {len(picks)} 本を読む<span class="arr" aria-hidden="true">→</span></a>'
            f'</div><ol class="mi-th{" more" if more > 0 else ""}">{thumbs}</ol></section>')


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


def lead_html(feature: dict | None, picks: list, date: str, root: Path, fig_base: str, number: int | None = None,
              pick_from: tuple | None = None) -> str:
    """The first screen of the front page: what the site is (three lines), the button down to Today's Structure, and
    on the right Today's Structure in the 3D viewer, opened by itself on a wide screen, with no text around it but
    "読み込み中" while it loads. On a phone (where it is not opened) and when it cannot load, the same structure is
    shown as a drawing from the same view (pipeline.render: <pmcid>-hero.svg). pick_from: (feature, picks, date) of the
    Today's Structure to show when the latest issue has none (the one before it, the owner 2026-10-26)."""
    lines = "".join(f'<span style="--i:{i}">{"<br class=sp>".join(esc(x) for x in t.split("|"))}</span>' for i, t in enumerate(LEAD))
    y, m, d, wd = date_parts(date)
    # when it was updated, quietly under the structure (the owner, 2026-10-23: above the introduction it stood out)
    updated = (f'<p class="lead-upd">{y}.{m:02d}.{d:02d}（{wd}）更新'
               + (f'<span>Issue No. {number}</span>' if number else "") + '</p>')
    # what a visitor should do next: see today's structures, on the issue page (the one button: the front page shows
    # only the pick, 2026-10-21); the breakdown is information, so text, each part a way to it on the issue page;
    # About is for first-timers
    picked = (feature or {}).get("pmcid") if feature_of(feature, picks) else None
    shown = {k for k, items in page_groups(picks, picked) if items}
    issue = f"issues/{date}.html"
    go = (f'<div class="lead-btns"><a class="lead-go" href="{issue}">今日の構造を見る'
          f'<span class="arr" aria-hidden="true">→</span></a></div>')
    parts = "".join(f'<a href="{issue}#{k if k in shown else "pick"}"><i class="sw-{PART_CLS[k]}"></i>{PARTS[k][0]}<b>{len(v)}</b></a>'
                    for k, v in render.sections(picks).items() if v)
    go += (f'<p class="lead-parts kl"><span class="t">今日の内訳</span>{parts}</p>' if parts else "")
    go += '<a class="lead-about" href="#concept">はじめての方へ：FoldFeed について<span aria-hidden="true">▽</span></a>'
    pf, pp, pdate = pick_from or (feature, picks, date)
    hit = feature_of(pf, pp)
    # a phone (the owner, 2026-10-24): the introduction top left, in the serif of the first design; the structure large
    # under it; the Picked up mark (Today's Structure, its target) at the bottom left. The date is in the header bar
    mark = (f'<a class="lead-mark" href="#today"><span class="k">Picked up</span><b>Today’s Structure</b>'
            f'<span class="t">{esc(hit[2]["target_label"])}'
            + (f'（{date_parts(pdate)[1]}.{date_parts(pdate)[2]:02d} 号から）' if pdate != date else "") + '</span></a>') if hit else ""
    art_html = ""
    if hit and (root / pdate / "figures" / f"{hit[1]['pmcid']}-hero.svg").exists():
        pm, pk = hit[1]["pmcid"], hit[2]
        fd = root / pdate / "figures"
        info = read_json(fd / f"{pm}.json") if (fd / f"{pm}.json").exists() else {}
        attrs, _, _ = viewer_bar(info, "", info.get("hero_view"))
        img = (f'<img class="fig" src="{fig_base}/{pdate}/{esc(pm)}-hero.svg"{wh(fd / f"{pm}-hero.svg")} '
               f'alt="構造図：{esc(pk.get("target_en") or pk["target_label"])}">')
        # under the structure, small: what it is (the pick's headline), a way down to it
        cap = (f'<a class="lead-cap" href="#today"><span class="k">Picked up Today’s Structure'
               + (f' · {date_parts(pdate)[1]}.{date_parts(pdate)[2]:02d} 号から' if pdate != date else "") + '</span>'
               f'<span class="h">{esc(pk["headline"])}</span></a>')
        if attrs:   # opens by itself on a wide screen (assets/viewer.js). While it loads, only "読み込み中" shows; the
            # drawing is for a phone (where it does not open) and for a browser that cannot load it. No buttons, no help.
            art_html = (f'<div class="lead-art"><div class="v3d lead-3d" data-auto="wide"{attrs}><div class="v3d-view">{img}'
                        f'<div class="v3d-canvas"></div><p class="lead-wait" aria-live="polite">読み込み中</p></div></div>{cap}{updated}</div>')
        else:
            art_html = f'<div class="lead-art"><a class="lead-pic" href="#today" tabindex="-1">{img}</a>{cap}{updated}</div>'
    return (f'<section class="lead"><div class="wrap lead-grid"><div class="lead-text">'
            f'<h1 class="lead-h">{lines}</h1>{go}{"" if art_html else updated}</div>{art_html}{mark}</div></section>')


LEAD_SCRIPT = r"""
(function () {
  /* A phone's first screen: Today's Structure turns a little and draws back as the page is scrolled, so the reader's
     own scrolling moves it (it stays in its place: the text under it is never covered). Not on a wide screen (the 3D
     viewer is there) and not for a reader who asked for less motion. */
  var view = document.querySelector('.lead-art .v3d-view, .lead-art .lead-pic');
  if (!view || !window.matchMedia || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var phone = matchMedia('(max-width: 860px)'), ticking = false;
  function place() {
    ticking = false;
    var k = phone.matches ? Math.min(1, window.scrollY / (window.innerHeight * 0.8)) : 0;
    view.style.transform = k ? 'rotate(' + (k * 16).toFixed(2) + 'deg) scale(' + (1 - k * 0.12).toFixed(3) + ')' : '';
  }
  window.addEventListener('scroll', function () {
    if (!ticking) { ticking = true; requestAnimationFrame(place); }
  }, { passive: true });
  place();
})();
"""


def paper_button(cand: dict, note: bool = True) -> str:
    """The way to the paper; on an article page with the journal under it (the front page has the button alone)."""
    journal = cand.get("journal_abbrev") or cand.get("journal") or "掲載誌"
    small = f'<small>{esc(journal)} · 英語 · 外部サイト</small>' if note else ""
    return (f'<a class="read-paper" href="{doi_url(cand)}" rel="noopener"><b>原著論文を開く</b><span class="arr" aria-hidden="true">↗</span>'
            f'{small}</a>')


def paper_link(cand: dict) -> str:
    """An article's way to the paper, as the button Today's Structure has on the front page."""
    return f'<div class="reads">{paper_button(cand)}</div>'


def read_links(cand: dict, href: str) -> str:
    """The two ways on from Picked up Today's Structure: FoldFeed's article and the paper itself (the owner asked for
    the buttons without the small lines under them, 2026-10-19)."""
    return (f'<div class="reads">'
            f'<a class="read-art" href="{href}"><b>詳しい解説を読む</b><span class="arr" aria-hidden="true">→</span></a>'
            f'{paper_button(cand, note=False)}</div>')


def today_html(feature: dict | None, picks: list, date: str, root: Path, fig_base: str, href_base: str = "",
               carried: bool = False) -> str:
    """Picked up Today's Structure on the front page, the largest card: the article's own figure (overview and binding
    site; its portrait version on a phone), its part and number, the target, the headline and supplement, the four
    lines (question, approach, result, learned; the owner, 2026-10-23) and the ways on (the article, the paper, Read
    Later). Highlights and Lab Notes are in the article; the reason it was picked is for the pull request. carried: the
    latest issue had none, so this is the one before it, and the heading says from which issue."""
    hit = feature_of(feature, picks)
    if not hit:
        return ""
    n, cand, pk = hit
    href = f"{href_base}#a{n}"
    pm = cand["pmcid"]
    fd = root / date / "figures"
    alt = f'構造図：{esc(pk.get("target_en") or pk["target_label"])}'
    if (fd / f"{pm}.svg").exists():
        # a phone: the article's portrait figure (labels and the binding site), not the first screen's drawing again
        phone = f"{pm}-m.svg" if (fd / f"{pm}-m.svg").exists() else f"{pm}-thumb.svg" if (fd / f"{pm}-thumb.svg").exists() else ""
        small = f'<source media="(max-width:860px)" srcset="{fig_base}/{date}/{esc(phone)}">' if phone else ""
        fig = (f'<a class="pk-fig" href="{href}"><picture>{small}<img src="{fig_base}/{date}/{esc(pm)}.svg"{wh(fd / f"{pm}.svg")} '
               f'alt="{alt}"></picture></a>')
    else:
        fig = thumb_html(cand, pk, date, root, fig_base, href)
    fig = f'<div class="cfig">{fig}<span class="cno" aria-hidden="true">{pad(n)}</span></div>'
    _, m, d, _ = date_parts(date)
    when = f'<span class="ixm"><b>{m}.{d:02d} 号から</b></span>' if carried else ""
    return (f'<section class="today" id="today"><div class="ix-h"><h2 class="label">Picked up Today’s Structure</h2>{when}</div>'
            f'<article class="pickup">{fig}<div class="pk-t">'
            f'<p class="ck"><span class="ct">{PARTS[render.part_of(cand, pk)][0]}</span>{later_button(pm, True)}</p>'
            f'<p class="pk-tg ctg">{target_html(pk)}</p>'
            f'<h3 class="pk-h"><a href="{href}">{esc(pk["headline"])}</a></h3>'
            f'<p class="pk-s">{esc(pk.get("subhead") or "")}</p>{intro_html(pk, open_line=False, cls="pk-4")}'
            f'{read_links(cand, href)}</div></article></section>')


def part_html(key: str, count: int, date: str) -> str:
    """An issue page's 'New Structure 2026.10.18' heading over its articles, with how many on the right."""
    title = PICK_TITLE if key == "pick" else PARTS[key][0]
    y, m, d, _ = date_parts(date)
    return (f'<section class="part" id="{key}"><h2>{title}<span class="hd">{y}.{m:02d}.{d:02d}</span>'
            f'<span class="ixm"><b>{n_articles(count)}</b></span></h2></section>')


def toc_side(picks: list, featured: str | None) -> str:
    """The sidebar of an issue page (beside the articles): the way back to the top of the issue, then every article,
    grouped, each a link to its anchor."""
    number = numbers(picks)
    out = ""
    for key, items in page_groups(picks, featured):
        links = "".join(f'<a href="#a{number[c["pmcid"]]}"><b>{pad(number[c["pmcid"]])}</b>'
                        f'<span>{esc(pk["headline"])}</span></a>' for c, pk in items)
        out += f'<h3>{PICK_TITLE if key == "pick" else PARTS[key][0]}</h3>{links}'
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
               index_date: str | None = None, index_from: str | None = None, nature: bool = False,
               root: Path = ISSUES_DIR, fig_base: str = "../figures", featured: str | None = None) -> str:
    """The first screen of an issue page, in the order the owner asked for (2026-10-19). On the left, which issue it is
    (the date, large), then how much there is to read, large (articles, minutes), then the parts, small (links down to
    them), and the neighbouring issues. On the right the day's structures, each its figure, number and target and a link
    to its article, six at a time: more turn over every CAROUSEL_SECONDS (CAROUSEL_SCRIPT), with the page shown (1 / 3),
    buttons back and on, a pause while the pointer or the keyboard is on them, no more turning once the reader has
    turned a page, and none for a reader who asked for less motion. Without JavaScript all of them are shown."""
    y, m, d, _ = date_parts(date)
    mins = [reading_minutes(pk) for _, pk in picks]
    total = max(1, round(sum(mins))) if picks else 0
    kicker = (f"Issue No. {number}" if number else "Preview") + f" · {WEEKDAY_EN[datetime.date(y, m, d).weekday()]}"
    # (the papers not written up are listed at the foot of the page, under Not Covered, not counted here)
    src = (f'<p class="ih-src">{index_span(index_date, index_from)}に論文データベースへ登録された論文'
           + ('と、同じ日に Nature 系の雑誌で出版された論文' if nature else '') + 'から</p>') if index_date else ""
    big = (f'<div class="ih-big"><div class="kpi"><span class="kv" data-count="{len(picks)}">{len(picks)}</span>'
           f'<span class="kl">Articles</span></div><div class="kpi"><span class="kv"><small>約</small><span data-count="{total}">'
           f'{total}</span><small>分</small></span><span class="kl">Reading time</span></div></div>') if picks else ""
    # the parts (kinds of article; the pick counts in its own), small; each a link to its articles
    shown = {k for k, _ in page_groups(picks, featured)}
    parts = "".join(f'<a href="#{k if k in shown else "pick"}"><i class="sw-{PART_CLS[k]}"></i>{PARTS[k][0]}<b data-count="{len(v)}">{len(v)}</b></a>'
                    for k, v in render.sections(picks).items() if v)
    parts = f'<p class="ih-parts kl">{parts}</p>' if parts else ""
    nav = issue_nav(prev_date, next_date, "ih-nav") if prev_date or next_date else ""
    left = f'<div class="ih-left"><p class="ih-k">{kicker}</p><h1 class="ih-date">{y}.{m:02d}.{d:02d}</h1>{src}{big}{parts}{nav}</div>'
    if not picks:
        return f'<header class="ihead" id="issue-top"><div class="ih-grid">{left}</div></header>'
    cards = ""
    for n, (c, pk) in enumerate(picks, 1):
        pm = c["pmcid"]
        img = (f'<img src="{fig_base}/{date}/{esc(pm)}-thumb.svg" width="600" height="400" alt="" loading="lazy">'
               if (root / date / "figures" / f"{pm}-thumb.svg").exists() else f'<span class="thumb none">{no_figure(c)}</span>')
        cards += (f'<li data-page="{(n - 1) // CAROUSEL_PAGE}"><a href="#a{n}"><span class="cfig">{img}'
                  f'<span class="cno" aria-hidden="true">{pad(n)}</span></span>'
                  f'<span class="ctg"><span class="ct">{target_html(pk)}</span></span>'
                  f'<span class="sr">{esc(pk["headline"])}</span></a></li>')
    pages = (len(picks) - 1) // CAROUSEL_PAGE + 1
    ctl = ('<div class="ihc-ctl"><button class="ihc-prev" type="button" aria-label="前の構造">←</button>'
           f'<span class="ihc-n" aria-live="polite">1 / {pages}</span>'
           '<button class="ihc-next" type="button" aria-label="次の構造">→</button></div>') if pages > 1 else ""
    right = (f'<section class="ihc" data-pages="{pages}" aria-label="この号の構造"><div class="ihc-head">'
             f'<h2 class="ihc-t">Today’s Structures</h2>{ctl}'
             f'<a class="ihc-toc" href="#contents"><span class="tri" aria-hidden="true">▽</span>Contents</a></div>'
             f'<ol class="ihc-grid">{cards}</ol>'
             + ('<div class="ihc-bar" aria-hidden="true"><i></i></div>' if pages > 1 else "") + '</section>')
    return f'<header class="ihead" id="issue-top"><div class="ih-grid">{left}{right}</div></header>'


CAROUSEL_SCRIPT = r"""
(function () {
  /* An issue's first screen: its structures six at a time. A page turns when the line under them has run (8 s), not
     while the pointer or the keyboard is on them, never again once the reader has turned a page (buttons, a swipe),
     and not at all for a reader who asked for less motion. */
  var c = document.querySelector('.ihc');
  if (!c) return;
  var pages = +c.getAttribute('data-pages');
  if (pages < 2) return;
  var items = [].slice.call(c.querySelectorAll('.ihc-grid > li')), grid = c.querySelector('.ihc-grid'),
      n = c.querySelector('.ihc-n'), bar = c.querySelector('.ihc-bar'), cur = 0;
  var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches, auto = !calm;
  c.classList.add('paged');
  if (!auto) c.classList.add('manual');
  function run() {
    if (!auto) return;
    bar.classList.remove('run'); void bar.offsetWidth; bar.classList.add('run');
  }
  function show(p) {
    cur = (p + pages) % pages;
    items.forEach(function (li) { li.hidden = +li.getAttribute('data-page') !== cur; });
    n.textContent = (cur + 1) + ' / ' + pages;
    run();
  }
  function shownItems() { return items.filter(function (li) { return !li.hidden; }); }
  function turn(p, byHand) {   // the six go one after another and the next six come in the same way
    if (byHand) { auto = false; c.classList.add('manual'); bar.classList.remove('run'); }
    if (calm || !grid.animate) return show(p);
    var old = shownItems(), last;
    old.forEach(function (li, k) {
      last = li.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(-10px)' }],
                        { duration: 220, delay: k * 45, easing: 'ease-in', fill: 'forwards' });
    });
    last.onfinish = function () {
      old.forEach(function (li) { li.getAnimations().forEach(function (a) { a.cancel(); }); });
      show(p);
      shownItems().forEach(function (li, k) {
        li.animate([{ opacity: 0, transform: 'translateY(14px)' }, { opacity: 1, transform: 'none' }],
                   { duration: 420, delay: k * 60, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'backwards' });
      });
    };
  }
  bar.addEventListener('animationend', function () { if (auto) turn(cur + 1); });
  c.querySelector('.ihc-prev').addEventListener('click', function () { turn(cur - 1, true); });
  c.querySelector('.ihc-next').addEventListener('click', function () { turn(cur + 1, true); });
  c.addEventListener('mouseenter', function () { c.classList.add('hold'); });
  c.addEventListener('mouseleave', function () { c.classList.remove('hold'); });
  c.addEventListener('focusin', function () { c.classList.add('hold'); });
  c.addEventListener('focusout', function (e) { if (!c.contains(e.relatedTarget)) c.classList.remove('hold'); });
  var x0 = null;
  grid.addEventListener('touchstart', function (e) { x0 = e.touches[0].clientX; }, { passive: true });
  grid.addEventListener('touchend', function (e) {
    if (x0 === null) return;
    var dx = e.changedTouches[0].clientX - x0;
    x0 = null;
    if (Math.abs(dx) > 40) turn(cur + (dx < 0 ? 1 : -1), true);
  });
  show(0);
})();
"""


def issue_page(date: str, prev_date: str | None, next_date: str | None,
               root: Path = ISSUES_DIR, preview: bool = False, number: int | None = None,
               related: dict | None = None) -> str:
    meta, picks, types = load_issue(date, root)
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
    feature = load_feature(date, root, picks) if picks else None
    top = [issue_head(date, number, picks, prev_date, next_date, meta.get("index_date"), meta.get("index_from"),
                      bool((meta.get("springer") or {}).get("read")), root, fig_base, feature and feature["pmcid"])]
    if picks:
        top.append(f'<section class="toc" id="contents">{contents_html(picks, date, root, fig_base, feature and feature["pmcid"])}</section>')
    else:
        top.append('<section class="part"><h2>No Articles</h2></section>')
    main = []
    number = numbers(picks)
    for key, items in page_groups(picks, feature and feature["pmcid"]):
        main.append(part_html(key, len(items), date))
        for cand, pk in items:
            n = number[cand["pmcid"]]
            main.append(article_html(cand, pk, date, n, root, fig_base, types.get(cand["pmcid"]), "../",
                                     (related or {}).get((date, n))))
    if rest:
        items = "".join(f'<li><a href="{doi_url(x)}">{esc(x["title"])}</a> — <i>{esc(x["journal_abbrev"] or x["journal"])}</i>'
                        f'（{esc(render.KIND_JA[x["kind"]])}）</li>' for x in rest)
        main.append(f'<section class="rest"><div class="ix-h"><h2 class="label">Not Covered</h2></div><ul class="bl">{items}</ul></section>')
    main.append(issue_nav(prev_date, next_date, "issue-nav", top=True))
    side = toc_side(picks, feature and feature["pmcid"]) if picks else ""   # the issue links and the search are elsewhere
    head = masthead(1, current=None if preview else "latest" if date == LATEST["date"] else "back")
    from_nlm = meta.get("source") == pmcoa.SOURCE      # Europe PMC was not indexing: the papers came from NLM's PMC data
    foot = colophon(f'<p>{esc(pmcoa.CREDIT)}</p>' if from_nlm else "", "../")   # what the issue covers is on its first screen
    desc = "、".join(pk["target_label"] for _, pk in picks[:6])
    title = f"{brand.SITE_NAME} {date.replace('-', '')}"   # "FoldFeed 20261018" (the owner's naming, 2026-10-18)
    body = f'<div class="wrap issue-top">{"".join(top)}</div>' + shell("".join(main), side, sticky=True)
    return page(("［プレビュー］" if preview else "") + title, bar + head + body + foot, depth=1,
                image=og_image(date, root, "preview/figures" if preview else "figures"),
                description=f"{jp_date(date)}の新しい構造：{desc}", noindex=preview, viewer=True, script=CAROUSEL_SCRIPT)


ABOUT = (   # About FoldFeed, in polite Japanese (the owner's wording, 2026-10-18; rewritten by the owner 2026-10-26)
    # (heading, lead, items). The lead is said first, plainly; the items under it are a list: a sentence (a short dash)
    # or a part of the issue with what goes in it ((part, text): the part's colour). One sentence per line. "|" marks
    # where a sentence may wrap (between phrases), so a long sentence breaks into even halves at a phrase boundary and
    # no line is left with 「す。」 alone.
    ("Concept",   # was どんなサイトか (the owner, 2026-10-23); on the front page it is its own section (concept_html)
     "FoldFeed は、|構造生物学に携わる方のための|情報収集サポートサイトです。"
     "前日に|論文データベース（Europe PMC）へ|新しく登録された|査読済みの|オープンアクセス論文の中から、|"
     "構造生物学の論文を|毎日ピックアップして|ご紹介します。", ()),
    ("紹介する論文",
     "FoldFeed では、|構造生物学の論文を|以下のように分類し、|ご紹介しています。",
     (("new", "構造が初めて解かれた|タンパク質の論文"),
      ("update", "既に構造がある|タンパク質の新しい構造|（別の構造状態や複合体など）の論文"),
      ("impact", "既存の構造を使って|仕組みの解明や創薬に迫った|注目の研究論文|（1 日 3 本まで）"),
      "PDB ID を|本文から確かめられない論文は、|構造図なしでの|紹介になります。")),
    ("掲載のタイミング",
     "論文は、|論文データベースに登録された|翌日にご紹介します。",
     ("データベースに|論文が届く時期は、|雑誌によって異なり、|Nature 系の雑誌は|出版からおよそ 1 か月後に届くため、|"
      "掲載もそのころになります。",)),
    ("記事のつくり",
     "各記事では、|論文が明らかにしたことを|短くまとめ、|構造図と|リガンド結合部位の拡大図、|実験条件のメモを|添えています。",
     ("分解能・PDB ID・リガンド名は、|PDB と UniProt から|取得しています。",
      "構造図は、|PDB の座標から|描画しています。",
      "紹介文は AI が|論文本文をもとに作成し、|内容ごとに|論文本文の該当箇所を|引用したうえで、|編集者が確認してから|"
      "公開しています。")),
    ("著作権と出典",   # facts only (the owner's request, 2026-10-20): what is used under which terms, not "no problems"
     "ご紹介する論文は、|全文が無料で読める|オープンアクセス論文です。",
     ("その多くは、|CC BY などの|クリエイティブ・コモンズ・ライセンスで|公開されています。",
      "記事の文章は、|論文の転載や翻訳ではなく、|論文の内容をもとに|日本語で新しく書いたものです。",
      "論文の文章は、|根拠として|短い英文を引用するにとどめ、|「原文」と明示しています。",
      "各記事には、|雑誌名とライセンス、|原著論文へのリンクを|載せています。",
      "論文の図や表は|載せていません。",
      "論文の著作権は、|それぞれの著者または出版社にあります。",
      "PDB のデータ（CC0）と|UniProt のデータ（CC BY 4.0）を、|それぞれのライセンスに従って|使っています。")),
)


def numbers_html(issues: list[str], lib: list[dict]) -> str:
    """FoldFeed in Numbers: three counts that count up as they come into view, the share of papers that are a
    protein's first PDB structure as a meter, and the papers of each issue (the three parts, stacked) as
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
        rows.append((date, *(len(v) for v in render.sections(picks).values())))
    top = max([a + u + b for _, a, u, b in rows] + [1])
    step = 5 if top <= 25 else 10
    ymax = -(-top // step) * step
    ticks = "".join(f'<span style="--y:{t / ymax:.3f}">{t}</span>' for t in range(0, ymax + 1, step))
    cols = []
    for i, (date, *ns) in enumerate(rows):
        _, m, d, _ = date_parts(date)
        tip = f"{m}月{d}日号 · " + " · ".join(f"{PARTS[k][0]} {v} 本" for k, v in zip(render.PART_KEYS, ns) if v)
        # stacked from the baseline in page order: New Structure at the bottom, Structure-Based Study on top
        seg = "".join(f'<i class="{PART_CLS[k]}" style="--h:{v / ymax:.4f}"></i>'
                      for k, v in reversed(list(zip(render.PART_KEYS, ns))) if v)
        last = f'<b class="cap" style="--h:{sum(ns) / ymax:.4f}">{sum(ns)}</b>' if i == len(rows) - 1 else ""
        cols.append(f'<a class="col" href="issues/{date}.html" style="--i:{i}" data-tip="{esc(tip)}" aria-label="{esc(tip)}">'
                    f'<span class="stack">{seg}</span>{last}<span class="x">{m}.{d:02d}</span></a>')
    table = "".join(f"<tr><th>{date}</th>" + "".join(f"<td>{v}</td>" for v in ns) + "</tr>" for date, *ns in rows)
    legend = "".join(f'<i class="{PART_CLS[k]}"></i>{PARTS[k][0]}' for k in render.PART_KEYS)
    chart = (f'<figure class="ppi"><figcaption><b>Papers per Issue</b><span class="lg">{legend}</span></figcaption>'
             f'<div class="plot"><div class="yt">{ticks}</div><div class="cols">{"".join(cols)}</div></div>'
             f'<table class="sr"><caption>号ごとの紹介本数</caption><tr><th>号</th>'
             + "".join(f"<th>{PARTS[k][0]}</th>" for k in render.PART_KEYS)
             + f'</tr>{table}</table><div class="tip" hidden></div></figure>')
    return f'<div class="kpis">{kpis}</div>{meter}{chart}'


def phrases(x: str) -> str:
    """Text that may wrap only at the marked phrase boundaries ("|"), balanced over its lines."""
    return "<span class=s>" + "<wbr>".join(f'<span class=w>{esc(w)}</span>' for w in x.split("|")) + "</span>"


def sentences(text: str) -> str:
    """An ABOUT passage: one sentence per line, each wrapping only at its phrase boundaries."""
    return "".join(phrases(x + "。") for x in text.split("。") if x)


ABOUT_ID = {"紹介する論文": "papers", "掲載のタイミング": "timing", "記事のつくり": "making", "著作権と出典": "rights"}


def about_body(lead: str, items: tuple) -> str:
    """An ABOUT passage: the lead, then its items as lists (a run of sentences, a run of parts)."""
    out = f'<p class="ab-lead">{sentences(lead)}</p>'
    for is_part, run in itertools.groupby(items, key=lambda x: isinstance(x, tuple)):
        if is_part:
            out += '<ul class="bl bl-parts">' + "".join(
                f'<li><b><i class="sw-{PART_CLS[k]}"></i>{PARTS[k][0]}</b>{phrases(t)}</li>' for k, t in run) + '</ul>'
        else:
            out += '<ul class="bl">' + "".join(f'<li>{sentences(t)}</li>' for t in run) + '</ul>'
    return out


def about_html(issues: list[str], lib: list[dict], own_page: bool = False) -> str:
    """About FoldFeed: what the site is (polite Japanese), FoldFeed in Numbers (an infographic, see numbers_html) and
    every journal the papers came from, as bars. All numbers are computed from the data; a paper that came back in a
    later issue counts once. On the front page Concept is left out: it has its own section near the top."""
    journals = journal_counts(issues)
    rows = "".join(f'<div{f" id={ABOUT_ID[k]}" if k in ABOUT_ID else ""}><dt>{esc(k)}</dt><dd>{about_body(lead, items)}</dd></div>'
                   for k, lead, items in ABOUT if own_page or k != "Concept")
    most = max([c for _, c in journals] + [1])
    jl = "".join(f'<li style="--i:{i}"><span class="jn">{esc(n)}</span><span class="c">{c}</span>'
                 f'<span class="bar" aria-hidden="true"><i style="--v:{c / most:.3f}"></i></span></li>'
                 for i, (n, c) in enumerate(journals))
    return (f'<section class="about" id="about"><div class="wrap">'
            f'<div class="ix-h"><{"h1" if own_page else "h2"} class="label">About FoldFeed</{"h1" if own_page else "h2"}></div>'
            f'<div class="about-grid"><dl class="about-dl">{rows}</dl>'
            f'<div class="about-side"><h3 class="about-sub" id="numbers">FoldFeed in Numbers</h3>{numbers_html(issues, lib)}</div></div>'
            f'<h3 class="about-sub" id="journals">Journals<span>論文数</span></h3><ul class="jl">{jl}</ul></div></section>')


METHOD_WORDS = (("クライオ", "cryo-em cryoem"), ("X線", "x-ray crystallography"), ("NMR", "nmr"))


def method_kinds(method: str) -> list[str]:
    """'クライオ電顕 / X線結晶構造解析' -> ['クライオ電顕', 'X線結晶構造解析']; the page groups by these."""
    return [m.strip() for m in method.split("/") if m.strip() and m.strip() != "—"]


# Words that put an article under a kind of research (schema.TOPICS), looked for in the paper's title and abstract and the
# article's headline, supplement and four lines (not the whole paper: a method section mentions everything once).
TOPIC_WORDS = {
    "design": r"de novo|designed (?:protein|binder|enzyme|assembl|cage|nanocage|peptide)|computational(?:ly)? design|"
              r"protein design|RFdiffusion|ProteinMPNN|デノボ|タンパク質設計|設計したタンパク質",
    "drug": r"inhibitor|antagonist|agonist|small[- ]molecule|drug (?:discovery|design|candidate|target|development)|"
            r"lead compound|阻害剤|拮抗薬|作動薬|創薬",
    "antibody": r"antibod|nanobod|\bFab\b|scFv|抗体|ナノボディ",
    "ai": r"AlphaFold|RoseTTAFold|deep learning|machine learning|co-?folding|neural network|language model|"
          r"構造予測|機械学習|深層学習",
    "membrane": r"membrane protein|transmembrane|lipid bilayer|nanodisc|膜タンパク質|膜貫通|膜輸送",
    "dynamics": r"time-resolved|smFRET|single-molecule FRET|conformational (?:change|transition|landscape|dynamics|cycle)|"
                r"intermediate state|時分割|中間状態|構造変化",
    "insitu": r"cryo-?electron tomography|cryo-?ET\b|subtomogram|in situ|トモグラフィー",
}
TOPIC_RE = {k: re.compile(v, re.I) for k, v in TOPIC_WORDS.items()}
IMPACT_TOPIC = {"ai_design": "ai", "drug": "drug", "method": "method"}   # a Structure-Based Study's type says it too


def topics_of(cand: dict, pick: dict, impact_type: str | None = None) -> list[str]:
    """The kinds of research of an article (schema.TOPICS): the writer's `topics` when given, else found by TOPIC_WORDS;
    channels and transporters are membrane proteins."""
    if pick.get("topics") is not None:
        return [t for t in pick["topics"] if t in TOPICS]
    s = pick.get("summary") or {}
    own = " ".join([cand.get("title") or "", pick.get("headline") or "", pick.get("subhead") or "",
                    *(str(s.get(k) or "") for k in SUMMARY_FIELDS)])
    full = own + " " + (cand.get("abstract") or "")
    # AI and design: only when the paper is about them (title, headline, four lines), not when an abstract mentions an
    # AlphaFold model it used along the way
    found = {k for k, rx in TOPIC_RE.items() if rx.search(own if k in ("ai", "design") else full)}
    if set(pick.get("categories") or []) & {"channel", "transporter"}:
        found.add("membrane")
    if render.is_impact(pick) and IMPACT_TOPIC.get(impact_type or ""):
        found.add(IMPACT_TOPIC[impact_type])
    return [k for k in TOPICS if k in found]


def library(issues: list[str], root: Path = ISSUES_DIR) -> list[dict]:
    """Every article of every issue (newest first) with what the browse-and-search page needs: protein names,
    UniProt accessions, gene names, PDB IDs, categories, and a lower-cased search string."""
    out = []
    for date in issues:
        _, picks, types = load_issue(date, root)
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
            row = {"date": date, "n": n, "cand": cand, "pick": pick, "cats": pick.get("categories") or cand.get("categories_draft") or ["other"],
                   "topics": topics_of(cand, pick, types.get(cand["pmcid"])), "part": render.part_of(cand, pick),
                   "uniprot": sorted(set(ids)), "genes": sorted(set(genes)), "names": sorted(set(names)),
                   "pdbs": sorted({x["pdb_id"] for x in released}), "method": method,
                   "gene_of": {p["uniprot"]: p["gene"] for p in cand.get("proteins", []) if p.get("gene")},
                   # for related articles: the proteins' Pfam families (id -> short name; not a polyprotein's or a
                   # giant's, whose many domains would tie it to everything) and the ligands but the common cofactors
                   "pfam": {k: v for p in cand.get("proteins", []) if len(dict(p.get("pfam") or {})) <= 6
                            for k, v in dict(p.get("pfam") or {}).items()},
                   "ligands": {lig["id"]: lig.get("name") or lig["id"] for x in released for lig in x.get("ligands", [])
                               if lig["id"] not in COFACTORS}}
            text = " ".join([pick["target_label"], pick.get("target_en") or "", pick["headline"], pick.get("subhead") or "", cand["title"]]
                            + [render.label_text(t) for t in pick.get("legend_labels") or []] + row["genes"] + row["uniprot"] + row["names"] + row["pdbs"]
                            + [method] + [w for key, w in METHOD_WORDS if key in method]
                            + [CATEGORIES.get(c, "") for c in row["cats"]] + [TOPICS[t] for t in row["topics"]]
                            + [CATEGORY_EN.get(c, "") for c in row["cats"]] + [TOPIC_EN.get(t, "") for t in row["topics"]])
            row["search"] = unicodedata.normalize("NFKC", text).lower()
            out.append(row)
    return out


BROAD_KINDS = {"enzyme", "nucleic", "other"}   # too wide to say two articles are related


# cofactors and sugars that many unrelated structures carry: not a reason to call two articles related
COFACTORS = {"ATP", "ADP", "AMP", "ANP", "ACP", "AGS", "GTP", "GDP", "GNP", "GSP", "GCP", "NAD", "NAI", "NAP", "NDP", "NADPH",
             "FAD", "FMN", "SAM", "SAH", "HEM", "HEC", "SF4", "FES", "F3S", "COA", "PLP", "TPP", "BGC", "GLC", "NGA", "SIA",
             "UDP", "CTP", "UTP", "MLY", "CLA", "BCR", "PQN", "PL9", "LUT", "HEA", "CU1", "MO",
             "BEF", "ALF", "AF3", "VO4", "MGF",   # phosphate analogues in nucleotide sites
             "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER",
             "THR", "TRP", "TYR", "VAL"}   # free amino acids


def related_articles(lib: list[dict], limit: int = 3) -> dict[tuple[str, int], list[dict]]:
    """For every article, up to `limit` other articles of the site that share something checkable, in this order: a
    UniProt accession (the same protein); a Pfam family (InterPro was tried: its superfamilies tie unrelated proteins);
    a ligand of the structures; the same main kind of protein. A family or a ligand counts only when few articles have
    it (a domain or a ligand found in many says little). Nothing here is a claim about the papers."""
    def df(key):
        n = {}
        for r in lib:
            for k in set(r[key]):
                n[k] = n.get(k, 0) + 1
        return n
    rare = max(3, len(lib) // 12)   # in at most this many articles
    fam_n, lig_n = df("pfam"), df("ligands")
    out = {}
    for r in lib:
        tiers = ([], [], [], [])
        for o in lib:
            if o is r or o["cand"]["pmcid"] == r["cand"]["pmcid"]:
                continue
            shared = sorted(set(r["uniprot"]) & set(o["uniprot"]))
            fam = [k for k in sorted(set(r["pfam"]) & set(o["pfam"])) if fam_n[k] <= rare]
            lig = [k for k in sorted(set(r["ligands"]) & set(o["ligands"])) if lig_n[k] <= rare]
            when = (o["date"], o["n"])
            if shared:
                names = [r["gene_of"].get(a) or o["gene_of"].get(a) or a for a in shared]
                tiers[0].append((len(shared), when, o, "同じタンパク質：" + "、".join(dict.fromkeys(names[:3]))))
            elif fam:
                tiers[1].append((len(fam), when, o, "同じファミリー（Pfam）：" + "、".join(r["pfam"][k] for k in fam[:2])))
            elif lig:
                tiers[2].append((len(lig), when, o, "同じリガンド：" + "、".join(lig[:2])))
            elif r["cats"][0] == o["cats"][0] and r["cats"][0] not in BROAD_KINDS:
                tiers[3].append((0, when, o, f"同じ種類：{CATEGORIES.get(r['cats'][0], r['cats'][0])}"))
        picked = []
        for t in tiers:   # most shared, then newest
            t.sort(key=lambda x: (x[0], x[1]), reverse=True)
            picked += [{"row": x[2], "why": x[3]} for x in t[:limit - len(picked)]]
        out[(r["date"], r["n"])] = picked
    return out


def category_counts(lib: list[dict]) -> list[tuple[str, int]]:
    counts = {k: 0 for k in CATEGORIES}
    for r in lib:
        for c in set(r["cats"]):
            counts[c if c in counts else "other"] += 1
    return [(k, v) for k, v in counts.items() if v]


def about_page(issues: list[str], lib: list[dict]) -> str:
    """About (about.html): what the site is, in numbers, and its journals, as on the front page; the place where more
    about how FoldFeed works and who makes it will go (TODO.md)."""
    return page(f"About · {brand.SITE_NAME}", masthead(0, current="about") + f'<div class="about-page">{about_html(issues, lib, True)}</div>'
                + colophon(), depth=0, description="FoldFeed がどんなサイトか、記事の作り方と掲載のタイミング。")


def newsletter_page() -> str:
    """Newsletters & Alerts (newsletter.html): what is planned; not working yet (TODO.md)."""
    main = ('<div class="wrap list-page mail"><div class="ix-h"><h1 class="label">Newsletters &amp; Alerts</h1>'
            '<span class="soon">準備中</span></div><div class="mail-grid"><div>'
            '<ul class="bl"><li>毎朝の号の知らせ（New Structure・Structure Update・Structure-Based Study）</li><li>週のまとめ</li>'
            '<li>気になるタンパク質のアラート（UniProt ID を登録）</li></ul></div><div>'
            '<form aria-disabled="true" onsubmit="return false"><input type="email" placeholder="メールアドレス" disabled '
            'aria-label="メールアドレス（準備中）"><button type="button" disabled>登録（準備中）</button></form>'
            '</div></div></div>')
    return page(f"Newsletters & Alerts · {brand.SITE_NAME}", masthead(0, current="news") + main + colophon(), depth=0,
                description="FoldFeed のメール配信とアラート（準備中）。")


LEGAL = ROOT / "config" / "legal.json"   # the operator's details for the two pages below (empty until the owner fills them)


def doc_page(title: str, lead: str, sections: list[tuple[str, str]], file: str, description: str, foot: str = "") -> str:
    """A page of text (Privacy Policy, 特定商取引法に基づく表示): a lead, then headed sections."""
    body = "".join(f'<section class="doc-sec"><h2>{esc(h)}</h2>{html_}</section>' for h, html_ in sections)
    main = (f'<div class="wrap list-page doc"><div class="ix-h"><h1 class="label">{esc(title)}</h1></div>'
            f'<p class="doc-lead">{lead}</p>{body}{foot}</div>')
    return page(f"{title} · {brand.SITE_NAME}", masthead(0) + main + colophon(), depth=0, description=description)


def legal_info() -> dict:
    return read_json(LEGAL) if LEGAL.exists() else {"tokushoho": {}}


def established(info: dict) -> str:
    y, m, d, _ = date_parts(info.get("established") or "2026-10-26")
    return f"{y}年{m}月{d}日"


def privacy_page() -> str:
    """privacy.html (the owner's request, 2026-10-22): what the site does with its readers' information, as it is now —
    no accounts, no cookies, no analytics; Read Later stays in the browser; the outside services the pages load from.
    To be revised before newsletters, analytics or advertising start."""
    info = legal_info()
    contact = esc(info.get("contact") or "") or "準備中です（About に掲載します）。"
    li = lambda *xs: '<ul class="bl">' + "".join(f"<li>{x}</li>" for x in xs) + "</ul>"
    sections = [
        ("取得する情報",
         "<p>FoldFeed は、会員登録・お問い合わせフォーム・メールの登録などで、氏名やメールアドレスなどの個人情報を取得していません。"
         "Cookie を使っておらず、アクセス解析も行っていません。</p>"),
        ("ブラウザの中に保存する情報",
         "<p>「後で読む」で保存した記事の一覧は、お使いのブラウザの中（localStorage）だけに保存され、FoldFeed や第三者には送信されません。"
         "ブラウザのデータを消すと、一覧も消えます。「後で読む」のページでコピーできるリンクには、保存した記事の番号が含まれます。</p>"),
        ("外部のサービス",
         "<p>ページの表示のために、次のサービスから文字のデータや構造のデータを読み込みます。そのとき、お使いのブラウザから各サービスへ、"
         "IP アドレスやブラウザの種類などが送られます。各サービスでの扱いは、それぞれのプライバシーポリシーによります。</p>"
         + li("GitHub Pages（GitHub, Inc.）：このサイトの配信",
              "Google Fonts（Google LLC）：文字（フォント）の表示",
              "RCSB PDB：3D ビューアで表示する構造の座標")
         + "<p>記事には、出版社・Europe PMC・PDB・UniProt などへのリンクがあります。リンク先での情報の扱いは、各サイトの方針によります。</p>"),
        ("この方針の変更",
         "<p>メールの配信、アクセス解析、広告などを始めるときは、取得する情報・利用の目的・第三者への提供の有無をこのページに書き、"
         "始める前に改定します。</p>"),
        ("お問い合わせ", f"<p>{contact}</p>"),
    ]
    foot = f'<p class="doc-date">{established(info)} 制定</p>'
    return doc_page("Privacy Policy", "FoldFeed（以下「このサイト」）での、利用者の情報の扱いについて定めます。", sections,
                    "privacy.html", "FoldFeed のプライバシーポリシー。", foot)


TOKUSHOHO_TODO = "有料サービスの開始時に掲載します"


def tokushoho_page() -> str:
    """tokushoho.html (the owner's request, 2026-10-22; needed once anything is sold): FoldFeed sells nothing yet, so
    the page says so and lists what it will show; the values come from config/legal.json."""
    info = legal_info()
    rows = "".join(f'<div><dt>{esc(k)}</dt><dd>{esc(v) if v else f"<span class=todo>{TOKUSHOHO_TODO}</span>"}</dd></div>'
                   for k, v in (info.get("tokushoho") or {}).items())
    selling = any((info.get("tokushoho") or {}).values())
    lead = ("特定商取引法に基づき、有料サービスについて次のとおり表示します。" if selling else
            "現在、FoldFeed は有料の商品・サービスを提供していません。有料のサービスを始めるときに、"
            "特定商取引法に基づき、次の事項をこのページに表示します。")
    main = [("表示する事項", f'<dl class="doc-dl">{rows}</dl>')]
    return doc_page("特定商取引法に基づく表示", lead, main, "tokushoho.html", "FoldFeed の特定商取引法に基づく表示。",
                    f'<p class="doc-date">{established(info)} 制定</p>')


def browse_html(lib: list[dict]) -> str:
    """The ways into the articles on Search, as tiles that show the newest structures of each: the parts (their own
    pages), the kinds of protein, of research and the methods (this page, filtered). The header search's suggestions
    offer the same ways in, small; here they are seen whole."""
    def thumbs(rows):
        out, seen = "", set()
        for r in rows:
            pm = r["cand"]["pmcid"]
            if pm in seen or not (ISSUES_DIR / r["date"] / "figures" / f"{pm}-thumb.svg").exists():
                continue
            seen.add(pm)
            out += f'<img src="figures/{r["date"]}/{esc(pm)}-thumb.svg" width="600" height="400" alt="" loading="lazy">'
            if len(seen) == BROWSE_THUMBS:
                break
        return out

    def tiles(items, size):
        return f'<ol class="bt-grid {size}">' + "".join(
            f'<li class="bt"><a href="{href}"><span class="bt-th">{thumbs(rows)}</span>'
            f'<span class="bt-n">{esc(name)}<span class="bt-c">{n_articles(len(rows))}</span></span>'
            + (f'<span class="bt-d">{esc(note)}</span>' if note else "") + '</a></li>'
            for name, href, rows, note in items if rows) + '</ol>'

    def group(title, body):
        anchor = dict((t, a) for a, t in BROWSE_GROUPS)[title]
        return f'<section class="bt-sec" id="{anchor}"><h2 class="bt-h">{title}</h2>{body}</section>'

    parts = [(PARTS[k][0], PART_PAGES[k], [r for r in lib if r["part"] == k], PARTS[k][1]) for k in PARTS]
    cats = sorted(((CATEGORIES[k], f"search.html?cat={k}", [r for r in lib if k in r["cats"]], "") for k in CATEGORIES),
                  key=lambda t: -len(t[2]))
    topics = [(TOPICS[k], f"search.html?topic={k}", [r for r in lib if k in r["topics"]], "") for k in TOPICS]
    methods = [(m, f"search.html?method={m}", [r for r in lib if m in r["method"]], "") for m in METHOD_FACETS]
    return (group("Part", tiles(parts, "lg")) + group("Protein Type", tiles(cats, "sm"))
            + group("Research Type", tiles(topics, "sm")) + group("Method", tiles(methods, "sm")))


def home(issues: list[str]) -> str:
    """The front page. First screen: what the site is, in three lines, the way to today's structures (the latest issue
    page) and Today's Structure in 3D. Right under it Picked up Today's Structure, the one article shown in full as a
    taste of the site, and the way into the issue with its other structures as small figures: what else came out is
    seen and read on the issue page (the owner's request, 2026-10-21; the front page had every article as cards).
    Then the latest Back Issues (the newest past issues) and About FoldFeed. Browse and Newsletters & Alerts are pages of their own, in the
    global navigation (2026-10-19)."""
    latest = next((d for d in issues if load_issue(d, ISSUES_DIR)[1]), issues[0])  # newest issue that has articles
    past = [d for d in issues if d != latest]
    _, picks, _ = load_issue(latest, ISSUES_DIR)
    href = f"issues/{latest}.html"
    feature = load_feature(latest, ISSUES_DIR, picks)
    lib = library(issues)
    meta = load_issue(latest, ISSUES_DIR)[0]
    # no Today's Structure in the latest issue (fewer than FEATURE_MIN articles): the one before it is shown again, so
    # the front page is never without one (the owner, 2026-10-26)
    shown = (feature, picks, latest)
    if not feature_of(feature, picks):
        for d in issues[issues.index(latest) + 1:]:
            _, pp, _ = load_issue(d, ISSUES_DIR)
            pf = load_feature(d, ISSUES_DIR, pp)
            if feature_of(pf, pp):
                shown = (pf, pp, d)
                break
    carried = shown[2] != latest
    main = [concept_html(meta, picks, href, feature and feature["pmcid"]),
            today_html(*shown, ISSUES_DIR, "figures", f"issues/{shown[2]}.html", carried=carried),
            more_html(picks, latest, ISSUES_DIR, "figures", href, feature and feature["pmcid"])]
    if past:
        number = {d: len(issues) - i for i, d in enumerate(issues)}
        rows = "".join(issue_row(d, number[d], ISSUES_DIR, "figures", "issues/") for d in past[:PAST_ON_HOME])
        main.append(f'<section class="sec past"><div class="ix-h"><h2 class="label">Back Issues</h2>'
                    f'<a class="ix-all" href="issues.html">すべての号一覧<span class="arr" aria-hidden="true">→</span></a></div>'
                    f'<ol class="iss-list">{rows}</ol></section>')
    side = ""   # no sidebar: the search is in the header (2026-10-19), the interests were dropped (2026-10-18)
    return page(brand.SITE_NAME,
                masthead(0) + lead_html(feature, picks, latest, ISSUES_DIR, "figures", len(issues) - issues.index(latest), shown)
                + shell("".join(main), side)
                + about_html(issues, lib)
                + colophon(f'<p>{esc(pmcoa.CREDIT)}</p>' if load_issue(latest, ISSUES_DIR)[0].get("source") == pmcoa.SOURCE else ""),
                depth=0, viewer=True, script=LEAD_SCRIPT, image=og_image(shown[2]))


PAST_ON_HOME = 5   # the latest past issues on the front page; all of them are on issues.html (Back Issues)


def issue_row(date: str, number: int, root: Path, fig_base: str, href_base: str) -> str:
    """One issue in a list of issues: its date, number and weekday, how many articles of each part, and a strip of
    its structures with their targets (the first few; how many more after them)."""
    _, picks, _ = load_issue(date, root)
    y, m, d, _ = date_parts(date)
    href = f"{href_base}{date}.html"
    counts = "".join(f'<span><i class="sw-{PART_CLS[k]}"></i>{PARTS[k][0]}<b>{len(v)}</b></span>'
                     for k, v in render.sections(picks).items() if v) or "<span>記事なし</span>"
    thumbs = ""
    for c, pk in picks[:THUMBS_PER_ISSUE]:
        name = esc(pk.get("target_en") or pk["target_label"])
        fig = (f'<img src="{fig_base}/{date}/{esc(c["pmcid"])}-thumb.svg" width="600" height="400" alt="" loading="lazy">'
               if (root / date / "figures" / f'{c["pmcid"]}-thumb.svg').exists() else '<span class="no" aria-hidden="true"></span>')
        thumbs += f'<span class="it" title="{name}">{fig}<span class="th-tg">{target_name(pk)}</span></span>'
    more = len(picks) - THUMBS_PER_ISSUE
    thumbs += f'<span class="more">+{more}</span>' if more > 0 else ""
    return (f'<li class="iss"><a class="iss-d" href="{href}"><span class="k">Issue No. {number} · '
            f'{WEEKDAY_EN[datetime.date(y, m, d).weekday()]}</span><b>{y}.{m:02d}.{d:02d}</b></a>'
            f'<p class="iss-n kl">{counts}</p>'
            + (f'<a class="iss-th" href="{href}" tabindex="-1">{thumbs}</a>' if picks else "") + '</li>')


def issues_page(issues: list[str]) -> str:
    """Back Issues (issues.html): every issue, newest first, each with its parts and a strip of its structures."""
    rows = "".join(issue_row(d, len(issues) - i, ISSUES_DIR, "figures", "issues/") for i, d in enumerate(issues))
    main = (f'<div class="wrap list-page"><div class="ix-h"><h1 class="label">Back Issues</h1>'
            f'<span class="ixm"><b>{len(issues)} Issues</b></span></div><ol class="iss-list">{rows}</ol></div>')
    return page(f"Back Issues · {brand.SITE_NAME}", masthead(0, current="back") + main + colophon(), depth=0,
                description="FoldFeed のこれまでの号。")


PART_PAGES = {"new": "new.html", "update": "update.html", "impact": "study.html"}


def part_page(key: str, issues: list[str]) -> str:
    """New Structure / Structure Update / Structure-Based Study across every issue (new.html, update.html, study.html):
    the part's cards, newest issue first, under each issue's date."""
    title, note = PARTS[key]
    groups, total = [], 0
    for date in issues:
        _, picks, _ = load_issue(date, ISSUES_DIR)
        number = numbers(picks)
        items = render.sections(picks)[key] if picks else []
        if not items:
            continue
        total += len(items)
        y, m, d, _ = date_parts(date)
        cards = "".join(card_html(c, pk, number[c["pmcid"]], date, ISSUES_DIR, "figures",
                                  f'issues/{date}.html#a{number[c["pmcid"]]}', CARD_SIZE[key]) for c, pk in items)
        groups.append(f'<h2 class="toc-sub"><a href="issues/{date}.html">{y}.{m:02d}.{d:02d}</a><span>{len(items)}</span></h2>'
                      f'<ol class="cards {CARD_SIZE[key]}">{cards}</ol>')
    main = (f'<div class="wrap list-page"><div class="ix-h"><h1 class="label">{title}</h1>'
            f'<span class="ixm"><b>{n_articles(total)}</b></span></div><p class="lp-lead">{esc(note)}</p>{"".join(groups)}</div>')
    return page(f"{title} · {brand.SITE_NAME}", masthead(0, current="browse") + main + colophon(), depth=0,
                description=f"FoldFeed の {title}：{note}")


SEARCH_PAGE_STEP = 50   # results drawn at a time on the search page

SEARCH_PAGE_SCRIPT = r"""
(function () {
  var q = document.getElementById('q'), ways = document.getElementById('fp-ways'), results = document.getElementById('fp-results'),
      list = document.getElementById('pg-list'), more = document.getElementById('pg-more'), none = document.getElementById('pg-none'),
      total = document.getElementById('pg-total'), filt = document.getElementById('pg-filter'),
      labels = JSON.parse(document.getElementById('pg-labels').textContent);
  var params = new URLSearchParams(location.search), facet = null, data = null, hits = [], shown = 0;
  ['cat', 'topic', 'method', 'part'].forEach(function (k) { if (params.get(k) && !facet) facet = [k, params.get(k)]; });
  function norm(s) { return s.normalize('NFKC').toLowerCase(); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function inFacet(a) {
    if (!facet) return true;
    var v = facet[1];
    return facet[0] === 'cat' ? a.c.indexOf(v) >= 0 : facet[0] === 'topic' ? a.tp.indexOf(v) >= 0
         : facet[0] === 'method' ? a.m.indexOf(v) >= 0 : a.p === v;
  }
  function row(a) {
    var meta = [];
    if (a.u.length) meta.push('<i>UNIPROT</i>' + esc(a.u.join(', ')));
    if (a.g.length) meta.push('<i>GENE</i>' + esc(a.g.join(', ')));
    if (a.pdb.length) meta.push('<i>PDB</i>' + esc(a.pdb.join(', ')));
    return '<li class="pr"><span class="pd">' + a.d.replace(/-/g, '.') + ' · ' + (a.n < 10 ? '0' : '') + a.n + '</span>' +
      '<a class="pt" href="issues/' + a.d + '.html#a' + a.n + '">' + esc(a.h) + '<span class="ps">' + esc(a.t) + '</span></a>' +
      '<span class="pm">' + meta.join('<br>') + '</span></li>';
  }
  function page() {   // the next STEP of the matches
    list.insertAdjacentHTML('beforeend', hits.slice(shown, shown + STEP).map(row).join(''));
    shown = Math.min(hits.length, shown + STEP);
    more.hidden = shown >= hits.length;
    more.textContent = 'さらに表示（残り ' + (hits.length - shown) + ' 本）';
  }
  function apply() {
    var terms = norm(q.value).split(/\s+/).filter(Boolean), asked = !!(terms.length || facet);
    ways.hidden = asked; results.classList.toggle('on', asked);   // nothing asked: the ways in; something asked: the articles
    if (!asked) return;
    if (!data) { total.textContent = '読み込んでいます…'; return; }
    hits = data.filter(function (a) { return inFacet(a) && terms.every(function (t) { return a.s.indexOf(t) >= 0; }); });
    list.innerHTML = ''; shown = 0; page();
    none.style.display = hits.length ? 'none' : 'block';
    total.textContent = hits.length + ' 本が見つかりました';
  }
  if (facet) {
    filt.hidden = false;
    filt.innerHTML = '<span>' + esc(labels[facet[0] + '=' + facet[1]] || facet[1]) + '</span><a href="search.html">条件を外す ×</a>';
  }
  if (params.get('q')) q.value = params.get('q');
  var timer;
  q.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(apply, 120); });
  q.form.addEventListener('submit', function (e) { e.preventDefault(); apply(); });
  more.addEventListener('click', page);
  fetch('search.json').then(function (r) { return r.json(); }).then(function (d) {
    data = d.sort(function (x, y) { return x.d < y.d ? 1 : x.d > y.d ? -1 : x.n - y.n; }); apply();
  }).catch(function () { total.textContent = '記事の一覧を読み込めませんでした。'; });
  apply();
})();
"""


LATER_PAGE_SCRIPT = r"""
(function () {
  /* later.html: the saved articles (window.FFLater, kept in this browser) drawn from search.json; a link carries the
     list to another device (#ids=...), opening it here adds those articles. */
  var list = document.getElementById('lp-list'), empty = document.getElementById('lp-empty'), n = document.getElementById('lp-n'),
      msg = document.getElementById('lp-msg'), share = document.getElementById('lp-share'), clear = document.getElementById('lp-clear'),
      tools = document.getElementById('lp-tools'), data = {};
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function say(t) { msg.textContent = t; msg.hidden = !t; }
  var m = /#ids=([^&]+)/.exec(location.hash);
  if (m) {
    var add = decodeURIComponent(m[1]).split(',').filter(Boolean), have = FFLater.get();
    var fresh = add.filter(function (id) { return have.indexOf(id) < 0; });
    FFLater.set(fresh.concat(have));
    say(fresh.length ? fresh.length + ' 本を追加しました。' : 'リンクの記事は、すでにすべて保存されています。');
    if (history.replaceState) history.replaceState(null, '', location.pathname);
  }
  function draw() {
    var ids = FFLater.get().filter(function (id) { return data[id]; });
    empty.hidden = ids.length > 0;
    list.innerHTML = ids.map(function (id) {
      var a = data[id];
      return '<li class="lp-row" data-id="' + esc(id) + '">' + (a.f ? '<a class="lp-fig" href="issues/' + a.d + '.html#a' + a.n + '"><img src="' + a.f + '" alt="" loading="lazy"></a>' : '<span class="lp-fig"></span>') +
        '<div class="lp-t"><p class="lp-k">' + a.d.replace(/-/g, '.') + ' · ' + esc(a.pl) + '</p>' +
        '<a class="lp-h" href="issues/' + a.d + '.html#a' + a.n + '">' + esc(a.h) + '</a>' +
        '<p class="lp-s">' + esc(a.t) + ' · ' + esc(a.j) + '</p></div>' +
        '<button class="later on" type="button" data-id="' + esc(id) + '" aria-pressed="true"><span class="lt">✓ 後で読む</span></button></li>';
    }).join('');
    mark();
  }
  function mark() {   // a row taken off stays, greyed, until the page is opened again (pressing again puts it back)
    var ids = FFLater.get(), kept = ids.filter(function (id) { return data[id]; }).length;
    n.textContent = kept + (kept === 1 ? ' Article' : ' Articles');
    tools.hidden = !ids.length;
    [].forEach.call(list.querySelectorAll('.lp-row'), function (r) { r.classList.toggle('off', ids.indexOf(r.getAttribute('data-id')) < 0); });
  }
  share.addEventListener('click', function () {
    var url = location.origin + location.pathname + '#ids=' + FFLater.get().join(',');
    (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject()).then(function () {
      say('リンクをコピーしました。ほかの端末やブラウザで開くと、そこに追加されます。');
    }, function () { window.prompt('このリンクをほかの端末で開いてください', url); });
  });
  clear.addEventListener('click', function () { if (window.confirm('保存した記事をすべて外しますか？')) { FFLater.set([]); say(''); draw(); } });
  document.addEventListener('ff-later', mark);
  fetch('search.json').then(function (r) { return r.json(); }).then(function (d) {
    d.forEach(function (a) { if (!data[a.id]) data[a.id] = a; });
    draw();
  }).catch(function () { say('記事の一覧を読み込めませんでした。'); });
})();
"""


def later_page() -> str:
    """later.html, Read Later: the articles this reader saved (in this browser only), newest first, each with its figure;
    a button copies a link that carries the list to another device."""
    main = ('<div class="wrap later-page"><div class="ix-h"><h1 class="label">Read Later</h1><span class="ixm"><b id="lp-n"></b></span></div>'
            '<p class="lp-msg" id="lp-msg" hidden></p><ol class="lp-list" id="lp-list"></ol>'
            '<p class="lp-empty" id="lp-empty">まだ保存した記事はありません。記事の「＋ 後で読む」を押すと、ここに集まります。</p>'
            '<div class="lp-tools" id="lp-tools" hidden><button id="lp-share" type="button">ほかの端末で開くリンクをコピー</button>'
            '<button id="lp-clear" type="button">すべて外す</button></div>'
            '<p class="lp-note">保存した記事は、このブラウザの中だけに記録されます（サイトには送られません）。'
            'ほかの端末やブラウザでも見るときは、上のリンクをそこで開いてください。</p>'
            '<noscript><p class="lp-empty">この機能を使うには JavaScript を有効にしてください。</p></noscript></div>')
    return page(f"Read Later · {brand.SITE_NAME}", masthead(0) + main + colophon(), depth=0, noindex=True,
                description="あとで読むために保存した記事。", script=LATER_PAGE_SCRIPT)


def search_index(lib: list[dict]) -> list[dict]:
    """search.json: one entry per paper (its newest article) for the header search, the search page and Read Later.
    The pages fetch it when first needed and draw only what matches, so they stay light as the articles grow."""
    out, seen = [], set()
    for r in lib:
        pm = r["cand"]["pmcid"]
        if pm in seen:
            continue
        seen.add(pm)
        thumb = ISSUES_DIR / r["date"] / "figures" / f"{pm}-thumb.svg"
        out.append({"id": pm, "d": r["date"], "n": r["n"], "h": r["pick"]["headline"],
                    "t": r["pick"].get("target_en") or r["pick"]["target_label"],
                    "j": r["cand"].get("journal_abbrev") or r["cand"].get("journal") or "",
                    "p": r["part"], "pl": PARTS[r["part"]][0],
                    "f": f"figures/{r['date']}/{pm}-thumb.svg" if thumb.exists() else "", "s": r["search"],
                    # for the search page: its kinds and method (filters) and the IDs shown on a result's row
                    "c": r["cats"], "tp": r["topics"], "m": r["method"],
                    "u": r["uniprot"][:4] + (["…"] if len(r["uniprot"]) > 4 else []), "g": r["genes"][:4],
                    "pdb": r["pdbs"][:4] + (["…"] if len(r["pdbs"]) > 4 else [])})
    return out


def facets(lib: list[dict]) -> dict:
    """The header search's suggestions with how many articles each has (counted as the search page and the
    Protein Type list count them)."""
    count = lambda pred: sum(1 for r in lib if pred(r))   # noqa: E731
    out = {"cats": sorted(((k, count(lambda r, k=k: k in r["cats"])) for k in CATEGORIES), key=lambda kv: -kv[1]),
           "topics": [(k, count(lambda r, k=k: k in r["topics"])) for k in TOPICS],
           "methods": [(m, count(lambda r, m=m: m in r["method"])) for m in METHOD_FACETS],
           "parts": [(k, count(lambda r, k=k: r["part"] == k)) for k in PARTS]}
    return {k: [(a, n) for a, n in v if n] for k, v in out.items()}


def search_page(issues: list[str]) -> str:
    """Search (search.html): the one place to find articles (the owner found Browse and Article Search hard to tell
    apart, 2026-10-26). A box for words (UniProt ID, gene name, protein name, PDB ID or any word of the headline) and,
    while nothing is asked, the ways in as tiles (browse_html). A word typed, or a way in taken (?cat=, ?topic=,
    ?method=, ?part=), puts the matching articles in place of the tiles, newest first. The articles are not in the
    page: SEARCH_PAGE_SCRIPT reads search.json and draws the matches, SEARCH_PAGE_STEP at a time, so the page stays as
    light with thousands of articles as with a hundred."""
    lib = library(issues)
    labels = {**{f"cat={k}": v for k, v in CATEGORIES.items()}, **{f"topic={k}": v for k, v in TOPICS.items()},
              **{f"method={m}": m for m in METHOD_FACETS}, **{f"part={k}": v[0] for k, v in PARTS.items()}}
    main = (f'<div class="wrap list-page find-page"><div class="ix-h"><h1 class="label">Search</h1>'
            f'<span class="ixm"><b>{n_articles(len({r["cand"]["pmcid"] for r in lib}))}</b></span></div>'
            f'<p class="lp-lead">{phrases("これまでの記事を、|キーワードや、|部・タンパク質の種類・|研究の種類・手法から|探せます。")}</p>'
            f'<form class="find" role="search"><input id="q" type="search" name="q" autocomplete="off" '
            f'placeholder="例：P72047 / kinase / 25IP / リボソーム / de novo" aria-label="記事を探す"><button type="submit">探す</button></form>'
            f'<p id="pg-filter" class="pg-filter" hidden></p>'
            f'<script type="application/json" id="pg-labels">{json.dumps(labels, ensure_ascii=False)}</script>'
            f'<div class="fp-ways" id="fp-ways">{browse_html(lib)}</div>'
            f'<div class="fp-results" id="fp-results"><p id="pg-total" class="sd-meta" aria-live="polite"></p>'
            f'<ol id="pg-list" class="rows pg-list"></ol>'
            f'<button id="pg-more" class="pg-more" type="button" hidden></button>'
            f'<p id="pg-none" class="pg-none">該当する記事はありませんでした。</p></div>'
            f'<noscript><p class="pg-none" style="display:block">言葉での検索には JavaScript が要ります。'
            f'上の分類から、部や種類ごとに記事をたどれます。</p></noscript></div>')
    # the page's own search box is the search here: the header's would repeat it
    return page(f"Search · {brand.SITE_NAME}", masthead(0, search=False, current="browse") + main + colophon(), depth=0,
                description="FoldFeed のこれまでの記事を、キーワードや、部・タンパク質の種類・研究の種類・手法から探す。",
                script=SEARCH_PAGE_SCRIPT.replace("STEP", str(SEARCH_PAGE_STEP)))


def moved_page(to: str) -> str:
    """An old address (browse.html, proteins.html): on to its page, keeping what was asked (?cat=..., #...)."""
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="robots" content="noindex">'
            f'<meta http-equiv="refresh" content="0; url={to}"><link rel="canonical" href="{to}"><title>{brand.SITE_NAME}</title>'
            f'<script>location.replace("{to}" + location.search + location.hash);</script></head>'
            f'<body><p><a href="{to}">{to}</a></p></body></html>')


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
        counts = " · ".join(f"{PARTS[k][0]} {len(v)} 本" for k, v in render.sections(picks).items() if v) or "記事なし"
        pr = read_json(root / date / "pr.json") if (root / date / "pr.json").exists() else {}
        merge = (f'<a class="go" href="{esc(pr["url"])}">Pull Request #{pr["number"]}（マージ）</a>'
                 if pr.get("url") else "")
        items.append(f'<li><div class="d">{jp_date(date)}号</div>'
                     f'<div class="c">{counts}</div>'
                     f'<div class="btns"><a href="{date}.html">見た目を確認</a>{merge}</div></li>')
    body = [masthead(1, "Awaiting Review"),
            '<div class="wrap"><p class="intro">マージ前の号のプレビュー。見た目を確認して、問題がなければ Pull Request を開いて'
            '「Merge pull request」→「Confirm merge」で公開する。このページはブックマークやホーム画面に追加しておける。</p>',
            f'<ul class="queue">{"".join(items)}</ul>' if items else '<p class="intro">いま確認待ちの号はない。</p>',
            '</div>', colophon(up="../")]
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
    OG["default"] = next((u for u in map(og_image, issues) if u), None)
    shutil.copytree(ROOT / "assets" / "vendor", SITE_DIR / "assets" / "vendor")
    lib = library(issues)
    FACETS.update(facets(lib))
    if LATEST["date"]:
        _, lp, _ = load_issue(LATEST["date"], ISSUES_DIR)
        lf = load_feature(LATEST["date"], ISSUES_DIR, lp)
        NAV_INFO.update(articles=len(lp), groups=[(k, len(v)) for k, v in page_groups(lp, lf and lf["pmcid"])])
    NAV_INFO["recent"] = [(d, len(load_issue(d, ISSUES_DIR)[1])) for d in issues if d != LATEST["date"]][:5]
    related = related_articles(lib)
    for i, date in enumerate(issues):
        prev_date = issues[i + 1] if i + 1 < len(issues) else None
        next_date = issues[i - 1] if i > 0 else None
        (SITE_DIR / "issues" / f"{date}.html").write_text(
            issue_page(date, prev_date, next_date, number=len(issues) - i, related=related), encoding="utf-8")
        src = ISSUES_DIR / date / "figures"
        if src.exists():
            shutil.copytree(src, SITE_DIR / "figures" / date, ignore=shutil.ignore_patterns("*.json"))
    (SITE_DIR / "index.html").write_text(home(issues), encoding="utf-8")
    (SITE_DIR / "search.html").write_text(search_page(issues), encoding="utf-8")
    for old in ("proteins.html", "browse.html"):   # the two pages Search took over (2026-10-26)
        (SITE_DIR / old).write_text(moved_page("search.html"), encoding="utf-8")
    (SITE_DIR / "later.html").write_text(later_page(), encoding="utf-8")
    (SITE_DIR / "issues.html").write_text(issues_page(issues), encoding="utf-8")
    (SITE_DIR / "newsletter.html").write_text(newsletter_page(), encoding="utf-8")
    (SITE_DIR / "privacy.html").write_text(privacy_page(), encoding="utf-8")
    (SITE_DIR / "tokushoho.html").write_text(tokushoho_page(), encoding="utf-8")
    (SITE_DIR / "about.html").write_text(about_page(issues, lib), encoding="utf-8")
    for key, name in PART_PAGES.items():
        (SITE_DIR / name).write_text(part_page(key, issues), encoding="utf-8")
    (SITE_DIR / "search.json").write_text(json.dumps(search_index(lib), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
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
