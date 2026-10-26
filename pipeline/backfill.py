"""Fill in, for the issues already made, what the collector now records for every new paper.

    python3 -m pipeline.backfill licenses     # each article's licence address (jats.license_url)
    python3 -m pipeline.backfill uniprot      # its proteins' keywords, EC numbers, Pfam / InterPro, lineage, and the
                                              # draft of its kinds of protein (uniprot.categories_draft)

Only the articles (picks) of each issue are looked at; candidates.json gets the new field, nothing else changes.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from . import europepmc, jats, pmcoa, uniprot
from .common import ISSUES_DIR, cached, fetch, read_json, write_json


def full_text(pmcid: str) -> str | None:
    """The JATS XML from Europe PMC, else from PMC's open data on AWS (the first version)."""
    try:
        xml = europepmc.fulltext_xml(pmcid)
    except Exception as e:   # a server error on one paper should not stop the rest
        print(f"  {pmcid}: Europe PMC {e}", file=sys.stderr)
        xml = None
    if xml:
        return xml
    pid = f"{pmcid}.1"
    try:
        body = fetch(f"{pmcoa.BASE}/{pid}/{pid}.xml", ok_404=True, retries=2)
    except Exception as e:
        print(f"  {pmcid}: PMC open data {e}", file=sys.stderr)
        return None
    if not body:
        return None
    text = body.decode("utf-8", "replace")
    cached("fulltext", pmcid, ".xml").write_text(text, encoding="utf-8")
    return text


def picked(issue) -> set[str]:
    return {read_json(p).get("pmcid") for p in (issue / "picks").glob("*.json")}


def licenses() -> None:
    for issue in sorted(d for d in ISSUES_DIR.iterdir() if (d / "candidates.json").exists()):
        meta = read_json(issue / "candidates.json")
        todo = [c for c in meta["candidates"] if c["pmcid"] in picked(issue) and not c.get("license_url")]
        if not todo:
            continue

        def one(c):
            xml = full_text(c["pmcid"])
            try:
                return jats.license_url(ET.fromstring(xml)) if xml else None
            except ET.ParseError:
                return None
        with ThreadPoolExecutor(6) as ex:
            for c, url in zip(todo, ex.map(one, todo)):
                c["license_url"] = url
        write_json(issue / "candidates.json", meta)
        print(f"{issue.name}: {sum(bool(c['license_url']) for c in todo)}/{len(todo)} with a licence address", file=sys.stderr)


def uniprot_fields() -> None:
    for issue in sorted(d for d in ISSUES_DIR.iterdir() if (d / "candidates.json").exists()):
        meta = read_json(issue / "candidates.json")
        todo = [c for c in meta["candidates"] if c["pmcid"] in picked(issue)]
        accs = sorted({p["uniprot"] for c in todo for p in c.get("proteins", []) if "keywords" not in p})
        if not todo:
            continue
        fresh = uniprot.lookup(accs) if accs else {}
        for c in todo:
            c["proteins"] = [{**p, **fresh.get(p["uniprot"], {})} for p in c.get("proteins", [])]
            c["categories_draft"] = uniprot.categories_draft(c["proteins"])
        write_json(issue / "candidates.json", meta)
        print(f"{issue.name}: {len(accs)} proteins, {len(todo)} articles", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", choices=["licenses", "uniprot"])
    a = ap.parse_args(argv)
    {"licenses": licenses, "uniprot": uniprot_fields}[a.what]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
