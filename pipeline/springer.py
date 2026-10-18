"""Springer Nature's open-access papers the day after they come out, instead of a month later through PMC.

Why: papers of the Nature journals reach Europe PMC / PMC long after they are published (from publication to the database,
median 51 days for Nature Communications and 38 for Nature Structural & Molecular Biology among the papers written up
2026-10-07..18), while Science, eLife or Nucleic Acids Research arrive in days.

How:
  1. Crossref lists each journal's articles published online on the day (licence and DOI included);
  2. for the open-access ones (a Creative Commons licence), the Springer Nature Open Access API gives the full text as JATS
     XML, the format PMC uses, so `jats.parse` and the rest of the pipeline read it unchanged;
  3. the same words as the Europe PMC query (structure x deposition; or, for Structure-Based Study, impact x experimental
     structure x biology) decide which papers become candidates.

The API key is read from the environment variable SPRINGER_NATURE_OA_API_KEY (set in the cloud environment's settings; it is
never written into the repository). Without it, or when the API fails, the route is skipped and the day is not marked as
covered, so those papers still come later through PMC.

A paper has no PMCID yet, so its id (kept under the key "pmcid", which names the paper everywhere in the pipeline) is "SN-" +
the article number of its DOI: 10.1038/s41467-026-77727-2 -> SN-s41467-026-77727-2. When PMC delivers the same paper a month
later, `collect` drops it by DOI (and drops any paper of these journals published on a day this route covered).

    python3 -m pipeline.springer --probe 10.1038/s41467-026-77727-2     # fetch one paper and show what was read
    python3 -m pipeline.springer --day 2026-10-17                         # list the day's candidates (no files written)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET

from . import europepmc, jats, pmcoa
from .common import cached, fetch

SOURCE = "springer-nature"
KEY_ENV = "SPRINGER_NATURE_OA_API_KEY"
CROSSREF = "https://api.crossref.org"
OA_API = "https://api.springernature.com/openaccess/jats"
PAUSE = 1.0          # seconds between API calls (be gentle; the free plan has a daily allowance)
MAX_PER_DAY = 150    # cap on full texts fetched for one publication day

# The journals read this way, by the journal code in their DOIs (10.1038/<code>-...) and the online ISSN Crossref knows.
# Scientific Reports (about 150 papers a day, seldom a structure paper) stays on the PMC route.
JOURNALS = {
    "s41467": ("2041-1723", "Nature Communications"),
    "s41594": ("1545-9985", "Nature Structural & Molecular Biology"),
    "s42003": ("2399-3642", "Communications Biology"),
    "s41586": ("1476-4687", "Nature"),
    "s41589": ("1552-4469", "Nature Chemical Biology"),
    "s42004": ("2399-3669", "Communications Chemistry"),
    "s41421": ("2056-5968", "Cell Discovery"),
    "s41422": ("1748-7838", "Cell Research"),
    "s41564": ("2058-5276", "Nature Microbiology"),
    "s41592": ("1548-7105", "Nature Methods"),
    "s41557": ("1755-4349", "Nature Chemistry"),
    "s41392": ("2059-3635", "Signal Transduction and Targeted Therapy"),
    "s44318": ("1460-2075", "The EMBO Journal"),
    "s44319": ("1469-3178", "EMBO Reports"),
}


def key() -> str | None:
    return os.environ.get(KEY_ENV) or None


def journal_code(doi: str | None) -> str | None:
    m = re.match(r"10\.1038/(s4\d{4})-", (doi or "").lower())
    return m.group(1) if m and m.group(1) in JOURNALS else None


def paper_id(doi: str) -> str:
    return "SN-" + doi.lower().split("/", 1)[1]


def licence(item: dict) -> str | None:
    """'cc by', 'cc by-nc-nd', ... from the Crossref licence URLs (the form Europe PMC uses), or None if not open access."""
    for lic in item.get("license") or []:
        m = re.search(r"creativecommons\.org/licenses/([a-z-]+)/", lic.get("URL", ""))
        if m:
            return "cc " + m.group(1)
        if "creativecommons.org/publicdomain/zero" in lic.get("URL", ""):
            return "cc0"
    return None


def published(day: str) -> tuple[list[dict], bool]:
    """Crossref: every journal article of JOURNALS published online on `day`, with its DOI, title and licence; and
    whether every journal could be listed (one that fails does not stop the others, but the day is then incomplete)."""
    out, complete = [], True
    for code, (issn, name) in JOURNALS.items():
        q = {"filter": f"from-online-pub-date:{day},until-online-pub-date:{day},type:journal-article", "rows": 500,
             "select": "DOI,title,container-title,short-container-title,published-online,license,abstract"}
        try:
            d = json.loads(fetch(f"{CROSSREF}/journals/{issn}/works", params=q, retries=3))["message"]
        except Exception as e:
            print(f"  Crossref failed for {name}: {e}", file=sys.stderr)
            complete = False
            continue
        for it in d.get("items", []):
            doi = (it.get("DOI") or "").lower()
            if journal_code(doi) != code:
                continue
            out.append({"doi": doi, "code": code, "journal": name, "license": licence(it),
                        "short": (it.get("short-container-title") or [None])[0],
                        "title": (it.get("title") or [""])[0], "pub_date": day})
    return out, complete


def fulltext(doi: str, api_key: str) -> str | None:
    """The article's JATS XML from the Open Access API (cached), or None when the API has no full text for it yet.

    Right after publication the API may hold only the accepted manuscript's record: metadata and authors, no <body>
    (seen 2026-10-18 for papers published two days before). That is not full text: it is neither cached nor returned,
    so the paper is tried again on a later run and, until then, still counts as not read."""
    path = cached("fulltext", paper_id(doi), ".xml")
    if path.exists():
        return path.read_text(encoding="utf-8")
    body = fetch(OA_API, params={"q": f"doi:{doi}", "api_key": api_key}, retries=3).decode("utf-8", "replace")
    root = ET.fromstring(body)
    art = next((e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "article"), None)
    if art is None or not any(e.tag.rsplit("}", 1)[-1] == "body" for e in art.iter()):
        return None
    xml = ET.tostring(art, encoding="unicode")
    path.write_text(xml, encoding="utf-8")
    return xml


def meta(item: dict, xml: str) -> dict:
    """The fields europepmc.metadata() gives, from Crossref and the JATS."""
    root = ET.fromstring(xml)

    def text(el):
        return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""

    authors = []
    for c in root.findall(".//article-meta/contrib-group/contrib[@contrib-type='author']")[:30]:
        sur, giv = text(c.find("name/surname")), text(c.find("name/given-names"))
        if sur:
            initials = "".join(w[0] for w in re.split(r"[\s.-]+", giv) if w)
            authors.append(f"{sur} {initials}".strip())
    kind = (root.get("article-type") or "research-article").lower()
    abbrev = root.find(".//journal-meta/journal-id[@journal-id-type='iso-abbrev']")
    return {
        "pmcid": paper_id(item["doi"]), "pmid": None, "doi": item["doi"],
        "title": text(root.find(".//article-meta/title-group/article-title")) or item["title"],
        "journal": text(root.find(".//journal-meta//journal-title")) or item["journal"],
        "journal_abbrev": text(abbrev) or item["short"] or item["journal"],
        "authors": ", ".join(authors), "pub_date": item["pub_date"], "index_date": item["pub_date"],
        "license": item["license"],
        "pub_types": [pmcoa.JATS_TYPES.get(kind, kind)] if kind != "research-article" else ["research-article"],
        "abstract": text(root.find(".//article-meta/abstract")), "route": SOURCE,
    }


def screen_text(parsed: dict) -> str:
    """The text the query words are looked for in: abstract, data availability, body, captions."""
    return "\n".join([parsed["abstract"], parsed["data_availability"], *(s["text"] for s in parsed["sections"]),
                      *parsed["figure_captions"]])


def discover(days: list[str], skip_dois: set[str]) -> dict:
    """The candidates of the publication days, shaped like the Europe PMC ones:
    {"days": the days fully read, "read": {day: [DOIs read]}, "main": [(meta, parsed)], "impact": [(meta, parsed)]}.

    main: the paper has the structure and deposition words (the main query); impact: the 注目の構造 words instead.
    A day counts as covered only when every open-access paper of it was read."""
    api_key = key()
    if not api_key:
        print(f"Springer Nature: {KEY_ENV} is not set; papers of the Nature journals come through PMC as before",
              file=sys.stderr)
        return {"days": [], "read": {}, "main": [], "impact": []}
    out = {"days": [], "read": {}, "main": [], "impact": []}
    for day in days:
        items, complete = published(day)
        oa = [i for i in items if i["license"] and i["doi"] not in skip_dois]
        if len(oa) > MAX_PER_DAY:
            oa, complete = oa[:MAX_PER_DAY], False
        for it in oa:
            try:
                xml = fulltext(it["doi"], api_key)
            except urllib.error.HTTPError as e:
                print(f"  Springer Nature API refused {it['doi']}: HTTP {e.code}", file=sys.stderr)
                complete = False
                if e.code in (401, 403):    # the key is wrong or has run out for today: no use trying the rest
                    break
                continue
            except (urllib.error.URLError, ET.ParseError, TimeoutError, ConnectionError) as e:
                print(f"  Springer Nature API failed for {it['doi']}: {e}", file=sys.stderr)
                complete = False
                continue
            finally:
                time.sleep(PAUSE)
            if not xml:
                continue
            out["read"].setdefault(day, []).append(it["doi"])
            try:
                m, parsed = meta(it, xml), jats.parse(xml)
            except Exception as e:      # malformed XML should not stop the day
                print(f"  parse failed for {it['doi']}: {e}", file=sys.stderr)
                continue
            if europepmc.is_excluded_type(m):
                continue
            body = screen_text(parsed)
            strong = {x.group(0).lower() for x in pmcoa.IMPACT.finditer(body)}
            if pmcoa.STRUCTURE.search(body) and pmcoa.DEPOSITION.search(body):
                out["main"].append((m, parsed))
            elif strong and pmcoa.EXPERIMENTAL.search(body) and pmcoa.BIO.search(body):
                m["source"] = "impact_search"
                out["impact"].append((m, parsed))
        if complete:
            out["days"].append(day)
        print(f"Springer Nature {day}: {len(items)} articles in {len(JOURNALS)} journals, {len(oa)} open access read"
              f"{'' if complete else ' (some failed: the day is not marked covered)'}", file=sys.stderr)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", metavar="DOI", help="fetch one paper's full text and show what was read")
    ap.add_argument("--day", help="list one publication day's candidates (nothing is written but the full-text cache)")
    ap.add_argument("--coverage", nargs="+", metavar="DAY",
                    help="for each publication day, how many open-access papers already have full text in the API")
    ap.add_argument("--sample", type=int, default=8, help="papers tried per day with --coverage (default 8)")
    a = ap.parse_args(argv)
    if a.probe:
        if not key():
            raise SystemExit(f"{KEY_ENV} is not set")
        doi = a.probe.lower()
        xml = fulltext(doi, key())
        if not xml:
            raise SystemExit(f"no full text for {doi}")
        p = jats.parse(xml)
        body = screen_text(p)
        print(json.dumps({"id": paper_id(doi), "title": p["title"], "journal": p["journal"], "sections": len(p["sections"]),
                          "screen": {"structure_words": sorted({x.group(0).lower() for x in pmcoa.STRUCTURE.finditer(body)})[:8],
                                     "deposition_words": sorted({x.group(0).lower() for x in pmcoa.DEPOSITION.finditer(body)})[:8]},
                          "section_titles": [s["title"] for s in p["sections"]][:40],
                          "root": ET.fromstring(xml).tag, "xml_chars": len(xml),
                          "chars": sum(len(s["text"]) for s in p["sections"]), "pdb_ids": p["pdb_ids"][:12],
                          "emdb_ids": p["emdb_ids"][:6], "deposition_statement": p["deposition_statement"],
                          "data_availability": p["data_availability"][:300]}, ensure_ascii=False, indent=2))
    elif a.coverage:
        if not key():
            raise SystemExit(f"{KEY_ENV} is not set")
        for day in a.coverage:
            items, _ = published(day)
            oa = [i for i in items if i["license"]][:a.sample]
            full = 0
            for it in oa:
                try:
                    full += fulltext(it["doi"], key()) is not None
                except Exception as e:
                    print(f"  {it['doi']}: {e}", file=sys.stderr)
                time.sleep(PAUSE)
            print(f"{day}: {full} of {len(oa)} open-access papers have full text in the API "
                  f"({len([i for i in items if i['license']])} open access that day)")
    elif a.day:
        r = discover([a.day], set())
        for kind in ("main", "impact"):
            for m, p in r[kind]:
                print(f"{kind:6s} {m['pmcid']:28s} {m['journal_abbrev'][:22]:22s} PDB {','.join(p['pdb_ids'][:4]) or '-':20s} {m['title'][:70]}")
        print(f"covered days: {r['days']}  read: {sum(map(len, r['read'].values()))}  structure: {len(r['main'])}  "
              f"impact: {len(r['impact'])}")


if __name__ == "__main__":
    main()
