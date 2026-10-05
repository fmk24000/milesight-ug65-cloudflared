#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UG65 子機一鍵部署：令子機經 Cloudflare Tunnel 變成主機 Gateway Fleet 的 agent。

原理（已實測，詳見 SKILL.md）：
  * 子機只需要 cloudflared 做「client 代理」（cloudflared access tcp），
    唔需要裝 Cloudflare connector（connector 係主機嗰邊跑 tunnel run 嘅那隻）。
  * 子機 packet forwarder 用 Remote Embedded NS 模式，將 LoRa 封包以 MQTT/TLS 送去主機 18883。
  * ⭐ firmware 會將 serv_addr = 127.0.0.1 / localhost 當成「本地 Embedded NS」模式（唔會出門），
    所以要用一個穩定嘅非 loopback 地址：預設 10.99.99.1/32 掛喺 lo，cloudflared 綁佢。

用法：
  python sub_install.py --hosts 192.168.68.109 --host mqtt-xxx.example.com --password '<ADMIN_PASSWORD>'
  python sub_install.py --hosts 192.168.68.110,192.168.68.111 --host mqtt-xxx.example.com --password '...'
  python sub_install.py --hosts 192.168.68.109 --revert
"""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time

import requests
import urllib3

urllib3.disable_warnings()

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import ug65_lib as L  # noqa: E402
import rootctl  # noqa: E402

DEFAULT_ADDR = "10.99.99.1"
DEFAULT_PORT = 28883
SERVE_PORT = 8000
BIN_NAME = "cloudflared-linux-arm64"
GH_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/" + BIN_NAME
SNAP_DIR = os.path.join(SKILL, "snapshots")

INIT_TMPL = r'''#!/bin/sh /etc/rc.common
# cfagent：子機 LoRa bridge 用 Remote Embedded NS，將 MQTT 經 Cloudflare Tunnel 送去主機。
# ⚠️ 唔可以將 serv_addr 設成 127.0.0.1 / localhost：firmware 會當成本地 Embedded NS，
#    封包只會去子機自己嘅 mosquitto。所以用一個非 loopback 地址掛喺 lo。
START=95
STOP=15
USE_PROCD=1

PROG=/usr/bin/cloudflared
CFHOST=__CFHOST__
CFPORT=__CFPORT__
CFADDR=__CFADDR__

start_service() {
	ip addr add "$CFADDR/32" dev lo 2>/dev/null
	procd_open_instance
	procd_set_param command "$PROG" access tcp --hostname "$CFHOST" --url "$CFADDR:$CFPORT"
	procd_set_param respawn 3600 5 5
	procd_set_param stdout 1
	procd_set_param stderr 1
	procd_close_instance
}

stop_service() {
	ip addr del "$CFADDR/32" dev lo 2>/dev/null
}
'''

DIAG = r'''
echo "== model"; cat /etc/openwrt_release | grep -E "DESCRIPTION|ARCH"
echo "== mem"; free | head -2
echo "== existing binary"; ls -l /usr/bin/cloudflared 2>&1
echo "== listen"; netstat -ltn | grep -E "__PORT__|18883"
echo "== http to deployer"; wget -q -O - --timeout=10 "http://__MYPC__:__SPORT__/" >/dev/null 2>&1; echo "wget_rc=$?"
'''

PUSH = r'''
cd /tmp && rm -f /tmp/cf.new
wget -q -O /tmp/cf.new "http://__MYPC__:__SPORT__/__BIN__"; echo "wget_rc=$?"
ls -l /tmp/cf.new
SUM=$(md5sum /tmp/cf.new 2>/dev/null | cut -c1-32); echo "sum=$SUM"
if [ "$SUM" = "__MD5__" ]; then
  chmod 755 /tmp/cf.new; cp /tmp/cf.new /usr/bin/cloudflared; echo "INSTALLED"
else
  echo "MD5_MISMATCH"; exit 1
fi
/usr/bin/cloudflared --version 2>&1
'''

SERVICE = r'''
cat > /etc/init.d/cfagent <<'EOF'
__INIT__
EOF
chmod 755 /etc/init.d/cfagent
/etc/init.d/cfagent enable
/etc/init.d/cfagent restart
sleep 6
echo "== lo"; ip addr show lo | grep "inet "
echo "== listener"; netstat -ltn | grep __PORT__
echo "== proc"; ps | grep "[c]loudflared"
'''

CHECK = r'''
echo "== toml"; grep -E "^server|^ca_cert" /etc/lora-gateway-bridge/lora-gateway-bridge.toml
echo "== bridge type"; cat /tmp/pkt_fwd_type 2>/dev/null
echo "== conn"; netstat -tn 2>/dev/null | grep __PORT__ | head -3
echo "== bridge log"; tail -2 /etc/urlog/lora-gateway-bridge.log 2>&1
'''

CLEANUP = r'''
/etc/init.d/cfagent stop 2>/dev/null
/etc/init.d/cfagent disable 2>/dev/null
rm -f /etc/init.d/cfagent /etc/rc.d/S95cfagent /etc/rc.d/K15cfagent
ip addr del __CFADDR__/32 dev lo 2>/dev/null
pkill -f "cloudflared access tcp" 2>/dev/null
rm -f /usr/bin/cloudflared
echo "cleaned"
'''


def log(msg):
    print(msg, flush=True)


def my_ip_towards(host):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((host, 80))
    ip = s.getsockname()[0]
    s.close()
    return ip


def sha_bin(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest(), os.path.getsize(path)


def resolve_binary(explicit=None):
    """找 cloudflared arm64 binary：--binary → skill/bin → workspace 快取 → GitHub 下載。"""
    cands = [explicit,
             os.path.join(SKILL, "bin", BIN_NAME),
             os.path.join(os.getcwd(), BIN_NAME),
             os.path.join(os.getcwd(), "UG65", "_serve", BIN_NAME),
             os.path.join(os.getcwd(), "UG65", "_pki", BIN_NAME)]
    for c in cands:
        if c and os.path.isfile(c) and os.path.getsize(c) > 10_000_000:
            return c
    dst = os.path.join(SKILL, "bin", BIN_NAME)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    log("== 本機冇 cloudflared binary，試由 GitHub 下載（約 37MB）…")
    try:
        with requests.get(GH_URL, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(dst, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        log("   下載完成：%s" % dst)
        return dst
    except Exception as e:
        log("   下載失敗：%s" % e)
        log("   → 請由任何一隻已裝好嘅 gateway 抄：scp root@<gw>:/usr/bin/cloudflared ./%s" % BIN_NAME)
        log("     或用 --binary <路徑> 指定。")
        return None


def root_shell(gui, agent):
    gui.import_file("nodered_flows_json", "nodered flows json",
                    json.dumps(rootctl.ctl_nodes()).encode(), "flows.json")
    L.node_red_set(gui, False)
    time.sleep(10)
    L.node_red_set(gui, True)
    ctl = rootctl.RootCtl(gui, agent)
    return ctl if ctl.wait_up(180) else None


def finish(gui):
    gui.import_file("nodered_flows_json", "nodered flows json", b"[]", "flows.json")
    L.node_red_set(gui, False)


def get_conf(gui):
    j = gui.rpc("yruo_loragw", "get", [{"base": "general_conf"}]).json()
    return j["result"][0]["get"][0]["value"]


def set_conf(gui, value):
    gui.rpc("yruo_loragw", "set", [{"type": "general_conf", "index": 0,
                                    "base": "general_conf", "value": value}])
    return gui.rpc("yruo_apply", "apply", []).json()


def snapshot_path(agent):
    return os.path.join(SNAP_DIR, "conf_%s.json" % agent.replace(".", "_"))


def do_revert(agent, gui, addr):
    ctl = root_shell(gui, agent)
    if not ctl:
        log("!! root shell 唔通，還原中止")
        return
    log(ctl.run(CLEANUP.replace("__CFADDR__", addr), timeout=120))
    sp = snapshot_path(agent)
    if os.path.isfile(sp):
        with open(sp, encoding="utf-8") as f:
            before = json.load(f)["servs"]
        value = get_conf(gui)
        value["servs"] = before
        set_conf(gui, value)
        log("   已還原 destination")
    finish(gui)
    log("   還原完成（Node-RED 已熄）")


def fleet_api(main, password):
    s = requests.Session()
    s.verify = False
    r = s.post("https://%s/api/internal/login" % main,
               json={"username": "admin", "password": L.encrypt_password(password)}, timeout=15)
    j = r.json()
    tok = j.get("jwt") or (j.get("data") or {}).get("jwt") or (j.get("result") or {}).get("jwt")
    return s, tok


def fleet_check(main, agent_ip, gateway_id, password, register=False, name=None):
    """查主機 Gateway Fleet；register=True 時，未登記就自動 POST /api/gateways。"""
    try:
        s, tok = fleet_api(main, password)
        if not tok:
            log("   Fleet: 拎唔到 JWT")
            return
        hdr = {"Authorization": "Bearer " + tok}
        r = s.get("https://%s/api/gateways" % main,
                  params={"limit": 9999, "offset": 0, "organizationID": 1},
                  headers=hdr, timeout=20)
        gws = r.json().get("result", [])
        hit = next((g for g in gws if gateway_id and g["mac"].lower() == gateway_id.lower()), None)
        if hit is None and register and gateway_id:
            gw_name = name or ("Gateway_" + gateway_id[-4:])
            rr = s.post("https://%s/api/gateways" % main, headers=hdr, timeout=25,
                        json={"mac": gateway_id, "name": gw_name, "organizationID": "1",
                              "networkServerID": "1", "description": "UG65 agent via Cloudflare Tunnel"})
            log("   Fleet: 未登記 → 自動加入 %s (%s) http %s" % (gw_name, gateway_id, rr.status_code))
            if rr.status_code >= 300:
                log("   Fleet: %s" % rr.text[:300])
                return
            time.sleep(6)
            r = s.get("https://%s/api/gateways" % main,
                      params={"limit": 9999, "offset": 0, "organizationID": 1},
                      headers=hdr, timeout=20)
            gws = r.json().get("result", [])
            hit = next((g for g in gws if gateway_id and g["mac"].lower() == gateway_id.lower()), None)
        if hit:
            log("   Fleet: %s connected=%s lastSeen=%s" %
                (hit["name"], hit["connected"], hit.get("lastSeenAt")))
        else:
            log("   Fleet: 未見到 gateway id %s（見 SKILL.md 第 3 步）" % gateway_id)
    except Exception as e:
        log("   Fleet 查詢失敗：%s" % e)


def verify_only(agent, args):
    """唔改任何設定：睇子機 destination 狀態 + （有 --main 時）主機 Fleet 狀態。"""
    gui = L.WebGui(agent, args.user, args.password)
    gui.login()
    value = get_conf(gui)
    for s in value["servs"]:
        log("  id=%s enabled=%s addr=%s port=%s connected=%s" %
            (s.get("id"), s.get("serv_enabled"), s.get("serv_addr"),
             s.get("serv_mqtt_port"), s.get("connected")))
    j = gui.rpc("yruo_loragw", "get", [{"base": "status"}]).json()["result"][0]
    pf = j.get("get", [{}])[0].get("value", j)
    gid = pf.get("gateway_id", "?")
    log("  gateway_id=%s lns_type=%s push_data_ack=%s" %
        (gid, pf.get("lns_type", "?"), pf.get("push_data_ack", "?")))
    if args.main:
        fleet_check(args.main, agent, gid, args.password,
                    register=args.register, name=args.gw_name)
    return True


def install_one(agent, args, binary, md5, size):
    log("\n================ %s ================" % agent)
    gui = L.WebGui(agent, args.user, args.password)
    gui.login()
    log("登入成功")

    if args.revert:
        L.node_red_set(gui, True)
        time.sleep(6)
        do_revert(agent, gui, args.addr)
        return True

    L.node_red_set(gui, True)
    time.sleep(6)
    log("== 取臨時 root shell")
    ctl = root_shell(gui, agent)
    if not ctl:
        log("!! root shell 唔通，跳過")
        return False

    mypc = args.local_ip or my_ip_towards(agent)
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(args.serve_port),
                            "--bind", "0.0.0.0"],
                           cwd=os.path.dirname(binary),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    ok = True
    try:
        diag = (DIAG.replace("__MYPC__", mypc).replace("__SPORT__", str(args.serve_port))
                .replace("__PORT__", str(args.port)))
        log(ctl.run(diag, timeout=120))

        push = (PUSH.replace("__MYPC__", mypc).replace("__SPORT__", str(args.serve_port))
                .replace("__BIN__", os.path.basename(binary)).replace("__MD5__", md5))
        out = ctl.run(push, timeout=600)
        log(out)
        if "INSTALLED" not in out:
            log("!! binary 推送/校驗失敗")
            ok = False

        if ok:
            init = (INIT_TMPL.replace("__CFHOST__", args.host).replace("__CFPORT__", str(args.port))
                    .replace("__CFADDR__", args.addr))
            log(ctl.run(SERVICE.replace("__INIT__", init.strip()).replace("__PORT__", str(args.port)),
                        timeout=180))

            value = get_conf(gui)
            os.makedirs(SNAP_DIR, exist_ok=True)
            sp = snapshot_path(agent)
            if not os.path.isfile(sp):
                with open(sp, "w", encoding="utf-8") as f:
                    json.dump({"agent": agent, "servs": value["servs"]}, f,
                              ensure_ascii=False, indent=1)
                log("   已存底原設定 → %s" % sp)

            servs = json.loads(json.dumps(value["servs"]))
            remote = [s for s in servs if s.get("id") != 0]
            if not remote and args.add_dest:
                new_id = max([s.get("id", 0) for s in servs] or [0]) + 1
                servs.append({"id": new_id, "serv_enabled": True, "serv_addr": args.addr,
                              "serv_type": "ursalink", "serv_mqtt_port": args.port, "connected": 0})
                remote = [servs[-1]]
                log("   已新增 remote destination id=%d" % new_id)
            if not remote:
                log("!! 呢台子機冇 Remote Embedded NS destination（servs 只有 id=0）。")
                log("   請先喺 Packet Forwarder > General 加一個 destination，或用 --add-dest。")
                ok = False
            else:
                for s in remote:
                    s["serv_enabled"] = True
                    s["serv_addr"] = args.addr
                    s["serv_mqtt_port"] = args.port
                # Milesight KB：agent 必須停用自己的 Embedded NS（id=0 = 本地模式）。
                for s in servs:
                    if s.get("id") == 0:
                        s["serv_enabled"] = False
                value["servs"] = servs
                log("== 設定 destination → %s:%d" % (args.addr, args.port))
                log("   apply: %s" % set_conf(gui, value))
                time.sleep(args.settle)
                out = ctl.run(CHECK.replace("__PORT__", str(args.port)), timeout=120)
                log(out)
                if args.main and args.password:
                    gid = ""
                    try:
                        sj = gui.rpc("yruo_loragw", "get", [{"base": "status"}]).json()["result"][0]
                        gid = sj.get("get", [{}])[0].get("value", sj).get("gateway_id", "")
                    except Exception:
                        pass
                    fleet_check(args.main, agent, gid, args.password,
                                register=args.register, name=args.gw_name)

        log("== 收尾：清 ctl flow、熄 Node-RED")
        finish(gui)
    finally:
        srv.kill()
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts", required=True, help="子機 IP，用逗號分隔")
    ap.add_argument("--host", help="Cloudflare Tunnel 的 hostname（例 mqtt-xxx.example.com）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="本機 tunnel 出口 port（預設 %d）" % DEFAULT_PORT)
    ap.add_argument("--addr", default=DEFAULT_ADDR, help="綁喺 lo 的非 loopback 地址（預設 %s）" % DEFAULT_ADDR)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", required=True)
    ap.add_argument("--binary", help="cloudflared arm64 binary 路徑（唔填會自動找／下載）")
    ap.add_argument("--local-ip", help="本機喺子機 LAN 的 IP（預設自動偵測）")
    ap.add_argument("--serve-port", type=int, default=SERVE_PORT)
    ap.add_argument("--add-dest", action="store_true", help="冇 remote destination 時自動新增一個")
    ap.add_argument("--main", help="主機 IP：裝完順手查 Gateway Fleet connected 狀態")
    ap.add_argument("--register", action="store_true",
                    help="子機未登記入主機 Gateway Fleet 時自動加入（需 --main）")
    ap.add_argument("--gw-name", help="自動登記時用嘅名字（預設 Gateway_<mac 尾 4 位>）")
    ap.add_argument("--settle", type=int, default=20, help="apply 之後等幾秒（預設 20）")
    ap.add_argument("--revert", action="store_true", help="還原：移除服務／binary，還原 destination")
    ap.add_argument("--verify-only", action="store_true", help="唔改設定，只檢查子機 + 主機 Fleet 狀態")
    args = ap.parse_args()

    if not args.revert and not args.verify_only and not args.host:
        ap.error("--host（tunnel hostname）係必須")

    if args.verify_only:
        for ip in [h.strip() for h in args.hosts.split(",") if h.strip()]:
            log("==== %s ====" % ip)
            try:
                verify_only(ip, args)
            except Exception as e:
                log("!! %s 出錯：%r" % (ip, e))
        return

    binary = None
    if not args.revert:
        binary = resolve_binary(args.binary)
        if not binary:
            sys.exit(1)
        md5, size = sha_bin(binary)
        log("binary: %s (%d bytes, md5 %s)" % (binary, size, md5))
    results = {}
    for ip in [h.strip() for h in args.hosts.split(",") if h.strip()]:
        try:
            results[ip] = install_one(ip, args, binary, md5 if binary else "", size if binary else 0)
        except Exception as e:
            log("!! %s 出錯：%r" % (ip, e))
            results[ip] = False
    log("\n==== 結果 ====")
    for ip, ok in results.items():
        log("  %-16s %s" % (ip, "OK" if ok else "FAILED"))


if __name__ == "__main__":
    main()
