"""Europe PMC: daily candidate search and full-text retrieval."""

from __future__ import annotations

import datetime as dt
import json
import statistics

from .common import cached, fetch

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"

STRUCTURE_TERMS = (
    '"cryo-EM" OR "cryoEM" OR "cryo-electron microscopy" OR "cryo-electron tomography" OR '
    '"subtomogram" OR "crystal structure" OR "X-ray crystallography" OR "crystallographic" OR '
    '"NMR structure" OR "solution structure"'
)
DEPOSITION_TERMS = (
    '"Protein Data Bank" OR "PDB" OR "wwPDB" OR "EMDB" OR "accession code" OR "accession codes"'
)
# Second search for 注目の構造 that are not new structures: prediction / design checked by experiment,
# structure-based drug discovery, mechanisms. Papers already found by the structure search are skipped.
IMPACT_TERMS = (
    '"AlphaFold" OR "RoseTTAFold" OR "ESMFold" OR "structure prediction" OR "protein design" OR '
    '"de novo design" OR "designed protein" OR "binder design" OR "ProteinMPNN" OR "RFdiffusion" OR '
    '"structure-based drug design" OR "structure-guided" OR "structure-based" OR '
    '"co-crystal structure" OR "cocrystal structure" OR "mechanism"'
)
# ... and only papers that work with experimentally determined structures (no MD- or prediction-only work).
EXP_STRUCTURE_TERMS = '"crystal structure" OR "cryo-EM" OR "cryo-electron microscopy" OR "NMR structure" OR "Protein Data Bank"'
BIO_TERMS = '"protein" OR "enzyme" OR "receptor" OR "antibody" OR "peptide" OR "RNA"'
# Article types that never report a new structure of their own.
EXCLUDED_PUB_TYPES = {"review", "systematic review", "editorial", "comment", "letter",
                      "erratum", "published erratum", "retraction of publication",
                      "retracted publication", "news", "correction"}


PUB_FLOOR_DAYS = 21  # a catch-up search ignores papers published longer ago than this before its first day


def _window(index_date: str, index_from: str | None) -> str:
    """FIRST_IDATE for one day, or for a catch-up window. A window also asks for recently published papers only, so a
    bulk re-index of old records (Europe PMC did one on 2026-10-13) cannot flood it."""
    if not index_from or index_from == index_date:
        return f"FIRST_IDATE:[{index_date} TO {index_date}]"
    floor = dt.date.fromisoformat(index_from) - dt.timedelta(days=PUB_FLOOR_DAYS)
    return f"FIRST_IDATE:[{index_from} TO {index_date}] AND FIRST_PDATE:[{floor} TO {index_date}]"


def build_query(index_date: str, index_from: str | None = None) -> str:
    return (f"({STRUCTURE_TERMS}) AND ({DEPOSITION_TERMS}) AND OPEN_ACCESS:y AND NOT SRC:PPR "
            f"AND {_window(index_date, index_from)}")


def build_impact_query(index_date: str, index_from: str | None = None) -> str:
    return (f"({IMPACT_TERMS}) AND ({EXP_STRUCTURE_TERMS}) AND ({BIO_TERMS}) AND OPEN_ACCESS:y AND NOT SRC:PPR "
            f"AND {_window(index_date, index_from)}")


# ---------- is Europe PMC taking in new papers? ----------
# From 2026-10-11 Europe PMC stopped indexing new open-access papers (about 1,000 a day before, a handful after) and,
# on 10-13, indexed 63,000 old records at once. A search for "yesterday" then finds nothing, which looks the same as a
# day without structure papers. health() tells the two apart by counting the open-access papers that were first indexed
# on the day AND published in the last two weeks ("fresh"), and comparing that with the previous two weeks.
HEALTH_DAYS = 14        # days of history for the baseline
STALL_FLOOR = 50        # fewer fresh open-access papers than this is a stall whatever the baseline
STALL_RATIO = 0.10      # ... and so is less than this share of the baseline (the median of the previous days)


def count(query: str) -> int | None:
    """Number of hits (no records fetched); None when Europe PMC does not answer."""
    try:
        d = json.loads(fetch(f"{BASE}/search", params={"query": query, "format": "json", "resultType": "lite",
                                                        "pageSize": 1}, retries=3))
        return d["hitCount"]
    except Exception:
        return None


def fresh_oa(day: dt.date) -> int | None:
    lo = day - dt.timedelta(days=14)
    return count(f"OPEN_ACCESS:y AND NOT SRC:PPR AND FIRST_IDATE:[{day} TO {day}] AND FIRST_PDATE:[{lo} TO {day}]")


def health(index_date: str) -> dict:
    """{"status": "ok" | "stalled" | "unknown", "fresh_oa", "baseline", "threshold", "days"}."""
    day = dt.date.fromisoformat(index_date)
    today = fresh_oa(day)
    history = [n for n in (fresh_oa(day - dt.timedelta(days=k)) for k in range(1, HEALTH_DAYS + 1)) if n is not None]
    baseline = statistics.median(history) if history else None
    out = {"fresh_oa": today, "baseline": baseline, "days": len(history)}
    if today is None:
        return {**out, "status": "unknown", "threshold": None}
    threshold = max(STALL_FLOOR, STALL_RATIO * (baseline or 0))
    return {**out, "threshold": round(threshold), "status": "stalled" if today < threshold else "ok"}


def search(query: str, page_size: int = 500) -> list[dict]:
    results, cursor = [], "*"
    while True:
        d = json.loads(fetch(f"{BASE}/search", params={
            "query": query, "format": "json", "resultType": "core",
            "pageSize": page_size, "cursorMark": cursor}))
        batch = d["resultList"]["result"]
        results += batch
        nxt = d.get("nextCursorMark")
        if not batch or not nxt or nxt == cursor:
            return results
        cursor = nxt


def metadata(r: dict) -> dict:
    journal = r.get("journalInfo", {}).get("journal", {})
    pub_types = [t.lower() for t in r.get("pubTypeList", {}).get("pubType", [])]
    return {
        "pmcid": r.get("pmcid"),
        "pmid": r.get("pmid"),
        "doi": (r.get("doi") or "").lower() or None,
        "title": r.get("title", "").strip(),
        "journal": journal.get("title"),
        "journal_abbrev": journal.get("isoabbreviation") or journal.get("medlineAbbreviation"),
        "authors": r.get("authorString", ""),
        "pub_date": r.get("firstPublicationDate"),
        "index_date": r.get("firstIndexDate"),
        "license": r.get("license"),
        "pub_types": pub_types,
        "abstract": r.get("abstractText", ""),
    }


def is_excluded_type(meta: dict) -> bool:
    return any(t in EXCLUDED_PUB_TYPES for t in meta["pub_types"]) and \
        "research-article" not in meta["pub_types"]


def fulltext_xml(pmcid: str) -> str | None:
    path = cached("fulltext", pmcid, ".xml")
    if path.exists():
        return path.read_text(encoding="utf-8")
    body = fetch(f"{BASE}/{pmcid}/fullTextXML", ok_404=True)
    if body is None:
        return None
    text = body.decode("utf-8", "replace")
    path.write_text(text, encoding="utf-8")
    return text
