#!/usr/bin/env python3
"""由 Tunnel Token 生成可上傳的 Node-RED flows.json（安裝 cloudflared 用）。

用法:
    python make_flows.py eyJhIjoi...                      # 產生 flows.json
    python make_flows.py --file tunnel.token -o flows.json
    python make_flows.py --file tunnel.token --enable-ssh --ssh-lan 192.168.68.0/24

產出的 flows.json 上傳到 UG65 後，Node-RED 會以 root 身分執行整段安裝：
  1. 寫 /etc/cloudflared/token (600)
  2. /usr/bin/cloudflared 已經行得就**跳過下載**（慳 SIM 數據），否則下載 arm64 binary
  3. 寫 /etc/init.d/cloudflared (procd, respawn, TUNNEL_TOKEN 環境變數) + enable/start
  4. 裝 procd 常駐保活 /usr/bin/cf-maintain.sh + /etc/init.d/cfkeepalive (START=96)
     —— 唔靠 cron，因為 Milesight 開機／apply 會重寫 /etc/crontabs/root
  5. 可選：開 SSH（ubus ssh_enable + /etc/init.d/sshd）＋ 只准指定 CIDR 入 22
     嘅 firewall 規則（/etc/init.d/cfsshfw START=20）
全部輸出會寫入 gateway 的 /tmp/cfinstall.log（tmpfs，重開機即冇）。
"""
import argparse
import json
import sys

SETUP = r"""
set -x
exec > /tmp/cfinstall.log 2>&1
echo "### RUN __RUN_ID__ ###"
echo "__RUN_ID__" > /tmp/cfinstall.runid
echo "### running as ###"
id
echo "### firmware ###"
grep DISTRIB_ARCH /etc/openwrt_release

mkdir -p /etc/cloudflared
printf '%s' '__TUNNEL_TOKEN__' > /etc/cloudflared/token
chmod 600 /etc/cloudflared/token
echo "token bytes: $(wc -c < /etc/cloudflared/token)"

echo "### cloudflared binary ###"
if /usr/bin/cloudflared --version 2>/dev/null; then
  echo "already-installed: 跳過下載（慳 SIM 數據）"
else
  U=https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
  if wget -O /tmp/cloudflared "$U"; then echo wget-ok
  elif wget --no-check-certificate -O /tmp/cloudflared "$U"; then echo wget-nocheck-ok
  elif curl -L -k -o /tmp/cloudflared "$U"; then echo curl-ok
  else echo "!!! DOWNLOAD FAILED (試手動上傳 binary) !!!"; fi
  chmod 755 /tmp/cloudflared
  /tmp/cloudflared --version || echo "!!! BINARY NOT RUNNABLE (可能架構唔對) !!!"
  if /tmp/cloudflared --version >/dev/null 2>&1; then
    cp /tmp/cloudflared /usr/bin/cloudflared
    chmod 755 /usr/bin/cloudflared
    sync
  fi
fi

echo "### installed ###"
ls -l /usr/bin/cloudflared

echo "### init script ###"
cat > /etc/init.d/cloudflared <<'INITEOF'
#!/bin/sh /etc/rc.common
START=95
STOP=10
USE_PROCD=1

start_service() {
    [ -x /usr/bin/cloudflared ] || return 1
    [ -s /etc/cloudflared/token ] || return 1
    procd_open_instance
    procd_set_param command /usr/bin/cloudflared --no-autoupdate tunnel run
    procd_set_param env TUNNEL_TOKEN="$(cat /etc/cloudflared/token)"
    procd_set_param respawn 3600 5 5
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}
INITEOF
chmod 755 /etc/init.d/cloudflared
/etc/init.d/cloudflared enable
echo "### rc.d ###"
ls -l /etc/rc.d/ | grep cloudflared
/etc/init.d/cloudflared start
sleep 12

echo "### keepalive (procd，唔靠 cron) ###"
cat > /usr/bin/cf-maintain.sh <<'MAINTEOF'
#!/bin/sh
# cloudflared 保活 + （可選）SSH 存取規則。Milesight 會重寫 /etc/crontabs/root，所以用 procd loop。
export LD_LIBRARY_PATH=/usr/lib/iptables
SSH_LAN="__SSH_LAN__"
if [ -n "$SSH_LAN" ]; then
  iptables -C INPUT -p tcp -s "$SSH_LAN" --dport 22 -j ACCEPT 2>/dev/null || \
    iptables -I INPUT 2 -p tcp -s "$SSH_LAN" --dport 22 -j ACCEPT 2>/dev/null
  iptables -C INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || \
    iptables -I INPUT 1 -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null
fi
pgrep -f "cloudflared.*tunnel" >/dev/null || /etc/init.d/cloudflared start
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
echo "### rc.d keepalive ###"
ls -l /etc/rc.d/ | grep -E 'cfkeepalive|cfsshfw'

if [ "__ENABLE_SSH__" = "1" ]; then
  echo "### enable SSH (ubus) ###"
  ubus call yruo_system set '{"base":"general","index":0,"value":{"ssh_enable":1}}'
  ubus call yruo_apply write
  ubus call yruo_apply apply
  /etc/init.d/sshd enable
  /etc/init.d/sshd start
  sleep 3
  netstat -ltn 2>/dev/null | grep ':22 ' || echo "!!! 22 未 listen !!!"
fi

echo "### cron 保活（best-effort；vendor apply 會洗走） ###"
C=/etc/crontabs/root
grep -q 'cloudflared' $C 2>/dev/null || echo '*/5 * * * * pgrep -f "cloudflared.*tunnel" >/dev/null || /etc/init.d/cloudflared start' >> $C
cat $C
/etc/init.d/cron restart 2>/dev/null

echo "### process ###"
ps | grep '[c]loudflared' || echo "*** cloudflared NOT RUNNING ***"
logread 2>/dev/null | grep -i cloudflared | tail -12
echo "### DONE ###"
chmod 644 /tmp/cfinstall.log
""".strip()

FLOW = [
    {"id": "cf_setup", "type": "tab", "label": "cloudflared setup", "disabled": False,
     "info": "由 make_flows.py 產生。inject once=true 代表 Node-RED 一啟動就會執行安裝。"},
    {
        "id": "cf_trig", "type": "inject", "z": "cf_setup",
        "name": "setup once on start",
        "props": [{"p": "payload"}],
        "repeat": "", "crontab": "", "once": True, "onceDelay": 5,
        "topic": "", "payload": "", "payloadType": "date",
        "x": 320, "y": 120, "wires": [["cf_exec"]],
    },
    {
        # ⚠️ useSpawn false + oldrc true 係實測最可靠嘅組合：
        #    oldrc=false 喺 UG65 上試過永遠唔 emit（HTTP response 永遠 timeout）。
        "id": "cf_exec", "type": "exec", "z": "cf_setup",
        "command": SETUP,
        "addpay": False, "append": "", "useSpawn": "false", "timer": "", "oldrc": True,
        "name": "install cloudflared (root)",
        "x": 600, "y": 120, "wires": [["cf_dbg"], ["cf_dbg"], ["cf_dbg"]],
    },
    {
        "id": "cf_dbg", "type": "debug", "z": "cf_setup",
        "name": "output", "active": True, "tosidebar": True, "console": False,
        "tostatus": True, "complete": "payload", "targetType": "msg",
        "statusVal": "", "statusType": "auto",
        "x": 830, "y": 120, "wires": [],
    },
]


def build_flow(token, ssh_lan="", enable_ssh=False, run_id=""):
    """回傳可以直接上傳嘅 flows.json（list）。

    run_id：每次部署用一個新值（例如時間戳）。install flow 會寫落
    /tmp/cfinstall.runid，deploy.py 靠佢分辨「今次真係跑咗」定係讀到舊 log。
    """
    if not run_id:
        import time as _t
        run_id = _t.strftime("%Y%m%d-%H%M%S")
    txt = json.dumps(FLOW)
    txt = txt.replace("__RUN_ID__", run_id)
    txt = txt.replace("__TUNNEL_TOKEN__", token.strip())
    txt = txt.replace("__SSH_LAN__", (ssh_lan or "").strip())
    txt = txt.replace("__ENABLE_SSH__", "1" if enable_ssh else "0")
    return json.loads(txt)


def render_setup(token, ssh_lan="", enable_ssh=False, run_id=""):
    """回傳已填好 placeholder 嘅安裝 shell 腳本（deploy.py 可以經 ctl 直接執行）。"""
    flow = build_flow(token, ssh_lan, enable_ssh, run_id)
    for n in flow:
        if n.get("type") == "exec":
            return n["command"]
    raise RuntimeError("flow 冇 exec node")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("token", nargs="?", help="Tunnel Token")
    ap.add_argument("--file", help="由檔案讀 token")
    ap.add_argument("-o", "--out", default="flows.json")
    ap.add_argument("--ssh-lan", default="", help="只准呢個 CIDR 入 22，例如 192.168.68.0/24")
    ap.add_argument("--enable-ssh", action="store_true", help="順手開 SSH（ubus + sshd）")
    args = ap.parse_args()

    token = args.token
    if args.file:
        token = open(args.file, encoding="utf-8").read().strip()
    if not token:
        ap.error("需要 token（位置參數或 --file）")

    if not token.startswith("eyJ"):
        print("警告：token 通常以 eyJ 開頭，請確認冇複製錯。", file=sys.stderr)

    flow = build_flow(token, args.ssh_lan, args.enable_ssh)
    data = json.dumps(flow, ensure_ascii=False)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(data)
    print("已寫入 %s（%d bytes，token 長度 %d，ssh_lan=%r，enable_ssh=%s）"
          % (args.out, len(data), len(token.strip()), args.ssh_lan, args.enable_ssh))
    return 0


if __name__ == "__main__":
    sys.exit(main())
