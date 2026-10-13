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

# field: (required, max characters)
LIMITS = {
    "headline": (True, 32),
    "subhead": (True, 56),
    "target_label": (True, 30),
    "summary.background": (True, 140),
    "summary.approach": (True, 140),
    "summary.findings": (True, 160),
    "highlight.text": (True, 200),
    "lab_note": (False, 160),
}

EXAMPLE = {
    "pmcid": "PMC00000000",
    "rank": 1,
    "headline": "主見出し（32字以内）：発見の面白さを先に見せる短い一文。一覧と記事の頭に大きく出る",
    "subhead": "補足（56字以内）：主見出しの内容を、構造や手法の言葉で具体的に。一覧では主見出しの下に出る",
    "target_label": "標的の短い表記（例：RyR1（ウサギ骨格筋））",
    "summary": {
        "background": "背景：何が分かっていなかったか",
        "approach": "やったこと：手法と対象",
        "findings": "分かったこと：主な発見",
    },
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
    "method_label": "手法・分解能の表示（PDB 情報がないときだけ。例：クライオ電顕・2.8 Å）",
    "ligand_label": "結合分子の表示（略号より分かりやすい名前。例：オラパリブ、タラゾパリブ）",
    "legend_labels": ["構造図の色の凡例名を entity の順に上書き（任意）"],
    "ligand_names": {"A1CKT": "OX04529", "43M": "4CmC"},
    "section": "注目の構造（新しい構造以外）なら \"impact\"。新しい構造の記事では書かない",
    "site_ligand": "結合部位の拡大図に使うリガンドの ID（省略時は ligand_names の最初、なければ最大のリガンド。\"\" で拡大図なし）",
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
