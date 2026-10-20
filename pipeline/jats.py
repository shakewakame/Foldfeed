"""Parse JATS full-text XML into readable sections and extract PDB / EMDB identifiers."""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET

XLINK = "{http://www.w3.org/1999/xlink}href"

# Sections that never help summarise the science.
SKIP_TITLES = re.compile(
    r"acknowledg|author contribution|competing interest|conflict|funding|peer review|"
    r"reporting summary|supplementary|source data|footnote|references|associated data|"
    r"ethics|ethical|additional information|declaration|orcid", re.I)
SKIP_SEC_TYPES = {"ack", "ref-list", "fn-group", "supplementary-materials", "associated-data",
                  "history", "kwd-group", "COI-statement"}
DATA_TITLES = re.compile(r"data (and code )?availability|accession|data deposition", re.I)

PDB_HREF = re.compile(
    r"(?:10\.2210/pdb([0-9][a-z0-9]{3})/pdb|rcsb\.org/(?:structure|pdb/explore[^?]*\?structureId=)/?"
    r"([0-9][a-z0-9]{3})|pdbe/entry/pdb/([0-9][a-z0-9]{3})|pdbj\.org/mine/[a-z/]*([0-9][a-z0-9]{3}))",
    re.I)
EXTENDED_PDB = re.compile(r"\bpdb_0000([0-9][a-z0-9]{3})\b", re.I)
EMDB = re.compile(r"\bEMD-(\d{4,5})\b", re.I)
# A bare 4-character PDB code: digit 1-9 + 3 alphanumerics with at least one letter,
# letters all upper case (rejects "10mM", "2nd" and years).
PDB_TOKEN = re.compile(r"(?<![A-Za-z0-9-])([1-9][A-Z0-9]{3})(?![A-Za-z0-9])")
PDB_CONTEXT = re.compile(r"PDB|Protein Data Bank|accession|deposited|entry|entries|code", re.I)
# "Coordinates/maps have been deposited in the PDB/EMDB" — the paper reports its own structure,
# even when the accession codes only appear in a supplementary table. The auxiliary verb keeps out
# sentences about other people's entries ("structures deposited in the PDB include ...").
_REPO = r"(?:Protein Data Bank|\bPDB\b|\bwwPDB\b|\bEMDB\b|Electron Microscopy Data Bank)"
_DEPOSIT_VERB = r"\b(?:ha(?:ve|s) been|were|was|are|is|we(?: have)?)\s+(?:\w+\s+){0,2}?(?:deposited|submitted)\b"
DEPOSITED = re.compile(rf"{_DEPOSIT_VERB}[^.]{{0,200}}{_REPO}|{_REPO}[^.]{{0,200}}{_DEPOSIT_VERB}", re.I)
# "7 V3Q": a code split by typesetting. Only looked for inside deposition statements.
SPLIT_PDB = re.compile(r"(?<![A-Za-z0-9])([1-9]) ([A-Z0-9]{3})(?![A-Za-z0-9])")
PREPRINT_DOI_PREFIXES = ("10.1101/", "10.26434/", "10.21203/", "10.2139/", "10.20944/", "10.48550/")


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _paragraph_text(sec) -> str:
    """Text of a section, excluding nested <sec> children, tables and figures."""
    parts = []
    for child in sec:
        tag = child.tag
        if tag in ("sec", "title", "label", "table-wrap", "fig"):
            continue
        parts.append(_text(child))
    return "\n\n".join(p for p in parts if p)


def _walk_sections(sec, depth, out):
    if sec.get("sec-type") in SKIP_SEC_TYPES:
        return
    title_el = sec.find("title")
    title = _text(title_el) if title_el is not None else ""
    # Data availability is collected separately in parse().
    if title and (SKIP_TITLES.search(title) or DATA_TITLES.search(title)):
        return
    body = _paragraph_text(sec)
    out.append({"title": title, "depth": depth, "text": body})
    for child in sec.findall("sec"):
        _walk_sections(child, depth + 1, out)


INSTITUTE_STRONG = re.compile(r"univ|大学|polytechn|[ée]cole|\beth\b|technische|hochschule|\w \s*medical school", re.I)
INSTITUTE = re.compile(r"institut|hospital|hôpital|ospedale|clinic|college|academ|cent(?:er|re|ro)\b|foundation|"
                       r"max planck|helmholtz|leibniz|fraunhofer|riken|cnrs|inserm|embl|\bnih\b|\binc\b|\bltd\b|gmbh|\bA/S\b|研究所|センター", re.I)
INSTITUTE_WEAK = re.compile(r"laborator|school|facility|agency|council", re.I)
SYSTEM = re.compile(r"(?i)(?:the )?(?:university of (?:california|texas|massachusetts|illinois|maryland|colorado|"
                    r"north carolina|wisconsin|missouri|nebraska|tennessee|alabama|hawai.?i|london)|"
                    r"(?:state|city) university of new york)")
COUNTRY = re.compile(r"(?i)\b(united states(?: of america)?|usa|united kingdom|uk|(?:the )?netherlands|(?:republic of )?korea|"
                     r"south korea|(?:republic of )?singapore|hong kong|taiwan|china|japan|germany|france|italy|spain|"
                     r"switzerland|belgium|sweden|denmark|norway|finland|austria|poland|czech republic|israel|india|"
                     r"australia|new zealand|canada|brazil|mexico|argentina|chile|portugal|ireland|russia|t[üu]rkiye|"
                     r"turkey|iran|saudi arabia|egypt|south africa|thailand|malaysia|indonesia|vietnam|pakistan|"
                     r"bangladesh|algeria|tunisia|morocco|nigeria|hungary|greece|croatia|slovenia|slovakia|estonia|"
                     r"lithuania|latvia|luxembourg|iceland|qatar|united arab emirates|kenya|colombia|peru|philippines|"
                     r"sri lanka|nepal|ukraine|romania|bulgaria|serbia)\s*$")
DEPARTMENT = re.compile(r"^(?:the\s+)?(?:department|dept\b|division|faculty|graduate school|school of|section|unit\b|"
                        r"program|laboratory (?:of|for)|key laboratory|state key|center for|centre for)", re.I)


def _segments(text: str) -> list:
    """Comma-separated parts, with a company suffix ('Schrödinger, Inc.') kept on its name."""
    out = []
    for x in re.split(r"[,;]", re.sub(r"\s+", " ", text)):
        x = x.strip(" .;,")
        if out and re.fullmatch(r"(?i)inc|llc|ltd|gmbh|a/s|s\.?a|s\.?l|ag|co|plc|b\.?v", x):
            out[-1] += ", " + x
        elif x:
            out.append(x)
    return out


def _short_aff(aff) -> str:
    """'Princeton University, USA' out of an affiliation: the institution (not the department) and the country."""
    full = _text(aff)
    label = aff.find("label")
    if label is not None and full.startswith(_text(label)):
        full = full[len(_text(label)):]
    insts = list(aff.iter("institution"))
    named = [i for i in insts if (i.get("content-type") or "").lower() == "org-name"]
    insts = [_text(i) for i in named or insts
             if (i.get("content-type") or "").lower() not in ("dept", "department", "org-division")]
    parts = [re.sub(r"(?i)^affiliated with\s+", "", x) for x in _segments(", ".join(insts) if insts else full)]

    def score(x):
        n = 4 if INSTITUTE_STRONG.search(x) else 3 if INSTITUTE.search(x) else 1 if INSTITUTE_WEAK.search(x) else 0
        return n - (2 if DEPARTMENT.search(x) else 0) - (5 if re.search(r"\d{3}|@", x) else 0)
    at = max(reversed(range(len(parts))), key=lambda i: score(parts[i]), default=None)
    name = parts[at] if at is not None else ""
    # 'University of California, San Francisco': a multi-campus system is named with its campus
    if at is not None and SYSTEM.fullmatch(name) and at + 1 < len(parts) and not score(parts[at + 1]) \
            and not COUNTRY.search(parts[at + 1]):
        name += ", " + parts[at + 1]
    country = aff.find(".//country")
    country = _text(country).strip(" .,;") if country is not None else ""
    if not country:
        tail = (_segments(full)[-1:] or [""])[0]
        known = COUNTRY.search(tail)
        if known:
            country = known.group(1)
        elif tail != name and len(tail) < 30 and not re.search(r"\d|@|\.\w", tail):
            country = tail
    if country and country.lower() not in name.lower():
        name = f"{name}, {country}" if name else country
    return name[:120]


def _fold(text: str, umlaut: bool = False) -> str:
    """Lower-case ASCII letters only: 'Grüber' -> 'gruber' (or 'grueber' with umlaut=True)."""
    if umlaut:
        text = text.translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}))
    text = unicodedata.normalize("NFKD", text)
    return re.sub(r"[^a-z]", "", "".join(ch for ch in text if not unicodedata.combining(ch)).lower())


def _named(note: str, emails: list, people_: list) -> list:
    """The authors a correspondence note names, for papers that do not link the note to anyone: an author whose
    surname is a word of the note, or whose name is an email's local part ('jiao.li', 'r-gennis', 'zhujiapeng').
    A surname two authors share counts only with the given name or its initial beside it."""
    words = {_fold(w) for w in re.findall(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*", note)}
    locals_ = [(_fold(e.split("@")[0]), _fold(e.split("@")[0], True)) for e in emails]
    found = []
    for i, (given, sur) in enumerate(people_):
        g, inits = _fold(given), "".join(w[0] for w in re.findall(r"[^\W\d_]+", given)).lower()
        forms = {v for u in (False, True) for v in (_fold(sur, u),)} - {""}
        shared = sum(1 for _, s in people_ if _fold(s) == _fold(sur)) > 1
        by_word = any(f in words for f in forms) and (not shared or g in words or
                                                      re.search(rf"\b{re.escape(given[:1])}\.?\s*{re.escape(sur)}", note))
        by_mail = any(loc in {g + f, f + g, inits + f, f + inits, (inits[:1] + f), (f + inits[:1])} or
                      (len(f) >= 5 and f in loc and not shared)
                      for f in forms for pair in locals_ for loc in pair if loc)
        if by_word or by_mail:
            found.append(i)
    return found


def people(root) -> dict:
    """The first author and the corresponding authors, each {"name", "aff"}: aff is the institution and country of
    their first affiliation. Corresponding authors are the ones the paper marks: corresp="yes", or an xref to a
    <corresp> or correspondence footnote; when it links nobody, the authors that note names (_named). {} when the
    paper lists no authors; "corresp" is [] when nothing marks one."""
    meta = root.find(".//article-meta")
    meta = meta if meta is not None else root
    affs = {a.get("id"): a for a in meta.iter("aff") if a.get("id")}
    notes = [c for c in meta.iter("corresp")]
    for n in meta.iter("author-notes"):
        notes += [f for f in n.iter("fn") if re.search(r"(?i)correspond|to whom", _text(f)) or
                  (f.get("fn-type") == "corresp") or re.match(r"(?i)cor", f.get("id") or "")]
    note_ids = {n.get("id") for n in notes if n.get("id")}
    contribs = [c for g in meta.iter("contrib-group") if g.get("content-type") in (None, "author", "authors")
                for c in g.findall("contrib") if (c.get("contrib-type") or "author") == "author"]

    def names(c):
        sur, given = c.find(".//name/surname"), c.find(".//name/given-names")
        return (_text(given) if given is not None else "", _text(sur) if sur is not None else "")

    def person(c):
        name = " ".join(x for x in names(c) if x)
        if not name:
            sn = c.find(".//string-name")
            sn = sn if sn is not None else c.find("collab")
            name = _text(sn) if sn is not None else ""
        own = c.find("aff")
        ref = next((affs[r] for x in c.iter("xref") if x.get("ref-type") == "aff"
                    for r in (x.get("rid") or "").split() if r in affs), None)
        aff = own if own is not None else ref if ref is not None else (next(iter(affs.values())) if len(affs) == 1 else None)
        return {"name": re.sub(r"\s+", " ", name).strip(), "aff": _short_aff(aff) if aff is not None else ""}

    def marked(c):
        return c.get("corresp") == "yes" or any(
            x.get("ref-type") == "corresp" or set((x.get("rid") or "").split()) & note_ids for x in c.iter("xref"))

    found = [(c, person(c)) for c in contribs]
    found = [(c, p) for c, p in found if p["name"]]
    if not found:
        return {}
    corresp = [p for c, p in found if marked(c)]
    if not corresp and notes:
        text = " ".join(_text(n) for n in notes)
        emails = [_text(e) for n in notes for e in n.iter("email")]
        corresp = [found[i][1] for i in _named(text, emails, [names(c) for c, _ in found])]
    return {"first": found[0][1], "corresp": corresp}


def parse(xml: str) -> dict:
    root = ET.fromstring(xml)
    title_el = root.find(".//article-meta/title-group/article-title")
    abstract_el = root.find(".//article-meta/abstract")

    sections = []
    body = root.find("body")
    if body is not None:
        intro_text = _paragraph_text(body)
        if intro_text:
            sections.append({"title": "", "depth": 1, "text": intro_text})
        for sec in body.findall("sec"):
            _walk_sections(sec, 1, sections)

    # Data availability lives in <back>, in notes, or in a sec-type statement.
    data_avail = []
    for sec in root.iter("sec"):
        t = sec.find("title")
        if (t is not None and DATA_TITLES.search(_text(t))) or \
                sec.get("sec-type") in ("data-availability", "data-availability-statement"):
            data_avail.append(_text(sec))
    for notes in root.iter("notes"):
        if notes.get("notes-type") in ("data-availability", "data-availability-statement"):
            data_avail.append(_text(notes))
    for cm in root.iter("custom-meta"):  # PLOS puts the statement in article-meta
        name, value = cm.find("meta-name"), cm.find("meta-value")
        if name is not None and value is not None and "data availability" in _text(name).lower():
            data_avail.append(_text(value))

    captions = []
    for fig in root.iter("fig"):
        label = fig.find("label")
        cap = fig.find("caption")
        if cap is not None:
            captions.append(f"{_text(label) if label is not None else 'Figure'}: {_text(cap)}")
    tables = []
    for tw in root.iter("table-wrap"):
        label = tw.find("label")
        cap = tw.find("caption")
        tables.append({"label": _text(label) if label is not None else "Table",
                       "caption": _text(cap) if cap is not None else "",
                       "text": _text(tw.find("table")) if tw.find("table") is not None else ""})

    ids = extract_ids(root, "\n".join(data_avail), sections)
    article_ids = {a.get("pub-id-type"): _text(a) for a in root.findall(".//article-meta/article-id")}
    all_text = "\n".join([*data_avail, *(s["text"] for s in sections)])
    return {
        "journal": _text(jt) if (jt := root.find(".//journal-meta//journal-title")) is not None else None,
        "doi": (article_ids.get("doi") or "").lower() or None,
        "pmid": article_ids.get("pmid"),
        "deposition_statement": bool(DEPOSITED.search(all_text)),
        "title": _text(title_el) if title_el is not None else "",
        "abstract": _text(abstract_el) if abstract_el is not None else "",
        "sections": sections,
        "data_availability": "\n\n".join(dict.fromkeys(data_avail)),
        "figure_captions": captions,
        "tables": tables,
        "people": people(root),
        **ids,
    }


def extract_ids(root, data_availability: str, sections: list[dict]) -> dict:
    pdb, emdb = [], []

    def add(lst, x):
        if x not in lst:
            lst.append(x)

    for link in root.iter("ext-link"):
        href = link.get(XLINK, "") or ""
        text = _text(link)
        if link.get("ext-link-type", "").lower() == "pdb" and re.fullmatch(r"[1-9][A-Za-z0-9]{3}", text):
            add(pdb, text.upper())
        for m in PDB_HREF.finditer(href):
            add(pdb, next(g for g in m.groups() if g).upper())
        for m in EMDB.finditer(href + " " + text):
            add(emdb, f"EMD-{m.group(1)}")

    texts = [data_availability] + [s["text"] for s in sections]
    for t in texts:
        for m in EXTENDED_PDB.finditer(t):
            add(pdb, m.group(1).upper())
        for m in EMDB.finditer(t):
            add(emdb, f"EMD-{m.group(1)}")
    # Bare codes: only in the data-availability text, or in sentences that talk about the PDB.
    sentences = re.split(r"(?<=[.;])\s+", data_availability)
    for t in texts[1:]:
        sentences += [s for s in re.split(r"(?<=[.;])\s+", t) if PDB_CONTEXT.search(s)]
    for s in sentences:
        if not PDB_CONTEXT.search(s):
            continue
        for m in PDB_TOKEN.finditer(s):
            tok = m.group(1)
            if re.search(r"[A-Z]", tok):
                add(pdb, tok)

    # Deposition statements: the data-availability text plus body sentences saying the authors
    # deposited coordinates (and the sentence after, which often lists the codes).
    deposition = [data_availability]
    for t in texts[1:]:
        parts = re.split(r"(?<=[.;])\s+", t)
        for i, sent in enumerate(parts):
            if DEPOSITED.search(sent):
                deposition += parts[i:i + 2]
    deposition_text = "\n".join(deposition)
    own = set()
    for m in PDB_TOKEN.finditer(deposition_text):
        if re.search(r"[A-Z]", m.group(1)):
            add(pdb, m.group(1))
            own.add(m.group(1))
    for m in SPLIT_PDB.finditer(deposition_text):
        if re.search(r"[A-Z]", m.group(2)):
            add(pdb, m.group(1) + m.group(2))
            own.add(m.group(1) + m.group(2))
    for m in re.finditer(r"\bEMDB?[:\s]+(?:ID[:\s]+)?(\d{4,5})\b", deposition_text):
        add(emdb, f"EMD-{m.group(1)}")
    up = deposition_text.upper()
    own |= {p for p in pdb if p in up}
    return {"pdb_ids": pdb, "emdb_ids": emdb, "pdb_ids_in_deposition": sorted(own)}


def packet_text(parsed: dict, max_chars: int = 120_000) -> str:
    """Full text as Markdown for the article writer; also the corpus that evidence quotes are checked against."""
    out = [f"# {parsed['title']}", "", "## Abstract", parsed["abstract"], ""]
    for s in parsed["sections"]:
        if s["title"]:
            out.append("#" * min(s["depth"] + 1, 4) + " " + s["title"])
        if s["text"]:
            out.append(s["text"])
        out.append("")
    if parsed["figure_captions"]:
        out += ["## Figure legends", *parsed["figure_captions"], ""]
    for t in parsed["tables"]:
        out += [f"## {t['label']} {t['caption']}".rstrip(), t["text"][:4000], ""]
    if parsed["data_availability"]:
        out += ["## Data availability", parsed["data_availability"], ""]
    text = "\n".join(out)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... truncated ...]\n"
    return text
