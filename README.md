```
bbs/
├ amezo.cgi        ← 本体。POSTを受けるたびにHTMLを書き換える
├ dai.cgi          ← 各板の「ヘッドライン」を集めて表示するだけ
├ blist.txt        ← 板の定義(板名,背景色,日本語名)
├ header.html      ← 各ページ上部の共通HTML(投稿フォームなど)
├ ng.txt / delpass.txt  ← 任意(NGワード / 管理者合言葉のハッシュ)
├ global.mem       ← 自動生成。IPごとの最終スレ立て時刻
└ main/
    ├ index.html   ← 1ページ目(CGIが毎回再生成)
    ├ index2.html  ← 2ページ目以降用(同上)
    ├ 261012182335.html ← スレッド1本=ファイル1個(IDは年月日時分秒)
    └ lasttime.txt ← 板ごとの最終投稿時刻
```
