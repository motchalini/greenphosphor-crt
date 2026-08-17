# タイル型ウィンドウ管理 — Tiling Shell セットアップ

GreenPhosphor-CRT デスクトップのタイリングは
[Tiling Shell](https://github.com/domferr/tilingshell)
(UUID: `tilingshell@ferrarodomenico.com`)で行う。
GNOME 48 / v76 で導入(2026-08-17、extensions.gnome.org 版)。

2026-08-16〜17 は Forge を使っていたが、メンテナ不在(リポジトリ・EGO とも
"Needs a new maintainer" 掲示)・フロートトグル修正(#496/#510)が v89 に
未収録のまま8ヶ月リリースなし・ローカルパッチ3本が必要、という状態だったため
乗り換えた。Forge の設定とパッチの記録は末尾のアーカイブ参照
(拡張・パッチ・設定ファイルはフォールバック用にすべて残置、無効化のみ)。

## インストール

```sh
# GNOME 48 対応版(v76)。別バージョンは extension-info API で version_tag を確認:
# curl -s "https://extensions.gnome.org/extension-info/?uuid=tilingshell%40ferrarodomenico.com"
curl -sL -o tilingshell.zip "https://extensions.gnome.org/download-extension/tilingshell@ferrarodomenico.com.shell-extension.zip?version_tag=70233"
gnome-extensions install --force tilingshell.zip
# Wayland では再ログイン後に有効化される
gnome-extensions enable tilingshell@ferrarodomenico.com
```

丸角拡張はスクエア枠と衝突するため無効化のまま(Forge 時代から継続):

```sh
gnome-extensions disable rounded-window-corners@fxgn
```

## 適用済み設定(2026-08-17)

Forge と違い、**枠の色・太さも gsettings が実効値**(stylesheet 手術は不要):

```sh
d=~/.local/share/gnome-shell/extensions/tilingshell@ferrarodomenico.com/schemas
S() { gsettings --schemadir "$d" set org.gnome.shell.extensions.tilingshell "$@"; }
# 挙動
S enable-autotiling true            # 新窓を現在レイアウトの空きタイルへ自動配置
# 見た目(燐光緑CRT仕様)
S enable-window-border true
S window-use-custom-border-color true
S window-border-color "'#66E07A'"   # フォーカス窓 = 明るい燐光緑
S window-border-width 3
S inner-gaps 4                      # Forge の gap 4px を踏襲
S outer-gaps 0
# キー割当(i3 風・Forge からの移植)
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

GNOME 標準キーの付け替え(`Super+H` 最小化解除・画面ロック= `Super+Escape`・
通知= `Super+M` のみ)は Forge 時代のまま維持(アーカイブの表と gsettings
コマンド参照)。

## チートシート

| 操作 | キー | Forge との差 |
| --- | --- | --- |
| フォーカス移動 | `Super + H / J / K / L` | 同じ |
| ウィンドウをタイルへ移動 | `Shift + Super + H / J / K / L`(`Super + 矢印` も可) | 同じ+矢印 |
| フロート化(untile) | `Super + C` | **トグルではなく解除のみ**。再タイルは移動キーかドラッグ |
| レイアウト切替 | `Super + G` | Forge の分割方向トグルの代替 |
| ドラッグでタイル | ドラッグ中に `Ctrl` を押す | Forge に無かった機能 |
| スナップアシスト | 窓を画面上端へドラッグ | 同上 |
| レイアウト編集 | パネルのインジケーターから | — |

Forge に在って Tiling Shell に無いもの:

- タイリング全体の ON/OFF キー(`Super+W` 相当)。必要なら設定
  `enable-tiling-system` / `enable-autotiling` の切替で代替。
- スタック/タブ化(`Shift+Super+S/T`)。
- 手動の縦/横分割(`Super+V/Z`)。レイアウトベースなので分割形状は
  レイアウト側で決まる(インジケーターのエディタで自作可能)。

## 運用メモ

- **画面ロックは `Super + Escape`**(Forge 時代の付け替えを継続)。
- フォーカス中の窓 = 燐光緑 `#66E07A` 3px 枠(Tiling Shell 描画)。全窓共通の
  1px 枠はテーマ側(`decoration` / `window.csd` の box-shadow)が描く。
- `enable-smart-window-border-radius` は既定 true のまま: 窓の実際の角丸に
  枠が追従する。テーマがスクエアなので実質スクエア枠になる。

## ロールバック(Forge へ戻す)

```sh
gnome-extensions disable tilingshell@ferrarodomenico.com
gnome-extensions enable forge@jmmaranan.com   # 再ログインで反映
```

Forge 本体(ローカルパッチ1〜3適用済み window.js)・stylesheet・windows.json は
すべて残置してある。

---

# アーカイブ: Forge セットアップ(2026-08-16〜17)

当時のタイリングは [Forge](https://github.com/forge-ext/forge)
(i3 風の自動タイリング拡張、UUID: `forge@jmmaranan.com`)で行っていた。
GNOME 48 で動作確認(2026-08-16 導入、extensions.gnome.org 版)。

### インストール

```sh
curl -sL -o forge.zip "https://extensions.gnome.org/download-extension/forge@jmmaranan.com.shell-extension.zip?version_tag=67175"
gnome-extensions install --force forge.zip
# Wayland では再ログイン後に有効化される
gnome-extensions enable forge@jmmaranan.com
```

丸角拡張はスクエア枠と衝突するため無効化する:

```sh
gnome-extensions disable rounded-window-corners@fxgn
```

### テーマ適合の設定(燐光緑CRT仕様)

**枠の色・太さ・角丸は gsettings ではなく Forge のユーザースタイルシートが実効値**
(v89 で確認。gsettings の focus-border-color 等は描画に反映されない):

- ファイル: `~/.config/forge/stylesheet/forge/stylesheet.css`
- 適用済みの内容: フォーカス窓(`.window-tiled-border`)= 明るい燐光緑
  `rgba(102,224,122,1)` 3px、分割ヒント = CRTアンバー `#FFB000`、
  スタック=アンバー/タブ=淡緑も 3px、全クラス `border-radius: 0`(スクエア)。
  タブバー(`.window-tabbed-*`)も緑黒。
- **枠の太さは 3px が上限**。Forge は gap>0 のとき枠ウィジェットを窓の 3px
  外側に置くため、3px までなら文字と重ならない。4px 以上にすると差分が
  窓の内側に食い込み、端の文字に被る+フォーカス移動時に端がチラつく
  (gap=0 や最大化時は inset=0 になり全幅が内側に被る点にも注意)。
- **注意: 枠クラスに `box-shadow` のグローを付けてはいけない**。Forge の枠は
  ウィンドウ全面に重なる透明ウィジェットのため、St の box-shadow が
  窓の内側全体に緑がかって描画されてしまう(2026-08-16 に実地で確認)。
- **注意: このファイルに CSS コメント(`/* */`)を書くと Forge のパーサが
  クラッシュして拡張が ERROR になる**(theme.js の rules.filter が
  comment ノードで selectors 未定義になる)。コメント禁止。
- 反映は `gnome-extensions disable forge@… && enable forge@…`。ただし一度
  ERROR になると再ログインまで enable が効かなくなる(GNOME の仕様)。
  ログアウトせずに復帰するには `gnome-extensions uninstall` →
  `busctl --user call org.gnome.Shell.Extensions /org/gnome/Shell/Extensions \
  org.gnome.Shell.Extensions InstallRemoteExtension s forge@jmmaranan.com`
  で EGO から再インストール(確認ダイアログを承認すると即時ロードされる)。

挙動系の設定は gsettings が有効:

```sh
d=~/.local/share/gnome-shell/extensions/forge@jmmaranan.com/schemas
gsettings --schemadir $d set org.gnome.shell.extensions.forge window-gap-hidden-on-single true
```

### GNOME 標準キーとの衝突解消(適用済み・**Tiling Shell 移行後も維持**)

Forge の既定キー(i3 風 Super+hjkl)と衝突する GNOME 標準キーを付け替えた:

| キー | 旧割当(GNOME) | 新割当 |
| --- | --- | --- |
| `Super+H` | ウィンドウ最小化 | **解除**(タイリング: 左へフォーカス) |
| `Super+L` | **画面ロック** | ロックは **`Super+Escape`** へ移動(タイリング: 右へフォーカス) |
| `Super+V` | 通知トレイ | 通知は `Super+M` のみに |

```sh
gsettings set org.gnome.desktop.wm.keybindings minimize "[]"
gsettings set org.gnome.settings-daemon.plugins.media-keys screensaver "['<Super>Escape']"
gsettings set org.gnome.shell.keybindings toggle-message-tray "['<Super>m']"
```

巻き戻しは各キーを `gsettings reset` するだけ。

### ローカルパッチ: フォーカス移動時の再レンダリングブレ対策(2026-08-16)

Forge v89 はフォーカスが変わるたびに `renderTree("focus", true)` で全タイル窓へ
無条件に unmaximize + move_resize_frame を発行するため、端末(VTE)等が同サイズでも
再描画されて「幅がブレる」ように見える。対策として
`~/.local/share/gnome-shell/extensions/forge@jmmaranan.com/lib/extension/window.js`
の `move()` 冒頭(「// Window movement API」直後)に
「非最大化かつ frame rect が目標 rect と完全一致なら return する」ガードを追加した。

- **拡張のコード変更は再ログインするまで反映されない**(GNOME Shell が ES モジュールを
  キャッシュするため。disable/enable では JS は再読込されない)。
- **Forge を更新/再インストールするとパッチは消える** → 同じガードを再適用する。
  upstream への PR 候補でもある(no-op move の抑止)。

### ローカルパッチ2: Super+C フロートトグルの非対称バグ修正(2026-08-17)

Forge v89 の `Super+C`(FloatToggle)は windows.json の overrides に
「wmClass + wmId(窓個体)」の float 項目を書き/消しするが、実装が非対称で
「押してもタイルに戻される/フロートに戻せない」が起きる:

1. `addFloatOverride()` の重複チェックが wmId を見ずに「同じ wmClass の float 項目が
   1つでもあれば何も書かない」ため、**同じアプリの別窓がフロート中だと他の窓を
   `Super+C` でフロートにできない**(メモリ上は一瞬フロートになるが、実効値は
   JSON 側なので次の再描画 `processFloats()` でタイルに戻される)。
2. `removeFloatOverride(withWmId=true)` は wmId 一致の項目しか消さないため、
   クラス単位の float 規則(`Shift+Super+C` 製や既定の zoom 等)が生きている窓に
   `Super+C` を押しても規則が残り、次の再描画で即フロートに戻る(トグルが死んだように見える)。

`lib/extension/window.js` に修正を適用済み:

- `addFloatOverride()`: 重複チェックをスコープ一致に変更
  (`withWmId ? override.wmId === wmId : override.wmId === undefined`)。
- `removeFloatOverride()`: 第3引数 `includeWmClass`(既定 false)を追加。true なら
  タイトルなしのクラス単位項目も削除する。呼び出しは `toggleFloatingMode()` の
  解除側のみ true(窓破棄時の掃除は従来どおり自窓の wmId 項目だけ)。

効果: 同一アプリ複数窓でのトグルが正常化。`Super+C` がクラス単位の float 規則も
解除するようになるため、**誤 `Shift+Super+C` の救済も `Super+C` 一発で可能になる**
(windows.json の手術は不要に。ただし既定の zoom 等の規則も同様に消えるので、
消えたら `Shift+Super+C` で再登録)。

- パッチ1と同じ注意: **再ログインまで反映されず、Forge 更新で消える**。
  これも upstream PR 候補。
- 窓を閉じたときの掃除(destroy ハンドラ)は Forge 有効時しか走らないため、
  ログアウト等で windows.json に古い wmId 項目が残ることがある。本パッチ後は
  残っていても無害(トグルを阻害しない)。気になったら手で消してよい。
- なお window type 由来で必ずフロートになる窓(ダイアログ・transient・
  リサイズ不可)は仕様上 `Super+C` でタイルに固定できない(`processFloats()` が
  毎回フロートに戻す)。これはパッチ対象外。

### ローカルパッチ3: Super+C の float が再描画側に届かないバグ修正(2026-08-17)

パッチ2適用・再ログイン後も「タイリング ON 中は `Super+C` でフロートせず、
`Super+W` でタイリングを切ると効くように見える」症状が残った。原因はパッチ2とは
別の構造バグ(素の v89 を EGO から取得して diff し、upstream 由来を確認済み):

- `addFloatOverride()` / `removeFloatOverride()` は **ファイル**(windows.json)
  にしか書かない。
- 一方、再描画の `processFloats()` → `isFloatingExempt()` が参照するのは起動時に
  読み込んだ**メモリ内コピー `this.windowProps`** で、しかも読み込み時に
  **wmId 付き項目を全部捨てる**(`reloadWindowOverrides()`。過去セッションの
  wmId 誤マッチ対策で、これ自体は妥当)。
- そのため `Super+C` は書き込みに成功していても、自分自身の
  `renderTree("float-toggle")` → `processFloats()` で即タイルに巻き戻される。
  `Super+W` OFF 時に「効く」ように見えるのは、renderTree が早期 return して
  巻き戻し役の processFloats が走らなくなるだけ(タイリング OFF 中は全窓
  フロートなので当然でもある)。

修正: `addFloatOverride()` / `removeFloatOverride()` がファイル保存と同時に
`this.windowProps.overrides` へも同じスコープ規則で追加/削除するようにした
(読み込み時に古い wmId を捨てる upstream の仕様はそのまま)。これで
`Super+C` のトグルがセッション中に実効する。

- パッチ1・2と同じ注意: **再ログインまで反映されず、Forge 更新で消える**。
- upstream では #492 で同型の原因が特定され、PR #496(v89 タグの2日後にマージ)+
  #510 で main は修正済み。ただし以後リリースが無く、EGO 配布の v89 には未収録。
  よって**パッチ3の upstream PR は不要**(パッチ1・2は main に無いので候補のまま)。
- 既知の小さな制限: Forge 設定画面で overrides を編集すると reload trigger で
  メモリが作り直され、そのセッション中の `Super+C` フロートは解除される
  (もう一度 `Super+C` すればよい)。

### チートシート(Forge 既定キー)

| 操作 | キー |
| --- | --- |
| フォーカス移動 | `Super + H / J / K / L` |
| ウィンドウ移動 | `Shift + Super + H / J / K / L` |
| ウィンドウ入替(swap) | `Ctrl + Super + H / J / K / L` |
| 直前のアクティブ窓と入替 | `Super + Enter` |
| 縦分割 / 横分割 | `Super + V` / `Super + Z` |
| 分割方向トグル | `Super + G` |
| スタック / タブ化 | `Shift + Super + S` / `Shift + Super + T` |
| フロート切替(一時/常時) | `Super + C` / `Shift + Super + C` |
| タイリング全体の ON/OFF | `Super + W` |
| このワークスペースだけタイル切替 | `Shift + Super + W` |
| ギャップ増減 | `Ctrl + Super + +` / `Ctrl + Super + -` |
| フォーカス枠の表示切替 | `Super + X` |
| Forge 設定を開く | `Super + .` |

### 運用メモ(Forge 当時)

- フォーカス中の窓 = 明るい燐光緑 3px スクエア枠(窓の外側に描画・Forge)。全窓共通の 1px 枠はテーマ側
  (`decoration` / `window.csd` の box-shadow)が描く。
- ダイアログ等は Forge が自動でフロート扱いにする。挙動がおかしいアプリは
  `Shift + Super + C` で常時フロートに落とす。
- **注意: `Shift + Super + C` は `~/.config/forge/config/windows.json` の
  overrides に永続登録される**。誤って押すとそのアプリは以後ずっとフロートになる
  (2026-08-16 に tilix で発生)。**ローカルパッチ2・3適用後は `Super + C` で解除できる**。
  パッチ未反映(再ログイン前)や手動で戻す場合は、windows.json から該当 wmClass の
  項目を削除し、
  `gsettings --schemadir ~/.local/share/gnome-shell/extensions/forge@jmmaranan.com/schemas \
  set org.gnome.shell.extensions.forge window-overrides-reload-trigger <現在値+1>`
  で再読込(Forge 再起動不要)。
