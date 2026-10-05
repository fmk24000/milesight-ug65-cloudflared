---
name: ug65-sub-cloudflared
description: Turn a Milesight UG65/UG56/UG67 SUB gateway into a Gateway Fleet agent that ships its LoRa packets to a controller gateway over native MQTT, using only the admin account (no root password). Installs cloudflared as a client-side tunnel proxy on the sub gateway (the Cloudflare connector stays on the controller), pins a non-loopback address on lo because the firmware treats a loopback server address as local mode, and points the packet forwarder at it. Use when a user gives a sub-gateway IP plus a tunnel hostname such as mqtt-xxx.example.com. IMPORTANT — before any action, always ask the user for the sub-gateway IP(s), username, password and tunnel domain — never assume or reuse them.
whenToUse: 當用戶要將 Milesight UG65 子機（有 SIM、冇固定 IP、唔同 site）接去主機的 Gateway Fleet／Embedded NS，並提供一個 Cloudflare Tunnel hostname（例 mqtt-xxx.example.com）時使用。用戶只需要提供「子機 IP + hostname + admin 密碼」，唔需要 root。
---

# UG65 子機：經 Cloudflare Tunnel 做 Gateway Fleet agent

實測環境：UG65 ×2、firmware `60.0.0.49-r3`（主機）／`60.0.0.48-r3`（子機）、cloudflared `2026.9.1`、mosquitto mTLS。
主機側 tunnel 已裝好（見 skill `ug65-cloudflared`）。

## ⚠️ 第一步：必須先向用戶問齊（唔准靠估）

**用到本 skill 嘅第一件事，就係問清楚下面四樣。問齊先開始做。**

- ❌ 唔准用預設值蒙混（唔准當 `username` 一定係 `admin`）
- ❌ 唔准由舊對話、舊 log、`snapshots/` 或之前嘅部署紀錄推測
- ❌ 唔准叫用戶「照住上面嘅例子填」
- ✅ 缺任何一項 → **停手，直接問用戶**

| 必問 | 說明 |
|---|---|
| **1. 子機 IP** | 可以多過一個，逗號分隔，例如 `192.168.68.109,192.168.68.110`。順便問埋**主機（控制器）IP**，因為驗收要靠佢查 Fleet 狀態。 |
| **2. Username** | 子機 Web GUI 登入帳號。多數係 `admin`，但**一樣要問過**。 |
| **3. Password** | 該帳號嘅密碼。提醒用戶用**單引號**包住（密碼常含 `$$`）。主機同子機唔同密碼就要分別問。 |
| **4. Domain** | 主機 tunnel 上嘅 hostname，例如 `mqtt-xxx.example.com`。**一定要一層域名**（見下面嘅 SSL 限制）。 |

> 缺任何一項就**停手問**，唔准攞住例子值（`mqtt-xxx.example.com`）就落手做。

## 用戶只需要提供四樣

| 輸入 | 例 |
|---|---|
| 子機 IP（可以多過一個，逗號分隔） | `192.168.68.109,192.168.68.110` |
| Username | `admin`（**必問**，唔准假設） |
| Password | 子機 Web GUI 密碼（**必問**；同主機一樣嘅話講明） |
| Domain（Tunnel hostname，**一層域名**） | `mqtt-xxx.example.com` |

**唔需要**：root 密碼、SSH、VPN、固定 IP、主機 token。
**前置**：執行部署的電腦要**同子機同一個 LAN**（因為要經 HTTP 推 37MB binary 落子機）。

## 兩條最常問的問題

**Q：子機要唔要裝 Cloudflare connector？**
唔要。Cloudflare 的 connector（`cloudflared tunnel run`）**只喺主機**跑，負責將 tunnel 接上 Cloudflare edge。
子機只跑 **`cloudflared access tcp`**（client 端代理），係「access 工具」唔係 connector：
佢將本機一個 TCP port 反代去 `https://<hostname>`，令子機的原廠 packet forwarder 可以當佢係一個普通 MQTT server。
所以子機側只需要 **cloudflared 一個 binary**，唔需要開 tunnel、唔需要 token、唔需要 Cloudflare Access app／Service Token（實測唔帶 token 都通，安全靠 mosquitto 的 mTLS）。

**Q：點解唔可以直接填 `127.0.0.1`？**
UG65 firmware 用 **loopback 判別 local / remote**：

| `serv_addr` | firmware 當成 | 產生的 `/etc/lora-gateway-bridge/lora-gateway-bridge.toml` |
|---|---|---|
| `127.0.0.1` / `localhost` | 本地 Embedded NS | `server="tcp://127.0.0.1:1883"`（**封包只入自己 mosquitto，永遠去唔到主機**） |
| 任何其他地址 | Remote Embedded NS | `server="ssl://<addr>:<port>"` + `ca_cert`/`tls_cert`/`tls_key`（經 tunnel 出去） |

所以本 skill 將 **`10.99.99.1/32` 掛喺 `lo`**（非 loopback、穩定、唔隨 site/SIM 變），
cloudflared 綁 `10.99.99.1:28883`（唔綁 `0.0.0.0`，唔會在 LAN 曝露 listener），
packet forwarder 填 `10.99.99.1:28883`。

## 主機側前置（做一次）

Zero Trust → Networks → Tunnels → 該 tunnel → Public Hostname：

| Hostname | Service |
|---|---|
| `mqtt-xxx.example.com` | **`tcp://localhost:18883`** |
| （可選）`01-xxx.example.com` | `https://localhost:443`（noTLSVerify） |

⚠️ 兩個硬性限制：
1. **一定要 `tcp://`**。用 `http://localhost:18883` 會回 502／`tls: handshake failure`（HTTP 解析器等 raw MQTT binary stream）。
2. **hostname 要一層**。免費 Universal SSL 只包 `example.com` + `*.example.com`：
   `mqtt.example.com` ✅、`mqtt-site.example.com` ✅、`mqtt.site.example.com` ❌（edge 會直接 `fatal:handshake failure`，連 ServerHello 都冇）。

## 執行步驟

### 第 1 步：一鍵部署

```powershell
cd "<workspace>\.dsh\skills\ug65-sub-cloudflared\scripts"
python sub_install.py --hosts 192.168.68.110 --host mqtt-xxx.example.com \
       --password 'OVER50_PW' --add-dest --register --gw-name Gateway_03 --main 192.168.68.106
```

| 參數 | 作用 |
|---|---|
| `--hosts` | 子機 IP，多台用逗號分隔（會逐台做） |
| `--host` | tunnel hostname（必須一層域名） |
| `--port` | 本機 tunnel 出口 port，預設 `28883` |
| `--addr` | 綁喺 lo 的非 loopback 地址，預設 `10.99.99.1` |
| `--password` / `--user` | admin 登入（預設 user `admin`） |
| `--binary` | cloudflared arm64 binary；唔填會依次找 `bin/`、workspace 快取，最後試 GitHub 下載 |
| `--local-ip` | 本機喺子機 LAN 的 IP（預設自動偵測） |
| `--add-dest` | 子機冇 remote destination 時自動新增一個（預設只會提示，唔會亂改） |
| `--main` | 主機 IP：裝完順手查 Fleet `connected` 狀態 |
| `--register` | 配合 `--main`：未登記入 Fleet 就自動 `POST /api/gateways` 加入（名稱預設 `Gateway_<mac 尾 4 位>`） |
| `--gw-name` | 自動登記時用嘅名字（例 `Gateway_03`） |
| `--settle` | apply 之後等幾秒再檢查，預設 20 |

Script 亦會**強制將 `id=0`（本地 Embedded NS）設成 `serv_enabled=false`** —— Milesight KB 要求 agent 必須停用自己嘅 Embedded NS。

Script 每台做嘅事（全部經原廠 Web UI，登入 admin 就夠）：

1. 開 Node-RED（`yruo_loragw set node_red {enable:true}`）。
2. 上傳臨時 ctl flow（`/cgi-bin/file-import`）→ 關再開 Node-RED → 攞 **root shell**（`/node-red/cfrun`）。
3. 本機開 `python -m http.server`，子機 busybox `wget` 拉 binary → **驗 md5** → `cp /usr/bin/cloudflared`。
4. 寫 `/etc/init.d/cfagent`（procd、`START=95`、`respawn 3600 5 5`、`ip addr add 10.99.99.1/32 dev lo`）→ `enable` + `restart`。
5. 改 destination：第一個 `id != 0` 的 destination 設 `serv_addr=10.99.99.1`、`serv_mqtt_port=28883`、`serv_enabled=true` → `yruo_apply apply`。
6. 收尾：上傳空 flow `[]` + `node_red_set(false)`（root shell 消失，回復原狀）。
7. 原設定會存底去 `snapshots/conf_<ip>.json`（只第一次存）。

### 第 2 步：驗證（唔改設定）

```powershell
python sub_install.py --verify-only --hosts 192.168.68.109 --password 'OVER50_PW' --main 192.168.68.106
```
正常輸出（實測）：
```
  id=0 enabled=False addr=localhost port=1883 connected=0
  id=1 enabled=True addr=10.99.99.1 port=28883 connected=1
  gateway_id=C0BA1FFFFE02D2E8 push_data_ack=100.00%
   Fleet: Gateway_02 connected=True lastSeen=2026-10-05 05:13:46
```
想睇子機內部（toml／listener／log）就要 root shell，跑 `sub_install.py` 安裝模式時的第 5 步輸出已有，重點核對：
- 子機 shell：`netstat -ltn | grep 28883` → `10.99.99.1:28883 LISTEN`
- 子機 toml：`grep ^server /etc/lora-gateway-bridge/lora-gateway-bridge.toml` → `server="ssl://10.99.99.1:28883"`
- 主機 `GET https://<主機IP>/api/gateways?limit=9999&offset=0&organizationID=1` → 該子機 `connected: true`
- 主機 Status 首頁：Gateway Fleet `x/y`

### 第 2 步：重開機驗證（建議每台做一次）

```powershell
python sub_reboot_test.py --hosts 192.168.68.110 --password 'OVER50_PW' --main 192.168.68.106
```
會 reboot 子機 → 等 GUI 返嚟 → 檢查 `lo` 有冇 `10.99.99.1/32`、`28883` listener、cloudflared 進程、
toml 有冇保持 `ssl://10.99.99.1:28883`、destination `connected=1`、主機 Fleet 狀態，最後清走 root shell。

### 第 3 步：主機 Gateway Fleet 登記（如果未登記）

Network Server → Gateways → Add，填子機 **Gateway ID**（子機 `yruo_loragw get status` 的 `gateway_id`，例 `C0BA1FFFFE02D2E8`）。
或者用 HTTP API（需要 AES 加密密碼登入攞 JWT）：

```python
POST https://<主機IP>/api/internal/login   {"username":"admin","password":"<AES加密版>"}  → {"jwt": "..."}
POST https://<主機IP>/api/gateways          Bearer <jwt>
     {"mac":"C0BA1FFFFE02D2E8","name":"Gateway_02","organizationID":"1","networkServerID":"1"}
```

⚠️ 密碼**一定要加密版**（`ug65_lib.encrypt_password`）；用明碼會回 `wrong password` 而且**有鎖帳號計數器**。

### 還原

```powershell
python sub_install.py --hosts 192.168.68.109 --password 'OVER50_PW' --revert
```
停用並刪除 `cfagent`、移除 `10.99.99.1`、刪 binary，再還原 `snapshots/conf_<ip>.json` 的 destination。

## 驗收標準

- [ ] 子機 `10.99.99.1:28883 LISTEN`，`ps` 見到 `cloudflared access tcp --hostname <host>`
- [ ] 子機 toml `server="ssl://10.99.99.1:28883"`（**唔係** `tcp://127.0.0.1:1883`）
- [ ] 主機 Fleet 該子機 `connected: true`、`lastSeenAt` 持續更新
- [ ] 主機 mosquitto log 有 `New client connected from ::1 as <GATEWAY_ID>`
- [ ] `reboot` 後 50 秒內全部自動恢復
- [ ] `node_red.enable = false`、`/node-red/cfrun` 回 502（root shell 已清）

## 已踩過的坑

| 坑 | 症狀 | 解決 |
|---|---|---|
| 填 `127.0.0.1` | Fleet 永遠 `connected: false`，/tmp/pkt_fwd_type 只剩 `ursalink 1` | 用 `10.99.99.1`（本 skill 預設） |
| route 用 `http://` | `remote error: tls: handshake failure`、HTTP 502 | 改 `tcp://localhost:18883` |
| 兩層域名 | edge 冇 ServerHello、`SSLV3_ALERT_HANDSHAKE_FAILURE` | 用一層 `mqtt-xxx.example.com` |
| fw `60.0.0.48-r3` 登入 | login 回應 `result[0]` 冇 `td`；`CookieConflictError: multiple cookies named 'td'` | `ug65_lib.login()` 已修（由 Set-Cookie fallback） |
| cron 保活 | Milesight 會重寫 cron | 一律用 procd（`/etc/init.d/cfagent`） |
| Node-RED 原廠 flow | 子機本來冇用過 Node-RED，冇備份 | 收尾上傳 `[]` + 熄 Node-RED |
| PowerShell 改含中文檔 | `Get-Content \| Set-Content` 會變 ANSI 亂碼 | 用編輯工具／Python 寫檔 |

## 檔案

- `scripts/sub_install.py` — 一鍵部署／還原（本 skill 主入口）
- `scripts/ug65_lib.py` — Web GUI 登入 + `/cgi` RPC + 檔案上傳 + 密碼 AES
- `scripts/rootctl.py` — 借 Node-RED exec node 做臨時 root shell
- `bin/cloudflared-linux-arm64` — binary 快取（自動下載或由已裝好的 gateway 抄，`scp root@<gw>:/usr/bin/cloudflared .`）
- `snapshots/conf_<ip>.json` — 每台子機改動前的 `general_conf.servs` 存底
