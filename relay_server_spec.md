# PowerOn Relay Server Specification

## 1. 概要 (Overview)
本システムは、iOSアプリを「リモコン」として機能させ、実際の電源操作（Wake-on-LAN, SSH Sleep）や状態確認（Status Check）のロジックを「中継サーバー（Relay Server）」に集約するアーキテクチャを採用する。
iOSアプリは中継サーバーに対して抽象的な指示（「起動しろ」「停止しろ」）のみを送り、具体的な通信プロトコル（UDP Magic Packet, SSH Command等）は中継サーバーが隠蔽する。

## 2. システム構成 (Architecture)

```mermaid
graph LR
    iOS[iOS App] -- HTTP/JSON (Tailscale) --> Relay[Relay Server]
    Relay -- UDP (Broadcast) --> PC_Wake[Target PC (WoL)]
    Relay -- SSH (Port 22) --> PC_Sleep[Target PC (Sleep Cmd)]
    Relay -- TCP/ICMP --> PC_Status[Target PC (Status Check)]
```

*   **Network**: 全通信はTailscale VPN内で行われることを前提とする。
*   **Relay Server**: LAN内に常駐するデバイス（Raspberry Pi, Linux Server等）。
*   **Target PC**: 操作対象のWindows/Linuxマシン。

## 3. API仕様 (API Specification)
中継サーバーは以下のRESTful APIを提供する。

### 3.1. マシン操作 (Control Machine)
マシンの電源操作を行う。

*   **Endpoint**: `POST /api/control`
*   **Request Body**:
    ```json
    {
      "mac_address": "00:11:22:33:44:55",
      "ip_address": "192.168.1.10",
      "action": "wake" | "sleep"
    }
    ```
    *   `mac_address`: WoL用（Wake時必須）
    *   `ip_address`: SSH/Status用（Sleep時必須）
    *   `action`: 実行するアクション

*   **Response**:
    *   `200 OK`: `{ "status": "success", "message": "Command sent" }`
    *   `400 Bad Request`: パラメータ不足、または未対応のアクション
    *   `500 Internal Server Error`: SSH接続失敗など

### 3.2. ステータス確認 (Check Status)
マシンの現在の電源状態（オンライン/オフライン）を確認する。

* ### 4. ステータス確認（オンライン判定）

- メソッド: `GET /api/status?ip=192.168.1.10&port=22`
- 指定IPアドレスの端末の状態を確認
- **ロジック**:
  1. Ping 確認
  2. (Ping OKの場合) TCPポート接続確認 (デフォルト: 22)

#### レスポンス

**成功時 (200 OK):**
```json
{
  "ip": "192.168.1.10",
  "port": 22,
  "status": "online",   // online / sleeping / offline
  "ping": true,
  "tcp": true
}
```
* `status: "online"`   → Ping OK & TCP OK  
* `status: "sleeping"` → Ping OK & TCP NG（NICのみ応答）  
* `status: "offline"`  → Ping NG  
* `ping` / `tcp`: 判定に使用した生値（UI が詳細ステータスを表示する際に利用）

**エラー時:**
- **400 Bad Request**: IPアドレス未指定
  ```json
  {
    "error": "IP address required"
  }
  ```

#### エージェント手順

1. マシン一覧画面で各端末のIPアドレスを取得
2. `GET /api/status?ip=<ip>` を呼び出し
3. **成功時**: 
   - `status: "online"` → 緑色の●アイコン (稼働中)
   - `status: "sleeping"` → 青色の☾アイコン (スリープ中)
   - `status: "offline"` → 灰色の●アイコン (電源断)
4. **エラー時**: ステータス不明として表示（例: 黄色の●アイコン）

## 4. 中継サーバー機能要件 (Functional Requirements)

### 4.1. Wake (起動)
*   **プロトコル**: UDP Broadcast (Port 9)
*   **処理**: リクエストに含まれるMACアドレスを使用してMagic Packetを生成し、LAN内（`255.255.255.255`）にブロードキャストする。

### 4.2. Sleep (停止/スリープ)
*   **プロトコル**: SSH (Port 22)
*   **認証管理**:
    *   中継サーバーは、対象PCへのSSH接続に必要な**秘密鍵（Private Key）**または**パスワード**を安全に管理する必要がある。
    *   *推奨*: 中継サーバーの公開鍵を対象PCの `authorized_keys` に登録し、パスワードレスで実行できるようにする。
*   **実行コマンド**:
    *   **Windows**: `rundll32.exe powrprof.dll,SetSuspendState 0,1,0` (または `psshutdown -d -t 0`)
    *   **Linux**: `sudo systemctl suspend`
*   **OS判定**: リクエストにOSタイプを含めるか、中継サーバー側の設定ファイル（Config）でIPアドレスとOS・認証情報を紐付ける。

### 4.3. Status (状態確認)
*   **プロトコル**: TCP Connect (Port 22/3389) または ICMP Ping
*   **処理**: タイムアウト（例: 2秒）を設定し、応答があれば `online`、なければ `offline` を返す。

## 5. 設定ファイル例 (Config Example)
中継サーバー側で各マシンの詳細（SSH認証情報など）を持つ場合の設定ファイルイメージ。

```json
{
  "machines": [
    {
      "name": "Gaming PC",
      "mac": "00:11:22:33:44:55",
      "ip": "192.168.1.10",
      "os": "windows",
      "ssh_user": "admin",
      "ssh_key_path": "/home/pi/.ssh/id_rsa"
    }
  ]
}
```
※ アプリから認証情報を送る方式（ステートレス）にするか、サーバーに設定を持たせるか（ステートフル）は実装時に決定する。セキュリティ的にはサーバー持ちが安全。

## 6. セキュリティ (Security)
*   **ネットワーク制限**: 中継サーバーはTailscaleインターフェース（`tailscale0`）からのリクエストのみを許可する設定（Bind Address）にすること。
*   **認証**: 必要に応じてAPI Key認証（Headerに `X-API-Key`）を導入する。
