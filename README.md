# WakeOnLan Relay Server

LAN 内に常駐させる Wake-on-LAN (WoL) 中継サーバーです。Tailscale などの VPN から HTTP 経由で MAC アドレスを受け取り、ブロードキャストパケットを送信して自宅 PC を起動できます。

## 特徴

- ⚡ **WoL REST API**: `POST /wake` で簡潔に起動リクエストを送信
- 😴 **リモートスリープ**: SSH 経由で端末をスリープ（`POST /sleep`）
- 🩺 **ヘルスチェック**: `GET /healthz` で稼働確認
- 🔧 **環境変数で設定可能**: 待受ポートやブロードキャスト宛先を切り替え
- 📦 **uv ベースの環境構築**: 依存ゼロでも再現性のあるセットアップ

## 必要要件

- Python 3.10 以上
- [uv](https://github.com/astral-sh/uv) 0.8 以上
- WoL 対応のターゲットマシンと同一 L2 ネットワーク

## セットアップ

```bash
cd /home/hide-deployment/projects/WakeOnLan
uv sync           # .venv を作成してロックファイルに同期
uv run wol-relay  # または uv run python wol_relay.py
```

デフォルトでは `0.0.0.0:5000` で待受し、ブロードキャストアドレス `<broadcast>:9` にパケットを送ります。

### 環境変数

| 変数名 | デフォルト | 説明 |
| --- | --- | --- |
| `WOL_RELAY_PORT` | `5000` | HTTP サーバーのポート |
| `WOL_RELAY_BIND` | `0.0.0.0` | 待受アドレス |
| `WOL_BROADCAST_IP` | `<broadcast>` | WoL パケットを送る宛先 |
| `WOL_BROADCAST_PORT` | `9` | WoL パケットのポート |
| `WOL_SSH_BIN` | `ssh` | リモートスリープで利用する SSH 実行ファイル |
| `WOL_SSH_EXTRA_ARGS` | なし | `-i ~/.ssh/id_ed25519` など追加したいオプション |
| `WOL_SLEEP_CMD_LINUX` | `systemctl suspend` | Linux 系のデフォルトスリープコマンド |
| `WOL_SLEEP_CMD_WINDOWS` | PowerShell スクリプト | Windows のデフォルトスリープコマンド |
| `WOL_LOG_FILE` | `logs/wol_relay.log` | ローテーション付きのログファイル出力先。空文字で無効化 |
| `WOL_LOG_LEVEL` | `INFO` | `DEBUG` など Python 標準のログレベル |
| `WOL_LOG_MAX_BYTES` | `1000000` | 1 ファイルあたりの最大バイト数（超えるとローテート） |
| `WOL_LOG_BACKUP_COUNT` | `5` | 保持するローテーション済みファイル数 |

例:

```bash
WOL_RELAY_PORT=8080 WOL_BROADCAST_IP=192.168.1.255 uv run wol-relay
```

## API

### POST `/wake`

- body: `{"mac": "00:11:22:33:44:55"}`
- 成功時: `{"status": "success"}`

```
curl -X POST http://relay.local:5000/wake \
     -H "Content-Type: application/json" \
     -d '{"mac": "00:11:22:33:44:55"}'
```

### GET `/healthz`

稼働確認用の軽量エンドポイント。`{"status":"ok"}` を返します。

### POST `/sleep`

指定ホストを SSH でスリープさせます。要求 JSON:

```json
{
  "host": "evo-linux",
  "os": "linux",
  "command": "systemctl suspend"  // 任意。省略時は OS で既定値
}
```

- `host` は `ssh host ...` で接続できる名前（または `user@host`）を指定。
- `os` は `linux` / `windows` を想定。デフォルトコマンドを切り替えるためのヒントです。
- `command` を指定すれば OS に関係なく任意のスリープコマンドを上書きできます。

```
curl -X POST http://relay.local:5000/sleep \
     -H "Content-Type: application/json" \
     -d '{"host":"evo-linux","os":"linux"}'
```

## 開発/テスト

- ローカルで動作確認する際はブロードキャストを抑制したい場合があるため、`WOL_BROADCAST_IP=127.0.0.1` などに切り替えてネットワークにパケットを流さない設定も可能です。
- `/sleep` 利用時は中継サーバー上で `ssh host` がパスワードなしで実行できるよう、公開鍵認証や `~/.ssh/config` の設定を済ませてください。
- `uv run python -m http.server` などと同様、`uv run` でスクリプトを実行すれば仮想環境を意識せずに開発できます。

## ロギング

サーバーは標準出力に加えて `logs/wol_relay.log` へローテーション付きでログを書き出します。`WOL_LOG_FILE` を空文字にするとファイル出力を無効にでき、`WOL_LOG_LEVEL` などの環境変数で出力量も調整できます。

## iPhoneアプリ向け指示書

iOS クライアントやエージェントに実装してほしい API 呼び出しフローは `ios_agent_instructions.md` にまとめています。エンドポイントの使い方、ペイロード例、エラー時のハンドリング指針を共有する際に参照してください。

## ライセンス

MIT License
