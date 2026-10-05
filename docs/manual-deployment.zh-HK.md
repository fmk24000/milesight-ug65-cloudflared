# Milesight UG65 安裝 Cloudflare Tunnel — 完整手動教學

> **適用對象**：Milesight UG65 / UG56 / UG67（LEDE/OpenWrt 底、有內建 Node-RED）
> **前提**：你只有 Milesight 給的 `admin` 帳號，**沒有 root 密碼**、不想拆機、不想刷白包 firmware
> **目標**：裝好 cloudflared，做到開機自動連線，之後用 `https://xxx.你的域名` 遙控 gateway
> **實測環境**：UG65，firmware `60.0.0.49-r3`，cloudflared `2026.9.1`，已通過斷電重開測試

> ⚠️ **版本註記（2026-10 更新）**：本文件係最初嘅逐步手動教學，內容保留原貌。其中幾點之後嘅實測
> 已經修正，**請以 [`SKILL.md`](../ug65-cloudflared/SKILL.md) 為準**：
>
> 1. **保活改用 procd，唔再用 cron** —— Milesight 每次開機、同每次 `yruo_apply apply` 都會重寫
>    `/etc/crontabs/root`，自己加嘅 cron 行會消失。改用 `/etc/init.d/cfkeepalive`（procd 常駐 loop）。
> 2. **SSH 建議長期開住**，唔係做完就關；而且經 `eth0` 入嘅機被當成 WAN，`dport 22` 預設 REJECT，
>    要同時落 firewall 規則（`--ssh-lan <CIDR>`）。
> 3. 一鍵版本係 [`scripts/deploy.py`](../ug65-cloudflared/scripts/deploy.py) ——
>    本文件係佢失敗時嘅**人手 fallback**。

---

## 0. 原理速覽（先睇呢段，唔明都照做）

三個關鍵事實：

1. **UG65 底層係 OpenWrt（LEDE 17.01），有完整 `procd`**，所以可以用標準 init script 做開機自啟。
2. **內建 Node-RED 由 procd 以 `root` 身分啟動**（`/etc/init.d/node_red` 冇指定 user），
   所以 Node-RED 的 **`exec` node 出嚟嘅 shell 就係 `uid=0(root)`**。
   Milesight 官方 KB 自己都係咁示範跑 `iptables`。
3. **Web GUI 有一組 HTTP RPC API**（`/cgi`），可以做到界面做到嘅所有事——包括開 Node-RED。

所以路線係：

```
Web GUI API 登入  →  開 Node-RED  →  用 Node-RED exec node（root）裝 cloudflared
```

全程唔需要 root 密碼。唯一「非官方」嘅一步係「用 API 代替用手㩒界面」——
功能本身係 Milesight 官方支援嘅。

**Tunnel Token 係乜？** 佢就係你條 tunnel 嘅車匙。有 token 嘅人就可以啟動你條 tunnel。
唔好貼上網、唔好入 git、做完建議輪換一次。

---

## 1. 你需要準備嘅嘢

| 項目 | 說明 |
|---|---|
| UG65 的 IP | 例 `192.168.68.104`（用你實際嘅） |
| admin 帳號密碼 | 例 `admin` / `<ADMIN_PASSWORD>` |
| 一個 Cloudflare 管理的域名 | 例 `example.com` |
| 一部同 gateway 同網段嘅電腦 | Windows/macOS/Linux 都得，要裝 `curl` 同 `openssl` |
| Tunnel Token | 由 Cloudflare Zero Trust 攞（見 §2） |

Windows 用戶：`curl.exe` 同 `openssl.exe` 通常已經有（Git for Windows 自帶 openssl）。
如果冇，用 WSL 或者 Git Bash 跑以下命令。

---

## 2. Cloudflare 側準備

1. 去 **Cloudflare Dashboard → Zero Trust → Networks → Tunnels**
2. **Create a tunnel** → Connector 揀 **Cloudflared** → 改個名（例 `UG65-Gateway`）
3. 佢會顯示一段安裝指令，**淨係抄 `eyJ...` 開頭嗰串 token**（只顯示一次！）
   - 揀邊個平台（Docker / Debian / Red Hat）**完全唔影響 token**，求其揀一個就得
   - ⚠️ **唔好執行**佢畀你嗰句 `cloudflared service install`，我哋自己做
4. 加 **Public Hostname**：

   | 欄位 | 填乜 |
   |---|---|
   | Subdomain | `ug65`（或你喜歡嘅） |
   | Domain | 你嘅域名 |
   | Type | `HTTPS` |
   | URL | `localhost:443` |
   | **No TLS Verify** | **一定要開**（gateway 用自簽證書） |

   結果會係 `https://ug65.你的域名`。

5. **（強烈建議，之後一定要做）加 Access policy**：
   Zero Trust → **Access → Applications → Add an application → Self-hosted**
   - Application domain：`ug65.你的域名`
   - Policy：Action `Allow`、Include `Emails`、填你自己 email
   - Identity provider：**One-time PIN**（最快）或者 Google

---

## 3. 收集 gateway 資料（可選，但建議）

先開 SSH 方便之後驗收。用瀏覽器登入 Web GUI → `System → General Settings → General → Access Service`
→ 開 **SSH**，port 用 `22`。

然後由電腦驗證：

```bash
ssh admin@192.168.68.104
# 登入後會係一個真正嘅 ash shell（唔係 Milesight 選單 CLI）
```

入到去先確認幾樣：

```sh
id
# 應該見到 uid=6833(admin) ... groups=0(root),101(network),6833(admin)
# 記住：admin 唔係 root，group root 唔等於 root 權限

cat /etc/openwrt_release
# 要見到 DISTRIB_ARCH='aarch64_armv8-a'  ← 決定用邊個 cloudflared

ps | grep -i node | grep -v grep
# 未開 Node-RED 之前應該冇嘢
```

> ⛔ **唔好試 `su -`**：UG65 嘅 `/bin/su` **冇 setuid bit**（`-rwxr-xr-x`），
> 而且會回 `su: must be run from a terminal`。呢條路係死嘅，唔好嘥時間。

---

## 4. 步驟一：登入 Web GUI API

### 4.1 密碼加密方式

Web GUI 唔會明文送密碼，佢用 **AES-128-CBC** 加密：

| 參數 | 值 |
|---|---|
| key | `1111111111111111` |
| iv | `2222222222222222` |
| mode | CBC |
| padding | PKCS7 |
| 輸出 | Base64 |

用 `openssl` 一行搞掂（key/iv 轉 hex：`1`=0x31、`2`=0x32）：

```bash
ENC=$(printf '%s' '<ADMIN_PASSWORD>' | openssl enc -aes-128-cbc \
        -K 31313131313131313131313131313131 \
        -iv 32323232323232323232323232323232 \
        -base64 | tr -d '\n')
echo "$ENC"
```

### 4.2 呼叫 login

```bash
curl -sk -c ug65.cookies 'https://192.168.68.104/cgi' \
  -H 'Content-Type: application/json' \
  -d "{\"id\":\"1\",\"execute\":1,\"core\":\"user\",\"function\":\"login\",\"values\":[{\"username\":\"admin\",\"password\":\"$ENC\",\"base\":\"web_login\"}]}"
```

成功會回：

```json
{"id":"1","model":"UG65","pn":"...","rtver":"60.0.0.49-r3","status":0,
 "result":[{"ysrole":4,"ystimeout":1800,"ysexpires":1799,
            "username":"admin","td":"68ed92ebfb2b2d0b1d3e4438468d40da"}]}
```

`status:0` = 成功。`ug65.cookies` 已經存好 session。

記低個 session id（之後上傳檔案要）：

```bash
TD=$(awk '$6=="td"{print $7}' ug65.cookies)
echo "$TD"
```

> ⚠️ **Session 30 分鐘到期**（`ystimeout:1800`）。中途如果收到
> `"result":[-32001,"Session not found"]`，就重新做 §4.2 登入一次。

---

## 5. 步驟二：開啟 Node-RED

```bash
curl -sk -b ug65.cookies --max-time 30 'https://192.168.68.104/cgi' \
  -H 'Content-Type: application/json' \
  -d '{"id":"1","execute":1,"core":"yruo_loragw","function":"set","values":[{"type":"node_red","index":0,"base":"node_red","value":{"enable":true,"ssl_enable":true}}]}'
```

> ⚠️ **連線可能會斷**——因為 gateway 會改 `/etc/node_red/settings.js` 同
> `/etc/nginx/nginx.conf` 然後 reload nginx。斷線係正常，唔係出錯。

**payload 形狀好重要**，四樣都要有：

| 欄位 | 值 | 少咗會點 |
|---|---|---|
| `type` | `"node_red"` | |
| `index` | `0` | 回 `lack of index` |
| `base` | `"node_red"` | 回 `lack of base` |
| `value` | `{"enable":true,"ssl_enable":true}` | 回 `lack of value` |

### 5.1 驗證

等 20 秒，然後：

```bash
curl -sk -b ug65.cookies 'https://192.168.68.104/cgi' \
  -H 'Content-Type: application/json' \
  -d '{"id":"1","execute":1,"core":"yruo_loragw","function":"get","values":[{"base":"node_red"}]}'
```

要見到 `"enable": true`。

再用 SSH 確認 process：

```sh
ps | grep node-red | grep -v grep
# 應該見到：  12345 root   918m S   node-red
```

**見到 `root` 就係關鍵一步成功咗。**

---

## 6. 步驟三：生成並上傳 flows.json

### 6.1 生成 flows.json

用附帶嘅產生器（最穩陣）：

```bash
python make_flows.py 'eyJhIjoi...你嘅token...' -o flows.json
```

或者自己整一個 `flows.json`，內容係一個 `inject (once:true) → exec → debug` 嘅 flow。

### 6.2 上傳（關鍵一步）

Node-RED 自己個 admin API 我哋用唔到（`adminAuth` 個 bcrypt hash 同 gateway 密碼冇同步，
會回 `invalid_grant`），所以改用 **Web GUI 自己嘅檔案上傳 CGI**
——`/cgi-bin/file-import`，佢背後係 `/usr/libexec/luci2-io`，**以 root 身分執行**：

```bash
curl -sk -b ug65.cookies 'https://192.168.68.104/cgi-bin/file-import' \
  -F "sessionid=$TD" \
  -F "size=$(wc -c < flows.json)" \
  -F "filename=type=nodered_flows_json&file=nodered flows json" \
  -F "file=@flows.json;type=application/octet-stream"
```

成功會回：

```json
{"size": 3387, "checksum": "61ff2a373e59529cdd4acadae6fa1880", "filename": "flows.json"}
```

> ⚠️ **`file=` 係描述字串**（要同 `type` 對應），**唔係路徑**。
> 寫成 `file=/etc/node_red/data/flows.json` 會回 `import incorrect type file`。
> 實際上檔案會被寫入 `/etc/node_red/data/flows.json`。

### 6.3 驗證（用 SSH）

```sh
ls -l /etc/node_red/data/flows.json
# 應該見到 -rw------- 1 root root 3387 ...
```

---

## 7. 步驟四：Reload Node-RED 觸發安裝

Node-RED 唔會自動載入新 flows.json，要重啟一次。最簡單係用返同一招 RPC 關再開：

```bash
# 關
curl -sk -b ug65.cookies --max-time 30 'https://192.168.68.104/cgi' \
  -H 'Content-Type: application/json' \
  -d '{"id":"1","execute":1,"core":"yruo_loragw","function":"set","values":[{"type":"node_red","index":0,"base":"node_red","value":{"enable":false,"ssl_enable":true}}]}'

sleep 10

# 開（同時會執行 flow 裏面 inject once:true 嘅節點）
curl -sk -b ug65.cookies --max-time 30 'https://192.168.68.104/cgi' \
  -H 'Content-Type: application/json' \
  -d '{"id":"1","execute":1,"core":"yruo_loragw","function":"set","values":[{"type":"node_red","index":0,"base":"node_red","value":{"enable":true,"ssl_enable":true}}]}'
```

等 **60～90 秒**（要下載 35MB binary + 起服務）。

### 7.1 睇安裝 log（用 SSH）

```sh
cat /tmp/cfinstall.log
```

成功嘅話會見到：

```
+ id
uid=0(root) gid=0(root)          ← 確認真係 root
+ /tmp/cloudflared --version
cloudflared version 2026.9.1 (built ...)
### installed ###
-rwxr-xr-x 1 root root 37466252 /usr/bin/cloudflared
### rc.d ###
lrwxrwxrwx ... S95cloudflared -> ../init.d/cloudflared
### process ###
 4342 root  1263m S  /usr/bin/cloudflared --no-autoupdate tunnel run
### DONE ###
```

---

## 8. 步驟五：驗收

### 8.1 檔案同服務

```sh
ls -l /usr/bin/cloudflared /etc/init.d/cloudflared /etc/cloudflared/token
ls -l /etc/rc.d/ | grep cloudflared
ps | grep '[c]loudflared'
cat /etc/crontabs/root
```

應該見到：

| 檔案 | 預期 |
|---|---|
| `/usr/bin/cloudflared` | `-rwxr-xr-x root root` 約 37MB |
| `/etc/cloudflared/token` | `-rw------- root root` |
| `/etc/init.d/cloudflared` | `-rwxr-xr-x root root` |
| `/etc/rc.d/S95cloudflared` | symlink 去 `../init.d/cloudflared` |
| `ps` | `root ... /usr/bin/cloudflared --no-autoupdate tunnel run`（**唔應該見到 token**） |
| `/etc/init.d/cfkeepalive` | procd 常駐 keepalive，`ps` 見到 `cf-maintain-loop`（**唔係 cron** —— Milesight 會重寫 `/etc/crontabs/root`） |

### 8.2 Tunnel 連線紀錄

```sh
grep -i cloudflared /etc/urlog/system.log | tail -20
```

要見到 **4 條** `Registered tunnel connection`，全部 `protocol=quic`，例如：

```
INF Registered tunnel connection connIndex=0 ... location=hkg09 protocol=quic
INF Registered tunnel connection connIndex=1 ... location=hkg10 protocol=quic
INF Registered tunnel connection connIndex=2 ... location=hkg01 protocol=quic
INF Registered tunnel connection connIndex=3 ... location=hkg13 protocol=quic
```

順便會見到你嘅 ingress 設定：

```
INF Updated to new configuration config="{\"ingress\":[{\"hostname\":\"ug65.你的域名\",
    \"originRequest\":{\"noTLSVerify\":true}, \"service\":\"https://localhost:443\"}, ...]}"
```

### 8.3 由外部實測

由**唔同網絡**（例如手機 4G）開：

```
https://ug65.你的域名
```

應該見到 UG65 登入頁。或者用 command line：

```bash
curl -skI 'https://ug65.你的域名' | head -5
# 應該見到 server: cloudflare 同 cf-ray: ...-HKG
```

### 8.4 斷電重開測試（**必做**）

```bash
# 用 API 重開（session 過期就要重新登入）
curl -sk -b ug65.cookies 'https://192.168.68.104/cgi' \
  -H 'Content-Type: application/json' \
  -d '{"id":"1","execute":1,"core":"yruo_upgrade","function":"reboot","values":[{}]}'
```

等 2～3 分鐘，然後 SSH 入去：

```sh
uptime
# 應該見到 up 1 min / up 2 min ...（唔再係幾十日前）

ps | grep '[c]loudflared' || echo "*** 冇起返 ***"
grep -c 'Registered tunnel connection' /etc/urlog/system.log
```

> ⏱️ **注意**：cloudflared 開機後要 **約 60～70 秒** 才完成註冊
> （`START=95`，排喺一大堆 LoRaWAN 服務後面）。呢段時間個 domain 會 502，屬正常。

---

## 9. 步驟六：收尾

1. **加 Cloudflare Access**（見 §2.5）——實測如果冇加，`https://ug65.你的域名`
   會直接返回 gateway 登入頁，等於把管理介面裸露上網。**必做。**
2. **輪換 Tunnel Token**：Zero Trust → 你條 tunnel → Refresh token，
   然後更新 gateway 嘅 token 並重啟服務：
   ```sh
   # 用 Node-RED 再上傳一次新 flows.json（token 換咗），或者
   # 如果你已經有其他 root 途徑，直接改 /etc/cloudflared/token
   /etc/init.d/cloudflared restart
   ```
3. **SSH 建議長期開住**（唔好維護完就關）：去 `System → General Settings → Access Service`
   開住 SSH，日後維護最直接。記住要配合 firewall 規則（見 `SKILL.md`）先連得到。
4. **考慮關掉 Node-RED**：開住會用約 60～100MB RAM。
   cloudflared 靠 procd `respawn` + `/etc/init.d/cfkeepalive`（常駐 loop）保活，
   **關掉 Node-RED 唔會影響 tunnel**。
   （缺點：關掉之後就冇咗呢條 root 執行通道，下次要改嘢就要再開返。）

---

## 10. 常見問題排查

| 症狀 | 原因 | 解決 |
|---|---|---|
| `{"error":"lack of base"}` | set payload 少咗 `base` | 照 §5 四欄位齊全 |
| `{"error":"lack of index"}` | 少咗 `index` | 補 `"index":0` |
| `{"error":"lack of value"}` | `value` 冇包成 object | 用 `{"enable":true,...}` |
| `"result":[-32001,"Session not found"]` | session 過期（30 分鐘） | 重新做 §4.2 |
| `import incorrect type file` | `file=` 填咗路徑 | 填 `nodered flows json` |
| 上傳成功但 cloudflared 冇裝 | Node-RED 未載入新 flow | 做 §7 關再開；睇 `/tmp/cfinstall.log` |
| `!!! DOWNLOAD FAILED !!!` | gateway 上唔到 GitHub | 見 §10.1 |
| `Exec format error` | 下載錯 CPU 架構 | `uname -m` 應該係 `aarch64` → 用 `arm64` 版 |
| `ps` 見到 token | 用咗 `--token` 參數 | 改用 `TUNNEL_TOKEN` 環境變數（§8.1 嘅 init script 已經係咁） |
| Tunnel 一直唔 HEALTHY | UDP 被封 / 時間唔準 | 加 `--protocol http2`；`date` 檢查時間同步 |
| 外部開到但 502 | origin 設定錯 | Public Hostname 要 `HTTPS` + `localhost:443` + No TLS Verify |
| reboot 後 tunnel 冇起返 | 等唔夠久 | 等 90 秒；再睇 `ls -l /etc/rc.d/ \| grep cloudflared` |

### 10.1 Gateway 上唔到 GitHub 點算

最穩陣係由電腦餵過去。但 UG65 冇現成嘅任意檔案上傳位，所以兩個做法：

**做法 A**：電腦開 HTTP server，叫 gateway 用 plain HTTP 抓

```bash
# 電腦（同 gateway 同網段）
python -m http.server 8000        # 先把 cloudflared-linux-arm64 改名做 cloudflared 放同一個資料夾
```

然後把 flows.json 裏面嘅下載那行改成：

```sh
wget -O /tmp/cloudflared http://192.168.1.100:8000/cloudflared
```

**做法 B**：`wget --no-check-certificate`（LEDE 17.01 嘅 CA 太舊，好常見）

---

## 11. 附錄 A：走過嘅死路（唔好再試）

| 方法 | 實測結果 |
|---|---|
| `su -` / `su root -c` | `/bin/su` **冇 setuid bit**，而且 `su: must be run from a terminal` → 完全冇提權能力 |
| 覆寫 `/sbin/getty-bk`（mode `-rwsrwsrwx`） | kernel 喺**非特權用戶寫入時會清走 setuid/setgid bit**，寫完變 `-rwxrwxrwx`，執行 euid 仍係 6833 |
| `/bin/busybox`（本身係 setuid） | busybox 有 `FEATURE_SUID`，會按 `/etc/busybox.conf` **主動 drop privilege** → 一樣寫唔入 `/etc` |
| 用戶 cron | `crontab -l` → `can't open 'admin'`；`/etc/crontabs` 唔俾 admin 寫 |
| `/etc/rc.local` | root:root 755，admin 改唔到（雖然佢會執行 `/home/rc.local.custom`，同樣 755 root） |
| Docker | firmware 冇 `docker` binary，冇 `/var/run/docker.sock`（`/etc/init.d/docker` 存在唔代表裝咗） |
| Python App / supervisord | `/usr/python/bin/supervisord` 唔存在（Python SDK 未裝）；就算裝咗，App 默認**唔係 root** 跑 |
| 爆 root 密碼 hash | `root:$1$vbaXt/Sr$...`，測 41 個候選（含 `HelloMilesight!`）全部唔中，大機會係 per-device |
| 讀 `/etc/shadow` | mode 640 + admin 屬 group root(0) 所以讀得到，**但唔等於 root** |
| Node-RED admin API 拎 token | `https://ip:1880/node-red/auth/token` 存在但回 `invalid_grant`（bcrypt hash 同 gateway 密碼冇同步） |

**唯一行得通嘅就係 Node-RED exec node。**

---

## 12. 附錄 B：礦坑提示

1. **`uname` 被改寫**：UG65 嘅 `uname -a` / `uname -m` 只會回 `x.x.x`（Milesight 故意隱藏）。
   要知架構就要 `cat /etc/openwrt_release` 睇 `DISTRIB_ARCH`。
2. **`logread` 會 block**：以 admin 身分跑 `logread` 會卡住唔返。
   改為直接讀 `/etc/urlog/system.log`（世界可讀）。
3. **`timeout` 命令唔存在**（busybox 冇裝）。
4. **`/var` 係 symlink 去 `/tmp`**，所以 `/var/log` 其實係 RAM。
5. **Node-RED admin root 係 `/node-red`** 唔係 `/`（Milesight 改咗 `httpRoot`）。
6. **開機次序**：cloudflared `START=95`，Node-RED `START=82`。開機後要等 60～70 秒。
7. **記憶體**：UG65 只有 512MB，本身就跑緊 lora-app-server / loraserver / postgres /
   mosquitto / redis / bacserv / quagga。開埋 Node-RED 之後 free 會由 ~120MB 跌到 ~55MB。

---

## 13. 附錄 C：批量部署（多隻 gateway）

每隻 gateway 要**獨立一條 tunnel + 獨立 hostname**（因為各自唔同網絡、各自 SIM，
connector 去唔到對方 origin）：

```
ug65-01.你的域名  →  Tunnel A (token A)  →  gateway 01
ug65-02.你的域名  →  Tunnel B (token B)  →  gateway 02
```

流程：

1. 先做**一隻**黃金樣本，確認斷電重開都自動連返
2. 之後每隻重複 §4 → §8，約 10～15 分鐘一隻
3. 記錄表：SN / Tunnel 名 / hostname / token 存放位置 / firmware 版本
4. 驗收：Cloudflare dashboard 見到 N 條 tunnel 全部 HEALTHY

**要改嘅只有三樣**：gateway IP、admin 密碼、Tunnel Token。

---

## 14. 附錄 D：復原 / 移除

```sh
# 停服務（如果你有 root 途徑）
/etc/init.d/cloudflared stop
/etc/init.d/cloudflared disable

# 停 procd 保活
/etc/init.d/cfkeepalive stop
/etc/init.d/cfkeepalive disable

# 清 cron（如果之前試過用 cron 保活）
sed -i '/cloudflared/d' /etc/crontabs/root
/etc/init.d/cron restart

# 刪檔案
rm -f /usr/bin/cloudflared /etc/init.d/cloudflared
rm -rf /etc/cloudflared

# 去 Cloudflare 刪 tunnel
```

如果已經冇 root 途徑：重新做 §4 → §7，但 flows.json 換成清除腳本即可
（Node-RED 個 exec 一樣係 root）。
