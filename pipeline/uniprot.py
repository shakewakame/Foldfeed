"""UniProt: gene names, protein names, organisms, keywords, EC numbers and families (Pfam, InterPro) for accessions;
membrane topology for the figures."""

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
            "fields": "accession,gene_primary,gene_synonym,protein_name,organism_name,keyword,ec,xref_pfam,xref_interpro,lineage",
            "format": "json"})
        for r in (d or {}).get("results", []):
            desc = r.get("proteinDescription", {})
            name = (desc.get("recommendedName") or (desc.get("submissionNames") or [{}])[0]).get("fullName", {})
            genes = r.get("genes") or [{}]
            names = [desc.get("recommendedName") or {}] + desc.get("submissionNames", []) + desc.get("alternativeNames", [])
            xrefs = r.get("uniProtKBCrossReferences", [])
            out[r["primaryAccession"]] = {
                "uniprot": r["primaryAccession"],
                "gene": (genes[0].get("geneName") or {}).get("value"),
                "synonyms": [s["value"] for s in genes[0].get("synonyms", [])],
                "name": name.get("value"),
                "organism": r.get("organism", {}).get("scientificName"),
                # for the kind of protein (a draft of the article's categories) and for related articles
                "keywords": sorted({k["name"] for k in r.get("keywords", [])} - {"3D-structure", "Reference proteome",
                                   "Proteomics identification", "Direct protein sequencing"}),
                "ec": sorted({e["value"] for n in names for e in n.get("ecNumbers", [])}),
                "pfam": family_names(xrefs, "Pfam"),
                "interpro": family_names(xrefs, "InterPro"),
                "lineage": [l for l in (r.get("organism", {}).get("lineage") or [])][:2],
            }
    return out


def family_names(xrefs: list[dict], db: str) -> dict[str, str]:
    """{"PF00069": "Pkinase", ...}: a database's families of the protein, with their short names."""
    return {x["id"]: next((p["value"] for p in x.get("properties", []) if p.get("key") == "EntryName"), x["id"])
            for x in xrefs if x.get("database") == db}


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


# A draft of an article's kinds of protein (schema.CATEGORIES) from its proteins' UniProt keywords and EC numbers; the
# writer starts from it and corrects it (an antibody or a designed protein has no UniProt entry to go by).
CATEGORY_KEYWORDS = {
    "kinase": {"Kinase", "Serine/threonine-protein kinase", "Tyrosine-protein kinase"},
    "protease": {"Protease", "Hydrolase|Protease", "Aminopeptidase", "Carboxypeptidase", "Metalloprotease",
                 "Serine protease", "Cysteine protease", "Aspartyl protease", "Thiol protease"},
    "receptor": {"G-protein coupled receptor", "Receptor"},
    "channel": {"Ion channel", "Ligand-gated ion channel", "Voltage-gated channel", "Potassium channel", "Sodium channel",
                "Calcium channel", "Chloride channel"},
    "transporter": {"Symport", "Antiport", "Sugar transport", "Amino-acid transport", "Ion transport", "Lipid transport",
                    "Protein transport", "Translocase", "Transport"},
    "nucleic": {"DNA-binding", "RNA-binding", "DNA repair", "DNA replication", "DNA recombination", "Helicase", "Nuclease",
                "Endonuclease", "Exonuclease", "RNA-directed DNA polymerase", "DNA-directed RNA polymerase",
                "Nucleotidyltransferase", "mRNA processing", "mRNA splicing", "tRNA processing", "rRNA processing"},
    "chromatin": {"Chromatin regulator", "Nucleosome core"},
    "translation": {"Ribosomal protein", "Protein biosynthesis", "Chaperone", "Initiation factor", "Elongation factor",
                    "Aminoacyl-tRNA synthetase", "Ribonucleoprotein"},
    "regulation": {"Transcription regulation", "Activator", "Repressor"},
    "immune": {"Immunity", "Innate immunity", "Adaptive immunity", "Immunoglobulin domain", "MHC I", "MHC II"},
    "pathogen": {"Virion", "Virulence", "Toxin", "Antibiotic resistance", "Viral attachment to host cell",
                 "Viral penetration into host cytoplasm", "Fusion of virus membrane with host membrane"},
    "photosynthesis": {"Photosynthesis", "Electron transport", "Respiratory chain", "Chlorophyll"},
}


def categories_draft(proteins: list[dict], most: int = 3) -> list[str]:
    """The kinds of protein (schema.CATEGORIES keys, at most `most`, the most proteins first) that the proteins'
    UniProt keywords, EC numbers and lineage point to. A protein with an EC number and nothing more particular is an
    enzyme; a viral protein is under infection. [] when nothing points anywhere."""
    votes: dict[str, int] = {}
    for p in proteins:
        kws = set(p.get("keywords") or [])
        kinds = {k for k, words in CATEGORY_KEYWORDS.items() if kws & words}
        ec = p.get("ec") or []
        if any(e.startswith("3.4.") for e in ec):
            kinds.add("protease")
        if "Receptor" in kws and not kws & {"G-protein coupled receptor", "Transmembrane"}:
            kinds.discard("receptor")             # a soluble "receptor" (of a particle, of a signal) is not one here
        if "channel" in kinds or "receptor" in kinds:
            kinds.discard("transporter")          # "Transport" is on channels too
        if "Transmembrane" not in kws and not kws & (CATEGORY_KEYWORDS["transporter"] - {"Transport"}):
            kinds.discard("transporter")          # carrying something (haemoglobin, oxygen) is not a membrane transporter
        if (p.get("lineage") or [""])[0] == "Viruses":
            kinds.add("pathogen")
        if ec and not kinds & {"kinase", "protease", "nucleic", "chromatin", "translation"}:
            kinds.add("enzyme")
        for k in kinds:
            votes[k] = votes.get(k, 0) + 1
    order = list(CATEGORY_KEYWORDS) + ["enzyme"]
    return sorted(votes, key=lambda k: (-votes[k], order.index(k)))[:most]
