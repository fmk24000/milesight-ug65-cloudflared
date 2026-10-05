---
name: ug65-cloudflared
description: Deploy Cloudflare Tunnel (cloudflared) onto a Milesight UG65/UG56/UG67 LoRaWAN gateway using ONLY the admin account — no root password, no disassembly, no firmware reflash. Persistent across reboot, with a procd keepalive (cron is unreliable on this firmware), optional SSH enablement, and verification that works even without SSH. Use when a user supplies a Cloudflare Tunnel token and wants a gateway reachable at a domain without a fixed IP. IMPORTANT — before any action, always ask the user for the gateway IP, username, password and target domain — never assume or reuse them.
whenToUse: 當用戶提供 Cloudflare Tunnel Token，並要求喺 Milesight UG65 / UG56 / UG67 上安裝 cloudflared、或者想要一個 domain 遙控隻 gateway（尤其係用 SIM 卡、冇 router、冇 LAN、冇 root 密碼嘅情況）。
---

# Milesight UG65 一鍵裝 Cloudflare Tunnel

實測環境：UG65、firmware `60.0.0.49-r3`、cloudflared `2026.9.1`、已通過斷電重開測試。

## ⚠️ 第一步：必須先向用戶問齊（唔准靠估）

**用到本 skill 嘅第一件事，就係問清楚下面四樣。問齊先開始做。**

- ❌ 唔准用預設值蒙混（唔准當 `username` 一定係 `admin`）
- ❌ 唔准由舊對話、舊 log、`snapshots/` 或之前嘅部署紀錄推測
- ❌ 唔准叫用戶「照住上面嘅例子填」
- ✅ 缺任何一項 → **停手，直接問用戶**

| 必問 | 說明 |
|---|---|
| **1. Gateway IP** | 例如 `192.168.68.104`。多過一隻就逐隻列清楚，順便問邊隻係主機。 |
| **2. Username** | Web GUI 登入帳號。多數係 `admin`，但**一樣要問過**，唔准自己假設。 |
| **3. Password** | 該帳號嘅密碼（**唔係** root 密碼）。提醒用戶用**單引號**包住 —— 呢類密碼常含 `$$`，唔包會被 shell 展開而登入失敗。 |
| **4. Domain** | 用戶想部 gateway 喺邊個 hostname 出現，例如 `gw-xxx.example.com`。**一定要一層域名**（見下面「兩層域名會 handshake failure」）。 |

> 就算用戶淨係畀咗 Tunnel Token，**一樣要照問**其餘三樣。

## 你會需要嘅輸入

| 輸入 | 必須 | 說明 |
|---|---|---|
| Tunnel Token | ✅ | `eyJ...` 開頭。由 Cloudflare Zero Trust → Networks → Tunnels 攞（只顯示一次） |
| Gateway IP | ✅ | **必問**用戶（見上） |
| Username | ✅ | **必問**用戶（見上）；多數係 `admin` |
| Password | ✅ | **必問**用戶。Web GUI 嘅登入密碼（唔係 root 密碼，UG65 冇 root 密碼畀你） |
| Domain（hostname） | ✅ | **必問**用戶。一層域名，例 `gw-xxx.example.com`；要喺 Cloudflare 加 Public Hostname |
| SSH | 建議 | 可以叫 `deploy.py --enable-ssh` 順手開（見下）。有 SSH 驗收最直接 |

**唔需要**：root 密碼、拆機、白包 firmware、Docker、router、fixed IP。

> **預設做法**：Milesight gateway 嘅 **SSH 要長期開住**（唔係做完就關），
> （唔係做完就關），所以標準做法係一齊落 `--enable-ssh --ssh-lan <CIDR>`。

## 核心原理（唔好質疑，已經實測過）

1. UG65 底層係 OpenWrt/LEDE，有完整 `procd`。
2. **內建 Node-RED 由 procd 以 `root` 啟動**（`/etc/init.d/node_red` 冇指定 user），
   所以佢嘅 **`exec` node 就係 root shell**（實測 `id` → `uid=0(root) gid=0(root)`）。
   呢個係 Milesight 官方功能，官方 KB 自己都係咁示範跑 `iptables`。
3. **Web GUI 有 `/cgi` RPC API**，可以代替手㩒界面做任何設定。
4. Node-RED 自己個 admin API 用唔到（`adminAuth` 個 bcrypt hash 同 gateway 密碼冇同步，
   會回 `invalid_grant`），所以改用 Web GUI 嘅 `/cgi-bin/file-import`
   （`/usr/libexec/luci2-io`，**以 root 執行**）直接寫 `/etc/node_red/data/flows.json`。

## 執行步驟

### 第 0 步：確認輸入齊全

如果用戶只係畀咗 token，**必須先問齊** gateway IP 同 admin 密碼。
冇 SSH 都**照做**——`deploy.py` 會自動開一個臨時 Node-RED root shell 做驗收（用完清走）。

### 第 1 步：一鍵部署

```bash
cd <這個 skill 的目錄>/scripts
# 標準做法（本 repo：SSH 長開 + 只准指定網段，順手驗證）
python deploy.py --host <GATEWAY_IP> --password '<ADMIN_PW>' --token '<TOKEN>' \
                 --enable-ssh --ssh-lan 192.168.68.0/24
```

| 參數 | 作用 |
|---|---|
| `--ssh` | 指定用 SSH 做驗收（冇亦得，會自動用臨時 root shell） |
| `--enable-ssh` | 順手開 SSH（`ubus ssh_enable=1` + `yruo_apply` + `/etc/init.d/sshd`） |
| `--ssh-lan <CIDR>` | 只准呢個網段入 22（經 `eth0` 入嘅機被 gateway 當 **WAN**，預設 `REJECT`） |
| `--verify-mode auto\|ssh\|nodered` | auto＝有 SSH 用 SSH，否則用臨時 root shell |
| `--no-ctl` | 唔准用臨時 root shell（咁就一定要 SSH 才驗收到） |
| `--reboot-test` | 順手重開機驗證開機自啟（gateway 斷線 1–2 分鐘） |

> ⏱️ **`--reboot-test` 全程要 5 分鐘以上**：用 agent 工具跑嘅時候**要放 background／加大 timeout**。
> 如果行程中途被 kill，`finally` 嘅清理未必跑到 → 記得事後自己 check
> `https://IP:1880/node-red/cfping` 應該係 **404**；有 SSH 嘅話 deploy 會**先清走**
> 臨時 endpoint 才重開機（見下）。

指令碼會自動：登入 → 開 Node-RED → 生成 flows.json（＋可選 SSH 設定）→ 上傳 →
重啟 Node-RED → 由 root 完成安裝 → 等 run-id 確認真係跑咗 → 驗收 → **清走臨時 endpoint**。

**如果 `cryptography` 未裝**：`pip install cryptography requests paramiko`

### 第 2 步：讀結果

成功嘅話驗收會顯示：

- `cloudflared version 2026.9.1`
- `/usr/bin/cloudflared`、`/etc/init.d/cloudflared`、`/etc/cloudflared/token` 齊
- `/etc/rc.d/S95cloudflared` symlink 存在
- `/usr/bin/cf-maintain.sh`、`/etc/init.d/cfkeepalive`、`/etc/rc.d/S96cfkeepalive` 齊
  （**procd 常駐保活**，唔靠 cron），`ps` 見到 `cf-maintain-loop`
- 4 條 `Registered tunnel connection ... protocol=quic`
- `ps` 見到 `/usr/bin/cloudflared --no-autoupdate tunnel run`，**而且唔會見到 token**
- 如有 `--enable-ssh`：`netstat -ltn` 見到 `:22 LISTEN`，同 `iptables -S INPUT` 有
  `-s <CIDR> --dport 22 -j ACCEPT`
- 最後 `/node-red/cfrun` 檢查顯示 **✓ 已消失**（臨時 root shell 已清走）

### 第 3 步：叫用戶做嘅事（你做唔到）

1. **Cloudflare 側加 Public Hostname**：Type `HTTPS`、URL `localhost:443`、
   **No TLS Verify = ON**（gateway 自簽證書）。冇加嘅話個 domain 上唔到。
2. **加 Access policy**（Zero Trust → Access → Applications → Self-hosted，email OTP）。
   冇加嘅話 gateway 管理頁會裸露上網。
3. **輪換 Tunnel Token** —— token 一旦出現喺對話／檔案就有外洩風險。

> ⚠️ **唔好**叫用戶「維護完就關返 SSH」——呢類 gateway 慣常要長期開住 SSH 方便維護。

## 人手 fallback（deploy.py 失敗時）

逐步做，每一步都有驗證點。以下係逐步嘅 raw `curl` 版本；完整教學（連每步嘅驗收輸出、
常見問題排查同死路表）見 https://github.com/fmk24000/milesight-ug65-cloudflared/blob/main/docs/manual-deployment.zh-HK.md。

```bash
# 1. 登入（密碼加密：AES-128-CBC, key=1111111111111111, iv=2222222222222222, PKCS7, base64）
ENC=$(printf '%s' 'ADMIN_PW' | openssl enc -aes-128-cbc \
        -K 31313131313131313131313131313131 \
        -iv 32323232323232323232323232323232 -base64 | tr -d '\n')
curl -sk -c ug65.cookies "https://IP/cgi" -H 'Content-Type: application/json' \
  -d "{\"id\":\"1\",\"execute\":1,\"core\":\"user\",\"function\":\"login\",\"values\":[{\"username\":\"admin\",\"password\":\"$ENC\",\"base\":\"web_login\"}]}"
TD=$(awk '$6=="td"{print $7}' ug65.cookies)

# 2. 開 Node-RED（四欄位缺一不可：type / index / base / value）
curl -sk -b ug65.cookies --max-time 30 "https://IP/cgi" -H 'Content-Type: application/json' \
  -d '{"id":"1","execute":1,"core":"yruo_loragw","function":"set","values":[{"type":"node_red","index":0,"base":"node_red","value":{"enable":true,"ssl_enable":true}}]}'
# 連線斷一下係正常（gateway reload nginx）

# 3. 生成 flows.json
python make_flows.py "$TOKEN" -o flows.json

# 4. 上傳（file= 係描述字串，唔係路徑！）
curl -sk -b ug65.cookies "https://IP/cgi-bin/file-import" \
  -F "sessionid=$TD" -F "size=$(wc -c < flows.json)" \
  -F "filename=type=nodered_flows_json&file=nodered flows json" \
  -F "file=@flows.json;type=application/octet-stream"

# 5. 重啟 Node-RED 觸發（enable false → 等 10 秒 → enable true）
# 6. 驗收：有 SSH 就 ssh admin@IP 跑 scripts/verify.sh；
#    冇 SSH 就上傳含 rootctl ctl 節點嘅 flows（見 scripts/rootctl.py），
#        GET https://IP:1880/node-red/cfrun 以 root 跑 verify.sh，**之後一定要還原 flows.json**
```

### 冇 SSH 時嘅臨時 root shell（`scripts/rootctl.py`）

`deploy.py` 會自動用；想人手做就：

```python
import ug65_lib as L, rootctl, make_flows
gui = L.WebGui("192.168.68.104", "admin", PW); gui.login()
# 1) 上傳 install flow + rootctl.ctl_nodes()  →  重啟 Node-RED
# 2) endpoint 就緒後：
ctl = rootctl.RootCtl(gui, "192.168.68.104")
print(ctl.run(open("verify.sh").read()))          # 以 root 跑
print(ctl.read("/etc/crontabs/root"))             # 讀檔
# 3) 用完還原：只上傳 install flow，再重啟 → 檢查 /node-red/cfrun 已經 404
```

關鍵：http-in root 係 **`/node-red`**、exec node 要 **`oldrc:true`**、
**唔可以**用 `addpay`／`useSpawn:true`（會 timeout）。詳情見上面「錯誤對照表」。

## 錯誤對照表

| 訊息 | 原因 | 解決 |
|---|---|---|
| `lack of base` / `lack of index` / `lack of value` | set payload 缺欄位 | 四欄位 `type`/`index`/`base`/`value` 要齊 |
| `Session not found` (`-32001`) | session 30 分鐘到期 | 重新 login（`gui.ensure_login()` 已處理） |
| `import incorrect type file` | `file=` 填咗路徑 | 填描述字串 `nodered flows json` |
| 上傳成功但冇裝 | Node-RED 未載入新 flow | 確認有做 enable false→true；睇 `/tmp/cfinstall.log` |
| `!!! DOWNLOAD FAILED !!!` | gateway 上唔到 GitHub | 改用電腦開 `python -m http.server 8000` 餵過去 |
| `Exec format error` | 架構錯 | `uname -m` 被 Milesight 改成回 `x.x.x`，要睇 `cat /etc/openwrt_release` 嘅 `DISTRIB_ARCH`；`aarch64` → `cloudflared-linux-arm64` |
| `ps` 見到 token | 用咗 `--token` 參數放喺 command line | init script 要用 `procd_set_param env TUNNEL_TOKEN=...`（`make_flows.py` 產生嘅 flows 已經係咁寫） |
| reboot 後冇起返 | 等唔夠久 | cloudflared `START=95`，開機後要 **60–70 秒**；睇 `ls -l /etc/rc.d/ \| grep cloudflared` |
| 上傳完「已完成」但其實冇跑 | 讀到**上一次**嘅 `/tmp/cfinstall.log` | 已經處理好：每次 deploy 有 `run_id`（見 `make_flows.build_flow`），等唔到就經 root shell 直接觸發 |
| SSH 開唔到（`/cgi` 回 `status:-1`、ubus rc=255） | 一次過傳齊 general 全部欄位 | **只傳要改嘅欄位**：`{"base":"general","index":0,"value":{"ssh_enable":1}}` |
| SSH 開咗但連唔到（timeout） | 經 `eth0` 入嘅機被當 **WAN**，`Firewall_Remote` REJECT dport 22 | `--ssh-lan <CIDR>`（會加 INPUT 規則）；唔可以只靠 cron 持久化 |
| `iptables: error while loading libiptext.so` | 冇設 library path | `export LD_LIBRARY_PATH=/usr/lib/iptables` |
| 加咗嘅 cron 行重開機冇咗 | Milesight 重寫 `/etc/crontabs/root` | 用 procd 常駐 loop（`cfkeepalive`），唔好靠 cron |
| Node-RED exec node 永遠唔回（HTTP timeout） | `oldrc:false` 喺 UG65 上唔可靠 | exec node 用 `"oldrc": true`；亦唔好用 `addpay` / `useSpawn:true` |

## ⛔ 唔好試呢啲（已實測係死路，慳返時間）

| 方法 | 為什麼唔得 |
|---|---|
| `su - root -c` | `/bin/su` **冇 setuid bit**，而且 `su: must be run from a terminal` |
| 覆寫 `/sbin/getty-bk`（`-rwsrwsrwx`） | kernel 喺非特權用戶寫入時**清走 setuid/setgid bit**，寫完就冇特權 |
| `/bin/busybox`（有 setuid） | busybox 有 `FEATURE_SUID`，按 `/etc/busybox.conf` 主動 drop privilege |
| 用戶 cron（`crontab -e`） | `/etc/crontabs` 唔俾 admin 寫 |
| `/etc/rc.local` | root:root 755，admin 改唔到 |
| Docker | firmware 冇 `docker` binary，冇 socket（`/etc/init.d/docker` 存在 ≠ 裝咗） |
| Python App / supervisord | `/usr/python/bin/supervisord` 唔存在；就算裝咗，App 默認唔係 root 跑 |
| 爆 root 密碼 hash | per-device，41 個常見候選（含 `HelloMilesight!`）全部唔中 |
| Node-RED `/node-red/auth/token` | 回 `invalid_grant`（bcrypt 冇同步） |

## 環境注意

- **`uname -m` 只會回 `x.x.x`**（Milesight 故意改寫）→ 用 `cat /etc/openwrt_release`。
- **`logread` 以 admin 身分跑會 block** → 改讀 `/etc/urlog/system.log`（世界可讀）。
- **`timeout` 命令唔存在**（busybox 冇）。
- **Node-RED admin root 係 `/node-red`**，唔係 `/`。
- **記憶體**：512MB 總量，本身跑緊 lora-app-server / loraserver / postgres / mosquitto /
  redis / bacserv / quagga。開 Node-RED 之後 free 由 ~120MB 跌到 ~55MB。
  cloudflared 靠 procd `respawn` + `/etc/init.d/cfkeepalive`（常駐 loop）睇住，
  **唔需要 Node-RED 都跑得**，所以如果之後見到 OOM，可以叫用戶去 `App → Node-RED` 熄咗佢。
- **cron 唔可靠**：Milesight 開機時／每次 `yruo_apply apply` 都會重寫 `/etc/crontabs/root`，
  自己加嘅行會消失。保活一律用 procd（`cfkeepalive`）。
- **SSH（本 repo 用戶偏好長開）**：Milesight 用自家 `/usr/sbin/sshd`（唔係 dropbear），
  開關存喺 **ubus**；開啟步驟 `ubus set ssh_enable=1` → `yruo_apply write/apply` →
  `/etc/init.d/sshd enable && start`。用 `--enable-ssh --ssh-lan <CIDR>` 一齊做。
- **慳 SIM 數據**：install flow 見到 `/usr/bin/cloudflared` 行得就**跳過下載**
  （實測重跑約 30 秒完成，唔會再落 35MB）。
- **Node-RED 每次重啟都會跑一次 install flow**（inject `once`）：已裝好就只係快速重跑，
  但會改寫 `/etc/cloudflared/token`、`/etc/init.d/cloudflared` 等（idempotent）。
- **`/tmp/cfinstall.log` 係 tmpfs**，重開機即冇；tunnel 靠 procd 自啟，唔靠 flow。
- **升級 firmware / factory reset 會清走** `/usr/bin/cloudflared`、
  `/etc/init.d/cloudflared`、`/etc/cloudflared/token`、`cfkeepalive` 同 iptables 規則
  → 保留 token 重跑一次即可。

---

## 2026-09-16 補充（第二次實測，同一隻 192.168.68.104，已驗證）

### 1. 冇 SSH 時，用 Node-RED 自己開一個臨時 root shell（驗收必備）

`--ssh` 睇唔到嘅嘢（`/tmp/cfinstall.log`、`ps`、tunnel 註冊紀錄）可以咁樣攞：

- 上傳一個 flow：`http in GET /cfput` → function（`Buffer.from(query.c,'base64').toString()`）
  → **`file out` 寫 `/tmp/cfcmd.sh`**；另加 `http in GET /cfrun` → `exec sh /tmp/cfcmd.sh` → `http response`。
- ⚠️ **http-in 嘅 root 係 `/node-red`，唔係 `/`** → 打 `https://IP:1880/node-red/cfrun`。
- **`exec` node 一定要 `"oldrc": true`**：實測 `oldrc:false` 會**永遠唔 emit**（HTTP 永遠 timeout），
  連 `exec "id"` 都唔會回。`oldrc:true` + 3 個輸出（stdout/stderr/rc）就正常。
- **唔好用 `addpay`/`append`** 傳動態命令（實測 stdout 空）；亦**唔好用 heredoc + spawn**
  （`useSpawn:"true"` 唔經 shell，`cat` 會等 stdin 卡死）。用上面 file-out 落檔嘅做法最穩。
- 用完**記得還原 flows.json**（只留 install flow）再重啟 Node-RED，唔好擺住個 root shell 喺度。
- 附帶：用 `file in` node 讀 `/tmp/cfinstall.log`、`/etc/crontabs/root` 等都唔需要 fork。

### 2. 開 SSH（Milesight 自家 sshd，唔係 dropbear）

```sh
ubus call yruo_system set '{"base":"general","index":0,"value":{"ssh_enable":1}}'
ubus call yruo_apply write      # 存 flash
ubus call yruo_apply apply
/etc/init.d/sshd enable && /etc/init.d/sshd start   # 服務係 /usr/sbin/sshd
```

- ⚠️ **`value` 只可以傳要改嘅欄位**（`{"ssh_enable":1}`）。一次過傳齊成個 general 物件會 **rc=255**，
  Web GUI `/cgi` RPC 亦會回 `status:-1`（同樣原因）。呢個就係「GUI API 開唔到 SSH」嘅真相。
- SSH 設定唔喺 uci（`uci show | grep ssh` 冇嘢），而喺 **ubus**（`/usr/sbin/device_manage` 用 `ubus set access`）。

### 3. 開咗 SSH 都連唔到？係 firewall，唔係 sshd

呢隻 gateway 嘅 firewall 分兩邊：`-i wlan0 → Firewall_Local`（ACCEPT 22）、
`-i eth0 → wan_input → Firewall_Remote`（**REJECT 22**，但 ACCEPT 80/443）。
**經有線側（eth0）入嘅電腦會被當成 "Remote"**，所以 TLS 443 入得到（`https_remote=1`）、SSH 22 就 REJECT。

```sh
export LD_LIBRARY_PATH=/usr/lib/iptables        # ⚠️ 冇呢句 iptables 會 error while loading libiptext.so
iptables -I INPUT 1 -m state --state ESTABLISHED,RELATED -j ACCEPT      # 唔加呢條，SSH session 會被 dport 22 REJECT 打死
iptables -I INPUT 2 -p tcp -s 192.168.68.0/24 --dport 22 -j ACCEPT
```

- 要**持久**就唔可以只落 cron（見下一點）：用開機 init script `START=20`（firewall 係 S19）＋
  procd 常駐 loop（即 `--enable-ssh --ssh-lan <CIDR>` 落嘅嘢）。
- `/etc/init.d/firewall` 自己本身有寫住一條 `--dport 22 --source 192.168.0.0/16 -j ACCEPT`，
  但**佢冇設 `LD_LIBRARY_PATH`，所以開機時根本加唔到** —— 呢個係原廠嘅 bug。

### 4. cron 保活喺呢隻 firmware **唔可靠**（skill 原本做法要改）

Milesight **開機時（同每次 `yruo_apply apply`）會重寫 `/etc/crontabs/root`**，
自己加嘅 cron 行會消失。實測：加完 → 重開機 → 冇咗。
→ 改用 **procd 常駐 loop** 做保活（唔怕人洗 crontab）：

```
/usr/bin/cf-maintain.sh          # 補 iptables 規則 + cloudflared 唔跑就拉返起
/usr/bin/cf-maintain-loop.sh     # while true; do cf-maintain.sh; sleep 300; done
/etc/init.d/cfkeepalive          # USE_PROCD=1, START=96, procd_set_param respawn
/etc/init.d/cfsshfw              # START=20, 開機補 SSH firewall 規則
```

### 5. 重開機實測數據（同一隻 gateway）

- 開機到 **443 通 = 71 秒**、**22 通 = 107 秒**。
- cloudflared 會 respawn 幾次（05:46 boot 後 07:48:03→07:48:31 共 5 次 `Starting tunnel`）先成功註冊，
  跟住 4 條 connection 全 quic；呢個「重試幾次」係正常，唔代表設定錯。
- `/tmp/cfinstall.log` **係 tmpfs，重開機就冇**；tunnel 係靠 procd 自啟，唔係靠 flow。

### 6. 診斷流程上的提醒

- 用 `file in` / `exec` 每次 round-trip 都要重啟 Node-RED（~10 秒），
  `node_red_set(False)` 之後 1880 大約 **2 秒**就落線，唔好等太耐。
- 有舊安裝嘅機（呢隻之前 09-15 已裝過、token 一樣）：**外網 domain 200 ≠ 你今次 deploy 成功**。
  一定要睇 `/usr/bin/cloudflared` 嘅 mtime 同 `/tmp/cfinstall.log`。
