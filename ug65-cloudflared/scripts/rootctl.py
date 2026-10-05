#!/usr/bin/env python3
"""冇 SSH 時嘅驗收工具：用 Node-RED 開一個**臨時** root shell endpoint。

原理（全部實測過）：
  - Node-RED 由 procd 以 root 啟動 → exec node 出嚟嘅 shell 就係 root。
  - http-in 嘅 root 係 **/node-red**（唔係 /）→ 打 https://IP:1880/node-red/cfrun
  - exec node 一定要 **oldrc: true**；oldrc=false 喺 UG65 上實測永遠唔 emit。
  - 唔可以用 addpay 傳動態命令（stdout 空），所以改成：
        GET/POST /cfput?c=<base64> → function 解 base64 → file out 寫 /tmp/cfcmd.sh
        GET      /cfrun            → exec "sh /tmp/cfcmd.sh" → http response

⚠️ 驗收完一定要 remove() 還原 flows.json，唔好擺住個 root shell 喺度。
"""
import base64
import json
import time

import requests
import urllib3

urllib3.disable_warnings()

DECODE_FN = (
    "var p = (msg.req && msg.req.query && msg.req.query.c) ? msg.req.query.c : msg.payload;\n"
    "if (typeof p !== 'string') { p = JSON.stringify(p); }\n"
    "msg.payload = Buffer.from(p, 'base64').toString('utf8');\n"
    "return msg;"
)

PING = "pong"


def ctl_nodes():
    """回傳要 merge 入 flows 嘅臨時節點。"""
    return [
        {"id": "cfctl_tab", "type": "tab", "label": "tmp verify (root)", "disabled": False,
         "info": "rootctl.py 臨時 endpoint，驗收完會刪。"},
        {"id": "cfctl_resp", "type": "http response", "z": "cfctl_tab", "name": "",
         "statusCode": "", "headers": {"Content-Type": "text/plain; charset=utf-8"},
         "x": 980, "y": 60, "wires": []},

        {"id": "cfctl_ping_in", "type": "http in", "z": "cfctl_tab", "name": "cfping",
         "url": "/cfping", "method": "get", "upload": False, "swaggerDoc": "",
         "x": 220, "y": 60, "wires": [["cfctl_ping_fn"]]},
        {"id": "cfctl_ping_fn", "type": "function", "z": "cfctl_tab", "name": "pong",
         "func": "msg.payload='%s'; return msg;" % PING, "outputs": 1, "noerr": 0,
         "initialize": "", "finalize": "", "libs": [], "x": 420, "y": 60,
         "wires": [["cfctl_resp"]]},

        {"id": "cfctl_put_in_p", "type": "http in", "z": "cfctl_tab", "name": "cfput",
         "url": "/cfput", "method": "post", "upload": False, "swaggerDoc": "",
         "x": 220, "y": 160, "wires": [["cfctl_put_fn"]]},
        {"id": "cfctl_put_in_g", "type": "http in", "z": "cfctl_tab", "name": "cfput",
         "url": "/cfput", "method": "get", "upload": False, "swaggerDoc": "",
         "x": 220, "y": 240, "wires": [["cfctl_put_fn"]]},
        {"id": "cfctl_put_fn", "type": "function", "z": "cfctl_tab", "name": "b64 -> text",
         "func": DECODE_FN, "outputs": 1, "noerr": 0, "initialize": "", "finalize": "",
         "libs": [], "x": 430, "y": 200, "wires": [["cfctl_put_out"]]},
        {"id": "cfctl_put_out", "type": "file", "z": "cfctl_tab", "name": "write /tmp/cfcmd.sh",
         "filename": "/tmp/cfcmd.sh", "appendNewline": True, "createDir": False,
         "overwriteFile": "true", "encoding": "none", "x": 640, "y": 200,
         "wires": [["cfctl_put_ok"]]},
        {"id": "cfctl_put_ok", "type": "function", "z": "cfctl_tab", "name": "ok",
         "func": "msg.payload = 'written ' + msg.payload.length + ' bytes'; return msg;",
         "outputs": 1, "noerr": 0, "initialize": "", "finalize": "", "libs": [],
         "x": 820, "y": 200, "wires": [["cfctl_resp"]]},

        {"id": "cfctl_run_in", "type": "http in", "z": "cfctl_tab", "name": "cfrun",
         "url": "/cfrun", "method": "get", "upload": False, "swaggerDoc": "",
         "x": 220, "y": 340, "wires": [["cfctl_run_exec"]]},
        {"id": "cfctl_run_exec", "type": "exec", "z": "cfctl_tab", "name": "sh /tmp/cfcmd.sh",
         "command": "sh /tmp/cfcmd.sh", "addpay": False, "append": "",
         "useSpawn": "false", "timer": "600", "oldrc": True, "x": 480, "y": 340,
         "wires": [["cfctl_resp"], ["cfctl_err_fn"], ["cfctl_rc_fn"]]},
        {"id": "cfctl_err_fn", "type": "function", "z": "cfctl_tab", "name": "stderr",
         "func": "msg.payload = '\\n[stderr] ' + msg.payload; return msg;",
         "outputs": 1, "noerr": 0, "initialize": "", "finalize": "", "libs": [],
         "x": 690, "y": 420, "wires": [["cfctl_resp"]]},
        {"id": "cfctl_rc_fn", "type": "function", "z": "cfctl_tab", "name": "rc",
         "func": "msg.payload = '\\n[rc] ' + JSON.stringify(msg.payload); return msg;",
         "outputs": 1, "noerr": 0, "initialize": "", "finalize": "", "libs": [],
         "x": 670, "y": 500, "wires": [["cfctl_resp"]]},
    ]


class RootCtl:
    """臨時 root shell。gui = ug65_lib.WebGui；host = gateway IP。"""

    def __init__(self, gui, host, http_root="node-red", port=1880):
        self.gui = gui
        self.host = host
        self.base = "https://%s:%d/%s" % (host, port, http_root.strip("/"))

    # --- 內部 ---
    def _get(self, path, timeout=60):
        return requests.get(self.base + path, timeout=timeout, verify=False)

    def up(self, timeout=15):
        """臨時 endpoint 起咗未。"""
        try:
            return self._get("/cfping", timeout=timeout).status_code == 200
        except Exception:
            return False

    def wait_up(self, seconds=120):
        t0 = time.time()
        while time.time() - t0 < seconds:
            if self.up():
                return True
            time.sleep(5)
        return False

    def put(self, script, timeout=60):
        """把 shell 腳本寫入 gateway 嘅 /tmp/cfcmd.sh。"""
        b64 = base64.b64encode(script.encode()).decode()
        r = requests.post(self.base + "/cfput", data=b64, timeout=timeout, verify=False)
        return r.text

    def run(self, script, timeout=300):
        """以 root 執行 shell 腳本，回 stdout+stderr（合併）。"""
        self.put(script)
        try:
            return self._get("/cfrun", timeout=timeout).text
        except Exception as e:
            return "ERR: %s" % e

    def tail_log(self, lines=3):
        return self.run("tail -n %d /tmp/cfinstall.log 2>/dev/null" % lines)

    def read(self, path, timeout=60):
        """讀 gateway 上一個檔（用 cat）。"""
        return self.run("cat %s 2>/dev/null" % path, timeout=timeout)

    def run_id(self):
        return self.read("/tmp/cfinstall.runid").strip()

    def wait_run_id(self, run_id, seconds=90, every=5):
        """等今次 deploy 嘅 install flow 真係跑咗（唔會被舊 log 呃到）。"""
        t0 = time.time()
        while time.time() - t0 < seconds:
            try:
                if self.run_id() == run_id:
                    return True
            except Exception:
                pass
            time.sleep(every)
        return False

    def wait_install_done(self, run_id=None, seconds=300, every=10):
        """等 /tmp/cfinstall.log 出 ### DONE ###（比死等 100 秒準）。

        run_id：只認今次 run（見 make_flows.build_flow）。run id 寫喺
        /tmp/cfinstall.runid，唔可以用 log tail 判斷（run id 喺 log 最頂）。
        """
        t0 = time.time()
        while time.time() - t0 < seconds:
            out = self.tail_log(8)
            if "### DONE ###" in out:
                if run_id:
                    try:
                        if self.run_id() != run_id:
                            time.sleep(every)
                            continue      # DONE 係上一次 run 留低嘅
                    except Exception:
                        pass
                return True, out
            if "DOWNLOAD FAILED" in out:
                return False, out
            time.sleep(every)
        return False, self.tail_log(20)
