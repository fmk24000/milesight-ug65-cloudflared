#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""子機重開機驗證：reboot 之後 cloudflared / cfagent / destination 係咪全部自動恢復。

用法：
  python sub_reboot_test.py --hosts 192.168.68.110 --password '<ADMIN_PASSWORD>' --main 192.168.68.106
"""
import argparse
import json
import sys
import time

import requests
import urllib3

urllib3.disable_warnings()

import os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ug65_lib as L  # noqa: E402
import rootctl  # noqa: E402
from sub_install import finish, fleet_check, get_conf, root_shell  # noqa: E402


def log(m):
    print(m, flush=True)


def gui_up(host, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get("https://%s/" % host, verify=False, timeout=5)
            if r.status_code < 500:
                return time.time() - t0
        except Exception:
            pass
        time.sleep(5)
    return None


def test_one(host, args):
    log("==== %s ====" % host)
    gui = L.WebGui(host, args.user, args.password)
    gui.login()
    L.node_red_set(gui, True)
    time.sleep(6)
    ctl = root_shell(gui, host)
    if not ctl:
        log("!! root shell 唔通，中止")
        return False
    log(ctl.run('uptime; cat /tmp/pkt_fwd_type', timeout=60))
    log(ctl.run('sync; (sleep 3; reboot) >/dev/null 2>&1 & echo "reboot scheduled"', timeout=30))
    time.sleep(20)
    dt = gui_up(host, timeout=args.timeout)
    if dt is None:
        log("!! %ds 內 GUI 未返嚟" % args.timeout)
        return False
    log("   GUI %ds 後返嚟" % int(dt))
    time.sleep(30)

    gui = L.WebGui(host, args.user, args.password)
    gui.login()
    value = get_conf(gui)
    ok = False
    for s in value["servs"]:
        log("   servs id=%s enabled=%s addr=%s port=%s connected=%s" %
            (s.get("id"), s.get("serv_enabled"), s.get("serv_addr"),
             s.get("serv_mqtt_port"), s.get("connected")))
        if s.get("id") != 0 and s.get("serv_addr") == args.addr:
            ok = True
    if not ok:
        log("!! destination 冇指向 %s" % args.addr)
        return False

    L.node_red_set(gui, True)
    time.sleep(6)
    ctl = root_shell(gui, host)
    if ctl:
        log(ctl.run('uptime; ip addr show lo | grep "inet "; netstat -ltn | grep 28883; '
                    'ps | grep "[c]loudflared"; grep ^server /etc/lora-gateway-bridge/'
                    'lora-gateway-bridge.toml', timeout=120))
        log("== 收尾：清 ctl flow、熄 Node-RED")
        finish(gui)
    else:
        log("!! 重開機後取唔到 root shell（唔影響回傳功能）")

    if args.main:
        sj = gui.rpc("yruo_loragw", "get", [{"base": "status"}]).json()["result"][0]
        gid = sj.get("get", [{}])[0].get("value", sj).get("gateway_id", "")
        fleet_check(args.main, host, gid, args.password)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts", required=True)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", required=True)
    ap.add_argument("--addr", default="10.99.99.1")
    ap.add_argument("--main")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()
    res = {}
    for ip in [h.strip() for h in args.hosts.split(",") if h.strip()]:
        try:
            res[ip] = test_one(ip, args)
        except Exception as e:
            log("!! %s 出錯：%r" % (ip, e))
            res[ip] = False
    log("\n==== 結果 ====")
    for ip, ok in res.items():
        log("  %-16s %s" % (ip, "OK" if ok else "FAILED"))


if __name__ == "__main__":
    main()
