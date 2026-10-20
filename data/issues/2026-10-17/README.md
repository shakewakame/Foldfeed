<p align="center"><img src="../../../assets/logo.svg" alt="構造生物学デイリー" height="56"></p>

# 2026-10-17 号

**今日の新しい構造：1 本**　Europe PMC に 2026-10-16 に登録されたオープンアクセス論文 23 本のうち、新しい構造を報告した論文は 1 本。すべて紹介する。

**Structure-Based Study 1 本**（New Structure：構造が初めて解かれたタンパク質。Structure Update：既知のタンパク質の新しい構造。Structure-Based Study：既存の構造を使った注目の研究）

| # | 標的 | 手法・分解能 | 見出し |
|---|---|---|---|
| 1 | ZBP1のZαドメイン（ヒト） | NMR・CD・ITC | ZBP1は、できあがったZ型RNAにだけ結合する |

> この号は AI が論文本文から作成した**レビュー用の下書き**です。数値・ID・リガンドは PDB / UniProt から取得し、AI の記述には本文からの引用を付けています。

---

# Structure-Based Study

## 1｜ZBP1のZαドメイン（ヒト）

### ZBP1 specifically binds pre-formed Z-RNA, limiting its potential pool of nucleic acid ligands compared to ADAR1

*Nucleic Acids Research*（2026-10-13） · [論文](https://doi.org/10.1093/nar/gkag972) · ライセンス: cc by

筆頭著者：Jeffrey B Krall（University of Colorado Anschutz Medical Campus, United States）　責任著者：Beat Vögeli（University of Colorado Anschutz Medical Campus, United States）

**ZBP1は、できあがったZ型RNAにだけ結合する**

NMR・CD・ITCで調べると、無修飾のRNAをZ型に変換できなかった

- **疑問・課題**：ZBP1はADAR1と同様にA型RNAをZ型に変換できると考えられてきたが、実験での確認はなかった。
- **アプローチ**：ZBP1のZα1・Zα2ドメインと核酸の結合を、NMR・CD・ITCなどで調べた。
- **結果**：二本鎖DNAはZ型にできたが、無修飾の(CpG)n RNAはZ型に変換できなかった。
- **わかったこと**：ZBP1が感知するZ型RNAは、すでにZ型になりやすいものに限られる可能性がある。
- **残された課題**：ADAR1がZBP1を抑えるという競合モデルは、さらに検証が必要だ。

<details><summary>この紹介文の根拠（論文本文）</summary>

> ZBP1 has been previously shown to bind and convert B-DNA to the Z-conformation and was assumed to be able to convert A-RNA as well, despite the lack of experimental validation.  
> — 疑問・課題／Abstract

> Here, we use NMR and other biophysical and biochemical experiments to characterize the Z-DNA and Z-RNA binding properties of ZBP1’s Zα1 and Zα2 domains.  
> — アプローチ／Abstract

> While ZBP1’s Zα domains are able to convert and stabilize unmodified dsDNA in the Z-conformation, they are incapable of flipping unmodified (CpG)n repeat dsRNA.  
> — 結果／Abstract

> This functional difference likely restricts ZBP1 sensing to pre-Z-forming nucleic acids, preventing aberrant immune activation and reflecting mechanistic differences from ADAR1.  
> — わかったこと／Introduction

> This model is consistent with previously published genetic studies [2, 37, 102]; however, more research is needed to validate the competitive binding model.  
> — 残された課題／Discussion

</details>

<sub>分類：DNA・RNA に関わるもの・免疫（抗体・MHC・TCR・自然免疫）</sub>

#### 読みどころ

ZBP1は無修飾のRNAをZ型に変換できず、A型に近い形で結合した。ADAR1のZαドメインとは性質が異なる。

<details><summary>根拠（論文本文）</summary>

> but cannot convert A-RNA, instead binding it in an A-form-like mode.  
> — Introduction

> These results contrast with ADAR1’s Zα domain, which can bind and flip both dsDNA and dsRNA into the Z-conformation  
> — Abstract

</details>

8-メチルグアニンで安定化したZ型RNAにはZBP1が結合した。酸化で生じる8-オキソグアニンはさらに強くZ型を安定化し、生理的な標的候補になりうる。

<details><summary>根拠（論文本文）</summary>

> To our surprise, 8-oxoguanine seems to be a greater natural Z-RNA stabilizer than the 8-methylguanine modification (Fig. 5A).  
> — Results

> 8-Oxoguanine is the most prevalent oxidative product upon exposure to ROS  
> — Results

</details>

#### 構造データ

論文が使った PDB 構造はない。

#### 実験メモ

- **発現系**：大腸菌LOBSTR-BL21(DE3)で発現。15N標識はM9培地
- **データ収集・解析**：Bruker 600 MHzで15N-HSQC滴定

<details><summary>根拠（論文本文）</summary>

> Briefly, the plasmids were transformed and expressed in LOBSTR-BL21(DE3) Escherichia coli.  
> — Methods

> All experiments were carried out on a Bruker 600 MHz spectrometer  
> — Methods

</details>

> 📝 編集者への確認事項：結晶・クライオ電顕の構造決定はなく、NMR（化学シフト摂動）・CD・ITC で結合様式と立体配座を調べた研究。structure は 1 点とした。

---

## 注目の構造の採点（22 本を確認し、1 本を採点）

基準は `pipeline/impact.py`（10 点満点、6 点以上から最大 3 本）。

| 選定 | 論文 | 種類 | novelty | reach | structure | support | 計 | 理由 |
|---|---|---|---|---|---|---|---|---|
| ◯ | PMC13659236 ZBP1 specifically binds pre-formed Z-RNA, limiting its poten | 既存構造で機構を解明 | 2 | 2 | 1 | 2 | 7 | NMR・CD・ITCで、ZBP1がZ型RNAへの変換能を持たないことを示した。結晶構造は決めていないが、NMRで結合様式を実験的に確かめた。 |

<sub>novelty：何が初めてか / reach：誰に効くか / structure：実験で決めた構造（X 線・クライオ電顕・NMR / support：裏付けの強さ（2＝独立した実験で検証：変異体・機能測定・既存手法との比較など／1＝一部の検証のみ／0＝計算のみで実験的検証なし）</sub>
