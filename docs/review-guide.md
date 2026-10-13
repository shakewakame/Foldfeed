# 毎朝の号の確認とマージ（編集者向け）

毎朝 9:07（日本時間）にルーティンが号を作り、Pull Request（PR）を出す。
`config/editorial.json` の `review_required` が `true` の間は、編集者が確認してマージするまで公開されない。
確認とマージは、スマホのブラウザだけで完結する（Claude アプリでルーティンの作業内容を開く必要はない）。

## スマホでの手順

### 最初に一度だけ
- **確認待ちの一覧**を開いて、ホーム画面に追加する：
  https://shakewakame.github.io/Structuralbiology/preview/
  - iPhone（Safari）：下の共有ボタン →「ホーム画面に追加」
  - Android（Chrome）：右上のメニュー →「ホーム画面に追加」
- （任意）GitHub アプリを入れてログインし、通知を許可する。PR ができたときのメンション通知がスマホに届く。

### 毎朝（9:10 ごろ以降）
1. ホーム画面の「確認待ちの号」を開く。その日の号が出ていなければ、数分おいて開き直す。
2. 「**見た目を確認**」で、公開サイトと同じ見た目のプレビューを読む（上に朱色の「プレビュー（未公開）」の帯が出る）。
3. 問題がなければ、帯か一覧の「**Pull Request #番号（マージ）**」を押す。GitHub のページ（アプリがあればアプリ）が開くので、
   下の方の「**Merge pull request**」→「**Confirm merge**」。1〜2 分で公開サイトに出る。
4. 直してほしい点があれば、同じ PR のページの一番下のコメント欄に書く（マージはしない）。
   翌朝のルーティンがコメントを読み、今後の方針は `docs/style-notes.md` に追記される。
   その日のうちに直したい場合は、Claude に「<日付> の号の○○を直して」と頼む。

## パソコンでの手順

- PR の一覧：https://github.com/shakewakame/Structuralbiology/pulls （リポジトリ上部の「Pull requests」タブ）
- 見た目：上と同じプレビュー。PR の本文の先頭と、PR に付くメンションのコメントにもリンクがある。
- 文章と根拠を細かく見るなら、PR の「Files changed」タブで `data/issues/<日付>/README.md` を開く
  （右上の「…」→「View file」で表示形式になる）。注目の構造の採点表もこのファイルの末尾にある。

## 通知について

- PR はあなた自身のアカウントで作られるため、GitHub は「PR が作られた」という通知を送らない。
- 代わりに、PR ができると GitHub Actions がその PR に **@ユーザー名 付きのコメント**を書く（`.github/workflows/notify.yml`）。
  これは通常の通知として届く。届かない場合は GitHub の Settings → Notifications で
  「Participating, @mentions and custom」がメールまたはモバイルに届く設定になっているか確認する。
- Claude アプリにも、ルーティンの実行が終わったときのプッシュ通知が届く（ルーティンの設定で変更できる）。
