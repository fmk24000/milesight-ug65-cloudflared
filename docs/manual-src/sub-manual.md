---
title: Milesight UG65 子機接入 Gateway Fleet
type: field-manual
status: verified
---

# Milesight UG65 子機接入 Gateway Fleet

呢份係**子機**（SUB gateway）嘅人手操作版，同主機那份 manual 係兩件事。前置係：**主機已經裝好 cloudflared tunnel**（見 `browser-manual.html`）。

實測環境：UG65 ×2、firmware `60.0.0.49-r3`（主機）／`60.0.0.48-r3`（子機）、cloudflared `2026.9.1`、mosquitto mTLS。

> **Gateway Fleet**：Milesight Network Server 睇住一堆 gateway 嘅清單。子機要做「agent」，將自己收到嘅 LoRa 封包送去主機，主機喺 Fleet 度就會見到佢 `connected: true`。
> **cloudflared access tcp**：**client 端**代理，唔係 connector。佢將本機一個 TCP port 反代去 `https://HOSTNAME`，令子機原廠 packet forwarder 以為自己連住一個普通 MQTT server。
> **loopback 判別**：firmware 一見到 `serv_addr` 係 `127.0.0.1` / `localhost`，就當你要用「本地 Embedded NS」，封包永遠唔會出街。所以一定要用非 loopback 地址。

## 做之前要問齊（唔准靠估）

- ❌ 唔准當 `username` 一定係 `admin`
- ❌ 唔准由舊對話、舊 log、舊部署紀錄推測
- ❌ **唔准攞主機／其他機嘅 Tunnel Token 頂替**
- ✅ 缺任何一項 → 停手，直接問

| 必問 | 說明 |
|---|---|
| **1. 子機 IP** | 可以多過一部，逗號分隔，例 `192.168.68.109,192.168.68.110`。順便問埋**主機 IP／hostname**，驗收要靠佢查 Fleet |
| **2. Username** | 子機 Web GUI 帳號，多數 `admin` 但一樣要問 |
| **3. Password** | 該帳號密碼，用單引號包住（常含 `$$`）。主機同子機密碼唔同就分別問 |
| **4. Domain** | 主機 tunnel 上嘅 hostname，例 `mqtt-xxx.example.com`。**一定要一層域名** |
| **5. 子機自己嘅 Token** | **只做 Part B 先要**。Cloudflare dashboard 為子機開一條新 tunnel 攞，唔可以抄主機 |

**前置**：跑腳本嗰部電腦要**同子機同一個 LAN** —— 因為要經 HTTP 推一個約 37MB 嘅 cloudflared binary 落子機。

## 兩件事，唔好搞亂

| | Part A | Part B |
|---|---|---|
| 目的 | LoRa 封包上主機（Gateway Fleet） | 互聯網直接開子機自己 |
| 子機跑 | `cloudflared access tcp` | `cloudflared tunnel run` |
| Token | **唔要** | **要子機自己嗰條** |
| 入口 | `sub_install.py` | `sub_connector.py` |

純粹為咗 Fleet **唔需要**做 Part B。兩者可以並存（同一部機兩個 cloudflared 進程，實測冇衝突）。

## 原理：一個填錯就靜靜死嘅位

子機唔係「連去主機 IP」，而係**自己走出去** Cloudflare edge，再由主機嗰條 tunnel 接住。所以子機唔需要固定 IP、唔需要 port forwarding、行 SIM 都通。

真正決定成敗嘅係 firmware 點解讀 `serv_addr`：

| `serv_addr` 填乜 | firmware 當成 | 生成嘅 toml | 結果 |
|---|---|---|---|
| `127.0.0.1` / `localhost` | 本地 Embedded NS | `server="tcp://127.0.0.1:1883"` | 封包只入自己 mosquitto，Fleet **永遠** `connected: false` |
| 任何其他地址 | 遠端 Embedded NS | `server="ssl://ADDR:PORT"` + 三個 cert 欄 | 封包經 tunnel 出去主機 |

所以本方案將 **`10.99.99.1/32` 掛喺 `lo`**：唔喺 `127.0.0.0/8` 範圍，firmware 當佢係「出面」；但佢又唔會隨現場網絡或者 SIM 換 IP 而改變。

cloudflared 只綁 `10.99.99.1:28883`（**唔綁 `0.0.0.0`**），所以 LAN 上唔會多咗一個 listener。packet forwarder 就填 `10.99.99.1:28883`。

## Part A：主機側前置（做一次）

Zero Trust → Networks → Tunnels → 主機嗰條 tunnel → Public Hostname：

| Hostname | Service |
|---|---|
| `mqtt-xxx.example.com` | **`tcp://localhost:18883`** |
| （可選）`01-xxx.example.com` | `https://localhost:443`（No TLS Verify） |

兩個硬性限制：

1. **一定要 `tcp://`** —— 用 `http://localhost:18883` 會回 502 ／ `tls: handshake failure`，因為 HTTP 解析器會去啃一串原始 MQTT binary。
2. **hostname 要一層** —— 免費 Universal SSL 只包 `example.com` 同 `*.example.com`。`mqtt.example.com` ✅、`mqtt-site.example.com` ✅、`mqtt.site.example.com` ❌（edge 連 ServerHello 都唔出）。

## Part A：部署

```bash
cd ug65-sub-cloudflared/scripts

python sub_install.py --hosts 192.168.68.110 \
  --host mqtt-xxx.example.com \
  --password 'YOUR_ADMIN_PASSWORD' \
  --add-dest --register --gw-name Gateway_03 \
  --main 192.168.68.106
```

| 參數 | 作用 |
|---|---|
| `--hosts` | 子機 IP，逗號分隔，逐台做 |
| `--host` | 主機 tunnel hostname（一層域名） |
| `--port` | 本機 tunnel 出口 port，預設 `28883` |
| `--addr` | 綁喺 lo 嘅非 loopback 地址，預設 `10.99.99.1` |
| `--password` / `--user` | admin 登入（`--user` 預設 `admin`） |
| `--binary` | arm64 binary 路徑；唔填會搵 `bin/`、workspace 快取，最後試 GitHub 下載 |
| `--local-ip` | 本機喺子機 LAN 嘅 IP（預設自動偵測） |
| `--add-dest` | 子機冇 remote destination 時自動新增（預設只提示，唔會亂改） |
| `--main` | 主機 IP／hostname，裝完順手查 Fleet 狀態 |
| `--register` | 未登記入 Fleet 就自動 `POST /api/gateways` |
| `--gw-name` | 自動登記用嘅名（例 `Gateway_03`） |
| `--settle` | apply 之後等幾秒再檢查，預設 `20` |

腳本每台做嘅事：

1. 開 Node-RED（`yruo_loragw set node_red`）
2. 上傳臨時 ctl flow → 關再開 Node-RED → 攞 root shell（`/node-red/cfrun`）
3. 本機開 `python -m http.server`，子機 `wget` 拉 binary → 驗 md5 → `cp /usr/bin/cloudflared`
4. 寫 `/etc/init.d/cfagent`（procd、`START=95`、`respawn`、加 `10.99.99.1/32` 落 `lo`）
5. 改 destination：第一個 `id != 0` 嘅設 `serv_addr=10.99.99.1`、port `28883`、`serv_enabled=true`
6. 收尾：上傳空 flow `[]` + Node-RED 熄返（root shell 消失）
7. 原設定存底去 `snapshots/conf_IP.json`（只第一次存）

腳本亦會**強制將 `id=0`（本地 Embedded NS）設成 `serv_enabled=false`** —— Milesight KB 要求 agent 必須停用自己嘅 Embedded NS。

> ⚠️ 這個腳本只做 Part A。Part B 要用另一個入口 `sub_connector.py`，唔好混。

## Part A：驗收

```bash
python sub_install.py --verify-only \
  --hosts 192.168.68.109 \
  --password 'YOUR_ADMIN_PASSWORD' \
  --main 192.168.68.106
```

| 檢查 | 期望 |
|---|---|
| `id=0 enabled` | `False`（本地 Embedded NS 已停用） |
| `id=1 addr` / `port` | `10.99.99.1` / `28883` |
| `id=1 enabled` / `connected` | `True` / `1` |
| `gateway_id` | 例如 `C0BA1FFFFE0001` |
| `push_data_ack` | `100.00%` |
| 主機 Fleet | `connected=True`，`lastSeen` 持續更新 |

想睇子機內部就要 root shell（`sub_install.py` 安裝模式第 5 步嘅輸出已經有）：

```sh
netstat -ltn | grep 28883
# LISTEN 喺 10.99.99.1:28883

CFG=/etc/lora-gateway-bridge/lora-gateway-bridge.toml
grep ^server "$CFG"
# server="ssl://10.99.99.1:28883"
```

如果見到 `server="tcp://127.0.0.1:1883"`，即係 destination 改錯咗，唔會通。

## Part A：重開機驗證（建議每台做一次）

```bash
python sub_reboot_test.py \
  --hosts 192.168.68.110 \
  --password 'YOUR_ADMIN_PASSWORD' \
  --main 192.168.68.106
```

會 reboot 子機 → 等 GUI 返嚟 → 檢查 `lo` 有冇 `10.99.99.1/32`、`28883` listener、cloudflared 進程、toml 有冇保持、destination `connected=1`、主機 Fleet 狀態，最後清走 root shell。實測 **50 秒內**全部恢復。

## Part A：主機 Fleet 登記（如果未登記）

Network Server → Gateways → Add，填子機嘅 **Gateway ID**（子機 `yruo_loragw get status` 嘅 `gateway_id`）。

批量或者想自動化就用 HTTP API：

| 步驟 | 內容 |
|---|---|
| 1 | `POST https://MAIN_HOST/api/internal/login`，body 用 **AES 加密版**密碼，回 `jwt` |
| 2 | `POST https://MAIN_HOST/api/gateways`，`Authorization: Bearer JWT` |
| 3 | body：`mac`=`gateway_id`、`name`、`organizationID=1`、`networkServerID=1` |

> ⚠️ 密碼一定要用**加密版**（`ug65_lib.encrypt_password`）。用明碼會回 `wrong password`，而且**有鎖帳號計數器**。

## Part A：還原

```bash
python sub_install.py --hosts 192.168.68.109 \
  --password 'YOUR_ADMIN_PASSWORD' --revert
```

停用並刪除 `cfagent`、移除 `10.99.99.1`、刪 binary，再還原 `snapshots/conf_IP.json` 嘅 destination。

## Part B：幾時要做，同 token 規矩

只有當你想**由互聯網直接打子機自己嘅 hostname**（例 `02-xxx.example.com`）開子機 Web GUI 先要做。

**硬規矩：token 要問用戶攞，而且一定係子機自己嗰條。**

Cloudflare dashboard → Zero Trust → Networks → Tunnels → **Create a tunnel** → 複製 `eyJ...`。

唔可以抄主機嗰條：同一個 Cloudflare account 之下，兩條 tunnel 只係**共用 account id**，tunnel id 一定唔同。抄錯會令子機註冊唔到，甚至搶咗主機嘅設定。

`sub_connector.py` 會幫你攔：

| 情況 | 行為 |
|---|---|
| 冇帶 `--token` | 印出要問用戶攞咩，**exit 2**（唔會偷偷用舊值） |
| token 撞主機嗰條 | **exit 2**，並列出主機嘅 tunnel id |
| 正常 | 印出 account 同 tunnel id，明示兩者唔同 |

## Part B：執行

```bash
cd ug65-sub-cloudflared/scripts

# 1) 淨係驗 token，唔碰任何機。新 token 一到就先跑呢句
python sub_connector.py --check-token \
  --token 'YOUR_SUB_TUNNEL_TOKEN'

# 2) 裝（底層叫 ug65-cloudflared 嘅 deploy.py）
python sub_connector.py --hosts 192.168.68.109 \
  --password 'YOUR_ADMIN_PASSWORD' \
  --token 'YOUR_SUB_TUNNEL_TOKEN' \
  --enable-ssh --ssh-lan 192.168.68.0/24 \
  --main 01-xxx.example.com

# 3) 唯讀核對現狀，隨時跑得。機連唔到會 exit 3
python sub_connector.py --verify-only \
  --hosts 192.168.68.109 \
  --password 'YOUR_ADMIN_PASSWORD' \
  --main 01-xxx.example.com
```

其他參數：`--keep-nodered`（唔還原 Node-RED）、`--skip-mqtt-check`、`--user`（預設 `admin`）、`--wait`（預設 `100` 秒）。

`sub_connector.py` 做嘅事，順序係：

1. **token gate**：驗 base64、`a`/`t`/`s` 三個欄位，撞主機就停
2. 讀安裝前嘅 MQTT destination baseline（借 `sub_install.py --verify-only`）
3. 記低 Node-RED 本來開定關
4. 叫 `ug65-cloudflared/scripts/deploy.py`：借 Node-RED 攞 root，寫 `/etc/cloudflared/token`（600）、`/etc/init.d/cloudflared`（S95）、`cf-maintain.sh`、`cfkeepalive`（S96）、`cfsshfw`（S20）。binary 已經在就跳過下載，慳 SIM
5. **還原 Node-RED**：上傳 `[]` + 熄返。`deploy.py` 自己嘅收尾會將 Node-RED 留返開住，wrapper 就係補呢一刀
6. SSH 核對：`/proc/PID/cmdline` 睇兩個 cloudflared 並存、`28883` listener、rc.d、有冇 `ERR`
7. 再讀 destination／Fleet 同 baseline 對比；Web GUI 同 SSH 都唔通就 exit 3，**唔當成功**

## Part B：驗收

| 檢查 | 期望 |
|---|---|
| 兩個進程 | `cloudflared access tcp`（Part A）同 `cloudflared tunnel run`（Part B）同時存在 |
| 子機 log | `Starting tunnel tunnelID=` + 4 條 `Registered tunnel connection` 全 `protocol=quic` |
| 外部 | 唔同網絡開 `https://02-xxx.example.com/` → 200 同 UG65 登入頁 |
| MQTT 前後一致 | `general_conf.servs` 冇改、`10.99.99.1:28883` 仲 LISTEN、Fleet 仍然 `connected` |
| Node-RED | `node_red.enable=false`、`1880` 冇 listen、`/node-red/cfrun` 消失 |
| SSH（如要） | `:22` LISTEN；唔想要就唔好加 `--enable-ssh` |

睇邊個進程係邊個，**唔可以用 `ps`**（會截斷 hostname）：

```sh
for p in $(pgrep cloudflared); do
  tr '\0' ' ' < /proc/$p/cmdline
  echo
done
```

分辨兩台子機邊台打邊台：兩台返同一個登入頁、`pn` 一樣，**睇 `rtver` 最準**（`60.0.0.48-r3` vs `60.0.0.49-r3`）。經 `/cgi` login 再讀 `rtver` 就知。

## 排查

| 症狀 | 原因 | 解決 |
|---|---|---|
| Fleet 永遠 `connected: false` | `serv_addr` 填咗 `127.0.0.1` | 改用 `10.99.99.1`（本方案預設） |
| `tls: handshake failure` + HTTP 502 | route 用咗 `http://` | 改 `tcp://localhost:18883` |
| edge 冇 ServerHello | hostname 兩層 | 用一層 `mqtt-xxx.example.com` |
| 子機登入回 `CookieConflictError` | fw `60.0.0.48-r3` 回應冇 `result[0].td` | 已修：`login()` 會由 `Set-Cookie` fallback |
| 加咗嘅 cron 重開就冇 | Milesight 重寫 `/etc/crontabs/root` | 一律用 procd（`/etc/init.d/cfagent`） |
| 子機 IP 係 DHCP 會變 | LAN ping 唔到、ARP 冇咗 | 用 hostname 做檢查；tunnel 係子機主動出去，換 IP 唔影響 |
| hostname 返 **HTTP 530** | Cloudflare 話 origin 唔喺度 | 即係子機 connector 唔喺度（熄機／走咗），**唔係** route 錯 |
| 子機 `sh: timeout: not found` | busybox 冇 `timeout` | 自己用 `kill -0` 輪詢 |
| 子機 `iptables` error libiptext.so | 冇設 library path | 唔靠 iptables 診斷，睇 netstat 同 `/etc/urlog/system.log` |
| 主機 LAN IP 連唔到（WinError 10061） | 主機側只開咗 tunnel | 改用主機 hostname 經 tunnel 查 Fleet |
| 以為子機可以用主機 token | 兩條 tunnel 撞 | `sub_connector.py` 會攔，exit 2 |
| 裝完 Node-RED 仲開住 | `deploy.py` 收尾會開返 | 用 `sub_connector.py`（會還原）；free memory 差約 90MB |

## 實測：裝 Part B 唔會搞爛 MQTT

子機側一直有 `websocket: bad handshake` 呢類 retry，一度以為係新裝嘅 connector 搞爛 MQTT。查完結論：**冇因果**。

| 時間 | 事件 |
|---|---|
| 07:29 | 子機 boot，cloudflared PID 4047（DNS 未通） |
| 07:31 | 第一條 bad handshake（早過安裝 2 小時） |
| 09:46 | 裝 tunnel：`Starting tunnel tunnelID=` |
| 09:52 | 最後一條 bad handshake（安裝後 6 分鐘自己停） |
| 09:58 | `cfagent restart` 之後 0 條 |

`/tmp/cfinstall.log` 逐條命令證明：全程**冇**寫 `general_conf.servs`、**冇**改 `lora-gateway-bridge.toml`、**冇**重啟 mosquitto 或者 bridge。

Tunnel 真係通咗嘅證據：子機 `openssl s_client -connect 10.99.99.1:28883` 會收到主機 mosquitto 嘅 Ursalink server cert，跟住 `SSL alert 40`（= 冇帶 client cert，而 mosquitto 開咗 `require_certificate`）——**握手成功就代表 tunnel 通**。

## 附錄：檔案同依賴

| 檔案 | 作用 |
|---|---|
| `scripts/sub_install.py` | Part A 部署／驗收／還原 |
| `scripts/sub_connector.py` | Part B wrapper：token 硬規矩 + Node-RED 善後 + 前後對照 |
| `scripts/sub_reboot_test.py` | 重開機驗證 |
| `scripts/fleet_status.py` | 讀主機 Fleet `connected`／`lastSeen` |
| `scripts/ug65_lib.py` | Web GUI 登入、`/cgi` RPC、檔案上傳、密碼 AES |
| `scripts/rootctl.py` | 借 Node-RED exec node 做臨時 root shell |
| `bin/cloudflared-linux-arm64` | binary 快取（37MB，唔入 git，首次自動下載） |
| `snapshots/conf_IP.json` | 每台子機改動前嘅 `general_conf.servs` 存底 |

Part B 真正落手裝嘅係 skill **`ug65-cloudflared`** 嘅 `deploy.py`，所以主機那份 manual 要睇熟先。

## 附錄：驗收清單

- 子機 `10.99.99.1:28883` LISTEN，`ps` 見到 `cloudflared access tcp`
- 子機 toml `server="ssl://10.99.99.1:28883"`，唔係 `tcp://127.0.0.1:1883`
- 主機 Fleet 該子機 `connected: true`，`lastSeen` 持續更新
- 主機 mosquitto log 有 `New client connected from ::1 as GATEWAY_ID`
- 重開機後 50 秒內全部自動恢復
- `node_red.enable = false`，`/node-red/cfrun` 回 502
- 做咗 Part B 嘅話：兩個 cloudflared 並存，外部開到子機自己 hostname
