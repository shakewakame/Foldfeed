"""Check every pick of an issue before it is rendered.

    python3 -m pipeline.verify 2026-10-07

Errors (exit code 1):
  - missing or over-long fields, unknown tags, unknown or too many categories
  - the four-line summary (question / approach / result / learned): each line needs a verbatim quote, and the
    names, abbreviations and numbers in it must appear in the paper
  - an evidence quote that does not appear verbatim in the paper's full text
  - "初構造" on a paper whose proteins already had PDB structures
  - a resolution (… Å) or PDB ID in the Japanese text that the paper / PDB does not support
  - a headline / subhead whose names, numbers or abbreviations appear neither in the paper nor in the
    pick's own summary and highlights (the short headline may only say what the article supports)
  - feature.json (今日の一本): the pick, axes, a verbatim quote, the reason length
  - impact.json (注目の構造): a quote per score that is not verbatim, a score out of range, a selected
    paper under the threshold, more than the daily cap, or a mismatch between selected papers and
    picks marked "section": "impact"
Warnings: new-structure picks whose paper is not classified as a new structure, missing evidence for lab notes.
"""

from __future__ import annotations

import re
import sys
import unicodedata

from . import impact
from .common import cached, issue_dir, read_json
from .schema import (CATEGORIES, FEATURE_AXES, FEATURE_MIN, FEATURE_REASON_LIMIT, FIGURE_TITLE_LIMIT, LAB_NOTE_FIELDS,
                     LIMITS, SUMMARY_FIELDS, SUMMARY_OPTIONAL, TAGS, TOPICS)

NEW_KINDS = ("new_structure", "new_structure_ids_unlisted", "new_map_only")

ANGSTROM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:Å|Å|オングストローム)")
PDB_ID_JA = re.compile(r"(?<![A-Za-z0-9])([1-9][A-Z0-9]{3})(?![A-Za-z0-9])")


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[‐‑‒–—−]", "-", s)
    return re.sub(r"\s+", " ", s).strip().lower()


ALL_SUMMARY = {**SUMMARY_FIELDS, **SUMMARY_OPTIONAL}


def summary_texts(pick: dict) -> list[str]:
    s = pick.get("summary") or {}
    return [s.get(k) or "" for k in ALL_SUMMARY]


def japanese_fields(pick: dict) -> list[str]:
    out = [pick.get(k, "") or "" for k in ("headline", "subhead", "takeaway", "target_label", "method_label",
                                           "ligand_label")]
    out += summary_texts(pick)
    out += [h.get("text", "") for h in pick.get("highlights", [])]
    out += [v for k, v in (pick.get("lab_notes") or {}).items() if k in LAB_NOTE_FIELDS and v]
    cmp_ = pick.get("compare") or {}
    out += [(cmp_.get("mark") or {}).get("text", "")] + [pn.get("title", "") for pn in cmp_.get("panels") or []]
    out += [n.get("title", "") + " " + n.get("sub", "") for n in pick.get("figure_notes") or []]
    return [x for x in out if isinstance(x, str)]


def check_pick(pick: dict, cand: dict | None, corpus: str | None) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    if cand is None:
        return [f"{pick.get('pmcid')} is not in candidates.json"], []
    if pick.get("section") == "impact":
        if cand["kind"] in NEW_KINDS:
            errors.append("marked as 注目の構造 (section: impact) but the paper reports a new structure")
    elif cand["kind"] not in NEW_KINDS:
        warnings.append(f"classified as {cand['kind']}, not a new structure (use \"section\": \"impact\" for 注目の構造)")

    def length(name, value, key):
        required, limit = LIMITS[key]
        if not value:
            if required:
                errors.append(f"{name} is missing")
            return
        if len(value) > limit:
            errors.append(f"{name} is {len(value)} characters (limit {limit})")

    for f in ("headline", "subhead", "target_label", "target_en"):
        length(f, pick.get(f), f)
    if pick.get("target_en") and re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", pick["target_en"]):
        errors.append("target_en must be in English (no Japanese)")
    for f in ALL_SUMMARY:
        length(f"summary.{f}", (pick.get("summary") or {}).get(f), f"summary.{f}")
    topics = pick.get("topics")
    if topics is not None:
        bad = [t for t in topics if t not in TOPICS] if isinstance(topics, list) else ["(not a list)"]
        if bad or len(topics) > 3:
            errors.append(f"topics must be 0-3 of {', '.join(TOPICS)}; got {topics}")
    cats = pick.get("categories") or []
    if not 1 <= len(cats) <= 3:
        errors.append(f"categories must have 1-3 items, has {len(cats)}")
    for c in cats:
        if c not in CATEGORIES:
            errors.append(f"unknown category {c!r} (allowed: {', '.join(CATEGORIES)})")

    hs = pick.get("highlights") or []
    if not 1 <= len(hs) <= 3:
        errors.append(f"highlights must have 1-3 items, has {len(hs)}")
    first = any(p.get("first_structure") for p in cand.get("proteins", []))
    quotes = []
    for i, h in enumerate(hs, 1):
        if h.get("tag") not in TAGS:
            errors.append(f"highlight {i}: unknown tag {h.get('tag')!r} (allowed: {', '.join(TAGS)})")
        if h.get("tag") == "初構造" and not first:
            errors.append(f"highlight {i}: tagged 初構造 but no protein is flagged first_structure in the PDB")
        length(f"highlight {i} text", h.get("text"), "highlight.text")
        if not h.get("evidence"):
            errors.append(f"highlight {i}: needs at least one evidence quote")
        quotes += [(f"highlight {i}", e) for e in h.get("evidence") or []]

    sev = (pick.get("summary") or {}).get("evidence") or {}
    for k in ALL_SUMMARY:
        if k in SUMMARY_OPTIONAL and not (pick.get("summary") or {}).get(k):
            continue   # an optional line that is not written needs nothing
        if not sev.get(k):
            errors.append(f"summary.{k}: needs at least one evidence quote (summary.evidence.{k})")
        quotes += [(f"summary.{k}", e) for e in sev.get(k) or []]

    notes = pick.get("lab_notes") or {}
    for k in LAB_NOTE_FIELDS:
        if notes.get(k):
            length(f"lab_notes.{k}", notes[k], "lab_note")
    if any(notes.get(k) for k in LAB_NOTE_FIELDS) and not notes.get("evidence"):
        warnings.append("lab_notes has content but no evidence quotes")
    quotes += [("lab_notes", e) for e in notes.get("evidence") or []]
    fig_errors = check_figure_fields(pick, cand)
    errors += fig_errors
    quotes += [("figure_evidence", e) for e in pick.get("figure_evidence") or []]
    if pick.get("first_claim"):
        # the paper's own word that its protein had no structure before: used only when no protein could be matched
        if cand.get("proteins"):
            warnings.append("first_claim is ignored: the paper's proteins are matched to UniProt, and PDB decides")
        if not isinstance(pick["first_claim"], list) or not all(isinstance(e, dict) for e in pick["first_claim"]):
            errors.append('first_claim must be a list of {"quote": ..., "section": ...}')
        else:
            quotes += [("first_claim", e) for e in pick["first_claim"]]

    if corpus is None:
        errors.append("full-text packet missing; run pipeline.collect first")
    else:
        norm = normalize(corpus)
        for where, e in quotes:
            q = e.get("quote", "") if isinstance(e, dict) else str(e)
            if len(q) < 20:
                errors.append(f"{where}: quote too short to verify: {q!r}")
            elif normalize(q) not in norm:
                errors.append(f"{where}: quote not found in the paper: {q[:90]!r}")

    structures = cand["own_structures"] + cand.get("cited_structures", [])
    errors += check_headline_claims(pick, corpus)
    errors += check_summary_claims(pick, corpus)
    errors += check_figure_claims(pick, corpus)
    known_res = {round(s["resolution"], 2) for s in structures if s.get("resolution")}
    known_ids = {s["pdb_id"] for s in cand["own_structures"]} | set(cand.get("cited_pdb_ids", []))
    for text in japanese_fields(pick):
        for m in ANGSTROM.finditer(text):
            v = float(m.group(1))
            in_text = corpus is not None and re.search(rf"{re.escape(m.group(1))}\s*(?:Å|Å|angstrom)", corpus, re.I)
            if round(v, 2) not in known_res and not in_text:
                errors.append(f"resolution {m.group(0)} is not in the PDB entries or the paper")
        for m in PDB_ID_JA.finditer(text):
            tok = m.group(1)
            if re.search(r"[A-Z]", tok) and tok not in known_ids:
                errors.append(f"PDB ID {tok} is not among this paper's entries")
    return errors, warnings


def check_figure_fields(pick: dict, cand: dict) -> list[str]:
    """figure_notes (a stretch of residues pointed at on the figure) and compare (a second figure from other PDB entries
    of the same paper): well-formed, and backed by verbatim quotes (figure_evidence)."""
    errors = []
    notes, cmp_ = pick.get("figure_notes"), pick.get("compare")
    if not notes and not cmp_:
        return errors
    if not pick.get("figure_evidence"):
        errors.append("figure_notes / compare need figure_evidence (verbatim quotes that support the residues and the difference shown)")
    for i, n in enumerate(notes or [], 1):
        rng = n.get("range")
        if not (isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, int) for x in rng) and rng[0] <= rng[1]):
            errors.append(f"figure_notes {i}: range must be [first, last] residue numbers")
        if not isinstance(n.get("entity", 1), int) or n.get("entity", 1) < 1:
            errors.append(f"figure_notes {i}: entity is the 1-based number in the legend")
        if not n.get("title") or len(n["title"]) > FIGURE_TITLE_LIMIT:
            errors.append(f"figure_notes {i}: title is missing or over {FIGURE_TITLE_LIMIT} characters")
    if cmp_:
        known = {s["pdb_id"] for s in cand.get("own_structures", []) + cand.get("cited_structures", [])} | set(cand.get("cited_pdb_ids", []))
        panels = cmp_.get("panels") or []
        if not 2 <= len(panels) <= 3:
            errors.append("compare: 2 or 3 panels")
        for i, pn in enumerate(panels, 1):
            if pn.get("pdb", "").upper() not in known:
                errors.append(f"compare panel {i}: PDB ID {pn.get('pdb')} is not among this paper's entries")
            if not pn.get("title") or len(pn["title"]) > FIGURE_TITLE_LIMIT:
                errors.append(f"compare panel {i}: title is missing or over {FIGURE_TITLE_LIMIT} characters")
        if not (notes or cmp_.get("focus")):
            errors.append("compare: needs figure_notes (or compare.focus) to say which stretch to show")
    return errors


def check_figure_claims(pick: dict, corpus: str | None) -> list[str]:
    """Names and numbers written on the figure (note titles, panel titles, the arrow's text) must come from the paper."""
    if corpus is None:
        return []
    texts = [n.get("title", "") + " " + n.get("sub", "") for n in pick.get("figure_notes") or []]
    cmp_ = pick.get("compare") or {}
    texts += [p.get("title", "") for p in cmp_.get("panels") or []]
    texts.append((cmp_.get("mark") or {}).get("text", ""))
    support = normalize(corpus) + " " + normalize(pick.get("target_label", "")) + " " + normalize(" ".join(summary_texts(pick)))
    errors = []
    for t in texts:
        for tok in sorted(claim_tokens(t)):
            if tok.isdigit() and len(tok) == 1:
                continue
            if normalize(tok) not in support:
                errors.append(f"figure text {t.strip()!r}: {tok!r} is not in the paper")
    return errors


TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9α-ωΑ-Ω'’+\-]*[A-Za-z0-9]|\d+(?:\.\d+)?")


def claim_tokens(text: str) -> set[str]:
    """Names, abbreviations and numbers in a Japanese headline (the parts a reader could take as a fact)."""
    out = set()
    for m in TOKEN.finditer(unicodedata.normalize("NFKC", text)):
        tok = m.group(0)
        if len(tok) >= 2 or tok.isdigit():
            out.add(tok.lower())
    return out


def check_headline_claims(pick: dict, corpus: str | None) -> list[str]:
    """Every name or number in headline/subhead must come from the paper or from the pick's own summary,
    highlights and target — so a short headline cannot introduce a claim the article does not support."""
    if corpus is None:
        return []
    own = " ".join([pick.get("target_label", "")] + summary_texts(pick)
                   + [h.get("text", "") for h in pick.get("highlights", [])])
    support = normalize(corpus) + " " + normalize(own)
    errors = []
    for field in ("headline", "subhead"):
        for tok in sorted(claim_tokens(pick.get(field) or "")):
            if tok.isdigit() and len(tok) == 1:
                continue  # 3つの姿, 2種 — counts are read from the article text
            if normalize(tok) not in support:
                errors.append(f"{field}: {tok!r} is not in the paper or in this article's summary/highlights")
    return errors


def check_summary_claims(pick: dict, corpus: str | None) -> list[str]:
    """The four summary lines may only use names, abbreviations and numbers that the paper itself contains
    (the target label counts too: it is how the article names the protein)."""
    if corpus is None:
        return []
    support = normalize(corpus) + " " + normalize(pick.get("target_label", ""))
    errors = []
    for field in ALL_SUMMARY:
        for tok in sorted(claim_tokens((pick.get("summary") or {}).get(field) or "")):
            if tok.isdigit() and len(tok) == 1:
                continue
            if normalize(tok) not in support:
                errors.append(f"summary.{field}: {tok!r} is not in the paper")
    return errors


def corpus_for(pmcid: str) -> str | None:
    packet = cached("packets", pmcid or "", ".md")
    return packet.read_text(encoding="utf-8") if packet.exists() else None


def check_impact(record: dict, cands: dict, impact_picks: set[str]) -> list[str]:
    """impact.json: scores in range with verbatim quotes, threshold, daily cap, picks match the selection."""
    errors = []
    selected = set()
    for s in record.get("scored", []):
        pm = s.get("pmcid")
        where = f"impact.json {pm}"
        c = cands.get(pm)
        if c is None:
            errors.append(f"{where}: not in candidates.json")
            continue
        if c["kind"] in NEW_KINDS:
            errors.append(f"{where}: reports a new structure; it belongs in the new-structure articles")
        if s.get("type") not in impact.TYPES:
            errors.append(f"{where}: type must be one of {', '.join(impact.TYPES)}")
        scores, evidence = s.get("scores") or {}, s.get("evidence") or {}
        for k, (mx, _) in impact.RUBRIC.items():
            v = scores.get(k)
            if not isinstance(v, int) or not 0 <= v <= mx:
                errors.append(f"{where}: score {k} must be an integer 0-{mx}")
                continue
            if v > 0 and not evidence.get(k):
                errors.append(f"{where}: score {k}={v} needs at least one quote")
        if not s.get("reason"):
            errors.append(f"{where}: reason is missing")
        corpus = corpus_for(pm)
        norm = normalize(corpus) if corpus else None
        for k, quotes in evidence.items():
            for e in quotes:
                q = e.get("quote", "") if isinstance(e, dict) else str(e)
                if norm is None:
                    errors.append(f"{where}: full-text packet missing")
                    break
                if len(q) < 20 or normalize(q) not in norm:
                    errors.append(f"{where}: {k} quote not found in the paper: {q[:80]!r}")
        total = sum(v for v in scores.values() if isinstance(v, int))
        if s.get("selected"):
            selected.add(pm)
            if total < impact.THRESHOLD:
                errors.append(f"{where}: selected with {total} points (threshold {impact.THRESHOLD})")
            for k, need in impact.REQUIRED.items():
                if isinstance(scores.get(k), int) and scores[k] < need:
                    errors.append(f"{where}: selected with {k}={scores[k]}; needs at least {need} "
                                  f"(checked against an experimentally determined structure)")
    if len(selected) > impact.MAX_PER_DAY:
        errors.append(f"impact.json: {len(selected)} papers selected (at most {impact.MAX_PER_DAY} a day)")
    for pm in sorted(selected - impact_picks):
        errors.append(f"impact.json: {pm} is selected but has no pick with \"section\": \"impact\"")
    for pm in sorted(impact_picks - selected):
        errors.append(f"picks/{pm}.json is a 注目の構造 but is not selected in impact.json")
    return errors


def check_feature(feature: dict, picks: dict[str, dict]) -> list[str]:
    """feature.json: the pick exists among the articles, valid axes, a verbatim quote, a short reason."""
    errors = []
    pm = feature.get("pmcid")
    if pm not in picks:
        return [f"feature.json: {pm} is not one of this issue's articles"]
    if not feature.get("axes") or any(a not in FEATURE_AXES for a in feature["axes"]):
        errors.append(f"feature.json: axes must be a non-empty subset of {', '.join(FEATURE_AXES)}")
    reason = feature.get("reason") or ""
    if not reason:
        errors.append("feature.json: reason is missing")
    elif len(reason) > FEATURE_REASON_LIMIT:
        errors.append(f"feature.json: reason is {len(reason)} characters (limit {FEATURE_REASON_LIMIT})")
    corpus = corpus_for(pm)
    ev = feature.get("evidence") or []
    if not ev:
        errors.append("feature.json: needs at least one evidence quote")
    for e in ev:
        q = e.get("quote", "") if isinstance(e, dict) else str(e)
        if corpus is None:
            errors.append("feature.json: full-text packet missing")
            break
        if len(q) < 20 or normalize(q) not in normalize(corpus):
            errors.append(f"feature.json: quote not found in the paper: {q[:80]!r}")
    for c in feature.get("considered") or []:
        if c.get("pmcid") not in picks:
            errors.append(f"feature.json: considered {c.get('pmcid')} is not one of this issue's articles")
    return errors


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print(__doc__)
        return 2
    d = issue_dir(argv[0])
    cands = {c["pmcid"]: c for c in read_json(d / "candidates.json")["candidates"]}
    picks = sorted((d / "picks").glob("*.json"))
    if not picks:
        print(f"no picks in {d / 'picks'}")
        return 1
    failed = False
    impact_picks = set()
    for path in picks:
        pick = read_json(path)
        if pick.get("section") == "impact":
            impact_picks.add(pick.get("pmcid"))
        errors, warnings = check_pick(pick, cands.get(pick.get("pmcid")), corpus_for(pick.get("pmcid")))
        status = "NG" if errors else "OK"
        print(f"[{status}] {path.name}")
        for e in errors:
            print(f"   error: {e}")
        for w in warnings:
            print(f"   warning: {w}")
        failed |= bool(errors)
    by_id = {read_json(p).get("pmcid"): read_json(p) for p in picks}
    if (d / "feature.json").exists():
        feature_errors = check_feature(read_json(d / "feature.json"), by_id)
        print(f"[{'NG' if feature_errors else 'OK'}] feature.json")
        for e in feature_errors:
            print(f"   error: {e}")
        failed |= bool(feature_errors)
    elif len(picks) >= FEATURE_MIN:
        print(f"note: {len(picks)} articles but no feature.json (今日の一本)")
    record = read_json(d / "impact.json") if (d / "impact.json").exists() else {"scored": []}
    impact_errors = check_impact(record, cands, impact_picks)
    if record.get("scored") or impact_picks:
        print(f"[{'NG' if impact_errors else 'OK'}] impact.json "
              f"({sum(bool(s.get('selected')) for s in record.get('scored', []))} selected of {len(record.get('scored', []))} scored)")
        for e in impact_errors:
            print(f"   error: {e}")
        failed |= bool(impact_errors)
    written = {read_json(p).get("pmcid") for p in picks}
    missing = [c for c in cands.values()
               if c["kind"] in ("new_structure", "new_structure_ids_unlisted", "new_map_only") and c["pmcid"] not in written]
    if missing:
        print(f"note: {len(missing)} new-structure paper(s) have no article (explain why in the PR):")
        for c in missing:
            print(f"   {c['pmcid']} [{c['kind']}] {c['title'][:90]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
