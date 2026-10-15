"""PubMed Central's own Open Access dataset on AWS, as a stand-in for Europe PMC when Europe PMC is not indexing.

Why: from 2026-10-11 Europe PMC stopped taking in new open-access papers while PMC itself kept publishing them
(8,554 new ones by 10-15). NCBI's own servers (PubMed, E-utilities, PMC) cannot be reached from the cloud
environment, but NLM's open-data bucket can, and it holds, per article, the metadata (JSON), the full text (JATS XML
and plain text) and the licence:

    https://pmc-oa-opendata.s3.amazonaws.com/metadata/PMC<id>.<version>.json    (the listing carries the date it was written)
    https://pmc-oa-opendata.s3.amazonaws.com/PMC<id>.<version>/PMC<id>.<version>.{xml,txt}

There is no search, so: list the articles written in the window (found by bisecting the ID space on the listing's dates),
read each one's plain text and keep those that match the same words the Europe PMC query uses, then fetch the XML of the
few that remain. Used by `pipeline.collect` only when Europe PMC's health check says "stalled"; an issue made this way
says so (meta "source": "pmc-aws") and the site credits NLM on it. Terms of use: acknowledge NLM as the source; only
distribute data licensed for redistribution.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from . import europepmc
from .common import CACHE_DIR, cached, fetch

BASE = "https://pmc-oa-opendata.s3.amazonaws.com"
SOURCE = "pmc-aws"
CREDIT = ("この号の論文情報と全文は、米国国立医学図書館（NLM）の PMC オープンアクセスデータセット"
          "（AWS Open Data）から取得した。")
ID_FLOOR, ID_CEIL = 10_000_000, 99_999_999   # 8-digit PMC IDs: the ones being issued now
PUB_FLOOR_DAYS = 180    # papers published longer ago than this before the window are left out
ID_MARGIN = 6_000        # IDs are issued roughly in the order of arrival; list this far before the bisected start
IMPACT_CAP = 80          # impact-search candidates whose XML is read (best text score first)
WORKERS = 8

# Words of the Europe PMC queries (pipeline/europepmc.py), as regular expressions over the plain text.
def _terms(terms: str) -> re.Pattern:
    parts = []
    for t in re.findall(r'"([^"]+)"', terms):
        parts.append(re.escape(t).replace(r"\-", r"[-\s]?").replace(r"\ ", r"\s+"))
    # Europe PMC stems ("crystal structures" finds "crystal structure"): allow a plural ending
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(parts) + r")(?:e?s)?(?![A-Za-z0-9])", re.I)


STRUCTURE, DEPOSITION = _terms(europepmc.STRUCTURE_TERMS), _terms(europepmc.DEPOSITION_TERMS)
IMPACT, EXPERIMENTAL, BIO = (_terms(europepmc.IMPACT_TERMS), _terms(europepmc.EXP_STRUCTURE_TERMS),
                             _terms(europepmc.BIO_TERMS))
WEAK_IMPACT = {"structure-based", "mechanism"}   # nearly every paper says these; they do not rank
JATS_TYPES = {"review-article": "review", "editorial": "editorial", "letter": "letter", "correction": "correction",
              "retraction": "retraction of publication", "news": "news", "article-commentary": "comment",
              "expression-of-concern": "comment"}


def _list(prefix: str, start_after: str | None = None, token: str | None = None, max_keys: int = 1000) -> tuple[list, str | None]:
    """One page of the metadata listing: ([(8-digit ID, version, date written)], continuation token or None)."""
    q = {"list-type": "2", "prefix": prefix, "max-keys": str(max_keys)}
    if token:
        q["continuation-token"] = token
    elif start_after:
        q["start-after"] = start_after
    body = fetch(f"{BASE}/?{urllib.parse.urlencode(q)}", retries=3).decode()
    items = []
    for m in re.finditer(r"<Key>metadata/PMC(\d+)\.(\d+)\.json</Key><LastModified>([^<]+)</LastModified>", body):
        if len(m.group(1)) == 8:      # the 7-digit and shorter IDs are old articles
            items.append((int(m.group(1)), int(m.group(2)), m.group(3)[:10]))
    nxt = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", body)
    return items, (nxt.group(1) if nxt else None)


def _date_at(n: int) -> dt.date | None:
    """When the articles around ID n were written (median of a small sample); None when there are none after n."""
    items, _ = _list("metadata/PMC", f"metadata/PMC{n:08d}.1.json", max_keys=40)
    days = sorted(i[2] for i in items)
    return dt.date.fromisoformat(days[len(days) // 2]) if len(days) >= 5 else None


def first_id(since: dt.date) -> int:
    """About the first ID written on `since`, by bisection on the dates (IDs follow arrival order roughly), less a margin."""
    lo, hi = ID_FLOOR, ID_CEIL
    while hi - lo > 2_000:
        mid = (lo + hi) // 2
        d = _date_at(mid)
        if d is None or d >= since:       # nothing after mid yet, or already at/after the start
            hi = mid
        else:
            lo = mid
    return max(ID_FLOOR, lo - ID_MARGIN)


def listing(index_from: str, index_date: str) -> list[dict]:
    """Articles whose metadata was written on a day of the window: [{"pmcid", "version", "day"}].

    Listed from the bisected start, one million-ID block at a time (prefix "PMC13", then "PMC14" ...: a prefix bounds
    the listing, which would otherwise run through every older article in the bucket)."""
    a, b = dt.date.fromisoformat(index_from), dt.date.fromisoformat(index_date)
    start = first_id(a)
    out: dict[int, dict] = {}
    block, first = start // 1_000_000, True
    while block < 100:
        prefix, seen, token = f"metadata/PMC{block}", 0, None
        while True:
            items, token = _list(prefix, f"metadata/PMC{start:08d}.1.json" if first else None, token)
            seen += len(items)
            for n, v, day in items:
                if a <= dt.date.fromisoformat(day) <= b:
                    out[n] = {"pmcid": f"PMC{n}", "version": v, "day": day}   # a later version replaces an earlier one
            if not token:
                break
        if seen == 0:        # nothing issued in this block (yet): the end
            break
        block, first = block + 1, False
    return sorted(out.values(), key=lambda x: x["pmcid"])


def _flags(item: dict) -> dict:
    """Which of the Europe PMC query words the article's plain text has (cached: the text is 60 KB a paper)."""
    path = CACHE_DIR / "pmcoa" / f"{item['pmcid']}.{item['version']}.json"
    if path.exists():
        return json.loads(path.read_text())
    pid = f"{item['pmcid']}.{item['version']}"
    try:
        text = fetch(f"{BASE}/{pid}/{pid}.txt", retries=4).decode("utf-8", "replace")
    except Exception as e:      # one unreadable article must not stop the day's run (not cached: tried again next time)
        print(f"  could not read {pid}: {e}", file=sys.stderr)
        return {"structure": False, "impact": False, "rank": 0, "failed": True}
    strong = {m.group(0).lower() for m in IMPACT.finditer(text)}
    out = {"structure": bool(STRUCTURE.search(text) and DEPOSITION.search(text)),
           "impact": bool(strong and EXPERIMENTAL.search(text) and BIO.search(text)),
           "rank": sum(3 for t in strong if t not in WEAK_IMPACT) + sum(1 for t in strong if t in WEAK_IMPACT)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def _meta(item: dict, xml: str) -> dict:
    """The fields europepmc.metadata() gives, read from the metadata JSON and the XML."""
    pid = f"{item['pmcid']}.{item['version']}"
    info = json.loads(fetch(f"{BASE}/{pid}/{pid}.json", retries=3))
    root = ET.fromstring(xml)

    def text(el):
        return "".join(el.itertext()).strip() if el is not None else ""

    epub = next((d for d in root.findall(".//article-meta/pub-date") if d.get("pub-type") in ("epub", "pub")), None)
    any_date = epub if epub is not None else root.find(".//article-meta/pub-date")
    pub = None
    if any_date is not None:
        y, mo, d = (text(any_date.find(k)) or "1" for k in ("year", "month", "day"))
        try:
            pub = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        except ValueError:
            pub = None
    kind = (root.get("article-type") or "").lower()
    authors = []
    for c in root.findall(".//article-meta/contrib-group/contrib[@contrib-type='author']")[:30]:
        sur, giv = text(c.find("name/surname")), text(c.find("name/given-names"))
        if sur:
            initials = "".join(w[0] for w in re.split(r"[\s.-]+", giv) if w)
            authors.append(f"{sur} {initials}".strip())
    abbrev = root.find(".//journal-meta/journal-id[@journal-id-type='iso-abbrev']")
    abstract = root.find(".//article-meta/abstract")
    return {
        "pmcid": item["pmcid"], "pmid": info.get("pmid"), "doi": (info.get("doi") or "").lower() or None,
        "title": text(root.find(".//article-meta/title-group/article-title")) or info.get("title", ""),
        "journal": text(root.find(".//journal-meta//journal-title")) or None,
        "journal_abbrev": text(abbrev) or None, "authors": ", ".join(authors),
        "pub_date": pub, "index_date": item["day"], "license": (info.get("license_code") or "").lower() or None,
        "pub_types": [JATS_TYPES.get(kind, kind)] if kind != "research-article" else ["research-article"],
        "abstract": text(abstract), "source_kind": kind,
        "retracted": bool(info.get("is_retracted")), "manuscript": bool(info.get("is_manuscript")),
    }


def _xml(item: dict) -> str:
    """The article's JATS XML, written to the full-text cache so the rest of the pipeline reads it as usual."""
    path = cached("fulltext", item["pmcid"], ".xml")
    if path.exists():
        return path.read_text(encoding="utf-8")
    pid = f"{item['pmcid']}.{item['version']}"
    text = fetch(f"{BASE}/{pid}/{pid}.xml", retries=3).decode("utf-8", "replace")
    path.write_text(text, encoding="utf-8")
    return text


def discover(index_from: str, index_date: str, skip: set[str], max_main: int) -> dict:
    """Candidates for the window, shaped like the Europe PMC ones: {"listed", "screened", "main", "impact", ...}.

    skip: PMCIDs an earlier issue already listed. main = structure papers (words of the main query); impact = papers
    for the 注目の構造 search, best text score first (at most IMPACT_CAP)."""
    items = [i for i in listing(index_from, index_date) if i["pmcid"] not in skip]
    print(f"PMC Open Access (AWS): {len(items)} new articles written {index_from}..{index_date}", file=sys.stderr)
    with ThreadPoolExecutor(WORKERS) as ex:
        flags = list(ex.map(_flags, items))
    main_items = [i for i, f in zip(items, flags) if f["structure"]][:max_main]
    main_ids = {i["pmcid"] for i in main_items}
    impact_items = sorted(((i, f) for i, f in zip(items, flags) if f["impact"] and i["pmcid"] not in main_ids),
                          key=lambda x: -x[1]["rank"])[:IMPACT_CAP]

    def build(item):
        try:
            return _meta(item, _xml(item))
        except Exception as e:      # one broken record must not stop the day
            print(f"  skipped {item['pmcid']}: {e}", file=sys.stderr)
            return None

    with ThreadPoolExecutor(WORKERS) as ex:
        main = list(ex.map(build, main_items))
        impact = list(ex.map(build, [i for i, _ in impact_items]))

    floor = str(dt.date.fromisoformat(index_from) - dt.timedelta(days=PUB_FLOOR_DAYS))

    def keep(m):
        # Same as the Europe PMC flow (which also takes author manuscripts and papers published weeks earlier that only
        # now reached PMC), except: not retracted, not a preprint, and not published more than half a year before the
        # window (a bulk back-fill of old records is not news)
        return (m is not None and not m["retracted"] and m["source_kind"] != "preprint"
                and (m["pub_date"] or "9999") >= floor)

    def clean(ms):
        out = [m for m in ms if keep(m)]
        for m in out:
            for k in ("source_kind", "retracted", "manuscript"):
                m.pop(k, None)
        return out

    failed = sum(bool(f.get("failed")) for f in flags)
    if failed:
        print(f"  {failed} of {len(items)} articles could not be read", file=sys.stderr)
    return {"listed": len(items), "screened": sum(f["structure"] for f in flags), "failed": failed, "main": clean(main),
            "impact": clean(impact)}
