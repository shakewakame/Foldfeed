"""The editorial fields the writer fills in for each pick (data/issues/<date>/picks/<PMCID>.json).

Facts (title, journal, PDB entries, resolution, ligands, UniProt) are never written by hand:
they come from candidates.json and are merged in at render time.
"""

TAGS = ["初構造", "新しい結合部位", "機構の解明", "創薬・モダリティ", "手法の工夫", "実験Tips"]

LAB_NOTE_FIELDS = {
    "expression": "発現系",
    "construct": "コンストラクト",
    "purification": "精製",
    "sample_prep": "結晶化／グリッド作製",
    "data_collection": "データ収集・解析",
}

# The article's short introduction, four lines in this order (the page shows the label, then the text).
# Each line is short on purpose and says only what the paper says; each needs a verbatim quote in summary.evidence.
SUMMARY_FIELDS = {
    "question": "疑問・課題",
    "approach": "アプローチ",
    "result": "結果",
    "learned": "わかったこと",
}

# An optional fifth line: what the paper itself says is still open (a limit it states, a question it leaves, work it
# calls for). Written only when the paper says it; needs its own quote like the four lines above.
SUMMARY_OPTIONAL = {"open": "残された課題"}

# What a protein is, for browsing the archive (proteins.html). One to three per article, from this list.
CATEGORIES = {
    "kinase": "キナーゼ",
    "protease": "プロテアーゼ",
    "receptor": "GPCR・受容体",
    "channel": "イオンチャネル",
    "transporter": "トランスポーター",
    "enzyme": "酵素（その他）",
    "nucleic": "DNA・RNA に関わるもの",
    "chromatin": "クロマチン・ヒストン",
    "translation": "リボソーム・翻訳・フォールディング",
    "regulation": "転写調節・シグナル伝達",
    "immune": "免疫（抗体・MHC・TCR・自然免疫）",
    "pathogen": "感染・ウイルス・防御系",
    "photosynthesis": "光合成・電子伝達",
    "other": "その他",
}

# field: (required, max characters)
LIMITS = {
    "headline": (True, 36),
    "subhead": (True, 64),
    "target_label": (True, 30),
    "target_en": (True, 52),
    "summary.question": (True, 80),
    "summary.approach": (True, 80),
    "summary.result": (True, 90),
    "summary.learned": (True, 100),
    "summary.open": (False, 80),
    "highlight.text": (True, 200),
    "lab_note": (False, 160),
}
FIGURE_TITLE_LIMIT = 16   # a figure_notes title / a compare panel title

EXAMPLE = {
    "pmcid": "PMC00000000",
    "rank": 1,
    "headline": "主見出し（36字以内）：発見の面白さを先に見せる短い一文。一覧と記事の頭に大きく出る",
    "subhead": "補足（64字以内）：主見出しの内容を、構造や手法の言葉で具体的に。一覧では主見出しの下に出る",
    "target_label": "標的の短い表記（例：RyR1（ウサギ骨格筋））",
    "target_en": "標的の英語表記（52字以内。サイトの TARGET 欄に出る。例：RyR1 (rabbit skeletal muscle)）",
    "summary": {
        "question": "疑問・課題（80字以内）：論文が「分かっていなかった」と書いていること",
        "approach": "アプローチ（80字以内）：何をどう調べたか",
        "result": "結果（90字以内）：得られた観察・数値",
        "learned": "わかったこと（100字以内）：その結果から論文が結論していること（著者が推測なら推測と書く）",
        "open": "残された課題（80字以内・任意）：論文自身が「まだ分からない」「今後の課題」と書いていることだけ。書いていなければこの行ごと省く",
        "evidence": {
            "question": [{"quote": "上の各項目を支える、論文本文からの逐語引用（英語のまま）", "section": "Introduction"}],
            "approach": [{"quote": "…", "section": "Abstract"}],
            "result": [{"quote": "…", "section": "Results"}],
            "learned": [{"quote": "…", "section": "Abstract"}],
            "open": [{"quote": "…（open を書くときだけ）", "section": "Discussion"}],
        },
    },
    "categories": ["receptor"],  # CATEGORIES から 1〜3 個。タンパク質の種類で探すための分類
    "highlights": [
        {"tag": "機構の解明", "text": "面白い点の説明",
         "evidence": [{"quote": "論文本文からの逐語引用（英語のまま）", "section": "Results"}]},
    ],
    "lab_notes": {
        "expression": "例：HEK293F で一過性発現",
        "construct": None,
        "purification": None,
        "sample_prep": None,
        "data_collection": None,
        "evidence": [{"quote": "Methods からの逐語引用", "section": "Methods"}],
    },
    "keywords": ["RyR1", "スタチン"],
    "editor_notes": "確信が持てない点や、編集者に確認してほしい点（任意）",
    # Optional overrides for the structure figure and the page margin:
    "ga_pdb_id": "構造図に使う PDB ID（省略時は分解能が最も高い自前の構造）",
    "method_label": "手法・分解能の表示（PDB 情報がないときだけ。手法は英語で：Cryo-EM / Cryo-ET / X-ray / NMR。例：Cryo-EM・2.8 Å）",
    "ligand_label": "結合分子の表示（略号より分かりやすい名前。例：オラパリブ、タラゾパリブ）",
    "legend_labels": ["構造図の色の凡例名を entity の順に上書き（任意）"],
    "ligand_names": {"A1CKT": "OX04529", "43M": "4CmC"},
    "section": "既存の構造を使った研究（注目の構造の採点で選んだもの）なら \"impact\"。新しい構造の記事では書かない",
    # Optional: only when none of the paper's proteins could be matched to UniProt (PDB entries not yet released, IDs not
    # in the text) and the paper itself says its protein had no structure before. Puts the article in New Structure.
    "first_claim": [{"quote": "論文本文の逐語引用（例：the first structure of ...）", "section": "Results"}],
    "site_ligand": "結合部位の拡大図に使うリガンドの ID（省略時は ligand_names の最初、なければ最大のリガンド。\"\" で拡大図なし）",
    # Optional: what the figure points at. legend_labels / ligand_names already put a leader-line label on each protein and ligand.
    "figure_notes": [{"title": "ゲートヘリックス（16字以内）", "sub": "Wzt の一部（任意）",
                      "entity": "凡例の何番目のタンパク質か（1 始まり）", "range": ["最初の残基番号", "最後の残基番号（論文の番号）"]}],
    # Optional: a second figure that shows what the paper found, from two or three PDB entries of the same assembly
    # (e.g. open and closed). Superposed on one polymer, same view; the stretch of figure_notes[0] in the accent colour.
    "compare": {"panels": [{"pdb": "25IN", "title": "基質結合型"}, {"pdb": "25IO", "title": "ATP 結合型"}],
                "align": "重ね合わせに使うタンパク質（凡例の何番目か、1 始まり）",
                "focus": "（任意）省略時は figure_notes の最初",
                "mark": {"entity": 2, "residue": 36, "text": "約40 Å（論文が書いている距離）"}},
    # figure_notes / compare を付けたときは必須：論文本文の逐語引用（残基の範囲・構造の違い・距離を支えるもの）
    "figure_evidence": [{"quote": "…", "section": "Results"}],
}


# data/issues/<date>/feature.json — 今日の一本: the one article of the day shown large on the front page.
# Optional; only written when the issue has at least FEATURE_MIN articles. Checked by pipeline.verify.
FEATURE_MIN = 3
FEATURE_AXES = {"structure": "構造生物学", "biology": "生命科学", "drug": "創薬"}
FEATURE_REASON_LIMIT = 100
FEATURE_EXAMPLE = {
    "pmcid": "PMC00000000",
    "axes": ["structure", "biology"],
    "reason": "選んだ理由（100字以内）。上の軸のどこが突出しているかを、論文が実際に示したことだけで書く",
    "evidence": [{"quote": "理由を支える、論文本文からの逐語引用（英語のまま）", "section": "Abstract"}],
    "considered": [{"pmcid": "PMC00000001", "note": "最後まで迷った論文と、選ばなかった理由（任意）"}],
}
