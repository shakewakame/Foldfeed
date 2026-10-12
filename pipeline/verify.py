"""Check every pick of an issue before it is rendered.

    python3 -m pipeline.verify 2026-10-07

Errors (exit code 1):
  - missing or over-long fields, unknown tags
  - an evidence quote that does not appear verbatim in the paper's full text
  - "初構造" on a paper whose proteins already had PDB structures
  - a resolution (… Å) or PDB ID in the Japanese text that the paper / PDB does not support
  - impact.json (注目の論文): a quote per score that is not verbatim, a score out of range, a selected
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
from .schema import LAB_NOTE_FIELDS, LIMITS, TAGS

NEW_KINDS = ("new_structure", "new_structure_ids_unlisted", "new_map_only")

ANGSTROM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:Å|Å|オングストローム)")
PDB_ID_JA = re.compile(r"(?<![A-Za-z0-9])([1-9][A-Z0-9]{3})(?![A-Za-z0-9])")


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[‐‑‒–—−]", "-", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def japanese_fields(pick: dict) -> list[str]:
    out = [pick.get(k, "") or "" for k in ("headline", "takeaway", "target_label", "method_label", "ligand_label")]
    out += list((pick.get("summary") or {}).values())
    out += [h.get("text", "") for h in pick.get("highlights", [])]
    out += [v for k, v in (pick.get("lab_notes") or {}).items() if k in LAB_NOTE_FIELDS and v]
    return [x for x in out if isinstance(x, str)]


def check_pick(pick: dict, cand: dict | None, corpus: str | None) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    if cand is None:
        return [f"{pick.get('pmcid')} is not in candidates.json"], []
    if pick.get("section") == "impact":
        if cand["kind"] in NEW_KINDS:
            errors.append("marked as 注目の論文 (section: impact) but the paper reports a new structure")
    elif cand["kind"] not in NEW_KINDS:
        warnings.append(f"classified as {cand['kind']}, not a new structure (use \"section\": \"impact\" for 注目の論文)")

    def length(name, value, key):
        required, limit = LIMITS[key]
        if not value:
            if required:
                errors.append(f"{name} is missing")
            return
        if len(value) > limit:
            errors.append(f"{name} is {len(value)} characters (limit {limit})")

    for f in ("headline", "target_label"):
        length(f, pick.get(f), f)
    for f in ("background", "approach", "findings"):
        length(f"summary.{f}", (pick.get("summary") or {}).get(f), f"summary.{f}")

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

    notes = pick.get("lab_notes") or {}
    for k in LAB_NOTE_FIELDS:
        if notes.get(k):
            length(f"lab_notes.{k}", notes[k], "lab_note")
    if any(notes.get(k) for k in LAB_NOTE_FIELDS) and not notes.get("evidence"):
        warnings.append("lab_notes has content but no evidence quotes")
    quotes += [("lab_notes", e) for e in notes.get("evidence") or []]

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
        errors.append(f"picks/{pm}.json is a 注目の論文 but is not selected in impact.json")
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
