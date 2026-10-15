"""Collect one day's candidate papers and the facts the article writer needs.

    python3 -m pipeline.collect                      # index date = yesterday (UTC), issue date = index date + 1
    python3 -m pipeline.collect --index-date 2026-10-06 --issue-date 2026-10-07
    python3 -m pipeline.collect --catch-up           # from the day after the last issue that had papers, up to yesterday
    python3 -m pipeline.collect --index-from 2026-10-11 --index-date 2026-10-16   # an explicit window

Before searching it asks whether Europe PMC is taking in new papers at all (europepmc.health). If it is not, nothing is
written and the exit code is 3: an empty search on such a day says nothing about structure papers, so there is no issue
to make. --force collects anyway. A window (--catch-up / --index-from) also skips papers already listed in an earlier
issue, so papers Europe PMC indexes late are not lost and not repeated.

Writes:
    data/issues/<issue>/candidates.json   facts for every candidate (committed)
    data/issues/<issue>/candidates.md     overview for choosing the picks (committed)
    .cache/packets/<PMCID>.md             full text for writing and for checking quotes (not committed)
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from . import europepmc, impact, jats, pdb, uniprot
from .common import ISSUES_DIR, cached, default_index_date, issue_date_for, issue_dir, write_json

MAX_FULLTEXT = 120  # cap on papers whose full text is fetched on a busy day
MAX_IMPACT_FULLTEXT = 25  # extra papers from the impact search, best pre-screen scores first
KINDS = ("new_structure", "new_structure_ids_unlisted", "new_map_only", "uses_existing", "no_ids")
NEW_KINDS = KINDS[:3]


def _own_reason(e: dict, meta: dict, parsed: dict, pub: dt.date | None) -> str | None:
    """Why a released entry counts as deposited by this paper, or None when it is only cited."""
    if (meta["doi"] and e["citation_doi"] == meta["doi"]) or (meta["pmid"] and e["citation_pmid"] == meta["pmid"]):
        return "PDB primary citation matches this paper"
    in_data = e["pdb_id"] in parsed["pdb_ids_in_deposition"]
    if (e["citation_doi"] or "").startswith(jats.PREPRINT_DOI_PREFIXES) and in_data:
        return "PDB cites the preprint of this paper; listed in its deposition statement"
    deposited = dt.date.fromisoformat(e["deposit_date"]) if e.get("deposit_date") else None
    if not e["citation_doi"] and not e["citation_pmid"] and in_data and pub and deposited and \
            deposited >= pub - dt.timedelta(days=4 * 365):
        return "entry without a published citation, listed in the deposition statement"
    return None


def classify(meta: dict, parsed: dict, released: dict, holdings: dict) -> dict:
    """Decide which PDB entries the paper deposited itself, and whether it reports a new structure."""
    own, cited = [], []
    pub = dt.date.fromisoformat(meta["pub_date"]) if meta.get("pub_date") else None
    for pid in parsed["pdb_ids"]:
        e = released.get(pid)
        if e:
            reason = _own_reason(e, meta, parsed, pub)
            if reason:
                own.append({**e, "evidence": reason})
            else:
                cited.append(pid)
        elif holdings.get(pid) == "unreleased":
            own.append({"pdb_id": pid, "status": "unreleased",
                        "evidence": "cited by the paper but not yet released (on hold until publication)"})
    if own:
        kind = "new_structure"
    elif parsed["deposition_statement"]:
        # Deposited, but the codes are only in a supplementary table (or a map-only EMDB deposition).
        kind = "new_structure_ids_unlisted" if not parsed["emdb_ids"] else "new_map_only"
    elif cited:
        kind = "uses_existing"
    else:
        kind = "no_ids"
    return {"kind": kind, "own_structures": own, "cited_pdb_ids": cited}


def first_structure_flags(proteins: dict[str, dict], own_ids: set[str]) -> None:
    def check(acc):
        prior = pdb.structures_for_uniprot(acc) - own_ids
        return acc, len(prior)
    with ThreadPoolExecutor(8) as ex:
        for acc, n_prior in ex.map(check, list(proteins)):
            proteins[acc]["prior_pdb_count"] = n_prior
            proteins[acc]["first_structure"] = n_prior == 0


def score(c: dict) -> int:
    s = {"new_structure": 50, "new_structure_ids_unlisted": 40, "new_map_only": 35,
         "uses_existing": 5, "no_ids": 0}[c["kind"]]
    if any(p.get("first_structure") for p in c["proteins"]):
        s += 30
    res = [x["resolution"] for x in c["own_structures"] if x.get("resolution")]
    if res and min(res) <= 2.5:
        s += 5
    s += min(len(c["own_structures"]), 5)
    if any(x.get("ligands") for x in c["own_structures"]):
        s += 5
    return s


MIN_PARSED_COVERED = 10  # an older issue (written before health was recorded) counts as having covered its day from this many papers
MAX_WINDOW_DAYS = 14


def _issues() -> list[dict]:
    out = []
    for f in sorted(ISSUES_DIR.glob("*/candidates.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return out


def covered(meta: dict) -> bool:
    """Did this issue really cover its day? The ones made while Europe PMC was not indexing (hits 0, 1, 1 on
    2026-10-12..14) did not."""
    h = meta.get("health") or {}
    if h.get("status") in ("ok", "stalled"):
        return h["status"] == "ok"
    return (meta.get("counts") or {}).get("parsed", 0) >= MIN_PARSED_COVERED


def catch_up_start(end: dt.date) -> dt.date:
    """The day after the last index date that an issue covered (at most MAX_WINDOW_DAYS back; the end day when none)."""
    done = [dt.date.fromisoformat(m["index_date"]) for m in _issues()
            if covered(m) and dt.date.fromisoformat(m["index_date"]) < end]
    start = max(done) + dt.timedelta(days=1) if done else end
    return max(start, end - dt.timedelta(days=MAX_WINDOW_DAYS - 1))


def already_listed(except_issue: str) -> set[str]:
    """PMCIDs that an earlier issue already considered (they are not offered again by a catch-up window)."""
    return {c["pmcid"] for m in _issues() if m["issue_date"] != except_issue for c in m["candidates"]}


def run(index_date: str, issue_date: str, index_from: str | None = None, force: bool = False) -> dict:
    windowed = bool(index_from) and index_from != index_date
    health = europepmc.health(index_date)
    print(f"Europe PMC health for {index_date}: {health['status']} (new open-access papers that day {health['fresh_oa']}, "
          f"usual {health['baseline']}, stall below {health['threshold']})", file=sys.stderr)
    if health["status"] == "stalled" and not force:
        print(f"STALLED: Europe PMC indexed almost no new open-access papers on {index_date}. A search would find nothing "
              f"whatever the structure papers of the day were, so no issue is written (exit code 3). Run again once it "
              f"recovers with --catch-up; --force collects anyway.", file=sys.stderr)
        raise SystemExit(3)
    query = europepmc.build_query(index_date, index_from if windowed else None)
    raw = europepmc.search(query)
    if windowed:
        listed = already_listed(issue_date)
        before = len(raw)
        raw = [r for r in raw if r.get("pmcid") not in listed]
        print(f"Window {index_from}..{index_date}: {before} hits, {before - len(raw)} already in an earlier issue",
              file=sys.stderr)
    print(f"Europe PMC: {len(raw)} hits for index date {index_from + '..' if windowed else ''}{index_date}", file=sys.stderr)

    metas, excluded = [], []
    for r in raw:
        m = europepmc.metadata(r)
        if not m["pmcid"]:
            continue
        (excluded if europepmc.is_excluded_type(m) else metas).append(m)
    days = (dt.date.fromisoformat(index_date) - dt.date.fromisoformat(index_from)).days + 1 if windowed else 1
    metas = metas[:MAX_FULLTEXT * min(days, 3)]

    def load(m):
        xml = europepmc.fulltext_xml(m["pmcid"])
        if not xml:
            return m, None
        try:
            return m, jats.parse(xml)
        except Exception as e:  # malformed XML should not stop the day's run
            print(f"  parse failed for {m['pmcid']}: {e}", file=sys.stderr)
            return m, None

    with ThreadPoolExecutor(6) as ex:
        loaded = [x for x in ex.map(load, metas) if x[1] is not None]
    print(f"Full text parsed: {len(loaded)}", file=sys.stderr)

    # 注目の構造: a second search beyond the structure terms, pre-screened on title and abstract.
    impact_query = europepmc.build_impact_query(index_date, index_from if windowed else None)
    raw2 = europepmc.search(impact_query)
    seen = {m["pmcid"] for m in metas}
    if windowed:
        seen |= listed
    extra = []
    for r in raw2:
        m = europepmc.metadata(r)
        if m["pmcid"] and m["pmcid"] not in seen and not europepmc.is_excluded_type(m):
            seen.add(m["pmcid"])
            m["source"] = "impact_search"
            m["prescreen"] = impact.prescreen(m)
            extra.append(m)
    extra = sorted((m for m in extra if m["prescreen"]["score"] >= 1),
                   key=lambda m: -m["prescreen"]["score"])[:MAX_IMPACT_FULLTEXT]
    with ThreadPoolExecutor(6) as ex:
        loaded_extra = [x for x in ex.map(load, extra) if x[1] is not None]
    for m, _ in loaded_extra:
        del m["prescreen"]
    loaded += loaded_extra
    print(f"Impact search: {len(raw2)} hits, {len(loaded_extra)} more papers parsed", file=sys.stderr)

    all_ids = sorted({pid for _, p in loaded for pid in p["pdb_ids"]})
    released = pdb.entries(all_ids)
    missing = [pid for pid in all_ids if pid not in released]
    with ThreadPoolExecutor(8) as ex:
        holdings = dict(zip(missing, ex.map(pdb.holding_status, missing)))
    print(f"PDB IDs: {len(all_ids)} found, {len(released)} released, "
          f"{sum(v == 'unreleased' for v in holdings.values())} unreleased", file=sys.stderr)

    candidates = []
    for m, p in loaded:
        m["doi"] = m["doi"] or p["doi"]
        m["pmid"] = m["pmid"] or p["pmid"]
        m["journal"] = p["journal"] or m["journal"]  # publisher's capitalisation
        m["title"] = p["title"] or html.unescape(m["title"])  # Europe PMC titles carry escaped markup
        c = {**m, **classify(m, p, released, holdings),
             "emdb_ids": p["emdb_ids"], "abstract": p["abstract"] or m["abstract"]}
        if c["kind"] not in NEW_KINDS:
            # Facts for a possible 注目の構造 article: the structures it used, and the pre-screen hints.
            c["cited_structures"] = [released[pid] for pid in c["cited_pdb_ids"] if pid in released][:10]
            c["impact"] = impact.prescreen(c)
        candidates.append(c)
        cached("packets", m["pmcid"], ".md").write_text(jats.packet_text(p), encoding="utf-8")

    def structures(c):
        return c["own_structures"] + c.get("cited_structures", [])

    own_accs = {a for c in candidates for s in c["own_structures"]
                for poly in s.get("polymers", []) for a in poly["uniprot_ids"]}
    accs = sorted(own_accs | {a for c in candidates for s in c.get("cited_structures", [])
                              for poly in s.get("polymers", []) for a in poly["uniprot_ids"]})
    proteins = uniprot.lookup(accs) if accs else {}
    own_ids = {s["pdb_id"] for c in candidates for s in c["own_structures"]}
    first_structure_flags({a: v for a, v in proteins.items() if a in own_accs}, own_ids)
    for c in candidates:
        c_accs = sorted({a for s in structures(c) for poly in s.get("polymers", [])
                         for a in poly["uniprot_ids"]})
        c["proteins"] = [proteins[a] for a in c_accs if a in proteins]
        c["score"] = score(c)
    candidates.sort(key=lambda c: (-c["score"], c["title"]))

    counts = {
        "hits": len(raw), "excluded_types": len(excluded), "parsed": len(loaded),
        **{k: sum(c["kind"] == k for c in candidates)
           for k in KINDS},
        "impact_search_hits": len(raw2), "impact_search_parsed": len(loaded_extra),
        "impact_pool": sum(c["kind"] not in NEW_KINDS for c in candidates),
    }
    out = {"issue_date": issue_date, "index_date": index_date, **({"index_from": index_from} if windowed else {}),
           "health": health, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "query": query, "impact_query": impact_query, "counts": counts, "candidates": candidates}
    d = issue_dir(issue_date)
    write_json(d / "candidates.json", out)
    (d / "candidates.md").write_text(overview_md(out), encoding="utf-8")
    print(f"Wrote {d / 'candidates.json'}: {counts}", file=sys.stderr)
    return out


def structure_line(s: dict) -> str:
    if s["status"] != "released":
        return f"{s['pdb_id']}（未公開）"
    res = f" {s['resolution']:.2f} Å" if s.get("resolution") else ""
    lig = f" / リガンド: {', '.join(l['id'] for l in s['ligands'][:5])}" if s.get("ligands") else ""
    return f"{s['pdb_id']}（{s['method']}{res}{lig}）"


def other_ids(x: dict) -> str:
    if x["emdb_ids"]:
        return "EMDB: " + ", ".join(x["emdb_ids"][:4])
    return "寄託の記載あり（ID は補足資料のみ）"


def overview_md(out: dict) -> str:
    c = out["counts"]
    span = f"{out['index_from']}〜{out['index_date']}" if out.get("index_from") else out["index_date"]
    lines = [f"# 候補一覧 {out['issue_date']}（Europe PMC 索引日 {span}）", "",
             f"検索ヒット {c['hits']} 件 → 本文解析 {c['parsed']} 件 → 新しい構造 {c['new_structure']} 件"
             f"（ID が補足資料のみ {c['new_structure_ids_unlisted']} 件、マップのみ {c['new_map_only']} 件、"
             f"既存構造の利用 {c['uses_existing']} 件、ID なし {c['no_ids']} 件）", ""]
    for i, x in enumerate(out["candidates"], 1):
        if x["kind"] not in NEW_KINDS:
            continue
        prots = ", ".join(f"{p.get('gene') or p['uniprot']}"
                          f"{'【初構造】' if p.get('first_structure') else ''}" for p in x["proteins"][:8])
        if len(x["proteins"]) > 8:
            prots += f" ほか {len(x['proteins']) - 8} 件"
        lines += [f"## {i}. {x['title']}",
                  f"- {x['pmcid']} / {x['journal_abbrev']} / 公開 {x['pub_date']} / {x['license'] or 'ライセンス不明'} / score {x['score']}",
                  f"- 構造: {'; '.join(structure_line(s) for s in x['own_structures']) or other_ids(x)}",
                  f"- タンパク質: {prots or '（UniProt 未対応）'}",
                  "", (x["abstract"] or "")[:900], ""]
    others = sorted((x for x in out["candidates"] if x["kind"] not in NEW_KINDS),
                    key=lambda x: (-x["impact"]["score"], x["title"]))
    if others:
        lines += ["# 注目の構造の候補（新しい構造以外）", "",
                  f"事前スクリーニングの点数順（キーワードによる目安で、選定ではない）。上位から読み、"
                  f"`pipeline/impact.py` の基準で最大 {impact.SHORTLIST} 本を採点する。", ""]
        for i, x in enumerate(others, 1):
            im = x["impact"]
            types = "・".join(impact.TYPES[t] for t in im["types"]) or "種類の手がかりなし"
            ids = ", ".join(s["pdb_id"] for s in x.get("cited_structures", [])[:5])
            head = (f"- **{x['pmcid']}** [{im['score']}] {x['title']}（{x['journal_abbrev'] or x['journal']}）"
                    f" — {types}" + (f" / 使用 PDB: {ids}" if ids else "") +
                    ("".join(f" / ⚠ {f}" for f in im["flags"])))
            lines.append(head)
            if i <= 30:
                lines.append(f"  - {(x['abstract'] or '')[:500]}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index-date", default=str(default_index_date()), help="the last index day (default: yesterday, UTC)")
    ap.add_argument("--index-from", help="first index day of a window (papers already in an earlier issue are skipped)")
    ap.add_argument("--catch-up", action="store_true",
                    help="window from the day after the last issue that covered its day, up to --index-date")
    ap.add_argument("--issue-date", help="default: index date + 1")
    ap.add_argument("--force", action="store_true", help="collect even when Europe PMC is not indexing new papers")
    a = ap.parse_args(argv)
    end = dt.date.fromisoformat(a.index_date)
    start = a.index_from or (str(catch_up_start(end)) if a.catch_up else None)
    issue = a.issue_date or str(issue_date_for(end))
    run(a.index_date, issue, start, a.force)


if __name__ == "__main__":
    main()
