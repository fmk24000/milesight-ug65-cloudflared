---
title: Milesight UG65 手動安裝 Cloudflare Tunnel
type: field-manual
status: verified
---

# Milesight UG65 手動安裝 Cloudflare Tunnel

呢份係**人手操作**版：每一條命令、每一個預期輸出、每一個陷阱都寫出嚟。想一鍵搞掂嘅話有 `deploy.py`，但本文係它失敗時嘅 fallback，亦係你真正理解發生咩事嘅版本。

實測環境：UG65 ×1、firmware `60.0.0.49-r3`、cloudflared `2026.9.1`、已通過斷電重開測試（開機到 443 通 = 71 秒、22 通 = 107 秒）。

> **Tunnel Token**：Cloudflare 側一條 tunnel 嘅憑證（`eyJ...` 開頭），喺 Zero Trust 只會顯示一次。有 token 就等於有條 tunnel 嘅車匙，唔好貼上網、唔好入 git。
> **procd**：OpenWrt 嘅服務管理器。UG65 底層係 LEDE 17.01，所以可以用標準 init script 做開機自啟同 respawn —— 呢個 firmware 嘅 cron **唔可靠**，唔好用。
> **臨時 root shell**：內建 Node-RED 由 procd 以 root 身分啟動，所以佢嘅 `exec` node 出嚟嘅 shell 就係 `uid=0(root)`。本文借佢做安裝同驗收，**做完一定清走**。

## 你要準備嘅嘢

| 項目 | 例子 | 邊度嚟 |
|---|---|---|
| Gateway IP | `192.168.68.104` | 你個 LAN 或者 Milesight 掃描工具 |
| Username | `admin` | Web GUI 帳號，多數係 admin 但**唔好假設** |
| Password | `YOUR_ADMIN_PASSWORD` | Web GUI 密碼，**唔係** root 密碼（UG65 冇 root 密碼畀你） |
| 一個 Cloudflare 管理嘅域名 | `example.com` | Cloudflare dashboard |
| Tunnel Token | `eyJ...` | Zero Trust → Networks → Tunnels，只顯示一次 |
| 一部同 gateway 同網段嘅電腦 | Windows / macOS / Linux | 要裝 `curl`；用 `openssl` 計密碼加密 |

> ⚠️ Windows 用戶：`curl.exe` 同 `openssl.exe` 通常已經有（Git for Windows 自帶 openssl）。冇嘅話用 WSL 或者 Git Bash 跑本文所有命令。

以下呢啲**全部唔需要**：

- 唔需要 root 密碼 —— UG65 根本冇畀你
- 唔需要拆機、唔需要 TTL 線
- 唔需要刷白包 firmware
- 唔需要 Docker
- 唔需要 router 開 port forwarding
- 唔需要固定 IP 或者公網 IP
- 唔需要 VPN

## 成個流程長咩樣

五步，前四步喺你部電腦同 gateway 之間發生，第五步係驗收：

1. **Cloudflare 開 tunnel** —— 攞一個 token，順手加 Public Hostname 同 Access policy
2. **API 登入 gateway** —— 用 AES 加密密碼換一個 session cookie
3. **開 Node-RED** —— 一個 `/cgi` RPC 就搞掂，唔需要㩒界面
4. **上傳 flow 觸發安裝** —— Node-RED 以 root 執行你段腳本，寫檔、起服務、註冊 tunnel
5. **驗收 + 斷電重開測試** —— 睇 4 條 quic 連線、睇 rc.d、睇開機後自己起返

## 原理：三個關鍵事實

呢三點唔係漏洞，係廠商功能本身如此，Milesight 官方 KB 自己都咁用：

1. **UG65 底層係 OpenWrt（LEDE 17.01）**，帶完整 `procd`，所以可以用標準 init script 做開機自啟，唔需要搞 cron。
2. **內建 Node-RED 由 procd 以 root 啟動**（`/etc/init.d/node_red` 冇指定 user），所以佢嘅 `exec` node 就係一個 root shell。實測 `id` → `uid=0(root) gid=0(root)`。
3. **Web GUI 有一組 `/cgi` RPC API**，界面做得到嘅嘢佢都做得到 —— 包括開關 Node-RED。而 `/cgi-bin/file-import`（背後係 `/usr/libexec/luci2-io`）**以 root 身分寫檔**，可以用嚟落 flows.json。

所以路線係：

**Web GUI API 登入 → 開 Node-RED → exec node 以 root 裝 cloudflared → procd 接手。**

全程唔需要 root 密碼。唯一「非官方」嘅一步，係用 API 代替用手㩒界面 —— 功能本身係官方支援嘅。

## 步驟一：Cloudflare 側準備

1. 去 **Cloudflare Dashboard → Zero Trust → Networks → Tunnels**
2. **Create a tunnel** → Connector 揀 **Cloudflared** → 改個名（例 `UG65-Gateway`）
3. 佢會顯示一段安裝指令，**淨係抄 `eyJ...` 開頭嗰串 token**（只顯示一次）
4. 加 **Public Hostname**（下面張表）
5. **加 Access policy**（強烈建議，之後一定要做）

> ⚠️ 第 3 步：揀邊個平台（Docker / Debian / Red Hat）**完全唔影響 token**，求其揀一個就得。但**唔好執行**佢畀你嗰句 `cloudflared service install` —— 嗰句係假設有 root 嘅 Linux，我哋自己寫 init script。

第 4 步嘅 Public Hostname 咁填：

| 欄位 | 填乜 |
|---|---|
| Subdomain | `gw`（或你喜歡嘅） |
| Domain | 你嘅域名 |
| Type | `HTTPS` |
| URL | `localhost:443` |
| **No TLS Verify** | **一定要開** —— gateway 用自簽證書 |

填完個 hostname 就係 `https://gw.example.com`。

### 實測範例：一條 tunnel 掛兩個 hostname

同一條 tunnel 可以掛多個 route（Cloudflare 而家叫 **Published application**，舊名先叫 Public Hostname）。實測就係咁樣排：

| Order | Destination | Type | Service | Additional settings |
|---|---|---|---|---|
| 1 | `01-xxx.example.com` | Published application | `https://localhost:443` | 1 customized（= No TLS Verify） |
| 2 | `mqtt-xxx.example.com` | Published application | `tcp://localhost:18883` | Using defaults |

第 1 行係 gateway 自己嘅 Web GUI；第 2 行係子機 MQTT 上行用（見子機那份 manual）。**MQTT 一定要 `tcp://`**，寫 `http://localhost:18883` 會回 502。

兩行都係同一條 tunnel、同一個 token —— 所以主機側只需要裝一次。

第 5 步嘅 Access policy 咁設：Zero Trust → **Access → Applications → Add an application → Self-hosted**。Application domain 填 `gw.example.com`；Policy 揀 Action `Allow`、Include `Emails`、填你自己 email；Identity provider 用 **One-time PIN**（最快）或者 Google。

> ⚠️ 唔加 Access policy 嘅話，你部 gateway 嘅管理介面就等於對全世界開放。實測冇加，`https://gw.example.com` 會直接返回 UG65 登入頁。

## 步驟二：登入 Web GUI API

### 密碼點樣加密

Web GUI 唔會明文送密碼，佢用 **AES-128-CBC**，而且 key / iv 係寫死喺前端嘅：

| 參數 | 值 |
|---|---|
| key | `1111111111111111` |
| iv | `2222222222222222` |
| mode | CBC |
| padding | PKCS7 |
| 輸出 | Base64 |

呢個係**傳輸編碼，唔係保安** —— 所以一定要用 HTTPS。用 `openssl` 一行搞掂（key/iv 轉 hex：`1` = 0x31、`2` = 0x32）：

```bash
ENC=$(printf '%s' 'YOUR_ADMIN_PASSWORD' \
  | openssl enc -aes-128-cbc \
      -K 31313131313131313131313131313131 \
      -iv 32323232323232323232323232323232 \
      -base64 | tr -d '\n')
echo "$ENC"
```

> 密碼一定要用**單引號**包住。呢類機嘅密碼經常含 `$$`，用雙引號 shell 會展開，跟住你會見到一個莫名其妙的登入失敗。

### 呼叫 login

```bash
cat > login.json <<JSON
{"id":"1","execute":1,"core":"user",
 "function":"login","values":[
   {"username":"admin",
    "password":"$ENC","base":"web_login"}]}
JSON

curl -sk -c ug65.cookies \
  'https://192.168.68.104/cgi' \
  -H 'Content-Type: application/json' \
  -d @login.json
```

成功會回 `status: 0` 同一個 `td` session id：

```json
{"id":"1","model":"UG65","rtver":"60.0.0.49-r3",
 "status":0,"result":[
   {"ysrole":4,"ystimeout":1800,
    "username":"admin","td":"68ed92eb..."}]}
```

`ug65.cookies` 已經存好 session。記低個 session id，之後上傳檔案要用：

```bash
TD=$(awk '$6=="td"{print $7}' ug65.cookies)
echo "$TD"
```

> ⚠️ **Session 30 分鐘到期**（`ystimeout` = 1800 秒）。中途如果收到 `"result":[-32001,"Session not found"]`，就重新做一次登入。

## 步驟三：開 Node-RED

```bash
cat > nr_on.json <<'JSON'
{"id":"1","execute":1,"core":"yruo_loragw",
 "function":"set","values":[
   {"type":"node_red","index":0,
    "base":"node_red",
    "value":{"enable":true,"ssl_enable":true}}]}
JSON

curl -sk -b ug65.cookies --max-time 30 \
  'https://192.168.68.104/cgi' \
  -H 'Content-Type: application/json' \
  -d @nr_on.json
```

> ⚠️ **連線可能會斷** —— 因為 gateway 會改 `/etc/node_red/settings.js` 同 `/etc/nginx/nginx.conf` 然後 reload nginx。斷線係正常，唔係出錯。

payload 形狀好重要，四個欄位缺一不可：

| 欄位 | 值 | 少咗會點 |
|---|---|---|
| `type` | `"node_red"` | 被忽略 |
| `index` | `0` | 回 `lack of index` |
| `base` | `"node_red"` | 回 `lack of base` |
| `value` | `{"enable":true,"ssl_enable":true}` | 回 `lack of value` |

### 驗證

等 20 秒，然後讀返狀態：

```bash
cat > nr_get.json <<'JSON'
{"id":"1","execute":1,"core":"yruo_loragw",
 "function":"get","values":[{"base":"node_red"}]}
JSON

curl -sk -b ug65.cookies \
  'https://192.168.68.104/cgi' \
  -H 'Content-Type: application/json' \
  -d @nr_get.json
```

要見到 `"enable": true`。再用 SSH（如果有開）確認真係以 root 跑：

```sh
ps | grep node-red | grep -v grep
# 應該見到：  12345 root   918m S   node-red
```

**見到 `root` 就係關鍵一步成功咗。**

## 步驟四：生成並上傳 flows.json

### 生成

用附帶嘅產生器最穩陣，佢會幫你砌好 inject → exec → debug 同填好 token：

```bash
python make_flows.py 'eyJ...你嘅token...' -o flows.json

# 想順手開 SSH 同只准指定網段入 22：
python make_flows.py --file tunnel.token \
  --enable-ssh --ssh-lan 192.168.68.0/24 \
  -o flows.json
```

想自己砌都得，flow 只要三個 node：`inject`（`once: true`）→ `exec`（`oldrc: true`、`useSpawn: "false"`）→ `debug`。exec 嘅 command 就係附錄 A 嗰段腳本。

### 上傳

Node-RED 自己個 admin API 我哋用唔到（`adminAuth` 個 bcrypt hash 同 gateway 密碼冇同步，會回 `invalid_grant`），所以改用 Web GUI 自己嘅檔案上傳 CGI —— `/cgi-bin/file-import`，佢背後係 `/usr/libexec/luci2-io`，**以 root 身分執行**：

```bash
SIZE=$(wc -c < flows.json)

# file= 要嘅係描述字串，所以分兩截砌
DESC='type=nodered_flows_json'
DESC="$DESC&file=nodered flows json"

curl -sk -b ug65.cookies \
  'https://192.168.68.104/cgi-bin/file-import' \
  -F "sessionid=$TD" -F "size=$SIZE" \
  -F "filename=$DESC" \
  -F "file=@flows.json;type=application/octet-stream"
```

成功會回 size、checksum 同 filename：

```json
{"size": 3387, "filename": "flows.json",
 "checksum": "61ff2a373e59529cdd4acadae6fa1880"}
```

> ⚠️ **`file=` 係描述字串**（要同 `type` 對應），**唔係路徑**。寫成 `file=/etc/node_red/data/flows.json` 會回 `import incorrect type file`。實際上檔案會被寫入 `/etc/node_red/data/flows.json`。

用 SSH 核實：

```sh
ls -l /etc/node_red/data/flows.json
# -rw------- 1 root root 3387 ...
```

## 步驟五：Reload 觸發安裝

Node-RED 唔會自動載入新 flows.json，要重啟一次。用同一招 RPC 關再開：

```bash
cat > nr_off.json <<'JSON'
{"id":"1","execute":1,"core":"yruo_loragw",
 "function":"set","values":[
   {"type":"node_red","index":0,
    "base":"node_red",
    "value":{"enable":false,"ssl_enable":true}}]}
JSON

cat > nr_on.json <<'JSON'
{"id":"1","execute":1,"core":"yruo_loragw",
 "function":"set","values":[
   {"type":"node_red","index":0,
    "base":"node_red",
    "value":{"enable":true,"ssl_enable":true}}]}
JSON

# 關
curl -sk -b ug65.cookies --max-time 30 \
  'https://192.168.68.104/cgi' \
  -H 'Content-Type: application/json' \
  -d @nr_off.json

sleep 10

# 開 —— 同時會執行 flow 裏面 inject once 嘅節點
curl -sk -b ug65.cookies --max-time 30 \
  'https://192.168.68.104/cgi' \
  -H 'Content-Type: application/json' \
  -d @nr_on.json
```

等 **60～90 秒**（要下載 37MB binary 再起服務）。如果部機之前已經裝過，install flow 見到 `/usr/bin/cloudflared` 行得就會**跳過下載**，全程約 30 秒 —— 呢個係刻意設計，慳 SIM 數據。

### 睇安裝 log

```sh
cat /tmp/cfinstall.log
```

成功嘅話會見到：

```text
### running as ###
uid=0(root) gid=0(root)     ← 真係 root
+ /tmp/cloudflared --version
cloudflared version 2026.9.1
### installed ###
-rwxr-xr-x root root 37466252
### rc.d ###
S95cloudflared -> ../init.d/cloudflared
### process ###
 4342 root /usr/bin/cloudflared ...
### DONE ###
```

> ⚠️ 每次部署都應該有新嘅 `### RUN RUN_ID ###` 開頭。如果 log 開頭係舊 run id，即係你讀到上一次嘅 log（`/tmp/cfinstall.log` 喺 tmpfs，但未重開機就仲喺度），唔代表今次跑咗。

## 步驟六：驗收

### 檔案同服務

```sh
ls -l /usr/bin/cloudflared
ls -l /etc/init.d/cloudflared
ls -l /etc/cloudflared/token
ls -l /etc/rc.d/ | grep -E 'cloudflared|cfkeep'
ps | grep '[c]loudflared'
ps | grep cf-maintain-loop
```

應該見到：

| 檔案 | 預期 |
|---|---|
| `/usr/bin/cloudflared` | `-rwxr-xr-x root root`，約 37MB |
| `/etc/cloudflared/token` | `-rw------- root root`（權限 600） |
| `/etc/init.d/cloudflared` | `-rwxr-xr-x root root`，procd、`START=95` |
| `/etc/rc.d/S95cloudflared` | symlink 去 `../init.d/cloudflared` |
| `/etc/rc.d/S96cfkeepalive` | procd 常駐保活（**唔係 cron**） |
| `ps` | `root ... /usr/bin/cloudflared --no-autoupdate tunnel run`（**唔應該見到 token**） |
| `/etc/init.d/cfkeepalive` | 常駐 loop 存在，`ps` 見到 `cf-maintain-loop` |

### Tunnel 有冇真係註冊

```sh
grep -i cloudflared /etc/urlog/system.log | tail -20
```

要見到 **4 條** `Registered tunnel connection`，全部 `protocol=quic`：

```text
INF Registered tunnel connection
  connIndex=0  location=hkg09  protocol=quic
  connIndex=1  location=hkg10  protocol=quic
  connIndex=2  location=hkg01  protocol=quic
  connIndex=3  location=hkg13  protocol=quic
```

順便會見到你嘅 ingress 設定，確認 hostname 同 noTLSVerify 有落對：

```text
INF Updated to new configuration
  config="{\"ingress\":[
    {\"hostname\":\"gw.example.com\",
     \"originRequest\":{\"noTLSVerify\":true},
     \"service\":\"https://localhost:443\"}, ...]}"
```

### 由外部實測

由**唔同網絡**（例如手機 4G，唔好行返同一個 Wi-Fi）開：

```bash
curl -skI 'https://gw.example.com' | head -5
# 應該見到 server: cloudflare 同 cf-ray: ...-HKG
```

用瀏覽器開就應該見到 UG65 登入頁（如果有加 Access policy，會先彈 OTP）。

### 斷電重開測試（必做）

```bash
# 用 API 重開（session 過期就要重新登入）
cat > reboot.json <<'JSON'
{"id":"1","execute":1,"core":"yruo_upgrade",
 "function":"reboot","values":[{}]}
JSON

curl -sk -b ug65.cookies \
  'https://192.168.68.104/cgi' \
  -H 'Content-Type: application/json' \
  -d @reboot.json
```

等 2～3 分鐘，然後 SSH 入去核實：

```sh
uptime
# 應該見到 up 1 min / up 2 min ...（唔再係幾十日）

ps | grep '[c]loudflared' || echo "*** 冇起返 ***"
grep -c 'Registered tunnel' /etc/urlog/system.log
ls -l /etc/rc.d/ | grep cloudflared
```

實測時間：開機到 **443 通 = 71 秒**、**22 通 = 107 秒**。cloudflared 開機後要 **約 60～70 秒** 才完成註冊（`START=95`，排喺一大堆 LoRaWAN 服務後面）。呢段時間個 domain 會 502，屬正常。cloudflared 亦會 respawn 幾次先成功註冊 —— **重試幾次係正常，唔代表設定錯**。

## 步驟七：收尾

1. **加 Cloudflare Access**（見步驟一第 5 點）—— 冇加就等於把管理介面裸露上網，**必做**。
2. **輪換 Tunnel Token**：Zero Trust → 你條 tunnel → Refresh token。換完更新 gateway 嘅 `/etc/cloudflared/token` 再 `/etc/init.d/cloudflared restart`；或者用新 token 重新生成 flows.json 再上傳一次。
3. **SSH 建議長期開住**，唔好維護完就關 —— 呢類 gateway 擺喺 remote、插住 SIM，有 SSH 你先救得返。
4. **Node-RED 可以熄**：開住用約 60～100MB RAM（512MB 機好緊張）。tunnel 靠 procd 自啟，**熄咗 Node-RED 唔影響 tunnel**。代價係你冇咗條 root 通道，下次要改嘢就要再開返。

## 排查：一定會撞到嘅錯誤

| 訊息 | 原因 | 解決 |
|---|---|---|
| `lack of base` / `lack of index` / `lack of value` | set payload 缺欄位 | 四欄位 `type`/`index`/`base`/`value` 要齊 |
| `"result":[-32001,"Session not found"]` | session 過期（30 分鐘） | 重新登入 |
| `import incorrect type file` | `file=` 填咗路徑 | 填描述字串 `nodered flows json` |
| 上傳成功但 cloudflared 冇裝 | Node-RED 未載入新 flow | 確認有做 enable false → true；睇 `/tmp/cfinstall.log` |
| `!!! DOWNLOAD FAILED !!!` | gateway 上唔到 GitHub | 由電腦開 `python -m http.server 8000` 餵過去 |
| `Exec format error` | 架構錯 | 睇 `/etc/openwrt_release` 嘅 `DISTRIB_ARCH`；`aarch64` → `cloudflared-linux-arm64` |
| `ps` 見到 token | token 放咗喺 command line | init script 用 `procd_set_param env TUNNEL_TOKEN=...` |
| reboot 後冇起返 | 等唔夠久 | 等 90 秒；再睇 `ls -l /etc/rc.d/ \| grep cloudflared` |
| 上傳完「已完成」但其實冇跑 | 讀到上一次嘅 log | 睇 `### RUN RUN_ID ###` 係唔係今次 |
| SSH 開唔到（`status:-1`、ubus rc=255） | 一次過傳齊 general 全部欄位 | 只傳要改嘅欄位：`{"base":"general","index":0,"value":{"ssh_enable":1}}` |
| SSH 開咗但連唔到（timeout） | 經 `eth0` 入嘅機被當 WAN，`Firewall_Remote` REJECT 22 | 加 `-s YOUR_LAN_CIDR --dport 22 -j ACCEPT` 規則 |
| `iptables: error while loading libiptext.so` | 冇設 library path | `export LD_LIBRARY_PATH=/usr/lib/iptables` |
| 加咗嘅 cron 行重開機冇咗 | Milesight 會重寫 `/etc/crontabs/root` | 用 procd 常駐 loop（`cfkeepalive`），唔好靠 cron |
| Node-RED exec node 永遠唔回 | `oldrc:false` 喺 UG65 唔可靠 | exec node 用 `"oldrc": true` |
| Tunnel 一直唔 HEALTHY | UDP 被封 / 時間唔準 | 加 `--protocol http2`；`date` 檢查時間同步 |
| 外部開到但 502 | origin 設定錯 | Public Hostname 要 `HTTPS` + `localhost:443` + No TLS Verify |

### Gateway 上唔到 GitHub 點算

最穩陣係由你部電腦餵過去。兩個做法：

**做法 A**：電腦開 HTTP server，叫 gateway 用 plain HTTP 抓

```bash
# 電腦，同 gateway 同網段
# 先把 arm64 binary 改名做 cloudflared
python -m http.server 8000
```

然後將 install 腳本嗰行下載改成：

```sh
wget -O /tmp/cloudflared \
  http://192.168.68.10:8000/cloudflared
```

**做法 B**：`wget --no-check-certificate` —— LEDE 17.01 嘅 CA 太舊，呢個情況好常見（腳本本身已經試過呢個 fallback）。

## 死路：唔好試呢啲

以下全部實測過，係行唔通嘅。撞到就跳過，唔好嘥時間：

| 方法 | 為什麼唔得 |
|---|---|
| `su -` / `su root -c` | `/bin/su` **冇 setuid bit**，而且 `su: must be run from a terminal` |
| 覆寫 `/sbin/getty-bk`（mode `-rwsrwsrwx`） | kernel 喺非特權用戶寫入時**清走 setuid/setgid bit** |
| `/bin/busybox`（本身有 setuid） | 有 `FEATURE_SUID`，會按 `/etc/busybox.conf` 主動 drop privilege |
| 用戶 cron（`crontab -e`） | `/etc/crontabs` 唔俾 admin 寫 |
| `/etc/rc.local` | root:root 755，admin 改唔到 |
| Docker | firmware 冇 `docker` binary，冇 socket |
| Python App / supervisord | `/usr/python/bin/supervisord` 唔存在；就算有，App 都唔係 root 跑 |
| 爆 root 密碼 hash | per-device；41 個常見候選（含 `HelloMilesight!`）全部唔中 |
| Node-RED `/node-red/auth/token` | 回 `invalid_grant`，bcrypt hash 同 gateway 密碼冇同步 |

**唯一行得通嘅提權路線就係 Node-RED exec node。**

## 環境陷阱

- **`uname -m` 只會回 `x.x.x`** —— Milesight 故意改寫。要知架構就 `cat /etc/openwrt_release` 睇 `DISTRIB_ARCH`。
- **`logread` 以 admin 身分跑會 block** —— 改讀 `/etc/urlog/system.log`（世界可讀）。
- **冇 `timeout` 命令** —— busybox 冇編譯入去。
- **`/var` 係 symlink 去 `/tmp`** —— 所以 `/var/log` 其實喺 RAM，重開即冇。
- **Node-RED admin root 係 `/node-red`**，唔係 `/` —— HTTP in root 同樣係 `/node-red`。
- **開機次序**：firewall `START=19`、`cfsshfw` 20、Node-RED 82、cloudflared 95、`cfkeepalive` 96。
- **記憶體**：512MB，本身跑緊 lora-app-server / loraserver / postgres / mosquitto / redis / bacserv / quagga。開 Node-RED 之後 free 由約 120MB 跌到約 55MB。
- **`/tmp/cfinstall.log` 喺 tmpfs** —— 重開機即冇。tunnel 靠 procd 自啟，唔靠 flow。
- **升級 firmware / factory reset 會清走** `/usr/bin/cloudflared`、`/etc/init.d/cloudflared`、`/etc/cloudflared/token`、`cfkeepalive` 同 iptables 規則 —— 保留 token 重跑一次就得。

## 批量部署（多隻 gateway）

每隻 gateway 要**獨立一條 tunnel + 獨立 hostname**（各自唔同網絡、各自 SIM，一個 connector 去唔到對方 origin）：

```text
gw-01.example.com  →  Tunnel A（token A）  →  gateway 01
gw-02.example.com  →  Tunnel B（token B）  →  gateway 02
```

流程：先做**一隻黃金樣本**，確認斷電重開都自動連返；之後每隻重複步驟二至六，約 10～15 分鐘一隻；記錄表寫低 SN / Tunnel 名 / hostname / token 存放位置 / firmware 版本。

**每隻要改嘅只有三樣**：gateway IP、admin 密碼、Tunnel Token。

## 附錄：完整安裝腳本

呢段就係 `make_flows.py` 塞入 exec node 嘅內容（已去掉 run_id / token / SSH 佔位符嘅填值邏輯）。想完全手工做，可以直接貼入 exec node 嘅 command：

```sh
set -x
exec > /tmp/cfinstall.log 2>&1
echo "### running as ###"
id
echo "### firmware ###"
grep DISTRIB_ARCH /etc/openwrt_release

mkdir -p /etc/cloudflared
printf '%s' 'YOUR_TUNNEL_TOKEN' \
  > /etc/cloudflared/token
chmod 600 /etc/cloudflared/token
echo "token bytes: $(wc -c < /etc/cloudflared/token)"

if /usr/bin/cloudflared --version 2>/dev/null; then
  echo "already-installed: skip download"
else
  U="https://github.com/cloudflare/cloudflared"
  U="$U/releases/latest/download"
  U="$U/cloudflared-linux-arm64"
  if wget -O /tmp/cloudflared "$U"; then
    echo wget-ok
  elif wget --no-check-certificate \
         -O /tmp/cloudflared "$U"; then
    echo wget-nocheck-ok
  elif curl -L -k -o /tmp/cloudflared "$U"; then
    echo curl-ok
  else
    echo "!!! DOWNLOAD FAILED !!!"
  fi
  chmod 755 /tmp/cloudflared
  /tmp/cloudflared --version \
    || echo "!!! BINARY NOT RUNNABLE !!!"
  if /tmp/cloudflared --version >/dev/null 2>&1; then
    cp /tmp/cloudflared /usr/bin/cloudflared
    chmod 755 /usr/bin/cloudflared
    sync
  fi
fi

cat > /etc/init.d/cloudflared <<'INITEOF'
#!/bin/sh /etc/rc.common
START=95
STOP=10
USE_PROCD=1

start_service() {
    [ -x /usr/bin/cloudflared ] || return 1
    [ -s /etc/cloudflared/token ] || return 1
    procd_open_instance
    procd_set_param command \
      /usr/bin/cloudflared --no-autoupdate tunnel run
    procd_set_param env \
      TUNNEL_TOKEN="$(cat /etc/cloudflared/token)"
    procd_set_param respawn 3600 5 5
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}
INITEOF
chmod 755 /etc/init.d/cloudflared
/etc/init.d/cloudflared enable
/etc/init.d/cloudflared start
sleep 12

cat > /usr/bin/cf-maintain.sh <<'MAINTEOF'
#!/bin/sh
export LD_LIBRARY_PATH=/usr/lib/iptables
SSH_LAN="YOUR_LAN_CIDR"
if [ -n "$SSH_LAN" ]; then
  R="-p tcp -s $SSH_LAN --dport 22 -j ACCEPT"
  iptables -C INPUT $R 2>/dev/null || \
    iptables -I INPUT 2 $R 2>/dev/null

  E="-m state --state ESTABLISHED,RELATED -j ACCEPT"
  iptables -C INPUT $E 2>/dev/null || \
    iptables -I INPUT 1 $E 2>/dev/null
fi

pgrep -f "cloudflared.*tunnel" >/dev/null || \
  /etc/init.d/cloudflared start
MAINTEOF
chmod 755 /usr/bin/cf-maintain.sh

cat > /usr/bin/cf-maintain-loop.sh <<'LOOPEOF'
#!/bin/sh
while true; do
	/usr/bin/cf-maintain.sh
	sleep 300
done
LOOPEOF
chmod 755 /usr/bin/cf-maintain-loop.sh

cat > /etc/init.d/cfkeepalive <<'KEEPEOF'
#!/bin/sh /etc/rc.common
START=96
STOP=10
USE_PROCD=1

start_service() {
	procd_open_instance
	procd_set_param command /usr/bin/cf-maintain-loop.sh
	procd_set_param respawn 3600 5 5
	procd_close_instance
}
KEEPEOF
chmod 755 /etc/init.d/cfkeepalive
/etc/init.d/cfkeepalive enable

cat > /etc/init.d/cfsshfw <<'FWEOF'
#!/bin/sh /etc/rc.common
START=20
STOP=10
start() { /usr/bin/cf-maintain.sh; }
stop() { :; }
FWEOF
chmod 755 /etc/init.d/cfsshfw
/etc/init.d/cfsshfw enable

/etc/init.d/cfkeepalive start
/etc/init.d/cfsshfw start

echo "### process ###"
ps | grep '[c]loudflared' \
  || echo "*** NOT RUNNING ***"
echo "### DONE ###"
chmod 644 /tmp/cfinstall.log
```

## 附錄：復原 / 移除

```sh
# 停服務
/etc/init.d/cloudflared stop
/etc/init.d/cloudflared disable

# 停保活（procd）
/etc/init.d/cfkeepalive stop
/etc/init.d/cfkeepalive disable

# 刪檔案
rm -f /usr/bin/cloudflared /etc/init.d/cloudflared
rm -f /usr/bin/cf-maintain.sh
rm -f /usr/bin/cf-maintain-loop.sh
rm -f /etc/init.d/cfkeepalive /etc/init.d/cfsshfw
rm -rf /etc/cloudflared

# 最後去 Cloudflare 刪走條 tunnel
```

如果已經冇 root 途徑：重新做步驟二至五，但 flows.json 換成上面段清除腳本即可 —— Node-RED 個 exec 一樣係 root。

## 附錄：驗收清單

- `/usr/bin/cloudflared --version` 有 output，而且係 `2026.9.1`
- `/etc/cloudflared/token` 權限係 `600`、`root:root`
- `/etc/rc.d/S95cloudflared` symlink 存在，`/etc/rc.d/S96cfkeepalive` 都存在
- `ps` 見到 `cloudflared --no-autoupdate tunnel run`，而且**唔見到 token**
- `ps` 見到 `cf-maintain-loop`
- `/etc/urlog/system.log` 有 4 條 `Registered tunnel connection`、全 `protocol=quic`
- 由手機 4G 開 `https://gw.example.com` 見到登入頁（或 Access 嘅 OTP）
- 斷電重開後 90 秒內自己連返
- 臨時 root shell 已清走（`https://192.168.68.104:1880/node-red/cfrun` 回 404）
