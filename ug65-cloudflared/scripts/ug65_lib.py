#!/usr/bin/env python3
"""UG65 部署工具庫 — Web GUI API + SSH。完全自足，唔依賴 workspace 其他檔案。

實測於 UG65 firmware 60.0.0.49-r3。
"""
import base64
import json
import os
import re
import time

import requests
import urllib3

urllib3.disable_warnings()

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    _HAVE_CRYPTO = True
except Exception:  # pragma: no cover
    _HAVE_CRYPTO = False

AES_KEY = b"1111111111111111"
AES_IV = b"2222222222222222"


# --------------------------------------------------------------------------- Web GUI

def encrypt_password(pw: str) -> str:
    """Web GUI 嘅密碼加密：AES-128-CBC / PKCS7 / base64（key 同 iv 硬編碼）。"""
    if not _HAVE_CRYPTO:
        raise RuntimeError("需要 cryptography：pip install cryptography")
    pad = padding.PKCS7(128).padder()
    data = pad.update(pw.encode()) + pad.finalize()
    enc = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV)).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


class WebGui:
    """UG65 Web GUI 嘅 /cgi RPC 同 /cgi-bin/file-import。"""

    def __init__(self, host, user="admin", password="", scheme="https"):
        self.base = "%s://%s" % (scheme, host)
        self.user = user
        self.password = password
        self.s = requests.Session()
        self.s.verify = False
        self.s.headers.update({"User-Agent": "Mozilla/5.0",
                               "X-Requested-With": "XMLHttpRequest"})

    # --- 認證 ---
    def login(self, verbose=True):
        r = self.s.post(self.base + "/cgi", timeout=25, json={
            "id": "1", "execute": 1, "core": "user", "function": "login",
            "values": [{"username": self.user,
                        "password": encrypt_password(self.password),
                        "base": "web_login"}],
        })
        try:
            j = r.json()
        except Exception:
            raise RuntimeError("login 回應唔係 JSON：%s" % r.text[:200])
        if j.get("status") != 0:
            raise RuntimeError("login 失敗：%s" % json.dumps(j, ensure_ascii=False)[:300])
        res = j["result"][0]
        # fw 60.0.0.48-r3 唔會喺 result 回 td，只喺 Set-Cookie 回（實測 2026-10-05）
        # 注意：cookies jar 可能有多個同名 td（requests 會 raise CookieConflictError），
        # 所以要自己 iterate，唔可以用 cookies.get("td")。
        self.td = res.get("td") or next(
            (c.value for c in list(self.s.cookies) if c.name == "td"), None)
        if self.td and not any(c.name == "td" for c in list(self.s.cookies)):
            self.s.cookies.set("td", self.td)
        if verbose:
            print("  登入成功  td=%s  model=%s  fw=%s" % (self.td, j.get("model"), j.get("rtver")))
        return j

    def is_logged_in(self):
        try:
            r = self.rpc("yruo_system", "get", [{"base": "general"}])
            j = r.json()
            return j.get("status") == 0 and "result" in j and -32001 not in json.dumps(j)
        except Exception:
            return False

    def ensure_login(self):
        if not getattr(self, "td", None) or not self.is_logged_in():
            self.login()

    # --- RPC ---
    def rpc(self, core, func, values, timeout=40):
        return self.s.post(self.base + "/cgi", timeout=timeout, json={
            "id": "1", "execute": 1, "core": core, "function": func, "values": values,
        })

    # --- 檔案上傳（luci2-io，以 root 執行）---
    def import_file(self, filetype, description, content: bytes, filename="upload.bin"):
        self.ensure_login()
        r = self.s.post(self.base + "/cgi-bin/file-import", timeout=300,
                        data={"size": str(len(content)),
                              "filename": "type=%s&file=%s" % (filetype, description),
                              "sessionid": self.td},
                        files={"file": (filename, content, "application/octet-stream")})
        return r


# --------------------------------------------------------------------------- Node-RED 控制

NODE_RED_GET = ("yruo_loragw", "get", [{"base": "node_red"}])


def node_red_enabled(gui):
    try:
        r = gui.rpc(*NODE_RED_GET)
        return r.json()["result"][0]["get"][0]["value"]["enable"]
    except Exception:
        return None


def node_red_set(gui, enable, retries=3):
    """開/關 Node-RED。開嘅時候 gateway 會 reload nginx，連線可能斷，係正常。"""
    payload = {"type": "node_red", "index": 0, "base": "node_red",
               "value": {"enable": bool(enable), "ssl_enable": True}}
    last = ""
    for i in range(retries):
        try:
            r = gui.rpc("yruo_loragw", "set", [payload], timeout=30)
            last = r.text[:200]
            if '"status":0' in r.text and "error" not in r.text:
                return True, last
        except Exception as e:
            last = "connection dropped: %s" % e
            time.sleep(3)
        time.sleep(2)
    return False, last


def node_red_api_up(host, timeout=5):
    """Node-RED editor 起咗未（admin root 係 /node-red）。"""
    try:
        r = requests.get("https://%s:1880/node-red/" % host, timeout=timeout, verify=False)
        return r.status_code == 200
    except Exception:
        return False


# --------------------------------------------------------------------------- SSH

def ssh_run(host, user, password, command, timeout=60, port=22):
    """跑一條命令，回 (stdout+stderr, exit_code)。"""
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(hostname=host, port=port, username=user, password=password,
              look_for_keys=False, allow_agent=False,
              timeout=15, banner_timeout=15, auth_timeout=15)
    _, out, err = c.exec_command(command, timeout=timeout)
    text = out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")
    rc = out.channel.recv_exit_status()
    c.close()
    return text, rc


def ssh_run_script(host, user, password, script_text, timeout=180):
    """經 stdin 餵一段 sh script 入去跑（唔需要 sftp）。"""
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(hostname=host, username=user, password=password,
              look_for_keys=False, allow_agent=False,
              timeout=15, banner_timeout=15, auth_timeout=15)
    stdin, out, err = c.exec_command("sh -s", timeout=timeout)
    stdin.write(script_text)
    stdin.flush()
    stdin.channel.shutdown_write()
    text = out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")
    c.close()
    return text
