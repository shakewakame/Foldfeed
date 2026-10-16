"""Split a catch-up issue (one that mixes several days) into one issue per day, articles included.

    python3 -m pipeline.split 2026-10-16

Reads data/issues/<ISSUE>/ (candidates.json of a window, picks/, impact.json, feature.json) and writes, for each index
day of the window, data/issues/<day + 1>/ with that day's candidates (pipeline.collect.split_by_day), the picks of those
candidates (ranks renumbered in their old order), the scored 注目の構造 candidates of the day (impact.json) and the
feature.json when its article is among them. Existing directories of those dates are replaced; render the issues and
check them with pipeline.verify afterwards. What it cannot decide is left to the writer: a feature for a day that has
three or more articles and none yet, and `considered` entries of a feature whose rivals were on other days (dropped).
"""

from __future__ import annotations

import json
import shutil
import sys

from . import collect
from .common import ISSUES_DIR, issue_dir, read_json, write_json


def split(issue: str) -> list[str]:
    src = issue_dir(issue)
    meta = read_json(src / "candidates.json")
    if not meta.get("index_from") or meta["index_from"] == meta["index_date"]:
        raise SystemExit(f"{issue} covers a single day; nothing to split")
    picks = [read_json(p) for p in sorted((src / "picks").glob("*.json"))] if (src / "picks").exists() else []
    impact = read_json(src / "impact.json") if (src / "impact.json").exists() else {}
    feature = read_json(src / "feature.json") if (src / "feature.json").exists() else None
    day_of = {c["pmcid"]: (c.get("index_date") or meta["index_date"])[:10] for c in meta["candidates"]}
    issues = collect.split_by_day(meta)
    written = []
    for iss in issues:
        day = iss["index_date"]
        dest = ISSUES_DIR / iss["issue_date"]
        # replace the directory (it may hold the empty issue made while Europe PMC was not indexing)
        if dest.exists():
            shutil.rmtree(dest)
        collect.write_issue(iss)
        mine = [p for p in picks if day_of.get(p["pmcid"]) == day]
        for section in ("new", "impact"):   # ranks count within the issue, in the old order
            group = sorted((p for p in mine if (p.get("section") == "impact") == (section == "impact")),
                           key=lambda p: p.get("rank", 99))
            for rank, p in enumerate(group, 1):
                write_json(dest / "picks" / f"{p['pmcid']}.json", {**p, "rank": rank})
        ids = {c["pmcid"] for c in iss["candidates"]}
        scored = [s for s in impact.get("scored", []) if s["pmcid"] in ids]
        write_json(dest / "impact.json", {"screened": iss["counts"]["impact_pool"], "scored": scored})
        if feature and feature["pmcid"] in {p["pmcid"] for p in mine} and len(mine) >= 3:
            keep = [c for c in feature.get("considered", []) if c["pmcid"] in {p["pmcid"] for p in mine}]
            write_json(dest / "feature.json", {**feature, "considered": keep})
        written.append(iss["issue_date"])
        print(f"{iss['issue_date']}: {len(iss['candidates'])} candidates, {len(mine)} articles", file=sys.stderr)
    return written


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print(__doc__)
        return 2
    split(argv[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
