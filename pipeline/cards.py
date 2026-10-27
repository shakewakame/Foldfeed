"""Cards: a PNG (1200 x 630) of an article, its structure beside its headline, for link previews (og:image) and for
posting on social media. The structure is the article's own figure (the large one of Today's Structure, else the
small one); an article without a figure gets none.

Only each issue's lead article (Today's Structure, else the first article) gets a card kept with the issue: it is the
issue's link preview. A card is about 0.2 MB, so one for every article (about ten a day) would add some 0.6 GB a year to
the repository and to the published site (GitHub Pages stops at 1 GB). The others are made when wanted, into a folder
that is not kept:

    python3 -m pipeline.cards 2026-10-25                          # the issue's lead card (next to its figures)
    python3 -m pipeline.cards --all                               # every issue that lacks it
    python3 -m pipeline.cards 2026-10-25 --every --out /tmp/cards # every article's card, into a folder of your own

The page of each card is made here and photographed by tools/shoot.mjs (Chromium through playwright-core)."""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from . import render
from .common import ISSUES_DIR, ROOT, read_json

PART_COLOR = {"new": "#2F5DA8", "update": "#1F8A70", "impact": "#C8442A"}   # site.CHART_NEW / CHART_UPD / CHART_HOT
PART_NAME = {"new": "New Structure", "update": "Structure Update", "impact": "Structure-Based Study"}

CARD = """<!doctype html><html lang="ja"><head><meta charset="utf-8"><style>
html, body {{ margin:0; }}
body {{ width:1200px; height:630px; position:relative; overflow:hidden; background:#FAFAF8; color:#1B2638;
        font-family:"Noto Sans JP","Noto Sans CJK JP","IPAPGothic","Hiragino Sans",sans-serif; }}
.bar {{ position:absolute; left:0; right:0; top:0; height:10px; background:#1B2638; }}
.brand {{ position:absolute; left:64px; top:50px; display:flex; align-items:center; gap:14px; }}
.brand img {{ width:44px; height:44px; border-radius:24%; }}
.brand b {{ font-family:Georgia,"Liberation Serif","DejaVu Serif",serif; font-size:34px; letter-spacing:-.01em; }}
.k {{ position:absolute; left:64px; top:148px; font-family:"DejaVu Sans Mono",ui-monospace,monospace; font-size:16px;
      letter-spacing:.08em; text-transform:uppercase; color:{color}; }}
.h {{ position:absolute; left:64px; top:186px; width:540px; margin:0; font-size:38px; font-weight:700; line-height:1.5;
      word-break:auto-phrase; text-wrap:balance; }}
.t {{ position:absolute; left:64px; bottom:58px; width:540px; margin:0; font-family:Georgia,"Liberation Serif",serif;
      font-size:22px; line-height:1.4; color:#5B6577; }}
.fig {{ position:absolute; left:640px; right:40px; top:44px; bottom:40px; display:flex; align-items:center; justify-content:center; }}
.fig img {{ max-width:100%; max-height:100%; }}
</style></head><body><div class="bar"></div>
<div class="brand"><img src="{logo}" alt=""><b>FoldFeed</b></div>
<p class="k">{kicker}</p><p class="h">{headline}</p><p class="t">{target}</p>
<div class="fig"><img src="{figure}" alt=""></div></body></html>"""


def card_path(date: str, pmcid: str, out: Path | None = None, root: Path = ISSUES_DIR) -> Path:
    return (out / f"{date}-{pmcid}.png") if out else root / date / "figures" / f"{pmcid}-card.png"


def lead_of(date: str, root: Path = ISSUES_DIR) -> str | None:
    """The issue's lead article: Today's Structure, else the first article in page order. None for an issue that is
    not under `root` (a preview's issue is not in data/issues until it is merged)."""
    from . import site   # the page order and the pick are the site's
    if not (root / date / "candidates.json").exists():
        return None
    _, picks, _ = site.load_issue(date, root)
    feature = site.load_feature(date, root, picks)
    return feature["pmcid"] if feature else (picks[0][0]["pmcid"] if picks else None)


def figure_of(date: str, pmcid: str) -> Path | None:
    for name in (f"{pmcid}-hero.svg", f"{pmcid}-thumb.svg"):
        p = ISSUES_DIR / date / "figures" / name
        if p.exists():
            return p
    return None


def jobs_for(date: str, tmp: Path, force: bool = False, every: bool = False, out: Path | None = None) -> list[dict]:
    d = ISSUES_DIR / date
    if not (d / "candidates.json").exists():
        return []
    cands = {c["pmcid"]: c for c in read_json(d / "candidates.json")["candidates"]}
    lead = None if every else lead_of(date)
    jobs = []
    for p in sorted((d / "picks").glob("*.json")):
        pick = read_json(p)
        cand = cands.get(pick.get("pmcid"))
        fig = figure_of(date, pick.get("pmcid", ""))
        if not cand or not fig or (not every and cand["pmcid"] != lead) \
                or (card_path(date, cand["pmcid"], out).exists() and not force):
            continue
        part = render.part_of(cand, pick)
        page = tmp / f"{date}-{cand['pmcid']}.html"
        page.write_text(CARD.format(
            color=PART_COLOR[part], logo=(ROOT / "assets" / "logo-mark.svg").as_uri(), figure=fig.as_uri(),
            kicker=html.escape(f"{PART_NAME[part]} · {date.replace('-', '.')}"),
            headline=html.escape(pick["headline"]), target=html.escape(pick.get("target_en") or pick["target_label"])),
            encoding="utf-8")
        jobs.append({"html": str(page), "png": str(card_path(date, cand["pmcid"], out))})
    return jobs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PNG cards of the articles (SNS and link previews)")
    ap.add_argument("dates", nargs="*")
    ap.add_argument("--all", action="store_true", help="every issue")
    ap.add_argument("--force", action="store_true", help="make them again even if they exist")
    ap.add_argument("--every", action="store_true", help="every article's card, not only the lead's (use with --out)")
    ap.add_argument("--out", type=Path, help="write the cards here instead of next to the issue's figures")
    a = ap.parse_args(argv)
    if a.every and not a.out:
        ap.error("--every writes many cards: give --out, a folder that is not kept in the repository")
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
    dates = sorted(x.name for x in ISSUES_DIR.iterdir() if x.is_dir()) if a.all else a.dates
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        jobs = [j for date in dates for j in jobs_for(date, tmp, a.force, a.every, a.out)]
        if not jobs:
            print("no cards to make", file=sys.stderr)
            return 0
        manifest = tmp / "jobs.json"
        manifest.write_text(json.dumps(jobs), encoding="utf-8")
        subprocess.run(["node", str(ROOT / "tools" / "shoot.mjs"), str(manifest)], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
