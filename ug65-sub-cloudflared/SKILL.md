---
name: ug65-sub-cloudflared
description: Two things on a Milesight UG65/UG56/UG67 SUB gateway, both using only the admin account (no root password). PART A — turn the sub into a Gateway Fleet agent that ships its LoRa packets to a controller gateway over native MQTT by running cloudflared as a client-side access proxy (cloudflared access tcp), pinning a non-loopback address on lo because the firmware treats a loopback server address as local mode, and pointing the packet forwarder at it; this needs NO tunnel token. PART B — give the sub its OWN full Cloudflare Tunnel connector (cloudflared tunnel run) with its own Public Hostname so it is reachable from the internet; this needs a tunnel token. Use when a user gives a sub-gateway IP plus a tunnel hostname such as mqtt-xxx.example.com, or asks to install a Cloudflare tunnel on a sub gateway. IMPORTANT — before any action, always ask the user for the sub-gateway IP(s), username, password and tunnel domain, and for PART B also ask for that sub's own tunnel token — never assume, reuse or copy a token from an earlier session, a log, or the controller gateway.
whenToUse: 當用戶要將 Milesight UG65 子機（有 SIM、冇固定 IP、唔同 site）接去主機的 Gateway Fleet／Embedded NS（提供 Cloudflare Tunnel hostname，例 mqtt-xxx.example.com），或者要「子機都裝埋 cloudflare tunnel／子機要有自己 hostname」時使用。Part A 只需要「子機 IP + hostname + admin 密碼」，唔需要 root，亦唔要 token；Part B 要子機自己嘅 Tunnel Token，**一定要問用戶攞**。
---

# UG65 子機：Cloudflare Tunnel（兩種用法）

實測環境：UG65 ×2、firmware `60.0.0.49-r3`（主機）／`60.0.0.48-r3`（子機）、cloudflared `2026.9.1`、mosquitto mTLS。

| | Part A：子機做 Gateway Fleet agent | Part B：子機自己嘅 tunnel connector |
|---|---|---|
| 做乜 | LoRa 封包經**主機**嘅 tunnel 上主機 mosquitto（`cloudflared access tcp`） | 子機自己一條完整 tunnel + 自己 hostname（`cloudflared tunnel run`），互聯網直接開子機 |
| 要 token | ❌ 唔要 | ✅ **要，一定要問用戶攞** |
| 入口 | `scripts/sub_install.py` | `scripts/sub_connector.py`（Part B 喺文件最後） |

⚠️ 純粹為咗 Gateway Fleet **唔需要**做 Part B —— 呢兩件事完全獨立，可以只做 A、只做 B、或者兩樣並存（同一台機兩個 cloudflared 進程，實測冇衝突）。

## ⚠️ 第一步：必須先向用戶問齊（唔准靠估）

**用到本 skill 嘅第一件事，就係問清楚下面幾樣。問齊先開始做。**

- ❌ 唔准用預設值蒙混（唔准當 `username` 一定係 `admin`）
- ❌ 唔准由舊對話、舊 log、`snapshots/` 或之前嘅部署紀錄推測
- ❌ 唔准叫用戶「照住上面嘅例子填」
- ❌ 唔准攞其他機（尤其**主機／控制器**）嘅 Tunnel Token 頂替（見下）
- ✅ 缺任何一項 → **停手，直接問用戶**

| 必問 | 說明 |
|---|---|
| **1. 子機 IP** | 可以多過一個，逗號分隔，例如 `192.168.68.109,192.168.68.110`。順便問埋**主機（控制器）IP／hostname**，因為驗收要靠佢查 Fleet 狀態。 |
| **2. Username** | 子機 Web GUI 登入帳號。多數係 `admin`，但**一樣要問過**。 |
| **3. Password** | 該帳號嘅密碼。提醒用戶用**單引號**包住（密碼常含 `$$`）。主機同子機唔同密碼就要分別問。 |
| **4. Domain** | Part A：主機 tunnel 上嘅 hostname，例如 `mqtt-xxx.example.com`。**一定要一層域名**（見下面嘅 SSL 限制）。 |
| **5. Tunnel Token（只做 Part B 先要）** | 子機**自己嗰條** tunnel 嘅 token（`eyJ...`，Cloudflare dashboard → Zero Trust → Networks → Tunnels）。**一定要問用戶攞**，問嘅時候順便問佢條 Public Hostname 想指去邊（例 `https://localhost:443`）。 |

> 缺任何一項就**停手問**，唔准攞住例子值就落手做。

## 用戶只需要提供（Part A 四樣）

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
要唔要，睇你想做邊件事：

| 目的 | 子機要跑 | 要 token？ |
|---|---|---|
| 只係做 Gateway Fleet（LoRa 封包上主機） | **`cloudflared access tcp`**（client 端代理，唔係 connector） | ❌ 唔要 |
| 想由互聯網直接開子機 Web GUI（自己 hostname） | **`cloudflared tunnel run`**（真 connector） | ✅ 要子機自己嘅 token |

`access tcp` 係「access 工具」：佢將本機一個 TCP port 反代去 `https://<hostname>`，令子機原廠 packet forwarder
可以當佢係一個普通 MQTT server。實測唔帶 token 都通，安全靠 mosquitto 的 mTLS。

真 connector（Part B）就會同主機一樣長註冊 4 條 QUIC connection 上去 Cloudflare edge，子機要自己一條 tunnel。
兩者**可以同時跑**（實測 PID 並存冇衝突，`cf-maintain.sh` 用 `pgrep -f "cloudflared.*tunnel"` 唔會誤認 `access tcp`）。

**Q：點解唔可以直接填 `127.0.0.1`？**
UG65 firmware 用 **loopback 判別 local / remote**：

| `serv_addr` | firmware 當成 | 產生的 `/etc/lora-gateway-bridge/lora-gateway-bridge.toml` |
|---|---|---|
| `127.0.0.1` / `localhost` | 本地 Embedded NS | `server="tcp://127.0.0.1:1883"`（**封包只入自己 mosquitto，永遠去唔到主機**） |
| 任何其他地址 | Remote Embedded NS | `server="ssl://<addr>:<port>"` + `ca_cert`/`tls_cert`/`tls_key`（經 tunnel 出去） |

所以本 skill 將 **`10.99.99.1/32` 掛喺 `lo`**（非 loopback、穩定、唔隨 site/SIM 變），
cloudflared 綁 `10.99.99.1:28883`（唔綁 `0.0.0.0`，唔會在 LAN 曝露 listener），
packet forwarder 填 `10.99.99.1:28883`。

---

# Part A：子機做 Gateway Fleet agent（唔要 token）

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
cd ug65-sub-cloudflared/scripts
python sub_install.py --hosts 192.168.68.110 --host mqtt-xxx.example.com \
       --password '<ADMIN_PASSWORD>' --add-dest --register --gw-name Gateway_03 --main 192.168.68.106
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
python sub_install.py --verify-only --hosts 192.168.68.109 --password '<ADMIN_PASSWORD>' --main 192.168.68.106
```
正常輸出（實測）：
```
  id=0 enabled=False addr=localhost port=1883 connected=0
  id=1 enabled=True addr=10.99.99.1 port=28883 connected=1
  gateway_id=C0BA1FFFFE0001 push_data_ack=100.00%
   Fleet: Gateway_02 connected=True lastSeen=2026-10-05 05:13:46
```
想睇子機內部（toml／listener／log）就要 root shell，跑 `sub_install.py` 安裝模式時的第 5 步輸出已有，重點核對：
- 子機 shell：`netstat -ltn | grep 28883` → `10.99.99.1:28883 LISTEN`
- 子機 toml：`grep ^server /etc/lora-gateway-bridge/lora-gateway-bridge.toml` → `server="ssl://10.99.99.1:28883"`
- 主機 `GET https://<主機IP>/api/gateways?limit=9999&offset=0&organizationID=1` → 該子機 `connected: true`
- 主機 Status 首頁：Gateway Fleet `x/y`

### 第 2 步：重開機驗證（建議每台做一次）

```powershell
python sub_reboot_test.py --hosts 192.168.68.110 --password '<ADMIN_PASSWORD>' --main 192.168.68.106
```
會 reboot 子機 → 等 GUI 返嚟 → 檢查 `lo` 有冇 `10.99.99.1/32`、`28883` listener、cloudflared 進程、
toml 有冇保持 `ssl://10.99.99.1:28883`、destination `connected=1`、主機 Fleet 狀態，最後清走 root shell。

### 第 3 步：主機 Gateway Fleet 登記（如果未登記）

Network Server → Gateways → Add，填子機 **Gateway ID**（子機 `yruo_loragw get status` 的 `gateway_id`，例 `C0BA1FFFFE0001`）。
或者用 HTTP API（需要 AES 加密密碼登入攞 JWT）：

```python
POST https://<主機IP>/api/internal/login   {"username":"admin","password":"<AES加密版>"}  → {"jwt": "..."}
POST https://<主機IP>/api/gateways          Bearer <jwt>
     {"mac":"C0BA1FFFFE0001","name":"Gateway_02","organizationID":"1","networkServerID":"1"}
```

⚠️ 密碼**一定要加密版**（`ug65_lib.encrypt_password`）；用明碼會回 `wrong password` 而且**有鎖帳號計數器**。

### 還原

```powershell
python sub_install.py --hosts 192.168.68.109 --password '<ADMIN_PASSWORD>' --revert
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

---

# Part B：子機自己嘅 Cloudflare Tunnel connector

（獨立 tunnel + 自己 hostname，**要 token**。Part A 嗰四樣輸入照舊要問。）

## 幾時要做

用戶想**由互聯網直接打子機自己嘅 hostname**（例 `02-xxx.example.com`）開子機 Web GUI、
或者子機唔想靠主機條 tunnel 都要 reachable。純粹為咗 Gateway Fleet **唔需要**（嗰個只要 Part A）。

## ⚠️ 硬規矩：token 一定要問用戶攞

- 子機要**自己一條** tunnel：Cloudflare dashboard → Zero Trust → Networks → Tunnels →
  **Create a tunnel**（Cloudflare 出嘅就行）→ 複製 `eyJ...`。
- ❌ 唔准由舊對話、舊 log、`snapshots/` 推測；❌ **尤其唔准照抄主機（controller）嘅 token**。
  實測（同一個 account `<CF_ACCOUNT_ID>`）：
  主機 tunnel = `<CONTROLLER_TUNNEL_ID>`（放喺 workspace `tunnel.token`）；
  02 子機 = `<SUB_TUNNEL_ID>`。**兩條唔同**，只係同 account。
  所以「token 內 `a` 一樣」**唔代表**可以用同一條。
- `sub_connector.py` 幫你攔：
  - 冇 `--token` → 印出「要問用戶拎咩」清單，**exit 2**（唔會偷偷用舊值）
  - token 撞主機嘅 → **exit 2**，並列出主機 tunnel id
  - 正常 → 印 account / tunnel id，明示「同 account、tunnel 唔同」
- 提醒用戶：token 就係該 tunnel 嘅憑證，貼咗去公開地方就當洩漏，可以 rotate。

## 執行

```powershell
cd ug65-sub-cloudflared/scripts

# 1) 淨係驗 token（唔碰任何機；新 token 交嚟先跑呢句）
python sub_connector.py --check-token --token '<子機 TOKEN>'

# 2) 裝（會自動叫 ug65-cloudflared\deploy.py 落手）
python sub_connector.py --hosts 192.168.68.109 --password '<ADMIN_PASSWORD>' --token '<子機 TOKEN>' `
       --enable-ssh --ssh-lan 192.168.68.0/24 --main 01-xxx.example.com

# 3) 唔改任何嘢，淨核對現狀（隨時可跑；機連唔到會 exit 3）
python sub_connector.py --verify-only --hosts 192.168.68.109 --password '<ADMIN_PASSWORD>' --main 01-xxx.example.com
```

其他參數：`--keep-nodered`（唔還原 Node-RED）、`--skip-mqtt-check`、`--user`（預設 `admin`）、`--wait`（預設 100 秒）。

## `sub_connector.py` 做嘅事（順序）

1. **token gate**：驗 base64／`a`,`t`,`s`、撞主機就停。
2. 讀安裝前 MQTT destination baseline（借 `sub_install.py --verify-only`）。
3. 記低 Node-RED 原本開定關（`node_red_enabled`）。
4. 叫 `ug65-cloudflared\scripts\deploy.py --host … --token …`：借 Node-RED 攞 root，寫
   `/etc/cloudflared/token`(600)、`/etc/init.d/cloudflared`(S95)、`cf-maintain.sh`／`cf-maintain-loop.sh`、
   `cfkeepalive`(S96)、`cfsshfw`(S20)；binary 已存在就**跳過下載**（慳 SIM 數據）。
5. **還原 Node-RED**：上傳 `[]` 做 `nodered_flows_json` + `node_red_set(gui, False)`。
   ⚠️ `deploy.py` 嘅 `cleanup_ctl()`（deploy.py:54-74）收尾會 `node_red_set(false)` 再 `true`
   —— **佢會將 Node-RED 留返開住**，wrapper 就係負責補呢一刀。
6. SSH 核對：`/proc/<pid>/cmdline` 睇**兩個 cloudflared 並存**、`28883` listener、rc.d、
   有冇 `ERR`、ESTABLISHED 連線。
7. 再讀一次 destination／Fleet，同 baseline 對比；**Web GUI 同 SSH 都唔通 → exit 3**（唔當成功）。

### Node-RED 點解一定要還原

- tunnel 靠 procd `S95cloudflared` 開機自啟，**唔靠 flow**；Node-RED 只係安裝時借嚟攞 root shell 嘅臨時工具。
- 實測熄咗之後 free memory 由 104MB → **195920 KB**（省 ~90MB），對 SIM 機好重要。
- 驗收要見到 `node_red.enable=false` 同 `/node-red/cfrun` 消失（flow 清走）。

## 驗收標準（Part B）

- [ ] 子機**同時**有：`cloudflared access tcp --hostname <主機 hostname>`（Part A）
      同 `cloudflared --no-autoupdate tunnel run`（Part B）
      —— 一定要讀 `/proc/<pid>/cmdline`，**`ps` 會截斷 hostname**
- [ ] 子機 log 有 `Starting tunnel tunnelID=<子機 tunnel id>` + 4 條 `Registered tunnel connection … protocol=quic`
- [ ] 由外部（唔同網絡）`GET https://<子機 hostname>/` → **200** 同 UG65 登入頁（Server: cloudflare）
- [ ] **MQTT 前後一致**：`general_conf.servs` 冇改、`10.99.99.1:28883` 仲 LISTEN、
      主機 Fleet 該子機仍然 `connected: true` 且 `lastSeen` 有新
- [ ] `node_red.enable=false`、`1880` 冇 listen、`/node-red/cfrun` 消失
- [ ] SSH（如要）`:22 LISTEN`；唔想要就唔好加 `--enable-ssh`

**點分辨 hostname 去邊台機**：兩台返同一個登入頁、`pn` 都一樣，**睇 `rtver` 最準**
（`02-tmtp` → `60.0.0.48-r3`、`01-tmtp` → `60.0.0.49-r3`）。做法：經 `/cgi` login 再讀 `rtver`。

## 實測：裝 connector **唔會**影響 MQTT（2026-10-05 查清）

子機側一直有 `ERR failed to connect to origin error="websocket: bad handshake" originURL=https://mqtt-xxx.example.com`
（每次幾秒 retry），用戶一度懷疑係新裝嘅 connector 搞爛 MQTT。查完結論：**冇因果**。

| 子機本地時間 | 事件 |
|---|---|
| 07:29:12 | 子機 boot，cfagent cloudflared PID 4047（DNS 未通） |
| **07:31:50** | **第一條 bad handshake**（早過 install 2 小時 14 分） |
| 09:38 | 子機再 reboot（`uptime` 1258s @09:59） |
| 09:38–09:52:25 | 新 boot PID 3955 繼續 bad handshake |
| **09:46:19** | **裝 tunnel**：`procd: /etc/init.d/cloudflared start`、`Starting tunnel tunnelID=d93b34a5-…` |
| 09:47:27 | deploy 收尾再 start 一次 |
| **09:52:25** | **最後一條 bad handshake**（install 後 6 分鐘自己停咗） |
| 09:58:22 | `cfagent restart`（新 PID 18973）→ 之後 0 條 |

- `/tmp/cfinstall.log` 逐條命令證明：`### RUN 20261005-164541 ###` … `### DONE ###`，全程**冇**寫
  `general_conf.servs`、**冇**改 `lora-gateway-bridge.toml`、**冇** restart mosquitto／bridge
  （只寫 cloudflared 檔案 + `ssh_enable` + crontab + cron restart）。
- 通咗嘅實證：子機 `openssl s_client -connect 10.99.99.1:28883 -servername mqtt-xxx.example.com`
  → `CONNECTED` 收到主機 mosquitto 嘅 Ursalink server cert，跟住 `SSL alert number 40`
  （= 冇帶 client cert 而 mosquitto `require_certificate true`，**TLS 握到手即 tunnel 通**）；
  `netstat -ant` 有 `10.99.99.1:44450 ↔ 10.99.99.1:28883 ESTABLISHED` 同
  `<sub-sim-ip>:60726 → 172.67.156.65:443 ESTABLISHED`（**子機出 WAN 行 cellular**）；
  主機 Fleet `Gateway_02 connected=True`。

## Part B 已踩過的坑

| 坑 | 症狀 | 解決 |
|---|---|---|
| 以為子機 token 可以照抄主機 | 兩條 tunnel 撞，子機 register 唔到／主機 tunnel 設定被搶 | `sub_connector.py` 攔錯 exit 2；同 account 但 tunnel id 必須唔同 |
| `deploy.py` 收尾留返 Node-RED 開住 | `1880` 有 listen、free 少 ~90MB | 用 `sub_connector.py`（會還原），或 `--keep-nodered` 自己知 |
| `ps` 睇子機 cmdline | hostname 被截斷，睇唔清邊個係 connector | 讀 `/proc/<pid>/cmdline`（`tr '\0' ' '`） |
| 子機 IP 係 DHCP 會變 | LAN ping 唔到、ARP 冇咗、Web GUI timeout | 用 hostname（`02-tmtp…`）做 Web GUI 檢查；tunnel 係子機**主動**連出去，換 IP 唔影響 tunnel |
| hostname 返 **HTTP 530** | Cloudflare 話 origin 唔喺度 | 即係子機 connector 唔喺度（熄機／走咗／未上線），唔係 route 錯 |
| 子機 busybox 冇 `timeout` | `sh: timeout: not found` | 用 `(cmd &) ; pid=$!; while kill -0 $pid; do sleep 1; done; kill $pid` |
| 子機 `iptables` 壞 | `error while loading shared libraries: libiptext.so` | 唔靠 iptables 診斷，睇 netstat／`/etc/urlog/system.log` |
| 主機 LAN IP 連唔到 | 由本機打 `192.168.68.106:443` → WinError 10061 | 改用 hostname `01-xxx.example.com` 經 tunnel 查 Fleet |

## 檔案（Part A ＋ Part B）

- `scripts/sub_install.py` — **Part A** 一鍵部署／還原（Gateway Fleet agent）
- `scripts/sub_connector.py` — **Part B** 子機自己嘅 tunnel connector（wrapper：token 硬規矩 + Node-RED 善後 + 前後對照）
- `scripts/ug65_lib.py` — Web GUI 登入 + `/cgi` RPC + 檔案上傳 + 密碼 AES
- `scripts/rootctl.py` — 借 Node-RED exec node 做臨時 root shell
- `scripts/fleet_status.py` — 讀主機 Fleet `connected`／`lastSeen`（`--main <host> --password <pw>`）
- `bin/cloudflared-linux-arm64` — binary 快取（約 37MB，**唔入 git**；首次執行自動下載，或由已裝好嘅 gateway 抄：`scp root@<gw>:/usr/bin/cloudflared .`）—— 見 [`bin/README.md`](bin/README.md)
- `snapshots/conf_<ip>.json` — 每台子機改動前的 `general_conf.servs` 存底

## 依賴嘅另一個 skill

Part B 真正落手裝嘅係 skill **`ug65-cloudflared`** 嘅 `scripts/deploy.py`
（`--host --user --password --token [--enable-ssh --ssh-lan] --verify-mode --wait`）。
佢負責 Node-RED root shell、寫 `/etc/cloudflared/*`、procd 服務、SSH 開關同清理。
