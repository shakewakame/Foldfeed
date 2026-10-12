# 毎朝の号の確認とマージ（編集者向け）

毎朝 9:07（日本時間）にルーティンが号を作り、Pull Request（PR）を出す。
`config/editorial.json` の `review_required` が `true` の間は、編集者が確認してマージするまで公開されない。

## 1. 届いたことを知る

- PR は編集者自身のアカウントで作られるため、GitHub は「PR が作られた」という通知を送らない。
- 代わりに、PR ができると GitHub Actions がその PR に **@ユーザー名 付きのコメント**を書く。
  これは通常の通知として届く（GitHub の通知設定で Participating / @mentions がメールまたはアプリに届く設定になっていること）。
- Claude アプリにも、ルーティンの実行が終わったときのプッシュ通知が届く（ルーティンの設定で変更できる）。

## 2. PR を開く

- ブラウザ：https://github.com/shakewakame/Structuralbiology/pulls （リポジトリ上部の「Pull requests」タブ）
- スマホ：GitHub アプリの「Pull Requests」
- 通知のコメントのリンクからも開ける。

## 3. 見た目を確認する

- PR の本文と通知のコメントにある **プレビュー**のリンクを開く：
  `https://shakewakame.github.io/Structuralbiology/preview/<日付>.html`
  - 公開サイトと同じ見た目で、上に朱色の「プレビュー（未公開）」の帯が出る。トップページからはリンクされず、検索にも出ない。
  - PR を出してから 1〜2 分で表示される。PR に修正が push されると、プレビューも作り直される。
- 文章と根拠を細かく見るなら、PR の「Files changed」タブで `data/issues/<日付>/README.md` を開く（右上の「…」→「View file」で表示形式になる）。

## 4. マージする／直してもらう

- 問題がなければ、PR の下の「Merge pull request」→「Confirm merge」。1〜2 分で公開サイトに出る。
- 直してほしい点は PR にコメントする。翌朝のルーティンが直近の PR のコメントを読み、今後の方針は `docs/style-notes.md` に追記される。
  その日のうちに直したい場合は、Claude に「<日付> の号の○○を直して」と頼む。
