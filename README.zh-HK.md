# Milesight UG65 × Cloudflare Tunnel

[English](README.md) | **中文（廣東話）**

兩個可以重複使用嘅 agent skill，幫你將一部 **Milesight UG65 / UG56 / UG67 LoRaWAN gateway**
透過 **Cloudflare Tunnel（`cloudflared`）** 放上互聯網 —— 而且**只用得到 admin 帳號**。

唔需要 root 密碼（呢啲 gateway 根本冇）、唔需要拆機、唔需要刷 firmware、唔需要 Docker、
唔需要 port forwarding、唔需要固定 IP。

| Skill | 做啲咩 |
|---|---|
| [`ug65-cloudflared`](ug65-cloudflared/SKILL.md) | 直接**喺 gateway 本身**裝 `cloudflared`，之後 gateway（同佢嘅 web UI、Node-RED、你想 route 出去嘅任何嘢）就可以用你自己嘅域名入到，即使佢係插住流動 SIM、冇 public IP。斷電重開都仲喺度；保活用 `procd`，因為**呢個 firmware 嘅 cron 唔可靠**。可以同一次順手開埋 SSH，而且連 SSH 用唔到嘅情況下都做得到驗收。 |
| [`ug65-sub-cloudflared`](ug65-sub-cloudflared/SKILL.md) | 將**副 gateway** 變成 Gateway Fleet agent，透過原生 MQTT 將 LoRa 封包送去主控 gateway。喺副 gateway 裝 `cloudflared access tcp` 做**客戶端** tunnel proxy（Cloudflare connector 仍然留喺主控），並喺 `lo` 上面釘一個**非 loopback** 地址 —— 因為 firmware 見到 loopback server address 就會當你係 local mode —— 再將 packet forwarder 指過去。 |

兩個 skill 都係喺真機上面開發同驗證：UG65 ×2、firmware `60.0.0.49-r3`（主控）／
`60.0.0.48-r3`（副機）、`cloudflared 2026.9.1`、mosquitto mTLS，包括完整嘅**斷電重開測試**。

> 📖 想要逐個步驟嘅人手版本（連 raw `curl` 命令、每步驗證點、死路表）：
> **[`docs/manual-deployment.zh-HK.md`](docs/manual-deployment.zh-HK.md)**

---

## 點解呢個做法行得通

firmware 有三個特性令成件事可行。呢啲唔係漏洞 —— 全部都係廠商功能，只係用返佢本來嘅設計：

1. **Gateway 底層係 OpenWrt/LEDE**，本身帶一套完整 `procd`。
2. **內建 Node-RED 由 procd 以 `root` 身分啟動。** `/etc/init.d/node_red` 啟動嗰陣冇指定
   `user`，所以佢入面嘅 `exec` node *就係*一個 root shell（`id` → `uid=0(root) gid=0(root)`）。
   Milesight 自己嘅知識庫都係用呢招喺部機上面跑 `iptables`。呢批腳本就用佢做**臨時** root
   shell 嚟安裝同驗收，做完即刻清走。
3. **Web GUI 開放咗一組 `/cgi` RPC API**，而 `/cgi-bin/file-import`（以 root 身分執行）可以
   寫入 `/etc/node_red/data/flows.json`。即係話：淨係用 admin 密碼，就可以全程用 HTTPS
   完成安裝。

砌埋一齊就係：上傳一段 Node-RED flow → 佢以 root 身分執行 → 佢幫你裝同驗證 `cloudflared`
→ 刪走 flow。部 gateway 之後繼續由 `procd` 睇住 `cloudflared`；Node-RED 已經唔再需要，
可以熄咗佢。

---

## 環境要求

- **Python 3.8+**，並安裝：
  ```bash
  pip install requests cryptography paramiko
  ```
  （`paramiko` 只有用 SSH 做驗收嗰陣需要。）
- 網絡上到 gateway 嘅 web UI（HTTPS），同埋 **admin 密碼**（唔係 root）。
- Skill 1：一個 **Cloudflare Tunnel token**（`eyJ...`），喺
  Zero Trust → Networks → Tunnels 只會顯示一次。
- Skill 2：主控上面一個 **tunnel hostname**；另外，跑腳本嗰部機必須**同副 gateway 同一個 LAN**
  —— 因為要經本地 HTTP 推一個約 37 MB 嘅 binary 過去。

---

## 安裝

```bash
git clone https://github.com/fmk24000/milesight-ug65-cloudflared.git

# 當做 DSH skills 用
cp -r milesight-ug65-cloudflared/ug65-cloudflared      ~/.dsh/skills/
cp -r milesight-ug65-cloudflared/ug65-sub-cloudflared  ~/.dsh/skills/
```

或者直接跑腳本 —— 每個資料夾都係自足嘅（`SKILL.md` + `scripts/`）。

> ⚠️ **第一次用之前，先問齊四樣嘢**：Gateway IP、Username、Password、Domain。
> 兩個 `SKILL.md` 開頭都有一段「必須先向用戶問齊（唔准靠估）」，唔准用預設值蒙混、
> 唔准由舊對話／舊 log／舊部署紀錄推測。

---

## Skill 1 —— 喺 gateway 上面裝 `cloudflared`

```bash
cd ug65-cloudflared/scripts
python deploy.py --host 192.168.68.104 --password '<ADMIN_PASSWORD>' \
                 --token '<TUNNEL_TOKEN>' \
                 --enable-ssh --ssh-lan 192.168.68.0/24
```

| 參數 | 作用 |
|---|---|
| `--host` / `--password` / `--user` | Gateway IP 同 admin 帳密（`user` 預設 `admin`）。 |
| `--token` | Cloudflare Tunnel token（`eyJ...`）。 |
| `--ssh` | 用 SSH 做驗收（如果連唔到，就改用臨時 root shell）。 |
| `--enable-ssh` | 順手開埋 SSH：`ubus ssh_enable=1` + `yruo_apply` + `/etc/init.d/sshd`。 |
| `--ssh-lan <CIDR>` | 只接受嚟自呢個 CIDR 嘅 `dport 22`。**必加** —— 經 `eth0` 入嚟嘅機被當成 WAN，SSH 預設係 `REJECT`。 |
| `--verify-mode auto\|ssh\|nodered` | 揀喺邊度做驗收。`auto` 會優先試 SSH。 |
| `--no-ctl` | 禁止使用臨時 root shell（咁就一定要有 SSH 先驗收到）。 |
| `--reboot-test` | 重開機並確認開機自啟（部 gateway 會離線 1–2 分鐘）。 |

腳本由頭到尾做嘅事：登入 → 開 Node-RED → 生成 `flows.json`（＋可選 SSH 設定）→ 上傳 →
重啟 Node-RED → 以 root 身分安裝 → 等 `run_id` 確認**真係**跑到新一次 → 驗收 →
**清走臨時 root shell**。

### 預期驗收輸出

```
cloudflared version 2026.9.1
/usr/bin/cloudflared, /etc/init.d/cloudflared, /etc/cloudflared/token   present
/etc/rc.d/S95cloudflared                                         symlink present
/usr/bin/cf-maintain.sh, /etc/init.d/cfkeepalive, /etc/rc.d/S96cfkeepalive present
    (procd keepalive — deliberately NOT cron)
ps: cf-maintain-loop
4x  Registered tunnel connection ... protocol=quic
ps: /usr/bin/cloudflared --no-autoupdate tunnel run       and NO token in the process list
--enable-ssh:  netstat -ltn -> :22 LISTEN , iptables -S INPUT -> -s <CIDR> --dport 22 -j ACCEPT
/node-red/cfrun -> gone (temporary root shell removed)
```

### 只有你可以做嘅步驟（Cloudflare dashboard）

1. **加一個 Public Hostname** 去個 tunnel：Type `HTTPS`、URL `localhost:443`、
   **No TLS Verify 開 ON**（gateway 用自簽證書）。唔做呢步，個域名會出 error。
2. **加一個 Access policy**（Zero Trust → Access → Applications → self-hosted，例如 email
   OTP）。唔做呢步，你部 gateway 嘅 admin UI 就等於對全世界開放。
3. **輪換 tunnel token** —— 一個 token 一旦出現過喺對話、log 或者檔案裡面，就當佢已經失效。

> 持續性備註：SSH 係刻意保持開啟嘅（呢個就係本專案預期嘅運維模式）。Firewall 規則同
> `cloudflared` 都係由 **procd** init script 睇住（firewall `START=20`、cloudflared `95`、
> keepalive `96`），**一律唔用 cron** —— Milesight 每次開機同每次 `yruo_apply apply` 都會
> 重寫 `/etc/crontabs/root`。

---

## Skill 2 —— 將副 gateway 變成 Gateway Fleet agent

你只需要三樣嘢：副 gateway 嘅 IP（可以多過一部）、主控上面嘅 tunnel hostname、同 admin 密碼。

```bash
cd ug65-sub-cloudflared/scripts
python sub_install.py --hosts 192.168.68.110 --host mqtt-xxx.example.com \
       --password '<ADMIN_PASSWORD>' --add-dest --register \
       --gw-name Gateway_03 --main 192.168.68.106
```

| 參數 | 作用 |
|---|---|
| `--hosts` | 副 gateway IP，用逗號分隔 —— 逐部處理。 |
| `--host` | Tunnel hostname（**單層 label**，即係 `mqtt-xxx.example.com`）。 |
| `--port` | Tunnel 本地出口埠，預設 `28883`。 |
| `--addr` | 釘喺 `lo` 上面嘅非 loopback 地址，預設 `10.99.99.1`。 |
| `--binary` | arm64 `cloudflared` binary 嘅路徑（唔填就自動解析／下載）。 |
| `--add-dest` | 副 gateway 本身冇 destination 嘅話，幫佢加一個。 |
| `--main` / `--register` / `--gw-name` | 檢查（可選自動註冊）部機喺主控 Fleet 裡面嘅狀態。 |
| `--verify-only` | 只檢查，唔改任何嘢。 |
| `--revert` | 全部還原，並回復之前存低嘅 snapshot。 |

每部 gateway 會：開 Node-RED → 攞臨時 root shell → 經本地 HTTP 推 binary 過去（附 **md5 校驗**）
→ 寫 `/etc/init.d/cfagent`（procd、`START=95`、`respawn`，並將 `10.99.99.1/32` 加入 `lo`）
→ 將 destination 指去 `10.99.99.1:28883` → `yruo_apply apply` → 清走 root shell。原本嘅
destination 設定會喺第一次跑嗰陣存去 `snapshots/conf_<ip>.json`。

### 驗收

```bash
python sub_install.py --verify-only --hosts 192.168.68.109 \
       --password '<ADMIN_PASSWORD>' --main 192.168.68.106
```

```
  id=0 enabled=False addr=localhost  port=1883  connected=0
  id=1 enabled=True  addr=10.99.99.1 port=28883 connected=1
  gateway_id=C0BA1FFFFE0001 push_data_ack=100.00%
  Fleet: Gateway_02 connected=True lastSeen=...
```

```bash
python sub_reboot_test.py --hosts 192.168.68.110 --password '<ADMIN_PASSWORD>' --main 192.168.68.106
```

### 主控側，只做一次

喺主控嘅 tunnel 加一個 Public Hostname（Zero Trust → Networks → Tunnels）：

| Hostname | Service |
|---|---|
| `mqtt-xxx.example.com` | **`tcp://localhost:18883`** |
| *（可選）* `01-xxx.example.com` | `https://localhost:443`（No TLS Verify） |

之後喺 Network Server 註冊部副 gateway（Gateways → Add，用副 gateway 嘅 `gateway_id`），
或者直接叫 `--register` 經 HTTP API 幫你做。

---

## 人人都會踩嘅兩個坑

**1. MQTT route 用咗 `http://`。** 一定要係 **`tcp://localhost:18883`**。用 `http://` 會令
Cloudflare 嘅 HTTP parser 去啃一串原始 MQTT byte stream → 出 HTTP 502，而客戶端會見到
`tls: handshake failure`。

**2. Hostname 用咗兩層 label。** 免費 Universal SSL 只包 `example.com` 同 `*.example.com`：

| Hostname | 得唔得？ |
|---|---|
| `mqtt.example.com` | ✅ |
| `mqtt-site.example.com` | ✅（都係一層深） |
| `mqtt.site.example.com` | ❌ —— edge 連 ServerHello 都未出就失敗（`fatal: handshake failure`） |

另外副 gateway 仲有一個：**千萬唔好將 `serv_addr` 指去 `127.0.0.1` 或者 `localhost`。**
firmware 就係用 loopback 嚟判斷「本地 vs 遠端」內嵌 NS：

| `serv_addr` | firmware 生成啲咩 | 結果 |
|---|---|---|
| `127.0.0.1` / `localhost` | `server="tcp://127.0.0.1:1883"` | 封包只去到 gateway 自己嘅 mosquitto —— Fleet **永遠**都係 `connected: false` |
| 任何其他地址 | `server="ssl://<addr>:<port>"` + `ca_cert`/`tls_cert`/`tls_key` | 封包經 tunnel 出去主控 |

所以呢度用 `10.99.99.1/32` 釘喺 `lo`（非 loopback、穩定、唔受現場同 SIM 影響），而
`cloudflared` 亦只綁死喺嗰個地址 —— 唔係 `0.0.0.0` —— 所以 LAN 上唔會曝露任何嘢。

---

## 疑難排解

| 症狀 | 原因 | 解決 |
|---|---|---|
| `lack of base` / `lack of index` / `lack of value` | `set` payload 唔完整 | `type`/`index`/`base`/`value` 四個欄位缺一不可。 |
| `Session not found`（`-32001`） | GUI session 過期（30 分鐘） | 重新登入 —— `ensure_login()` 會自動處理。 |
| `import incorrect type file` | 將路徑塞咗入 `file=` 表單欄位 | 佢要嘅係一個**描述字串**（`nodered flows json`），唔係路徑。 |
| 上傳話成功，但咩都冇裝到 | Node-RED 冇載入新 flow | 確認真係做過 `enable false → true` 嘅重啟；睇 `/tmp/cfinstall.log`。 |
| `!!! DOWNLOAD FAILED !!!` | gateway 上唔到 GitHub | 由你部電腦派：`python -m http.server 8000`。 |
| `Exec format error` | 架構唔啱 | Milesight 會將 `uname -m` 改寫成 `x.x.x` —— 要改睇 `/etc/openwrt_release` 嘅 `DISTRIB_ARCH`。`aarch64` → `cloudflared-linux-arm64`。 |
| `ps` 睇到 token | token 用咗 command line 傳 | init script 一定要用 `procd_set_param env TUNNEL_TOKEN=...`；生成嘅 flow 已經係咁做。 |
| 重開機後咩都冇返嚟 | 等唔夠久 | `cloudflared` 係 `START=95`；開機後要等 **60–70 秒**，再睇 `/etc/rc.d/`。 |
| Reload 話「done」但冇嘢跑過 | `/tmp/cfinstall.log` 係上一次跑剩嘅 | 已處理：每次部署都有 `run_id`；如果觀察唔到，會直接經 root shell 觸發。 |
| SSH 開唔到（`status:-1`，ubus rc=255） | 一次過送咗成個 `general` 物件 | **淨係**送你改緊嗰個欄位：`{"base":"general","index":0,"value":{"ssh_enable":1}}`。 |
| SSH 開到但連線 timeout | 經 `eth0` 入嚟嘅機被當成 **WAN**，`Firewall_Remote` REJECT 咗 port 22 | 用 `--ssh-lan <CIDR>`；cron 冇辦法持久化呢樣嘢。 |
| `iptables: error while loading libiptext.so` | 少咗 library path | `export LD_LIBRARY_PATH=/usr/lib/iptables`。 |
| 加咗嘅 cron 行重開後消失 | Milesight 會重寫 `/etc/crontabs/root` | 改用 `procd` 常駐 loop（`cfkeepalive`），唔好用 cron。 |
| Node-RED `exec` node 唔返嘢／HTTP timeout | `oldrc: false` 喺呢個 firmware 唔可靠 | 設 `"oldrc": true`；避免用 `addpay` 同 `useSpawn: true`。 |
| `CookieConflictError: multiple cookies named 'td'`（fw `60.0.0.48-r3`） | 登入回應嘅 `result[0]` 冇 `td` | 已修好 —— `login()` 會 fallback 去 `Set-Cookie`。 |

### 死路 —— 唔好嘥時間

以下全部喺呢個 firmware 上面實測過，唔work：

| 方法 | 點解唔得 |
|---|---|
| `su - root -c` | `/bin/su` 冇 setuid bit，而且佢拒絕喺非 terminal 環境執行。 |
| 覆寫 `/sbin/getty-bk`（佢係 `-rwsrwsrwx`） | 非特權用戶寫入檔案時，kernel 會剝走 setuid/setgid。 |
| `/bin/busybox`（有 setuid） | 編譯時開咗 `FEATURE_SUID`；會按 `/etc/busybox.conf` 降權。 |
| 用戶 cron（`crontab -e`） | `/etc/crontabs` 對 `admin` 冇寫入權。 |
| `/etc/rc.local` | `root:root 755` —— `admin` 改唔到。 |
| Docker | 冇 `docker` binary 亦冇 socket（有 init script ≠ 裝咗）。 |
| Python App / supervisord | `/usr/python/bin/supervisord` 唔存在；而且個 App 本身就唔係以 root 跑。 |
| 爆破 root 密碼 hash | 每部機都唔同；41 個常見候選（連 `HelloMilesight!`）全部失敗。 |
| Node-RED `/node-red/auth/token` | 回 `invalid_grant` —— 佢個 bcrypt hash 同 gateway 密碼冇同步。 |

### 環境陷阱

- `uname -m` 係刻意回 `x.x.x` → 要讀 `/etc/openwrt_release`。
- 以 `admin` 身分跑 `logread` 會 block → 讀 `/etc/urlog/system.log`（world-readable）。
- 冇 `timeout` 指令（busybox 編譯時冇包）。
- Node-RED 嘅 admin root 係 `/node-red`，唔係 `/`。
- Node-RED 嘅 http-in root 同樣係 `/node-red` → 要 poll `https://<ip>:1880/node-red/cfrun`。
- **記憶體：** 總共 512 MB，本身已經跑住 lora-app-server / loraserver / postgres /
  mosquitto / redis / bacserv / quagga。一開 Node-RED，可用記憶體就由約 120 MB 跌到約 55 MB。
  `cloudflared` 係靠 `procd respawn` + `cfkeepalive` 保命，**唔需要 Node-RED**，所以如果你見到
  OOM 壓力，之後可以熄咗 Node-RED。
- `/tmp/cfinstall.log` 喺 tmpfs 上面 —— 重開機就冇。tunnel 係靠 `procd` 生存，唔係靠個 flow。
- **firmware 升級或者回復出廠**會清走 `/usr/bin/cloudflared`、`/etc/init.d/cloudflared`、
  `/etc/cloudflared/token`、`cfkeepalive` 同 firewall 規則。留住個 token，再跑一次腳本。

---

## Repo 結構

```
.
├── README.md
├── README.zh-HK.md               中文（廣東話）版 —— 即係你而家睇緊呢份
├── LICENSE                       MIT
├── .gitignore
├── docs/
│   └── manual-deployment.zh-HK.md  完整人手逐步教學（raw curl、驗證點、死路表）
├── ug65-cloudflared/
│   ├── SKILL.md                  完整 skill 指示（中文，含全部實測備註）
│   └── scripts/
│       ├── deploy.py             一鍵部署 + 驗收 + 清理
│       ├── make_flows.py         生成安裝用嘅 Node-RED flow／flows.json
│       ├── rootctl.py            經 Node-RED exec node 攞臨時 root shell
│       ├── ug65_lib.py           Web GUI 登入、/cgi RPC、檔案上傳、密碼 AES
│       └── verify.sh             喺 gateway 上面跑嘅驗收腳本（root 身分）
└── ug65-sub-cloudflared/
    ├── SKILL.md                  完整 skill 指示（中文，含全部實測備註）
    ├── bin/README.md             點樣取得 cloudflared arm64 binary
    └── scripts/
        ├── sub_install.py        部署／驗收／還原（主要入口）
        ├── sub_reboot_test.py    重開副 gateway 並由頭到尾重新驗收
        ├── rootctl.py            經 Node-RED exec node 攞臨時 root shell
        └── ug65_lib.py           Web GUI 登入、/cgi RPC、檔案上傳、密碼 AES
```

最詳細、實地測試過嘅備註都喺兩個 `SKILL.md` 裡面 —— 包括準確嘅 `/cgi` payload、連 raw
`curl` 命令嘅人手 fallback 流程，同埋實測嘅重開機時間。

---

## 安全須知

- 密碼係以 command line 參數傳入，呢批腳本**唔會**將佢寫落磁碟。記住用**單引號**包住 ——
  呢啲機嘅 admin 密碼經常含 `$$`，用雙引號嘅話 shell 會展開，跟住你就會見到一個莫名其妙的
  登入失敗。
- 臨時 root shell 每次跑完都會清走。你可以自己核實：
  `https://<gateway>:1880/node-red/cfrun` 一定要回 404/502。
- Web GUI 用嘅密碼混淆係 **AES-128-CBC 配硬編碼 key**
  （`1111111111111111` / `2222222222222222`）—— 佢係傳輸編碼，唔係安全措施。務必用 HTTPS。
- 任何對外發佈嘅嘢，前面都要擺一個 Cloudflare **Access policy**；token 一旦曝露過就要輪換。
- 只對你自己擁有、或者你獲授權管理嘅 gateway 執行呢批腳本。

## 鳴謝同免責聲明

同 Milesight、Cloudflare 並無隸屬關係，亦非佢哋認可或支援。產品名稱只作識別用途。
呢度所有嘢都係喺作者自己嘅硬件上面驗證過；你喺自己嗰部跑就係你自己嘅風險 —— 啲 gateway
可能擺喺remote、插住流動 SIM，所以改任何嘢之前，先想好你嘅復原路線。
