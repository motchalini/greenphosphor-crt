# タイル型ウィンドウ管理 — Forge セットアップ

GreenPhosphor-CRT デスクトップのタイリングは [Forge](https://github.com/forge-ext/forge)
(i3 風の自動タイリング拡張、UUID: `forge@jmmaranan.com`)で行う。
GNOME 48 で動作確認(2026-08-16 導入、extensions.gnome.org 版)。

## インストール

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

## テーマ適合の設定(燐光緑CRT仕様)

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

## GNOME 標準キーとの衝突解消(適用済み)

Forge の既定キー(i3 風 Super+hjkl)と衝突する GNOME 標準キーを付け替えた:

| キー | 旧割当(GNOME) | 新割当 |
| --- | --- | --- |
| `Super+H` | ウィンドウ最小化 | **解除**(Forge: 左へフォーカス) |
| `Super+L` | **画面ロック** | ロックは **`Super+Escape`** へ移動(Forge: 右へフォーカス) |
| `Super+V` | 通知トレイ | 通知は `Super+M` のみに(Forge: 縦分割) |

```sh
gsettings set org.gnome.desktop.wm.keybindings minimize "[]"
gsettings set org.gnome.settings-daemon.plugins.media-keys screensaver "['<Super>Escape']"
gsettings set org.gnome.shell.keybindings toggle-message-tray "['<Super>m']"
```

巻き戻しは各キーを `gsettings reset` するだけ。

## ローカルパッチ: フォーカス移動時の再レンダリングブレ対策(2026-08-16)

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

## ローカルパッチ2: Super+C フロートトグルの非対称バグ修正(2026-08-17)

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

## チートシート(Forge 既定キー)

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

## 運用メモ

- フォーカス中の窓 = 明るい燐光緑 3px スクエア枠(窓の外側に描画・Forge)。全窓共通の 1px 枠はテーマ側
  (`decoration` / `window.csd` の box-shadow)が描く。
- ダイアログ等は Forge が自動でフロート扱いにする。挙動がおかしいアプリは
  `Shift + Super + C` で常時フロートに落とす。
- **注意: `Shift + Super + C` は `~/.config/forge/config/windows.json` の
  overrides に永続登録される**。誤って押すとそのアプリは以後ずっとフロートになる
  (2026-08-16 に tilix で発生)。**ローカルパッチ2適用後は `Super + C` で解除できる**。
  パッチ未反映(再ログイン前)や手動で戻す場合は、windows.json から該当 wmClass の
  項目を削除し、
  `gsettings --schemadir ~/.local/share/gnome-shell/extensions/forge@jmmaranan.com/schemas \
  set org.gnome.shell.extensions.forge window-overrides-reload-trigger <現在値+1>`
  で再読込(Forge 再起動不要)。
- **画面ロックは `Super + Escape`**(移動済み。忘れやすいので注意)。
