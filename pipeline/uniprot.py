"""UniProt: gene names, protein names and organisms for accessions; membrane topology for the figures."""

from __future__ import annotations

import json
import urllib.error

from .common import cached, fetch_json

ACCESSIONS = "https://rest.uniprot.org/uniprotkb/accessions"
ENTRY = "https://rest.uniprot.org/uniprotkb/{}"
TOPOLOGY_TYPES = ("Transmembrane", "Topological domain")


def lookup(accessions: list[str]) -> dict[str, dict]:
    out = {}
    accs = sorted(set(accessions))
    for i in range(0, len(accs), 100):
        d = fetch_json(ACCESSIONS, params={
            "accessions": ",".join(accs[i:i + 100]),
            "fields": "accession,gene_primary,gene_synonym,protein_name,organism_name",
            "format": "json"})
        for r in (d or {}).get("results", []):
            desc = r.get("proteinDescription", {})
            name = (desc.get("recommendedName") or (desc.get("submissionNames") or [{}])[0]).get("fullName", {})
            genes = r.get("genes") or [{}]
            out[r["primaryAccession"]] = {
                "uniprot": r["primaryAccession"],
                "gene": (genes[0].get("geneName") or {}).get("value"),
                "synonyms": [s["value"] for s in genes[0].get("synonyms", [])],
                "name": name.get("value"),
                "organism": r.get("organism", {}).get("scientificName"),
            }
    return out


def topology(accession: str) -> list[dict] | None:
    """The transmembrane segments and topological domains (cytoplasmic, extracellular, ...) of one entry:
    [{"type", "start", "end", "description"}], [] for a soluble protein, None when UniProt cannot be reached
    (the figure then keeps its usual orientation). Cached in .cache/uniprot-topology/."""
    path = cached("uniprot-topology", accession, ".json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        d = fetch_json(ENTRY.format(accession), params={"fields": "accession,ft_transmem,ft_topo_dom", "format": "json"},
                       ok_404=True, retries=2)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    feats = []
    for f in (d or {}).get("features", []):
        if f.get("type") in TOPOLOGY_TYPES:
            loc = f.get("location", {})
            feats.append({"type": f["type"], "start": loc.get("start", {}).get("value"),
                          "end": loc.get("end", {}).get("value"), "description": f.get("description", "")})
    path.write_text(json.dumps(feats), encoding="utf-8")
    return feats
