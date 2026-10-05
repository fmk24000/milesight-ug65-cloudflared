# Milesight UG65 × Cloudflare Tunnel

**English** | [中文（廣東話）](README.zh-HK.md)

Two reusable agent skills that put a **Milesight UG65 / UG56 / UG67 LoRaWAN gateway**
on the internet through **Cloudflare Tunnel (`cloudflared`)** using **nothing but the
admin account**.

No root password (these gateways don't have one), no disassembly, no firmware reflash,
no Docker, no port forwarding, no fixed IP.

| Skill | What it does |
|---|---|
| [`ug65-cloudflared`](ug65-cloudflared/SKILL.md) | Installs `cloudflared` **on the gateway itself**, so the gateway (and its web UI, Node-RED, whatever you route) is reachable at your own domain even when it sits behind a mobile SIM with no public IP. Persistent across power cycles, with a `procd` keepalive because **cron is unreliable on this firmware**. Optionally enables SSH in the same run and verifies everything — even when SSH is unavailable. |
| [`ug65-sub-cloudflared`](ug65-sub-cloudflared/SKILL.md) | Turns a **sub gateway** into a Gateway Fleet agent that ships its LoRa packets to a controller gateway over native MQTT. Installs `cloudflared access tcp` as a *client-side* tunnel proxy on the sub gateway (the Cloudflare connector stays on the controller), pins a **non-loopback** address on `lo` because the firmware treats a loopback server address as local mode, and repoints the packet forwarder at it. |

Both were developed and verified against real hardware — UG65 ×2, firmware
`60.0.0.49-r3` (controller) / `60.0.0.48-r3` (sub), `cloudflared 2026.9.1`, mosquitto mTLS,
including full **power-cycle tests**.

> 📖 Want the step-by-step manual version, with raw `curl` commands, per-step verification
> points and the dead-end list? See
> **[`docs/manual-deployment.zh-HK.md`](docs/manual-deployment.zh-HK.md)** (Chinese).

---

## Why this works at all

Three properties of the firmware make the whole approach possible. None of them are
exploits — they are vendor features being used as intended:

1. **The gateway runs OpenWrt/LEDE underneath** and ships a complete `procd`.
2. **The built-in Node-RED runs as `root` under procd.** `/etc/init.d/node_red` starts it
   without a `user` directive, so its `exec` node *is* a root shell
   (`id` → `uid=0(root) gid=0(root)`). Milesight's own knowledge base uses exactly this
   to run `iptables` on the device. These scripts use it as a **temporary** root shell for
   installation and verification, then clean it up.
3. **The web GUI exposes a `/cgi` RPC API**, and `/cgi-bin/file-import` (which runs as
   root) can write `/etc/node_red/data/flows.json`. That means a complete installation can
   be driven over HTTPS with just the admin password.

Put together: upload a Node-RED flow → it executes as root → it installs and verifies
`cloudflared` → remove the flow. The gateway keeps running `cloudflared` under `procd`;
Node-RED is not needed afterwards and can stay off.

---

## Requirements

- **Python 3.8+** with:
  ```bash
  pip install requests cryptography paramiko
  ```
  (`paramiko` is only needed for SSH-based verification.)
- HTTPS reachability to the gateway's web UI, and the **admin password** (not root).
- Skill 1: a **Cloudflare Tunnel token** (`eyJ...`), shown once in
  Zero Trust → Networks → Tunnels.
- Skill 2: a **tunnel hostname** on the controller, plus the machine running the script
  must be **on the same LAN as the sub gateway** — a ~37 MB binary is pushed to the sub
  gateway over local HTTP.

---

## Install

```bash
git clone https://github.com/fmk24000/milesight-ug65-cloudflared.git

# as DSH skills
cp -r milesight-ug65-cloudflared/ug65-cloudflared      ~/.dsh/skills/
cp -r milesight-ug65-cloudflared/ug65-sub-cloudflared  ~/.dsh/skills/
```

Or just run the scripts directly — each folder is self-contained (`SKILL.md` + `scripts/`).

---

## Skill 1 — `cloudflared` on the gateway

```bash
cd ug65-cloudflared/scripts
python deploy.py --host 192.168.68.104 --password '<ADMIN_PASSWORD>' \
                 --token '<TUNNEL_TOKEN>' \
                 --enable-ssh --ssh-lan 192.168.68.0/24
```

| Flag | Effect |
|---|---|
| `--host` / `--password` / `--user` | Gateway IP and admin credentials (user defaults to `admin`). |
| `--token` | Cloudflare Tunnel token (`eyJ...`). |
| `--ssh` | Verify over SSH (if it isn't reachable, a temporary root shell is used instead). |
| `--enable-ssh` | Also enable SSH: `ubus ssh_enable=1` + `yruo_apply` + `/etc/init.d/sshd`. |
| `--ssh-lan <CIDR>` | Accept `dport 22` only from this CIDR. **Needed** — devices arriving via `eth0` are classified as WAN and SSH is `REJECT`ed by default. |
| `--verify-mode auto\|ssh\|nodered` | Where verification runs. `auto` prefers SSH. |
| `--no-ctl` | Forbid the temporary root shell (then SSH is required for verification). |
| `--reboot-test` | Reboot and confirm auto-start (the gateway is offline for 1–2 minutes). |

What the script does, end to end: log in → enable Node-RED → generate `flows.json`
(+ optional SSH setup) → upload → restart Node-RED → install as root → wait for the
`run_id` to confirm the *new* run actually executed → verify → **remove the temporary
root shell**.

### Expected verification output

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

### Steps only you can do (in the Cloudflare dashboard)

1. **Add a Public Hostname** to the tunnel: Type `HTTPS`, URL `localhost:443`,
   **No TLS Verify = ON** (the gateway uses a self-signed certificate). Without this the
   domain returns an error.
2. **Add an Access policy** (Zero Trust → Access → Applications → self-hosted, e.g. email
   OTP). Without it, the gateway admin UI is exposed to the entire internet.
3. **Rotate the tunnel token** — once a token has appeared in a chat, a log or a file, treat
   it as burned.

> Persistence note: SSH access is deliberately left enabled (that is the intended
> operational model here). Firewall rules and `cloudflared` are kept alive by **procd**
> init scripts (`START=20` for firewall, `95` cloudflared, `96` keepalive), **never cron** —
> Milesight rewrites `/etc/crontabs/root` on boot and on every `yruo_apply apply`.

---

## Skill 2 — sub gateway as a Gateway Fleet agent

You only need three things: the sub gateway IP(s), a tunnel hostname on the controller, and
the admin password.

```bash
cd ug65-sub-cloudflared/scripts
python sub_install.py --hosts 192.168.68.110 --host mqtt-xxx.example.com \
       --password '<ADMIN_PASSWORD>' --add-dest --register \
       --gw-name Gateway_03 --main 192.168.68.106
```

| Flag | Effect |
|---|---|
| `--hosts` | Sub gateway IPs, comma separated — processed one by one. |
| `--host` | Tunnel hostname (**single-label**, i.e. `mqtt-xxx.example.com`). |
| `--port` | Local tunnel exit port, default `28883`. |
| `--addr` | Non-loopback address pinned on `lo`, default `10.99.99.1`. |
| `--binary` | Path to the arm64 `cloudflared` binary (otherwise auto-resolved / downloaded). |
| `--add-dest` | Create a remote destination if the sub gateway has none. |
| `--main` / `--register` / `--gw-name` | Check (and optionally auto-register) the gateway in the controller's Fleet. |
| `--verify-only` | Inspect only — changes nothing. |
| `--revert` | Undo everything and restore the saved snapshot. |

Each gateway gets: Node-RED on → temporary root shell → push the binary over local HTTP
with an **md5 check** → `/etc/init.d/cfagent` (procd, `START=95`, `respawn`, adds
`10.99.99.1/32` to `lo`) → point the destination at `10.99.99.1:28883` → `yruo_apply apply`
→ clean up the root shell. The previous destination config is saved to
`snapshots/conf_<ip>.json` on first run.

### Verify

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

### Controller side, one time only

Add a Public Hostname to the controller's tunnel (Zero Trust → Networks → Tunnels):

| Hostname | Service |
|---|---|
| `mqtt-xxx.example.com` | **`tcp://localhost:18883`** |
| *(optional)* `01-xxx.example.com` | `https://localhost:443` (No TLS Verify) |

Then register the sub gateway in the Network Server (Gateways → Add, using the sub
gateway's `gateway_id`), or let `--register` do it over the HTTP API.

---

## The two mistakes everyone makes

**1. Using `http://` for the MQTT route.** It must be **`tcp://localhost:18883`**. An
`http://` service makes Cloudflare's HTTP parser chew on a raw MQTT byte stream → HTTP 502
and `tls: handshake failure` at the client.

**2. Using a two-label hostname.** Free Universal SSL covers `example.com` and
`*.example.com` only:

| Hostname | Works? |
|---|---|
| `mqtt.example.com` | ✅ |
| `mqtt-site.example.com` | ✅ (still one label deep) |
| `mqtt.site.example.com` | ❌ — the edge fails before ServerHello (`fatal: handshake failure`) |

And one more for sub gateways specifically: **never point `serv_addr` at `127.0.0.1` or
`localhost`.** The firmware uses loopback to decide *local vs remote* embedded NS:

| `serv_addr` | What the firmware generates | Result |
|---|---|---|
| `127.0.0.1` / `localhost` | `server="tcp://127.0.0.1:1883"` | packets only reach the gateway's own mosquitto — Fleet is **permanently** `connected: false` |
| anything else | `server="ssl://<addr>:<port>"` + `ca_cert`/`tls_cert`/`tls_key` | packets tunnel out to the controller |

That is why this uses `10.99.99.1/32` on `lo` (non-loopback, stable, independent of site
and SIM), with `cloudflared` bound to that address only — not `0.0.0.0` — so nothing is
exposed on the LAN.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `lack of base` / `lack of index` / `lack of value` | Incomplete `set` payload | All four fields `type`/`index`/`base`/`value` are required. |
| `Session not found` (`-32001`) | GUI session expired (30 min) | Log in again — `ensure_login()` handles this. |
| `import incorrect type file` | A path was put in the `file=` form field | It takes a **description string** (`nodered flows json`), not a path. |
| Upload says fine, nothing installed | Node-RED never loaded the new flow | Make sure the `enable false → true` restart happened; read `/tmp/cfinstall.log`. |
| `!!! DOWNLOAD FAILED !!!` | The gateway can't reach GitHub | Serve it from your PC: `python -m http.server 8000`. |
| `Exec format error` | Wrong architecture | `uname -m` is rewritten to `x.x.x` by Milesight — read `DISTRIB_ARCH` in `/etc/openwrt_release` instead. `aarch64` → `cloudflared-linux-arm64`. |
| The token is visible in `ps` | Token passed on the command line | The init script must use `procd_set_param env TUNNEL_TOKEN=...`; the generated flow already does. |
| Nothing comes back after reboot | Not waiting long enough | `cloudflared` is `START=95`; allow **60–70 s** after boot, then check `/etc/rc.d/`. |
| Reload says "done" but nothing ran | Stale `/tmp/cfinstall.log` from the previous run | Handled: every deploy gets a `run_id`, and if the run isn't observed it is triggered directly through the root shell. |
| SSH won't enable (`status:-1`, ubus rc=255) | Sending the whole `general` object at once | Send **only** the field you are changing: `{"base":"general","index":0,"value":{"ssh_enable":1}}`. |
| SSH enabled but connection times out | Via `eth0` the device is treated as **WAN**; `Firewall_Remote` REJECTs port 22 | Use `--ssh-lan <CIDR>`; cron cannot persist this. |
| `iptables: error while loading libiptext.so` | Library path missing | `export LD_LIBRARY_PATH=/usr/lib/iptables`. |
| Added cron lines vanish after reboot | Milesight rewrites `/etc/crontabs/root` | Use a `procd` resident loop (`cfkeepalive`), not cron. |
| Node-RED `exec` node never returns / HTTP times out | `oldrc: false` is unreliable on this firmware | Set `"oldrc": true`; avoid `addpay` and `useSpawn: true`. |
| `CookieConflictError: multiple cookies named 'td'` (fw `60.0.0.48-r3`) | Login reply has no `td` in `result[0]` | Already fixed — `login()` falls back to `Set-Cookie`. |

### Dead ends — don't waste your time

These were all tested and do not work on this firmware:

| Method | Why it fails |
|---|---|
| `su - root -c` | `/bin/su` has no setuid bit, and it refuses to run outside a terminal. |
| Overwriting `/sbin/getty-bk` (which is `-rwsrwsrwx`) | The kernel strips setuid/setgid when a non-privileged user writes the file. |
| `/bin/busybox` (has setuid) | Built with `FEATURE_SUID`; drops privilege per `/etc/busybox.conf`. |
| User cron (`crontab -e`) | `/etc/crontabs` isn't writable by `admin`. |
| `/etc/rc.local` | `root:root 755` — `admin` can't modify it. |
| Docker | No `docker` binary and no socket (the init script existing ≠ installed). |
| Python App / supervisord | `/usr/python/bin/supervisord` doesn't exist; and the App doesn't run as root anyway. |
| Cracking the root password hash | Per-device; 41 common candidates (including `HelloMilesight!`) all failed. |
| Node-RED `/node-red/auth/token` | Returns `invalid_grant` — its bcrypt hash isn't synced with the gateway password. |

### Environment gotchas

- `uname -m` returns `x.x.x` by design → read `/etc/openwrt_release`.
- `logread` blocks when run as `admin` → read `/etc/urlog/system.log` (world-readable).
- There is no `timeout` command (busybox build without it).
- Node-RED's admin root is `/node-red`, not `/`.
- The Node-RED http-in root is also `/node-red` → poll `https://<ip>:1880/node-red/cfrun`.
- **Memory:** 512 MB total, already running lora-app-server / loraserver / postgres /
  mosquitto / redis / bacserv / quagga. Enabling Node-RED drops free memory from ~120 MB to
  ~55 MB. `cloudflared` is kept alive by `procd respawn` + `cfkeepalive` and **does not need
  Node-RED**, so if you see OOM pressure you can turn Node-RED off afterwards.
- `/tmp/cfinstall.log` is on tmpfs — it disappears on reboot. The tunnel survives via
  `procd`, not via the flow.
- A **firmware upgrade or factory reset** wipes `/usr/bin/cloudflared`,
  `/etc/init.d/cloudflared`, `/etc/cloudflared/token`, `cfkeepalive` and the firewall rules.
  Keep the token and re-run the script.

---

## Repository layout

```
.
├── README.md
├── README.zh-HK.md               Chinese (Cantonese) version of this file
├── LICENSE                       MIT
├── .gitignore
├── docs/
│   └── manual-deployment.zh-HK.md  full step-by-step manual (raw curl, checkpoints, dead ends)
├── ug65-cloudflared/
│   ├── SKILL.md                  full skill instructions (Chinese, with all field notes)
│   └── scripts/
│       ├── deploy.py             one-shot deploy + verify + cleanup
│       ├── make_flows.py         generates the install Node-RED flow / flows.json
│       ├── rootctl.py            temporary root shell via a Node-RED exec node
│       ├── ug65_lib.py           web GUI login, /cgi RPC, file upload, password AES
│       └── verify.sh             on-gateway verification script (run as root)
└── ug65-sub-cloudflared/
    ├── SKILL.md                  full skill instructions (Chinese, with all field notes)
    ├── bin/README.md             how to obtain the cloudflared arm64 binary
    └── scripts/
        ├── sub_install.py        deploy / verify / revert (main entry point)
        ├── sub_reboot_test.py    reboot the sub gateway and re-verify end to end
        ├── rootctl.py            temporary root shell via a Node-RED exec node
        └── ug65_lib.py           web GUI login, /cgi RPC, file upload, password AES
```

The detailed, field-tested notes live in the two `SKILL.md` files — they include the exact
`/cgi` payloads, the manual fallback procedure with raw `curl` commands, and the measured
reboot timings.

---

## Security notes

- Passwords are passed as command-line arguments and are never written to disk by these
  scripts. Quote them in **single quotes** — admin passwords here often contain `$$`, which
  the shell will otherwise expand, and you'll get a confusing login failure.
- The temporary root shell is removed at the end of every run. Confirm it yourself:
  `https://<gateway>:1880/node-red/cfrun` must return 404/502.
- The password obfuscation used by the web GUI is **AES-128-CBC with a hard-coded key**
  (`1111111111111111` / `2222222222222222`) — it is transport encoding, not security. Always
  use HTTPS.
- Put a Cloudflare **Access policy** in front of anything you publish, and rotate the tunnel
  token after any exposure.
- Only run these scripts against gateways you own or are authorised to manage.

## Credits & disclaimer

Not affiliated with, endorsed by, or supported by Milesight or Cloudflare. Product names
are used for identification only. Everything here was validated on the author's own
hardware; running it on yours is at your own risk — the gateways may be remote and running
on a mobile SIM, so plan your recovery path before changing anything.
