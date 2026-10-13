"""注目の構造（新しい構造以外）: the machine pre-screen and the scoring rubric.

Papers that do not report a new structure can still deserve an article: a mechanism worked out on
existing structures, structure prediction / AI / protein design, a new method or software, or
structure-based drug discovery (owner's choice, 2026-10-12; up to MAX_PER_DAY a day, same format
as the new-structure articles). Only work checked against experimentally determined structures
(X-ray, cryo-EM, NMR, including existing PDB entries) qualifies: papers resting on molecular
dynamics, prediction or docking alone are left out (owner, 2026-10-12).

Two stages:
  1. prescreen() — keyword hints from the title, abstract and journal, computed by pipeline.collect.
     It only orders the pool and flags likely low-impact work (docking-only, network pharmacology);
     it never selects anything.
  2. The writer (Claude, in the daily routine) reads the shortlisted papers and scores them with
     RUBRIC, quoting the paper for every score, in data/issues/<date>/impact.json. pipeline.verify
     checks the quotes, the totals, the threshold and the daily cap.
"""

from __future__ import annotations

MAX_PER_DAY = 5
THRESHOLD = 7            # total score (out of 10) needed to be selected
SHORTLIST = 10           # papers the writer reads in full and scores

TYPES = {
    "mechanism": "既存構造で機構を解明",
    "ai_design": "構造予測・AI・デザイン",
    "method": "新しい手法・ソフト",
    "drug": "構造に基づく創薬",
}

# Rubric: criterion -> (max score, what earns the points). Every score above 0 needs a quote.
RUBRIC = {
    "novelty": (3, "何が初めてか。新しい機構・新しい能力・初めての実証を論文が示している（3＝分野の定説を変える／2＝明確に新しい／1＝既知の延長）"),
    "reach": (3, "誰に効くか。構造生物学の他の研究者が使える・考え方が変わる範囲（3＝分野全体／2＝大きなタンパク質ファミリーや広く使われる系／1＝その系だけ）"),
    "structure": (2, "実験で決めた構造（X 線・クライオ電顕・NMR。既存の PDB 構造を含む）で結論を確かめているか（2＝実験構造なしでは結論が成り立たない／1＝実験構造で一部を確かめた／0＝MD・予測・ドッキングなど計算モデルだけ。0 点は選ばない）"),
    "support": (2, "裏付けの強さ（2＝独立した実験で検証：変異体・機能測定・既存手法との比較など／1＝一部の検証のみ／0＝計算のみで実験的検証なし）"),
}
MAX_TOTAL = sum(m for m, _ in RUBRIC.values())
REQUIRED = {"structure": 1}  # minimum per criterion for a paper to be selected

# Not selected regardless of score (the writer records the reason).
EXCLUSIONS = [
    "MD・構造予測・ドッキングなど計算だけが根拠で、実験で決めた構造による裏付けがない",
    "ドッキング・ネットワーク薬理学・免疫インフォマティクス・ADMET 予測だけで、実験的な検証がない",
    "総説・解説・プロトコル集（新しい結果がない）",
    "臨床研究で、構造が議論に関わらない",
]

_TYPE_WORDS = {
    "method": ["we develop", "we present", "we introduce", "new method", "novel method", "software", "pipeline",
               "algorithm", "toolkit", "web server", "benchmark", "workflow", "time-resolved", "in situ",
               "sample preparation", "integrative modeling", "integrative modelling"],
    "ai_design": ["alphafold", "rosettafold", "esmfold", "deep learning", "machine learning", "language model",
                  "de novo design", "designed protein", "protein design", "binder design", "rfdiffusion",
                  "proteinmpnn", "generative model", "structure prediction"],
    "drug": ["structure-based", "structure-guided", "lead compound", "lead optimization", "potent", "selective inhibitor",
             "ic50", "nanomolar", "degrader", "protac", "covalent inhibitor", "fragment", "drug discovery",
             "co-crystal", "cocrystal"],
    "mechanism": ["mechanism", "conformational", "allosteric", "gating", "catalytic", "mutagenesis",
                  "substrate recognition", "activation"],
}
_VALIDATION = ["mutagenesis", "mutant", "in vitro", "in vivo", "assay", "electrophysiology", "binding affinity",
               "isothermal titration", "surface plasmon", "kinetic", "mice", "cryo-em", "crystal structure",
               "nmr", "hydrogen-deuterium", "hdx", "cross-linking", "saxs", "single-molecule"]
_EXP_STRUCTURE = ["crystal structure", "cryo-em", "cryoem", "cryo-electron", "x-ray", "nmr", "co-crystal",
                  "cocrystal", "protein data bank", "pdb"]
_LOW = ["network pharmacology", "molecular docking", "immunoinformatic", "reverse vaccinology", "admet",
        "virtual screening", "in silico", "multi-epitope", "multiepitope", "pharmacophore", "density functional",
        "mendelian randomization", "bibliometric", "bioinformatics analysis"]
_BIO = ["protein", "enzyme", "receptor", "antibody", "nanobody", "peptide", "rna", "dna", "nucleosome",
        "ribosome", "channel", "transporter", "kinase", "ligase", "polymerase", "virus", "viral", "bacterial",
        "membrane", "amyloid", "chaperone", "complex", "biomolecul", "condensate", "chromatin", "nucleic acid",
        "amino acid", "residue", "cellular", "bacteria", "mutation", "binding"]
_JOURNALS = {"Nature", "Science", "Cell", "Nat Struct Mol Biol", "Nat Chem Biol", "Nat Methods", "Nat Biotechnol",
             "Nat Chem", "Nat Commun", "Proc Natl Acad Sci U S A", "Elife", "Sci Adv", "Mol Cell", "EMBO J",
             "J Am Chem Soc", "Angew Chem Int Ed Engl", "Nucleic Acids Res", "PLoS Biol", "Structure",
             "J Med Chem", "Cell Chem Biol", "Cell Rep", "Commun Biol", "Protein Sci", "IUCrJ", "Nat Comput Sci"}


def prescreen(meta: dict) -> dict:
    """Hints for ordering the pool: likely types, validation words, low-impact words, journal."""
    text = f"{meta.get('title') or ''} {meta.get('abstract') or ''}".lower()
    types = [t for t, words in _TYPE_WORDS.items() if any(w in text for w in words)]
    validation = sorted({w for w in _VALIDATION if w in text})
    low = sorted({w for w in _LOW if w in text})
    journal = meta.get("journal_abbrev") in _JOURNALS
    bio = any(w in text for w in _BIO)
    # The impact search itself requires experimental-structure terms somewhere in the full text.
    exp_structure = (any(w in text for w in _EXP_STRUCTURE) or bool(meta.get("cited_structures"))
                     or meta.get("source") == "impact_search")
    score = (len(types) + min(len(validation), 3) + (2 if journal else 0) - 2 * len(low) - (4 if not bio else 0)
             + (2 if exp_structure else -4))
    flags = []
    if not exp_structure:
        flags.append("実験構造の手がかりなし")
    if "molecular dynamics" in text and not exp_structure:
        flags.append("MD 中心の可能性")
    if low and not validation:
        flags.append("計算のみの可能性（" + "、".join(low[:3]) + "）")
    if not bio:
        flags.append("生体高分子が主題でない可能性")
    if (meta.get("title") or "").lower().startswith(("correction", "erratum", "retraction")):
        score -= 10
        flags.append("訂正・撤回の告知")
    return {"score": score, "types": types, "validation": validation[:5], "low": low, "journal": journal,
            "exp_structure": exp_structure, "flags": flags}
