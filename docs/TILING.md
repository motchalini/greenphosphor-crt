# タイル型ウィンドウ管理 — Forge(メンテ済みフォーク)セットアップ

GreenPhosphor-CRT デスクトップのタイリングは、Forge のメンテ済みフォーク
[jcrussell/forge](https://github.com/jcrussell/forge)(UUID は本家と同じ
`forge@jmmaranan.com`)で行う。v49-90-beta.3 / GNOME 48 で動作確認(2026-08-17)。

経緯:

1. 2026-08-16: 本家 Forge v89(EGO 版)導入。バグ3件を踏みローカルパッチで対処
   (末尾アーカイブ参照)。
2. 2026-08-17: 本家がメンテナ不在("Needs a new maintainer")・修正未リリースと
   判明し **Tiling Shell へ乗り換え → 同日撤回**。Tiling Shell はレイアウトベースで、
   awesome/i3 型の動的タイリング(窓が増えるたび自動分割)ができないため
   (アーカイブ参照)。
3. 同日、本家 README が案内するフォーク jcrussell/forge に移行。
   **ローカルパッチ3本はすべてフォークで本質的に修正済み**
   (no-op move ガード / float トグルの float・tile 両方向 override 化+メモリ同期 /
   古い wmId のファイル掃除自動化)を移行前にコードで確認した。

## インストール(フォーク版)

EGO には無いので GitHub Releases から入れる:

```sh
gh release download v49-90-beta.3 -R jcrussell/forge -p "forge@jmmaranan.com.zip" -p SHA256SUMS
sha256sum -c SHA256SUMS
gnome-extensions install --force "forge@jmmaranan.com.zip"
gnome-extensions enable forge@jmmaranan.com
```

- UUID が本家と同じなので、stylesheet・gsettings・windows.json は**そのまま引き継がれる**。
- その セッションで一度も Forge がロードされていなければ(無効のまま
  ログインした直後など)**再ログインなしで即 ACTIVE になる**。ロード済みの場合は
  ES モジュールキャッシュのため再ログインが必要(本家と同じ)。
- 丸角拡張 `rounded-window-corners@fxgn` はスクエア枠と衝突するため無効化のまま。

## テーマ適合の設定(燐光緑CRT仕様)

**枠の色・太さ・角丸は gsettings ではなく Forge のユーザースタイルシートが実効値**
(v89 で確認した仕様。フォークでも同じ機構・同じファイルで動作):

- ファイル: `~/.config/forge/stylesheet/forge/stylesheet.css`
  (**マスターコピーは本リポジトリの `src/forge/stylesheet.css`**)
- 適用済みの内容: フォーカス窓(`.window-tiled-border`)= 明るい燐光緑
  `rgba(102,224,122,1)` 3px、分割ヒント = 淡い燐光緑 `rgba(102,224,122,0.55)`
  (2026-08-17 に CRT アンバーから変更。フォーカス枠の横でオレンジが
  「直り残り」に見えるため)、
  スタック=アンバー/タブ=淡緑も 3px、全クラス `border-radius: 0`(スクエア)。
  タブバー(`.window-tabbed-*`)も緑黒。フォーク新機能のチートシート
  オーバーレイ(`.forge-cheatsheet*`)も緑黒に着色。
- **注意: フォーク/本体は css バージョンが上がると(gsettings
  `css-last-update`)このファイルを既定スタイル(サーモン色・角丸14px)で
  上書きする**。2026-08-17 のフォーク導入時に実際に発生(旧内容は同ディレクトリの
  `stylesheet.css.bak` に自動退避されていた)。色が変わったらマスターコピーから
  復元して `gnome-extensions disable forge@… && enable forge@…` で再読込:
  `cp src/forge/stylesheet.css ~/.config/forge/stylesheet/forge/stylesheet.css`
- フォークは gsettings に `focus-border-radius`(既定14)を持つ。スクエア維持の
  ため **0 に設定済み**(stylesheet の radius 0 と併せて両建て)。
- **枠の太さは 3px が上限**(v89 時代の実測。gap>0 のとき枠ウィジェットが窓の
  3px 外側に乗る設計のため。4px 以上は窓内に食い込み端の文字に被る。なお枠は
  フォーカス窓にしか描画されず、非フォーカス窓はテーマ側の 1px 枠のみ)。
- **枠クラスに `box-shadow` のグローを付けてはいけない**(枠は窓全面に重なる
  透明ウィジェットで、窓の内側全体が緑がかる。2026-08-16 実地確認)。
- v89 では stylesheet に CSS コメントを書くと拡張が ERROR になったが、
  **フォークはコメント対応済み**(既定ファイル自体にコメントがあり、コメント入りで
  ACTIVE を確認)。

挙動系の設定は gsettings(引き継ぎ済み):

```sh
d=~/.local/share/gnome-shell/extensions/forge@jmmaranan.com/schemas
gsettings --schemadir $d set org.gnome.shell.extensions.forge window-gap-hidden-on-single true
gsettings --schemadir $d set org.gnome.shell.extensions.forge auto-split-enabled true
```

- **`auto-split-enabled`(縦横交互の自動分割・有効化済み)**: 新しい窓を開くとき、
  取り付け先の窓の**アスペクト比**で分割方向を自動決定する(横長→横並び/縦長→縦積み。
  分割を繰り返すと awesome の dwindle 型のように渦巻き状に刻まれていく)。
  2026-08-18 有効化、8/25 に常用決定。stacked/tabbed コンテナ内では分割せず末尾に
  合流。`new-window-attach last`(後述ローカルパッチ)と併用時は「最後の窓」を
  その窓のアスペクト比で分割する。キー割当は無く、戻すには同じキーを `false` に。

## GNOME 標準キーとの衝突解消(適用済み)

Forge の既定キー(i3 風 Super+hjkl)と衝突する GNOME 標準キーを付け替えた:

| キー | 旧割当(GNOME) | 新割当 |
| --- | --- | --- |
| `Super+H` | ウィンドウ最小化 | **解除**(Forge: 左へフォーカス) |
| `Super+L` | **画面ロック** | ロックは **`Super+Escape`** へ移動(Forge: 右へフォーカス) |
| `Super+V` | 通知トレイ | 通知は `Super+M` のみに(Forge: 縦分割) |
| `Super+1〜5` | dash のお気に入り N を起動 | **ワークスペース 1〜5 へ切替**(2026-09-11。詳細は「ワークスペース運用」) |
| `Super+Return` | (カスタムキー)Tilix 起動 | **解除**(Forge: 直前フォーカス窓と位置入替)。Tilix 起動は `Shift+Super+Return` へ |

```sh
gsettings set org.gnome.desktop.wm.keybindings minimize "[]"
gsettings set org.gnome.settings-daemon.plugins.media-keys screensaver "['<Super>Escape']"
gsettings set org.gnome.shell.keybindings toggle-message-tray "['<Super>m']"
```

巻き戻しは各キーを `gsettings reset` するだけ。

## チートシート(Forge 既定キー)

| 操作 | キー |
| --- | --- |
| フォーカス移動 | `Super + H / J / K / L` |
| ウィンドウ移動 | `Shift + Super + H / J / K / L` |
| ウィンドウ入替(swap) | `Ctrl + Super + H / J / K / L` |
| 直前のアクティブ窓と入替 | `Super + Enter` |
| Tilix を新窓で起動(`launch-app-command`) | `Shift + Super + Enter` |
| 縦分割 / 横分割 | `Super + V` / `Super + Z` |
| 分割方向トグル | `Super + G` |
| スタック / タブ化 | `Shift + Super + S` / `Shift + Super + T` |
| フロート切替(一時/常時) | `Super + C` / `Shift + Super + C` |
| タイリング全体の ON/OFF | `Super + W` |
| このワークスペースだけタイル切替 | `Shift + Super + W` |
| リサイズ(辺を広げる: 左/下/上/右) | `Ctrl + Super + Y / U / I / O` |
| リサイズ(辺を縮める) | 上記に `Shift` を追加 |
| ギャップ増減 | `Ctrl + Super + +` / `Ctrl + Super + -` |
| フォーカス枠の表示切替 | `Super + X` |
| Forge 設定を開く | `Super + .` |

※ この環境は **auto-split 有効**のため、新規窓の分割方向は取り付け先窓の
アスペクト比で自動決定される(横長→横並び/縦長→縦積み)。`Super + V / Z` の
手動指定は新規窓の出現時に auto-split が向きを再決定するため上書きされることが
ある(既存窓どうしのレイアウト操作には従来どおり効く)。

## ローカルパッチ: 新規ウィンドウは「最後」を分割(2026-08-21)

新しいウィンドウが「アクティブ(フォーカス中)の窓」ではなく「レイアウト末尾の窓」を
分割して開くようにするローカルパッチ。gsettings キー `new-window-attach`
(`focused`=フォーク既定 / `last`)を新設し、**この環境では `last` に設定済み**。
`focused` に戻せばパッチ前と同じコード経路を通る(既定値では不活性)。
Forge 設定画面(`Super + .`)にも「New window attaches to」のドロップダウンが出る。

- パッチ本体: **`src/forge/patches/new-window-attach-last.patch`**(対象は
  `lib/extension/window.js` / `lib/prefs/settings.js` /
  `schemas/org.gnome.shell.extensions.forge.gschema.xml` の3ファイル)
- 実装の要点: 取り付け先解決の一点集約 `_resolveAttachTarget()` の先頭で、
  monitor+workspace コンテナ配下の**文書順で最後**の窓をアンカーとして返す
  (`getNodeByType` は幅優先走査で入れ子時に末尾がズレるため使えず、深さ優先の
  `_lastAttachAnchor()` を追加)。アンカー候補は「レイアウトに入る(予定の)窓」=
  恒久フロート例外でなく最小化でもない窓(生まれたての窓は一瞬 FLOAT なので
  `isTile()` ではなく `isFloatingExempt()` で判定。候補ゼロなら全生存窓に
  フォールバック)。auto-split(quarter tiling)有効時はフォーカス窓ではなく
  アンカー窓をそのアスペクト比で `tree.split()` してから取り付ける
  (Split コマンドと同じ意味論。stacked/tabbed コンテナ内では分割せず末尾に合流)。
- 防御: キー読み取りは `settings_schema.has_key()` ガード付き(js だけパッチ済みで
  schema 未コンパイルでも abort せず `focused` 扱いに落ち、prefs の行も非表示に
  なるだけ)。2026-08-21 に独立レビュー済み(重大指摘なし・中3件は修正済み)。
- 切替(パッチ適用済みなら再ログイン不要・即時反映):

  ```sh
  d=~/.local/share/gnome-shell/extensions/forge@jmmaranan.com/schemas
  gsettings --schemadir $d set org.gnome.shell.extensions.forge new-window-attach focused  # 戻す
  ```

- 適用/復元(**フォーク更新で消えるので更新後は再適用**。gsettings 値は dconf に
  残るので設定し直しは不要):

  ```sh
  cd ~/.local/share/gnome-shell/extensions/forge@jmmaranan.com
  patch -p1 < <repo>/src/forge/patches/new-window-attach-last.patch
  glib-compile-schemas schemas/
  # コード反映は再ログイン(ES モジュールキャッシュのため disable/enable では不可)
  ```

- 注意: フォーカス窓の淡緑「分割ヒント」は従来どおり**手動分割(`Super+V/Z/G`)の
  向き**を示す。`last` モードでは新規窓の出現位置(レイアウト末尾)とは無関係に
  なるので、紛らわしければ `split-border-toggle` を false に。

## 運用メモ

- **画面ロックは `Super + Escape`**(忘れやすいので注意)。
- フォーカス中の窓 = 明るい燐光緑 3px スクエア枠(窓の外側に描画・Forge)。
  全窓共通の 1px 枠はテーマ側(`decoration` / `window.csd` の box-shadow)が描く。
- フォーカス窓の**右または下にだけ出る淡緑の線は「分割ヒント」**(手動分割の
  向き。右=横並び/下=縦積み。`Super+G` で切替)。ただしこの環境は
  auto-split + `new-window-attach last` のため、**新規窓の実際の出現位置・向きとは
  一致しない**(向きはアスペクト比・位置はレイアウト末尾で決まる)。紛らわしければ
  gsettings の `split-border-toggle` を false に。
- ダイアログ等は Forge が自動でフロート扱いにする。挙動がおかしいアプリは
  `Shift + Super + C` で常時フロートに落とす(windows.json にクラス単位で永続
  登録。フォークでは `Super + C` で個体解除できる)。
- フォークは古い wmId 項目を起動時に自動掃除してファイルにも反映する
  (2026-08-17 に実際に4件掃除されたのを確認)。
- 更新はフォークの Releases を見て同じ手順で入れ直す。**2026-08-21 から
  ローカルパッチ1本が復活**(新規窓の取り付け先。上のセクション参照)しているので、
  更新後はパッチ再適用+`glib-compile-schemas`+再ログインを忘れない。

- **mission-control からの claude セッションは Tilix のペイン分割で開く**(2026-09-02〜)。
  常駐パネルは Tilix 窓の右ペイン(`mc-tui`)になり、thread を開くと
  mission-control 側の `shell/mcopen.py` が「最後に生まれたペイン」をアスペクト比で
  右/下に分割して `claude --continue` を起動する(Forge の auto-split +
  `new-window-attach last` と同じ思想を Tilix 内で再現)。Forge がタイルするのは
  Tilix 窓 1 枚だけになるので、セッションの並びを直すときは Forge のキーではなく
  Tilix のペイン操作(仕切りのドラッグ / 移動 `Alt+矢印` / リサイズ `Shift+Alt+矢印` /
  手動分割 `Ctrl+Alt+R`=右・`Ctrl+Alt+D`=下)を使う。詳細は mission-control の
  README「Tilix ペイン版パネル」。

## ワークスペース運用(2026-09-11 決定)

**固定5枚・役割固定(i3 流)**。GNOME の動的ワークスペースは「中間の空ワークスペース」を
自動で消すため、番号と役割を結び付ける運用ができない。固定にして `Super+数字` で
直接飛ぶ。

| WS | 名前 | 入れるもの | 自動配置(auto-move-windows) |
| --- | --- | --- | --- |
| 1 | COCKPIT | **Tilix だけ**(シェル+mission-control+claude ペイン) | `com.gexperts.Tilix.desktop:1` |
| 2 | WEB | Brave と Claude/GitHub/GitLab の PWA | `brave-browser.desktop:2` |
| 3 | CODE | VS Code | `code.desktop:3` |
| 4 | TALK | Signal / Telegram Web / Proton Mail | `signal-desktop.desktop:4` ほか |
| 5 | STUDIO | Inkscape / Blender / GIMP など浮動向きのアプリ | なし。**タイル停止** |

- **WS1 が Tilix の「全画面」**: Forge は単窓のときギャップを消す
  (`window-gap-hidden-on-single`)ので、Tilix 1枚で作業領域いっぱいになる。
  上バーまで消したいときだけ Tilix の `F11`(フォークは全画面窓を TILE のまま保持し
  再分割しない=forge-fw8)。WS1 に他の窓を開かないのが前提。
- **通常作業は WS5 ではなく役割別**(2 Web / 3 Code / 4 Talk)。自動配置ルールの無い
  アプリ(Files、画像ビューア、LibreOffice、PDF など)は**今いるワークスペース**に開き、
  auto-split でその場の窓の隣に付く。用が済んだら閉じる、が基本の作法。
  長時間の整理作業なら WS5 で普通の重なり窓として使ってもよい。
- **WS5 はタイル停止**: Forge の `workspace-skip-tile` = `4`(0 始まりの索引)。
  `Shift+Super+W` でも同じ値がトグルされる(永続化される)。
- **机の2画面時**: `org.gnome.mutter workspaces-only-on-primary` = true なので
  副画面(HDMI-1)はワークスペースの外の固定領域。Brave を副画面に置けば
  Tilix 全画面とブラウザを同時に見られる。ノート単体では `Super+1` / `Super+2` の往復。
- `Alt+Tab`(`switch-applications`)は全ワークスペース横断のまま
  (`org.gnome.shell.app-switcher current-workspace-only` = false)。アプリを選ぶと
  そのワークスペースへ飛ぶ。
- 名前(`workspace-names`)は GNOME 標準 UI にはほぼ出ない。自分の呼び名として設定してある。

### キー

| 操作 | キー |
| --- | --- |
| WS1〜5 へ切替 | `Super + 1〜5`(`Super+Home/End` = 1/5、`Super+PgUp/PgDn` = 隣も残置) |
| 今の窓を WS1〜5 へ移動 | `Shift + Super + 1〜5` |
| Tilix を新窓で起動 | `Shift + Super + Return`(Forge `prefs-app-launch`、`launch-app-command` = `tilix`。auto-move で WS1 に行くので、他所で使うなら `Shift+Super+N` で移す) |
| 直前フォーカス窓と位置入替 | `Super + Return`(Forge `window-swap-last-active`。Alt+Tab 順で次の窓=直前にフォーカスしていた窓とツリー上の位置を交換し、ポインタも付いてくる) |
| 直前のワークスペースへ戻る | `Super + `` ``(Space Bar `activate-previous-key`、i3 の back_and_forth。GNOME の同アプリ内窓切替は `Alt+`` `` のみに) |

`Super+1〜9` は GNOME 既定では dash のお気に入り N 起動(`switch-to-application-N`)。
1〜5 を空にして付け替えた(6〜9 は既定のまま)。`Super+Return` は 2026-09-11 まで
カスタムキー「Tilix 起動」(media-keys custom0)と Forge の swap-last-active が二重割当
だった。カスタムキーを削除して Forge に寄せ、Tilix 起動は Forge の
`launch-app-command` に移した。

### 左上の表示: Space Bar(2026-09-11)

GNOME 45 以降の左上のワークスペース・ドットを i3 風の名前バーに置き換える拡張
[Space Bar](https://extensions.gnome.org/extension/5090/space-bar/)
(UUID `space-bar@luchrioh`、v34、GNOME 46〜49 対応)を導入。`1 COCKPIT  2 WEB …` と
並び、現在の WS は淡緑の面+燐光緑 1px 枠(スクエア)、窓のある WS は明緑、空の WS は
暗緑。クリック/ホイールで切替、現在の WS をクリックすると overview。

- **設定のマスターコピーは `src/space-bar/space-bar.dconf`**。復元は
  `dconf load /org/gnome/shell/extensions/space-bar/ < src/space-bar/space-bar.dconf`。
  色は個別キー(`appearance.*-workspace-*`)から `application-styles` が自動生成される。
- キーの整理: Space Bar 独自の Super+1〜9・移動キーは無効(GNOME 側の割当が正)。
  `open-menu` 既定の `Super+W` は Forge のタイリング ON/OFF と衝突するので空。
  `activate-empty-key`(`Super+N`)は固定 WS では無意味なので空。
  `activate-previous-key` = `Super+`` `` は残し、代わりに GNOME の `switch-group` から
  `<Super>Above_Tab` を外した(`Alt+`` `` は残る)。
- インストール手順: EGO の zip を `gnome-extensions install` しただけでは Shell が
  そのセッションでは読まない(再ログインが要る)。再ログインなしで読ませるには Shell の
  D-Bus `InstallRemoteExtension` を呼ぶ(画面に確認ダイアログが出るので「インストール」):

  ```sh
  gdbus call --session --timeout 180 --dest org.gnome.Shell.Extensions \
    --object-path /org/gnome/Shell/Extensions \
    --method org.gnome.Shell.Extensions.InstallRemoteExtension "space-bar@luchrioh"
  ```

  gdbus 側は `NoReply` で終わることがあるが、ダイアログで承認すれば ACTIVE になる
  (2026-09-11 実測)。extension-list 拡張が新規拡張の出現で JS エラーを1回吐くが無害。
- 外すとき: `gnome-extensions disable space-bar@luchrioh` でドット表示に戻る。
  `switch-group` を戻すなら `gsettings reset org.gnome.desktop.wm.keybindings switch-group`。

### 適用コマンド(再現用・適用済み)

```sh
gsettings set org.gnome.mutter dynamic-workspaces false
gsettings set org.gnome.desktop.wm.preferences num-workspaces 5
gsettings set org.gnome.desktop.wm.preferences workspace-names "['COCKPIT','WEB','CODE','TALK','STUDIO']"
for i in 1 2 3 4 5; do
  gsettings set org.gnome.shell.keybindings switch-to-application-$i "[]"
  gsettings set org.gnome.desktop.wm.keybindings switch-to-workspace-$i "['<Super>$i']"
  gsettings set org.gnome.desktop.wm.keybindings move-to-workspace-$i "['<Super><Shift>$i']"
done
gsettings set org.gnome.desktop.wm.keybindings switch-to-workspace-1 "['<Super>1','<Super>Home']"
gsettings set org.gnome.desktop.wm.keybindings move-to-workspace-1 "['<Super><Shift>1','<Super><Shift>Home']"
d=~/.local/share/gnome-shell/extensions/auto-move-windows@gnome-shell-extensions.gcampax.github.com/schemas
gsettings --schemadir $d set org.gnome.shell.extensions.auto-move-windows application-list \
  "['com.gexperts.Tilix.desktop:1','brave-browser.desktop:2','code.desktop:3','signal-desktop.desktop:4','proton-mail.desktop:4','brave-ibblmnobmgdmpoeblocemifbpglakpoi-Default.desktop:4']"
# カスタムキー Super+Return(Tilix 起動)を削除。custom1 = Mission Control Capture(Super+I)は残す
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings \
  "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/']"
dconf reset -f /org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/
f=~/.local/share/gnome-shell/extensions/forge@jmmaranan.com/schemas
gsettings --schemadir $f set org.gnome.shell.extensions.forge launch-app-command 'tilix'
gsettings --schemadir $f set org.gnome.shell.extensions.forge workspace-skip-tile '4'
```

全部 gsettings なので再ログイン不要・即時反映。適用時点で開いていた窓は元の場所に
残るので、初回だけ Tilix を `Shift+Super+1`、Brave を `Shift+Super+2` で移す。
Brave の PWA は `brave-<id>-Default.desktop` という個別 ID を持つので、Telegram Web
(`ibblmnob…`)だけ WS4 に送っている(ID は `~/.local/share/applications/` で確認)。

### 巻き戻し

```sh
gsettings reset org.gnome.mutter dynamic-workspaces
gsettings reset org.gnome.desktop.wm.preferences num-workspaces
gsettings reset org.gnome.desktop.wm.preferences workspace-names
for i in 1 2 3 4 5; do
  gsettings reset org.gnome.shell.keybindings switch-to-application-$i
  gsettings reset org.gnome.desktop.wm.keybindings switch-to-workspace-$i
  gsettings reset org.gnome.desktop.wm.keybindings move-to-workspace-$i
done
d=~/.local/share/gnome-shell/extensions/auto-move-windows@gnome-shell-extensions.gcampax.github.com/schemas
gsettings --schemadir $d set org.gnome.shell.extensions.auto-move-windows application-list \
  "['code.desktop:2','com.gexperts.Tilix.desktop:2']"   # 9/11 以前の値
f=~/.local/share/gnome-shell/extensions/forge@jmmaranan.com/schemas
gsettings --schemadir $f set org.gnome.shell.extensions.forge workspace-skip-tile ''
gsettings --schemadir $f set org.gnome.shell.extensions.forge launch-app-command 'gnome-terminal'
```

(削除したカスタムキー Super+Return は GNOME 設定の「キーボード → カスタムショートカット」で
コマンド `tilix` を登録し直せば戻る)

## バックアップとロールバック

- ローカルパッチ1〜3適用済みの本家 v89 一式は
  `~/.local/share/gnome-shell/forge-v89-patched.bak` に保全してある。
  戻すには extensions ディレクトリの `forge@jmmaranan.com` をこれで差し替えて
  再ログイン。
- Tiling Shell(下記)はインストールしたまま無効化してあり、設定も dconf に残って
  いるので、`gnome-extensions disable forge@… && enable tilingshell@…` +
  再ログインでいつでも再試行できる。

---

# アーカイブ1: Tiling Shell の試行(2026-08-17・同日撤回)

[Tiling Shell](https://github.com/domferr/tilingshell) v76
(UUID: `tilingshell@ferrarodomenico.com`、EGO 版・GNOME 48 対応)を導入したが、
**レイアウトベース**(定義済みタイル枠へ窓を配置する方式)で、awesome/i3/Forge の
ような動的タイリング(窓の増減で自動分割)は `enable-autotiling` を含め構造的に
できないため、同日フォーク版 Forge へ移行した。

適用した設定(dconf に残置。再試行時はそのまま生きる):

```sh
d=~/.local/share/gnome-shell/extensions/tilingshell@ferrarodomenico.com/schemas
S() { gsettings --schemadir "$d" set org.gnome.shell.extensions.tilingshell "$@"; }
S enable-autotiling true
S enable-window-border true
S window-use-custom-border-color true
S window-border-color "'#66E07A'"
S window-border-width 3
S inner-gaps 4
S outer-gaps 0
S enable-move-keybindings true
S focus-window-left "['<Super>h']"
S focus-window-down "['<Super>j']"
S focus-window-up "['<Super>k']"
S focus-window-right "['<Super>l']"
S move-window-left "['<Super>Left', '<Shift><Super>h']"
S move-window-down "['<Super>Down', '<Shift><Super>j']"
S move-window-up "['<Super>Up', '<Shift><Super>k']"
S move-window-right "['<Super>Right', '<Shift><Super>l']"
S untile-window "['<Super>c']"
S cycle-layouts "['<Super>g']"
```

**注意: disable 時の設定復元漏れがある。** 無効化した後、GNOME 側の
`maximize` / `unmaximize` / `toggle-tiled-left` / `toggle-tiled-right`
(Super+矢印)が空のまま残されていた(Tiling Shell が Super+矢印を掴むために
潰した組と一致。`overridden-settings` の記録は消えているのに値だけ残置)。
また画面ロック(`screensaver`)と通知(`toggle-message-tray`)の付け替え値も
空になっていた(こちらは原因未特定)。2026-08-17 に以下で復旧済み:

```sh
gsettings reset org.gnome.desktop.wm.keybindings maximize
gsettings reset org.gnome.desktop.wm.keybindings unmaximize
gsettings reset org.gnome.mutter.keybindings toggle-tiled-left
gsettings reset org.gnome.mutter.keybindings toggle-tiled-right
gsettings set org.gnome.settings-daemon.plugins.media-keys screensaver "['<Super>Escape']"
gsettings set org.gnome.shell.keybindings toggle-message-tray "['<Super>m']"
```

(`org.gnome.mutter edge-tiling` はこの環境ではスキーマ既定が false なので対応不要)

---

# アーカイブ2: 本家 Forge v89(EGO)+ローカルパッチ(2026-08-16〜17)

当時のタイリングは本家 [Forge](https://github.com/forge-ext/forge)
(i3 風の自動タイリング拡張)v89 で行っていた。本家はメンテナ不在
(リポジトリ・EGO とも "Needs a new maintainer" 掲示)で、フロートトグル修正
(upstream #492/#496/#510)が main のみ・8ヶ月未リリースの状態だった。
以下のローカルパッチ3本で運用していた(パッチ適用済み一式は
`~/.local/share/gnome-shell/forge-v89-patched.bak` に保全)。
**フォーク版には3本とも同等以上の修正が入っているため、この節は記録として残す。**

### インストール(当時)

```sh
curl -sL -o forge.zip "https://extensions.gnome.org/download-extension/forge@jmmaranan.com.shell-extension.zip?version_tag=67175"
gnome-extensions install --force forge.zip
# Wayland では再ログイン後に有効化される
gnome-extensions enable forge@jmmaranan.com
```

### ローカルパッチ1: フォーカス移動時の再レンダリングブレ対策(2026-08-16)

Forge v89 はフォーカスが変わるたびに `renderTree("focus", true)` で全タイル窓へ
無条件に unmaximize + move_resize_frame を発行するため、端末(VTE)等が同サイズでも
再描画されて「幅がブレる」ように見える。対策として
`lib/extension/window.js` の `move()` 冒頭に
「非最大化かつ frame rect が目標 rect と完全一致なら return する」ガードを追加した。

- **拡張のコード変更は再ログインするまで反映されない**(GNOME Shell が ES モジュールを
  キャッシュするため。disable/enable では JS は再読込されない)。

### ローカルパッチ2: Super+C フロートトグルの非対称バグ修正(2026-08-17)

v89 の `Super+C`(FloatToggle)は windows.json の overrides に
「wmClass + wmId(窓個体)」の float 項目を書き/消しするが、実装が非対称:

1. `addFloatOverride()` の重複チェックが wmId を見ずに「同じ wmClass の float 項目が
   1つでもあれば何も書かない」ため、同じアプリの別窓がフロート中だと他の窓を
   フロートにできない。
2. `removeFloatOverride(withWmId=true)` は wmId 一致の項目しか消さないため、
   クラス単位の float 規則が生きている窓ではトグルが死んだように見える。

修正: 重複チェックをスコープ一致に変更し、`removeFloatOverride()` に
`includeWmClass` 引数を追加してトグル解除時はクラス単位項目も消すようにした。

### ローカルパッチ3: Super+C の float が再描画側に届かないバグ修正(2026-08-17)

パッチ2適用後も「タイリング ON 中は `Super+C` でフロートせず、`Super+W` で
タイリングを切ると効くように見える」症状が残った。原因は v89 の構造バグ:

- `addFloatOverride()` / `removeFloatOverride()` は **ファイル**(windows.json)
  にしか書かない。
- 再描画の `processFloats()` → `isFloatingExempt()` が参照するのは起動時に読み込んだ
  **メモリ内コピー `this.windowProps`** で、しかも読み込み時に wmId 付き項目を
  全部捨てる(`reloadWindowOverrides()`)。
- そのため `Super+C` は自分自身の `renderTree("float-toggle")` →
  `processFloats()` で即タイルに巻き戻される。`Super+W` OFF 時に「効く」ように
  見えるのは、renderTree が早期 return して巻き戻し役が走らないだけ。

修正: ファイル保存と同時に `this.windowProps.overrides` へも同じスコープ規則で
追加/削除するようにした。upstream では #492 で同型の原因が特定され
#496(v89 タグの2日後にマージ)+ #510 で main は修正済みだが、リリースが無く
EGO 配布の v89 には未収録だった。

### GNOME 標準キー付け替え・チートシート・stylesheet

現行(フォーク版)と共通のため本文側を参照。stylesheet の知見(3px 上限・
box-shadow 禁止・CSS コメントで ERROR)も v89 時代の実測が本文に引き継がれている。
