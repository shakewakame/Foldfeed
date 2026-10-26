"""RCSB PDB / PDBe lookups: entry facts, release status, prior structures, coordinates."""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse

from .common import cached, fetch, fetch_json

GRAPHQL = "https://data.rcsb.org/graphql"
HOLDINGS = "https://data.rcsb.org/rest/v1/holdings/status/{}"
PDBE_BEST = "https://www.ebi.ac.uk/pdbe/api/mappings/best_structures/{}"
COORDS = "https://files.rcsb.org/download/{}.cif.gz"
PDBE_SEARCH = "https://www.ebi.ac.uk/pdbe/search/pdb/select"   # PDBe's Solr search (RCSB's search API is not reachable here)

# Buffer components, cryoprotectants, detergents, common ions and glycans that are rarely the story.
COMMON_ADDITIVES = {
    "HOH", "DOD", "SO4", "PO4", "GOL", "EDO", "PEG", "PG4", "PGE", "1PE", "P6G", "MPD", "DMS",
    "ACT", "ACY", "FMT", "TRS", "EPE", "MES", "BME", "DTT", "CIT", "IMD", "NA", "CL", "K", "BR",
    "IOD", "NO3", "SCN", "CA", "MG", "ZN", "MN", "NI", "CO", "CD", "CU", "FE", "FE2", "UNX", "UNL",
    "NAG", "NDG", "BMA", "MAN", "FUC", "GAL", "GLC", "BOG", "LMT", "LMN", "DDM", "CLR", "Y01",
    "PCW", "POV", "PEE", "PLM", "OLA", "OLC", "MYR", "STE", "D10", "HEX", "LDA", "UNK", "AZI",
    "MLI", "TAR", "SIN", "MAL", "SUC", "TLA", "BCT", "CO3", "NH4", "OXY", "PEO", "CXS", "B3P",
    # lipids and lipid-like detergents modelled around membrane proteins
    "PC1", "PCF", "PEF", "PEV", "PGV", "PGW", "LHG", "LMG", "DGD", "SQD", "CDL", "3PE", "6PL",
    "PTY", "PLX", "P5S", "LPP", "8PE", "PEK", "D12", "OCT", "HP6", "LNK", "UND", "R16", "C14",
    "PX4", "POV", "PSF", "CHD", "CPQ", "AJP", "DU0", "NKP", "LBN", "PIO", "9Z9", "A1L",
    "CPS", "LMU", "DMU", "UMQ", "BNG", "HTG", "SDS", "TWT",  # detergents
    "BTB", "B3P", "HEZ", "IPA", "PE8", "PG0", "2PE", "12P",  # buffers and PEG fragments
}

ENTRY_QUERY = """
query($ids: [String!]!) {
  entries(entry_ids: $ids) {
    rcsb_id
    struct { title }
    exptl { method }
    em_experiment { reconstruction_method }
    rcsb_entry_info { resolution_combined }
    rcsb_accession_info { initial_release_date deposit_date }
    rcsb_primary_citation { pdbx_database_id_DOI pdbx_database_id_PubMed title }
    polymer_entities {
      rcsb_polymer_entity { pdbx_description }
      entity_poly { rcsb_entity_polymer_type }
      rcsb_polymer_entity_container_identifiers { uniprot_ids }
      rcsb_entity_source_organism { scientific_name }
    }
    nonpolymer_entities {
      nonpolymer_comp { chem_comp { id name type } }
    }
  }
}
"""


def entries(ids: list[str]) -> dict[str, dict]:
    """Released entries keyed by upper-case ID. IDs that are unknown or unreleased are absent."""
    out = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        body = fetch(GRAPHQL, data=json.dumps({"query": ENTRY_QUERY, "variables": {"ids": chunk}}).encode(),
                     headers={"Content-Type": "application/json"})
        for e in (json.loads(body).get("data") or {}).get("entries") or []:
            if e:
                out[e["rcsb_id"].upper()] = _entry_facts(e)
    return out


def _entry_facts(e: dict) -> dict:
    cit = e.get("rcsb_primary_citation") or {}
    acc = e.get("rcsb_accession_info") or {}
    res = (e.get("rcsb_entry_info") or {}).get("resolution_combined") or []
    polymers = []
    for p in e.get("polymer_entities") or []:
        polymers.append({
            "description": (p.get("rcsb_polymer_entity") or {}).get("pdbx_description"),
            "type": (p.get("entity_poly") or {}).get("rcsb_entity_polymer_type"),
            "uniprot_ids": (p.get("rcsb_polymer_entity_container_identifiers") or {}).get("uniprot_ids") or [],
            "organisms": sorted({o["scientific_name"] for o in p.get("rcsb_entity_source_organism") or []
                                 if o.get("scientific_name")}),
        })
    ligands = []
    for n in e.get("nonpolymer_entities") or []:
        cc = ((n.get("nonpolymer_comp") or {}).get("chem_comp")) or {}
        if cc.get("id") and cc["id"] not in COMMON_ADDITIVES:
            ligands.append({"id": cc["id"], "name": cc.get("name")})
    return {
        "pdb_id": e["rcsb_id"].upper(),
        "status": "released",
        "title": (e.get("struct") or {}).get("title"),
        "method": ", ".join(x["method"] for x in e.get("exptl") or []),
        "em_reconstruction": (e.get("em_experiment") or {}).get("reconstruction_method"),
        "resolution": res[0] if res else None,
        "release_date": (acc.get("initial_release_date") or "")[:10] or None,
        "deposit_date": (acc.get("deposit_date") or "")[:10] or None,
        "citation_doi": (cit.get("pdbx_database_id_DOI") or "").lower() or None,
        "citation_pmid": str(cit["pdbx_database_id_PubMed"]) if cit.get("pdbx_database_id_PubMed") else None,
        "citation_title": cit.get("title"),
        "polymers": polymers,
        "ligands": ligands,
    }


EM_QUERY = "query($ids: [String!]!) { entries(entry_ids: $ids) { rcsb_id em_experiment { reconstruction_method } } }"


def em_reconstructions(ids: list[str]) -> dict[str, str | None]:
    """PDB ID -> the EM reconstruction method ("SINGLE PARTICLE", "SUBTOMOGRAM AVERAGING", "HELICAL", ...)."""
    out = {}
    for i in range(0, len(ids), 100):
        body = fetch(GRAPHQL, data=json.dumps({"query": EM_QUERY, "variables": {"ids": ids[i:i + 100]}}).encode(),
                     headers={"Content-Type": "application/json"})
        for e in (json.loads(body).get("data") or {}).get("entries") or []:
            if e:
                out[e["rcsb_id"].upper()] = (e.get("em_experiment") or {}).get("reconstruction_method")
    return out


def holding_status(pdb_id: str) -> str | None:
    """'unreleased', 'removed', or None when the ID does not exist."""
    d = fetch_json(HOLDINGS.format(pdb_id), ok_404=True)
    if not d:
        return None
    status = (d.get("rcsb_repository_holdings_combined") or {}).get("status", "").upper()
    return {"UNRELEASED": "unreleased", "REMOVED": "removed", "CURRENT": "released"}.get(status, status.lower() or None)


def structures_for_uniprot(acc: str) -> set[str]:
    """All PDB IDs that SIFTS maps to this UniProt accession (upper case). SIFTS lags new releases by days."""
    d = fetch_json(PDBE_BEST.format(acc), ok_404=True)
    if not d:
        return set()
    return {x["pdb_id"].upper() for x in d.get(acc, [])}


def coordinates(pdb_id: str, max_bytes: int = 20_000_000) -> bytes | None:
    path = cached("coords", pdb_id, ".cif.gz")
    if path.exists():
        return path.read_bytes()
    body = fetch(COORDS.format(pdb_id), ok_404=True, timeout=180)
    if body is None or len(body) > max_bytes:
        return None
    path.write_bytes(body)
    return body


GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "κ": "kappa", "λ": "lambda",
         "μ": "mu", "σ": "sigma", "ω": "omega", "Δ": "delta"}
TITLE_STOP = {"the", "and", "for", "with", "from", "into", "its", "via", "are", "this", "that", "how", "between", "reveal",
              "reveals", "structure", "structures", "structural", "basis", "insights", "insight", "analysis", "study"}


def title_words(title: str) -> list[str]:
    """The words of a title that carry it, lower case, Greek letters spelled out (a PDB citation writes β as beta)."""
    t = "".join(GREEK.get(ch, ch) for ch in unicodedata.normalize("NFKC", title or ""))
    return [w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 2 and w not in TITLE_STOP]


def _pdbe(q: str, rows: int = 100) -> list[dict]:
    params = {"q": q, "fl": "pdb_id,citation_title,citation_authors,citation_year,citation_doi,pubmed_id",
              "wt": "json", "rows": rows, "group": "true", "group.field": "pdb_id"}
    d = fetch_json(f"{PDBE_SEARCH}?{urllib.parse.urlencode(params)}", retries=2)
    return [g["doclist"]["docs"][0] for g in ((d or {}).get("grouped") or {}).get("pdb_id", {}).get("groups", [])]


def by_citation(doi: str | None, pmid: str | None, title: str, first_author: str | None = None) -> list[str]:
    """PDB entries whose primary citation is this paper, for a paper that does not list its codes in the text (they
    are in a supplementary table): by the citation's PubMed ID or DOI, else (a new entry's citation often has neither
    yet) by its title, kept only when nearly all of the title's words match and, if known, the first author's
    surname is among the citation's authors. Upper-case IDs, sorted."""
    ids = set()
    exact = [f"pubmed_id:{int(pmid)}"] if pmid and str(pmid).isdigit() else []
    if doi:
        exact.append('citation_doi:"%s"' % doi.lower().replace('"', ""))
    if exact:
        ids |= {d["pdb_id"].upper() for d in _pdbe(" OR ".join(exact))}
    words = title_words(title)
    if not ids and len(words) >= 3:
        mine = set(words)
        surname = (first_author or "").split(",")[0].split()[-1].lower() if first_author else ""
        for d in _pdbe(" AND ".join(f"t_citation_title:{w}" for w in words)):
            theirs = set(title_words(d.get("citation_title") or ""))
            same = len(mine & theirs) / max(len(mine | theirs), 1)
            authors = " ".join(d.get("citation_authors") or []).lower()
            if same >= 0.8 and (not surname or surname in authors):
                ids.add(d["pdb_id"].upper())
    return sorted(ids)
