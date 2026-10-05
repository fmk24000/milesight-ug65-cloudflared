#!/usr/bin/env python3
"""一鍵部署 cloudflared 到 Milesight UG65（唔需要 root 密碼）。

用法:
    python deploy.py --host 192.168.68.104 --password '<ADMIN_PASSWORD>' \
                     --token 'eyJhIjoi...' [--ssh] [--enable-ssh] \
                     [--ssh-lan 192.168.68.0/24] [--reboot-test]

流程（全部係實測過嘅步驟）:
    1. 登入 Web GUI API（/cgi，AES 加密密碼）
    2. 開 Node-RED（官方 RPC；Node-RED 由 procd 以 root 啟動）
    3. 生成含 Tunnel Token 嘅 flows.json（＋可選 SSH 設定）
    4. 經 /cgi-bin/file-import（luci2-io，以 root 執行）寫入 /etc/node_red/data/flows.json
    5. 重啟 Node-RED 觸發 flow → exec node 以 root 身分完成安裝
    6. 驗收（有 SSH 就用 SSH，冇 SSH 就經臨時 Node-RED root shell；用完清走）
    7. 可選斷電重開測試

⚠️ 冇 SSH 都驗收到（rootctl），但**唔會**留低任何後門：驗收完會還原 flows.json。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ug65_lib as L  # noqa: E402
import make_flows  # noqa: E402
import rootctl  # noqa: E402


def step(n, msg):
    print("\n" + "=" * 72)
    print("[%s] %s" % (n, msg))
    print("=" * 72)


def try_ssh(host, user, password, port=22, timeout=20):
    """SSH 通唔通（真登入）。回 (ok, 訊息)。"""
    try:
        out, rc = L.ssh_run(host, user, password, "echo SSH-OK; id", timeout=timeout,
                            port=port)
        return out.startswith("SSH-OK"), out.strip().splitlines()[0] if out else ""
    except Exception as e:
        return False, str(e)[:120]


def verify_script_text():
    return open(os.path.join(HERE, "verify.sh"), encoding="utf-8").read()


def cleanup_ctl(gui, install_flow, host, announce=True):
    """還原 canonical install-only flows.json，清走臨時 root shell。"""
    if announce:
        step("清", "移除臨時驗收 endpoint（還原 canonical flows.json）")
    try:
        gui.ensure_login()
        payload = json.dumps(install_flow, ensure_ascii=False).encode()
        r = gui.import_file("nodered_flows_json", "nodered flows json", payload, "flows.json")
        print("  還原上傳：HTTP %s" % r.status_code)
        gui.ensure_login()
        L.node_red_set(gui, False)
        time.sleep(10)
        gui.ensure_login()
        L.node_red_set(gui, True)
        time.sleep(25)
        still = rootctl.RootCtl(gui, host).up()
        print("  /node-red/cfrun 檢查：%s" % ("仍然存在！" if still else "✓ 已消失"))
        return not still
    except Exception as e:
        print("  ⚠️ 還原失敗，請手動重跑一次 deploy.py 清理：%s" % str(e)[:150])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", required=True)
    ap.add_argument("--token", required=True, help="Cloudflare Tunnel Token (eyJ...)")
    ap.add_argument("--ssh", action="store_true",
                    help="用 SSH 做驗收（推薦；要 Access Service 已開 SSH）")
    ap.add_argument("--enable-ssh", action="store_true",
                    help="順手開 SSH（ubus ssh_enable + /etc/init.d/sshd）")
    ap.add_argument("--ssh-lan", default="",
                    help="只准呢個 CIDR 入 22，例如 192.168.68.0/24（經 eth0 入嘅機被當 WAN，預設 REJECT）")
    ap.add_argument("--verify-mode", choices=["auto", "ssh", "nodered"], default="auto",
                    help="auto=有 SSH 用 SSH，否則用臨時 Node-RED root shell")
    ap.add_argument("--no-ctl", action="store_true",
                    help="唔准用臨時 Node-RED root shell（咁就一定要 --ssh 才驗收到）")
    ap.add_argument("--reboot-test", action="store_true")
    ap.add_argument("--ssh-port", type=int, default=22)
    ap.add_argument("--wait", type=int, default=100,
                    help="（冇 ctl 時）安裝等待秒數，預設 100")
    args = ap.parse_args()

    if not args.token.startswith("eyJ"):
        print("⚠️  token 通常以 eyJ 開頭，請確認冇抄錯。")

    gui = L.WebGui(args.host, args.user, args.password)
    use_ctl = not args.no_ctl and args.verify_mode != "ssh"
    ctl = None

    # ---------------------------------------------------------------- 1
    step(1, "登入 Web GUI API")
    info = gui.login()
    print("  型號 %s / firmware %s" % (info.get("model"), info.get("rtver")))

    # ---------------------------------------------------------------- 2
    step(2, "確認 Node-RED 已啟用")
    enabled = L.node_red_enabled(gui)
    print("  目前 enable = %s" % enabled)
    if not enabled:
        print("  正在啟用（gateway 會 reload nginx，連線斷一下係正常）…")
        ok, detail = L.node_red_set(gui, True)
        print("  set 結果：%s" % detail)
        if not ok:
            print("  ✗ 啟用失敗，中止。")
            return 1
        gui.ensure_login()
        for i in range(12):
            time.sleep(5)
            if L.node_red_api_up(args.host):
                print("  ✓ Node-RED 已上線（port 1880 /node-red/）")
                break
            print("    …等 Node-RED 起身 (%ds)" % ((i + 1) * 5))
        else:
            print("  ⚠️ 1880 未通，繼續試落去。")
    else:
        print("  ✓ 已經啟用")

    # ---------------------------------------------------------------- 3
    step(3, "生成 flows.json（含 Tunnel Token%s）"
         % ("、SSH 設定" if (args.enable_ssh or args.ssh_lan) else ""))
    token = args.token.strip()
    run_id = time.strftime("%Y%m%d-%H%M%S")
    install_flow = make_flows.build_flow(token, args.ssh_lan, args.enable_ssh, run_id)
    print("  token 長度 %d，enable_ssh=%s，ssh_lan=%r，run_id=%s"
          % (len(token), args.enable_ssh, args.ssh_lan, run_id))

    # ---------------------------------------------------------------- 4
    step(4, "上傳 flows.json 到 /etc/node_red/data/flows.json")
    gui.ensure_login()
    flow = install_flow + rootctl.ctl_nodes() if use_ctl else install_flow
    payload = json.dumps(flow, ensure_ascii=False).encode()
    print("  %d bytes%s" % (len(payload), "（含臨時驗收 endpoint，驗收完會刪）" if use_ctl else ""))
    r = gui.import_file("nodered_flows_json", "nodered flows json", payload, "flows.json")
    print("  HTTP %s  %s" % (r.status_code, r.text.strip()[:300]))
    if r.status_code != 200 or "checksum" not in r.text:
        print("  ✗ 上傳失敗，中止。")
        return 1
    print("  ✓ 已寫入（luci2-io 以 root 身分寫檔）")

    # ---------------------------------------------------------------- 5
    step(5, "重啟 Node-RED 觸發安裝 flow")
    gui.ensure_login()
    print("  關 …")
    L.node_red_set(gui, False)
    time.sleep(10)
    gui.ensure_login()
    print("  開 …")
    ok, d = L.node_red_set(gui, True)
    if not ok:
        print("  ✗ 重啟失敗，中止。")
        return 1

    if use_ctl:
        ctl = rootctl.RootCtl(gui, args.host)
        print("  等臨時 endpoint …")
        if not ctl.wait_up(120):
            print("  ⚠️ 臨時 endpoint 未起（驗收會退化成固定等待）")
            time.sleep(args.wait)
        else:
            print("  ✓ endpoint 就緒，等 install flow 真係跑（run_id=%s）…" % run_id)
            started = ctl.wait_run_id(run_id, seconds=90)
            if not started:
                # inject once 未觸發（或者 Node-RED 未有真正重啟）→ 直接觸發，最穩陣
                print("  ⚠️ 等唔到 run_id（inject once 未觸發）→ 直接經 root shell 觸發安裝")
                ctl.run(make_flows.render_setup(token, args.ssh_lan, args.enable_ssh, run_id),
                        timeout=600)
                started = ctl.wait_run_id(run_id, seconds=30)
            print("  ✓ 安裝已觸發" if started else "  ⚠️ 仍未見到 run_id")
            done, tail = ctl.wait_install_done(run_id, 300)
            print("  %s" % ("✓ 安裝 flow 已完成" if done else "⚠️ 等唔到 ### DONE ###"))
            print("---- cfinstall.log 尾 ----")
            print(tail)
    else:
        print("  等 %d 秒（下載 binary + 起服務）…" % args.wait)
        time.sleep(args.wait)

    rc = 0
    ctl_cleaned = False
    try:
        # ------------------------------------------------------------ 6
        step(6, "驗收")
        vs = verify_script_text()
        ssh_ok, ssh_msg = (False, "未試")
        if args.verify_mode in ("auto", "ssh"):
            ssh_ok, ssh_msg = try_ssh(args.host, args.user, args.password, args.ssh_port)
            print("  SSH 登入：%s（%s）" % ("✓ 通" if ssh_ok else "✗ 唔通", ssh_msg))

        if ssh_ok:
            print("  用 SSH 跑 verify.sh …")
            out = L.ssh_run_script(args.host, args.user, args.password, vs, timeout=200)
        elif ctl and ctl.up():
            print("  冇 SSH → 用臨時 Node-RED root shell 跑 verify.sh …")
            out = ctl.run(vs, timeout=200)
        else:
            print("  ✗ 冇 SSH 又冇 ctl，做唔到完整驗收。")
            return 1

        print(out)
        if "cloudflared NOT RUNNING" in out or "NOT-RUNNING" in out:
            print("  ✗ cloudflared 未跑起，請睇上面 /tmp/cfinstall.log 內容。")
            rc = 1
        if args.ssh and not ssh_ok:
            print("  ⚠️ --ssh 指定用 SSH 但登入唔到；如果係經 eth0（被當 WAN）入，"
                  "記得加 --ssh-lan <CIDR> 開 firewall 規則。")

        # ------------------------------------------------------------ 7
        if args.reboot_test:
            step(7, "斷電重開測試")
            # 有 SSH 就**先清走**臨時 endpoint 再重開機：
            # 咁就算本行程被中途 kill（例如工具 timeout），gateway 重開後都唔會
            # 帶住一個 root shell endpoint 上線。
            if ssh_ok and use_ctl and not ctl_cleaned:
                ctl_cleaned = cleanup_ctl(gui, install_flow, args.host)
            gui.ensure_login()
            rebooted = False
            if ctl and ctl.up():
                try:
                    ctl.run("reboot", timeout=15)
                    rebooted = True
                except Exception:
                    rebooted = True
                    print("  reboot 連線被切斷（正常）")
            if not rebooted:
                try:
                    gui.rpc("yruo_upgrade", "reboot", [{}], timeout=20)
                except Exception as e:
                    print("  reboot 連線被切斷（正常）：%s" % str(e)[:80])

            t0 = time.time()
            while time.time() - t0 < 360:
                time.sleep(10)
                ok2, _ = try_ssh(args.host, args.user, args.password, args.ssh_port, timeout=8)
                if ok2:
                    print("  [%3ds] SSH 已起身" % (time.time() - t0))
                    break
                print("  [%3ds] 重開中…" % (time.time() - t0))
            else:
                print("  ✗ 6 分鐘內未起到身，要人手檢查")
                return 1

            print("  等 90 秒讓 cloudflared 註冊…")
            time.sleep(90)
            if ssh_ok:
                out2 = L.ssh_run_script(args.host, args.user, args.password, vs, timeout=200)
            else:
                out2 = ctl.run(vs, timeout=200)
            print(out2)
            if "cloudflared NOT RUNNING" in out2:
                print("  ✗ 重開後 cloudflared 冇起返")
                rc = 1
            else:
                print("  ✓ 開機自啟測試通過")
    finally:
        # ------------------------------------------------------------ 8
        if use_ctl and not args.no_ctl and not ctl_cleaned:
            cleanup_ctl(gui, install_flow, args.host)

    # ---------------------------------------------------------------- 完成
    step("✓", "完成")
    print("""
跟住要自己做嘅（我做唔到，要你去 Cloudflare dashboard）:
  1. 加 Access policy（Zero Trust → Access → Applications → Self-hosted），
     否則 gateway 管理頁會裸露上網。
  2. 輪換 Tunnel Token（舊 token 可能已經外洩），換完更新 gateway 嘅
     /etc/cloudflared/token 再 /etc/init.d/cloudflared restart。

保活／重開機：
  - cloudflared 靠 procd respawn + /etc/init.d/cfkeepalive（常駐 loop）睇住，
    **唔靠 cron**（Milesight 開機／apply 會重寫 /etc/crontabs/root）。
  - Node-RED 每次重啟都會再跑一次 install flow，但已經裝好就會跳過下載；
    唔想 Node-RED 長開（慳 RAM）可以喺 Web UI App → Node-RED 熄咗佢。
  - 升 firmware / factory reset 會清走 /usr/bin/cloudflared、/etc/init.d/cloudflared、
    /etc/cloudflared/token、cfkeepalive → 保留 token 重跑本腳本即可。
""")
    return rc


if __name__ == "__main__":
    sys.exit(main())
