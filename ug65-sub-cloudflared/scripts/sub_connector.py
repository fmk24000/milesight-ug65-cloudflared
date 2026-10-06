#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""子機自己嘅 Cloudflare Tunnel connector（獨立 tunnel + 自己 hostname）。

同 `sub_install.py` 嘅分別：
  - `sub_install.py`：子機做 Gateway Fleet agent（跑 `cloudflared access tcp`，**唔要 token**）
  - 本 script：子機自己都有一條完整 tunnel（跑 `cloudflared tunnel run`，**要一條屬於子機嘅 token**）

本 script 係 wrapper，真正落手裝嘅係 skill `ug65-cloudflared` 嘅 `deploy.py`；
wrapper 負責硬規矩同善後：
  1. **冇 `--token` 就停手**，並印出「要問用戶拎 token」嘅指示（唔准由舊紀錄推測）。
  2. 驗 token 格式（base64 JSON，含 a/t/s），並且**唔准同主機（controller）嘅 token 一樣**。
  3. 裝之前問清楚子機 IP／username／password；記低 Node-RED 原本開定關。
  4. 裝完把 Node-RED 還原返安裝前狀態（原本關 → 上傳 `[]` + 熄），因為 tunnel 靠 procd 自啟，唔靠 flow。
  5. 裝完核對：**兩個 cloudflared 進程並存**（access tcp ＋ tunnel run）、28883 listener 冇被打爛、
     MQTT destination (`general_conf.servs`) 前後一致。

用法：
  python sub_connector.py --hosts 192.168.68.109 --password '<ADMIN_PASSWORD>' --token '<TOKEN>' \
         [--enable-ssh --ssh-lan 192.168.68.0/24] [--main 01-xxx.example.com]
  python sub_connector.py --verify-only --hosts 192.168.68.109 --password '<ADMIN_PASSWORD>'
  python sub_connector.py --check-token --token '<TOKEN>'
"""
import argparse
import base64
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ug65_lib as L  # noqa: E402

def _find_root():
    """搵 skill 根目錄 —— 支援兩種安裝方式。

    repo clone： <repo>/ug65-sub-cloudflared/scripts  → 根目錄 = <repo>（有 .git）
    DSH skills：<workspace>/.dsh/skills/ug65-sub-cloudflared/scripts → 根目錄 = <workspace>（有 .dsh）
    """
    repo = os.path.abspath(os.path.join(HERE, "..", ".."))
    ws = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
    if os.path.isdir(os.path.join(repo, ".git")):
        return repo
    if os.path.isdir(os.path.join(ws, ".dsh")):
        return ws
    if os.path.isdir(os.path.join(repo, "ug65-cloudflared")):
        return repo
    return ws


def _find_deploy_py():
    """搵另一個 skill 嘅 deploy.py —— 逐個候選路徑試，邊個存在就用邊個。

    可用環境變數 UG65_DEPLOY_PY 直接指定。
    """
    env = os.environ.get("UG65_DEPLOY_PY")
    if env:
        return os.path.abspath(env)
    cands = [
        # repo clone：<repo>/ug65-cloudflared/scripts/deploy.py
        os.path.join(HERE, "..", "..", "ug65-cloudflared", "scripts", "deploy.py"),
        # DSH skills：<workspace>/.dsh/skills/ug65-cloudflared/scripts/deploy.py
        os.path.join(HERE, "..", "..", "..", "ug65-cloudflared", "scripts", "deploy.py"),
        # DSH skills，但 home 下面嘅 .dsh
        os.path.join(os.path.expanduser("~"), ".dsh", "skills",
                     "ug65-cloudflared", "scripts", "deploy.py"),
    ]
    for c in cands:
        c = os.path.abspath(c)
        if os.path.isfile(c):
            return c
    return os.path.abspath(cands[0])


WORKSPACE = _find_root()
DEPLOY_PY = _find_deploy_py()
SUB_INSTALL_PY = os.path.join(HERE, "sub_install.py")

ASK_TOKEN_MSG = """
✗ 未提供子機嘅 Tunnel Token —— 停手，先問用戶。

請向用戶攞以下幾樣（唔准由舊對話／舊 log／snapshots／主機 token 推測）：
  1. 子機 IP（例 192.168.68.109）
  2. Username / Password（子機 Web GUI 帳號，密碼用單引號包住）
  3. **子機自己嘅 Cloudflare Tunnel Token**（在 Cloudflare dashboard 為子機開一條新 tunnel，
     例：Zero Trust → Networks → Tunnels → Create a tunnel，再複製 token `eyJ...`）
     —— 如果用戶打算子機共用主機嘅 tunnel，要同用戶講清楚：咁樣子機就冇自己嘅 hostname，
        而且改主機 tunnel 會影響埋子機，唔建議。

⚠️ 主機（controller）自己嘅 token 唔可以照抄落子機（兩條 tunnel 唔同 id）。
"""


# --------------------------------------------------------------------- token

def decode_token(tok):
    """Cloudflare tunnel token = base64(JSON {"a":account,"t":tunnel,"s":secret})。"""
    pad = "=" * (-len(tok) % 4)
    j = json.loads(base64.b64decode(tok + pad).decode("utf-8"))
    for k in ("a", "t", "s"):
        if k not in j:
            raise ValueError("token 解出嚟冇 %r（係咪抄漏／抄錯？）" % k)
    return j


def controller_tokens():
    """搵 workspace 內主機用過嘅 token 檔案（唔會用嚟裝，只用嚟攔錯）。"""
    found = []
    for pat in (os.path.join(WORKSPACE, "tunnel.token"),
                os.path.join(WORKSPACE, "*.token"),
                os.path.join(WORKSPACE, "**", "tunnel.token")):
        found += glob.glob(pat, recursive=True)
    out = []
    for f in sorted(set(found)):
        try:
            with open(f, encoding="utf-8") as fh:
                v = fh.read().strip()
            if v.startswith("eyJ"):
                out.append((f, v))
        except Exception:
            pass
    return out


def check_token(tok):
    j = decode_token(tok)
    print("  token OK：")
    print("    account = %s" % j["a"])
    print("    tunnel  = %s" % j["t"])
    same_account = False
    for path, v in controller_tokens():
        try:
            cj = decode_token(v)
        except Exception:
            continue
        if cj["t"] == j["t"]:
            print("\n✗ 呢個 token 就係主機自己嘅 tunnel（%s，來自 %s）。" % (cj["t"], path))
            print("  子機要另一條獨立 tunnel；請向用戶另開一條並攞新 token。")
            raise SystemExit(2)
        if cj["a"] == j["a"]:
            same_account = True
    print("    %s" % ("同主機同一個 Cloudflare account（正常；tunnel id 唔同就 OK）"
                      if same_account else "同主機唔同 account（確認係咪用戶想要）"))
    return j


# --------------------------------------------------------------------- 前後狀態

def mqtt_status(host, password, user, main):
    """用 sub_install.py --verify-only 讀子機 destination（唔改任何設定）。"""
    cmd = [sys.executable, SUB_INSTALL_PY, "--verify-only", "--hosts", host,
           "--user", user, "--password", password]
    if main:
        cmd += ["--main", main]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return "（查唔到：%s）" % e


def run_json_cmd(cmd, timeout=600):
    print("  $ %s" % " ".join(cmd[1:]))
    rc = subprocess.run(cmd, timeout=timeout).returncode
    return rc


def restore_nodered(host, user, password):
    """Node-RED 還原：flows.json = [] 並且關掉（tunnel 靠 procd S95 自啟，唔靠 flow）。"""
    print("  · 還原 Node-RED（上傳 [] + enable=false）…")
    gui = L.WebGui(host, user, password)
    gui.login(verbose=False)
    gui.ensure_login()
    r = gui.import_file("nodered_flows_json", "nodered flows json",
                        json.dumps([]).encode(), "flows.json")
    print("    flows.json 上傳 HTTP %s" % r.status_code)
    gui.ensure_login()
    ok, detail = L.node_red_set(gui, False)
    print("    node_red enable=false → %s" % ok)
    for i in range(6):
        time.sleep(3)
        if not L.node_red_api_up(host):
            print("    %ds 後 1880 已收（Node-RED 已停）" % ((i + 1) * 3))
            return True
    return L.node_red_api_up(host) is False


CHECK_SH = r"""
echo "###PROC###"
for p in $(ls /proc | grep -E '^[0-9]+$'); do
  c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)
  case "$c" in *cloudflared*) echo "PID $p: $c";; esac
done
echo "###LISTEN###"
netstat -ltn 2>/dev/null | grep -E '28883|1883|:22 '
echo "###RCD###"
ls /etc/rc.d/ 2>/dev/null | grep -E 'cloudflared|cfkeepalive|cfsshfw|cfagent'
echo "###TUNNELLOG###"
grep -i 'cloudflared' /etc/urlog/system.log 2>/dev/null | tail -6
echo "###ESTAB###"
netstat -ant 2>/dev/null | grep -vE '127\.0\.0\.1|::1' | grep -E 'ESTABLISHED|28883' | head -8
"""


def post_check(host, user, password):
    """SSH 核對：兩個 cloudflared 並存、28883 listener、rc.d、有冇 error。"""
    try:
        out = L.ssh_run_script(host, user, password, CHECK_SH, timeout=180)
    except Exception as e:
        print("  ⚠️ SSH 核對唔到（%s）；如果冇開 SSH，睇 deploy.py 嘅 Node-RED 驗收輸出。" % str(e)[:120])
        return {}
    sections = {}
    cur = None
    for line in out.splitlines():
        if line.startswith("###") and line.endswith("###"):
            cur = line.strip("#")
            sections[cur] = []
        elif cur:
            sections[cur].append(line)

    proc = "\n".join(sections.get("PROC", []))
    listen = "\n".join(sections.get("LISTEN", []))
    rcd = "\n".join(sections.get("RCD", []))
    log = "\n".join(sections.get("TUNNELLOG", []))
    estab = "\n".join(sections.get("ESTAB", []))

    has_access = "access tcp" in proc
    has_tunnel = "tunnel run" in proc
    has_sidecar_port = "28883" in listen and "LISTEN" in listen
    has_rcd = "S95cloudflared" in rcd
    has_cfagent = "S95cfagent" in rcd
    errors = [l for l in log.splitlines() if " ERR " in l]

    print("  · 進程：access tcp（MQTT sidecar）=%s    tunnel run（新 connector）=%s"
          % ("✓" if has_access else "✗", "✓" if has_tunnel else "✗"))
    print("  · cfagent 28883 listener = %s；rc.d S95cloudflared = %s；S95cfagent = %s"
          % ("✓" if has_sidecar_port else "✗", "✓" if has_rcd else "✗",
             "✓" if has_cfagent else "✗"))
    if estab.strip():
        print("  · 已建立連線：\n      " + "\n      ".join(estab.splitlines()[:4]))
    print("  · 最近 cloudflared log：%s" % ("（有 %d 條 ERR）" % len(errors) if errors else "無 ERR ✓"))
    for l in log.splitlines()[-3:]:
        print("      " + l.strip())
    return {"access": has_access, "tunnel": has_tunnel, "port": has_sidecar_port,
            "rcd": has_rcd, "cfagent": has_cfagent, "errors": errors}


# --------------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="子機自己嘅 cloudflared tunnel connector")
    ap.add_argument("--hosts", help="子機 IP，逗號分隔")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", help="子機 Web GUI 密碼（用單引號包住）")
    ap.add_argument("--token", help="★ 子機自己嘅 Tunnel Token（必問用戶，唔准用主機嘅）")
    ap.add_argument("--enable-ssh", action="store_true", help="順手開 SSH（＋防火牆限 LAN）")
    ap.add_argument("--ssh-lan", default="", help="只准呢個 CIDR 入 22，例 192.168.68.0/24")
    ap.add_argument("--main", help="主機 IP／hostname，用嚟查 Fleet connected（可選）")
    ap.add_argument("--keep-nodered", action="store_true",
                    help="唔還原 Node-RED（預設會還原返安裝前狀態）")
    ap.add_argument("--skip-mqtt-check", action="store_true", help="唔查 MQTT destination")
    ap.add_argument("--verify-only", action="store_true", help="只核對現狀，唔裝任何嘢")
    ap.add_argument("--check-token", action="store_true", help="只驗 token 格式／會否撞主機 token")
    ap.add_argument("--wait", type=int, default=100, help="（唔用 ctl 時）安裝等待秒數")
    args = ap.parse_args()

    # ---- token gate：最緊要嘅一步 ----------------------------------------
    if args.check_token:
        if not args.token:
            print(ASK_TOKEN_MSG)
            return 2
        check_token(args.token)
        return 0

    if not args.hosts or not args.password:
        print("✗ 要 --hosts 同 --password。問齊子機 IP／username／password 先做。")
        return 2

    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]

    if not args.verify_only:
        if not args.token:
            print(ASK_TOKEN_MSG)
            return 2
        print("[0] 驗 token")
        check_token(args.token)
        if not os.path.isfile(DEPLOY_PY):
            print("✗ 搵唔到 %s —— 需要 skill `ug65-cloudflared`。" % DEPLOY_PY)
            return 2

    rc_all = 0
    for host in hosts:
        print("\n" + "=" * 74)
        print("子機 %s" % host)
        print("=" * 74)

        before_mqtt = "(skip)"
        nodered_was_on = None
        if not args.verify_only and not args.skip_mqtt_check:
            print("[1] 裝之前嘅 MQTT／destination 狀態（baseline）")
            before_mqtt = mqtt_status(host, args.password, args.user, args.main)
            print("\n".join("    " + l for l in before_mqtt.splitlines()))

        if not args.verify_only and not args.keep_nodered:
            print("[2] 記低 Node-RED 原本開定關")
            try:
                gui = L.WebGui(host, args.user, args.password)
                gui.login(verbose=False)
                gui.ensure_login()
                nodered_was_on = L.node_red_enabled(gui)
                print("    node_red.enable = %s" % nodered_was_on)
            except Exception as e:
                print("    ⚠️ 讀唔到（%s）；收尾會照樣還原成關。" % str(e)[:100])

        if not args.verify_only:
            print("[3] 交俾 ug65-cloudflared/deploy.py 落手裝")
            cmd = [sys.executable, DEPLOY_PY, "--host", host, "--user", args.user,
                   "--password", args.password, "--token", args.token,
                   "--wait", str(args.wait)]
            if args.enable_ssh:
                cmd += ["--enable-ssh"]
                if args.ssh_lan:
                    cmd += ["--ssh-lan", args.ssh_lan]
            rc = run_json_cmd(cmd, timeout=1200)
            rc_all = rc_all or rc
            print("    deploy.py 結束：exit=%s" % rc)

            if not args.keep_nodered and nodered_was_on is not True:
                print("[4] 還原 Node-RED（原本唔係開住）")
                try:
                    restore_nodered(host, args.user, args.password)
                except Exception as e:
                    print("    ⚠️ 還原失敗：%s" % str(e)[:150])

        print("[5] SSH 核對（兩個 cloudflared 並存、MQTT sidecar 冇被打爛）")
        ck = post_check(host, args.user, args.password)

        if not args.skip_mqtt_check:
            print("[6] 裝之後嘅 MQTT／destination 狀態（對比 baseline）")
            after_mqtt = mqtt_status(host, args.password, args.user, args.main)
            print("\n".join("    " + l for l in after_mqtt.splitlines()))
            if not args.verify_only and before_mqtt != "(skip)":
                if before_mqtt == after_mqtt:
                    print("    ✓ destination／Fleet 狀態同安裝前一致")
                else:
                    print("    ⚠️ 同安裝前有出入，逐行睇上面（尤其 id=1 enabled / addr / port）")
            # 連唔到子機（IP 變咗／關機／唔同 LAN）→ 唔可以當成功
            unreachable = ("出錯" in after_mqtt or "timed out" in after_mqtt
                           or "Max retries" in after_mqtt)
            if unreachable and not ck:
                print("    ✗ 子機完全連唔到（Web GUI 同 SSH 都唔通）—— 確認 IP／LAN，或者"
                      "子機已經轉咗做 gateway 自己嘅 DHCP 位址。")
                rc_all = rc_all or 3

    print("\n完成。" if not args.verify_only else "\n驗證完成（冇改任何設定）。")
    return rc_all


if __name__ == "__main__":
    sys.exit(main())
