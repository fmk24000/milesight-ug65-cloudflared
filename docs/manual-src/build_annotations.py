#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由 manual.md 自動計出升格行號，生成 annotations.json（連內聯 SVG）。

手數行號極易錯，所以所有 line 都用「唯一字串」定位，搵唔到就即刻報錯。
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MD = os.path.join(HERE, "manual.md")
OUT = os.path.join(HERE, "annotations.json")


def load_lines(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read().split("\n")


def find(lines, needle, occurrence=1):
    """回傳 1-based 行號；搵唔到或者次數唔符就 raise。"""
    hits = [i + 1 for i, l in enumerate(lines) if needle in l]
    if len(hits) < occurrence:
        raise SystemExit("✗ 搵唔到 anchor（第 %d 次）：%r" % (occurrence, needle))
    return hits[occurrence - 1]


def svg(name):
    with io.open(os.path.join(HERE, "svgs", name), encoding="utf-8") as f:
        return f.read().strip()


def main():
    lines = load_lines(MD)

    # ---- 三條連續 blockquote → 術語卡
    def_lines = [
        find(lines, "> **Tunnel Token**"),
        find(lines, "> **procd**"),
        find(lines, "> **臨時 root shell**"),
    ]

    # ---- 五步流程（有序列表 1..5）
    proc_lines = [
        find(lines, "1. **Cloudflare 開 tunnel**"),
        find(lines, "2. **API 登入 gateway**"),
        find(lines, "3. **開 Node-RED**"),
        find(lines, "4. **上傳 flow 觸發安裝**"),
        find(lines, "5. **驗收 + 斷電重開測試**"),
    ]

    # ---- 「全部唔需要」清單
    chk_first = find(lines, "- 唔需要 root 密碼")
    chk_lines = []
    i = chk_first - 1
    while i < len(lines) and lines[i].startswith("- "):
        chk_lines.append(i + 1)
        i += 1
    if len(chk_lines) < 3:
        raise SystemExit("✗ checklist 只搵到 %d 行" % len(chk_lines))

    # ---- 結尾驗收清單
    acc_first = find(lines, "- `/usr/bin/cloudflared --version` 有 output")
    acc_lines = []
    i = acc_first - 1
    while i < len(lines) and lines[i].startswith("- "):
        acc_lines.append(i + 1)
        i += 1

    upgrades = [
        {"type": "def-card", "lines": def_lines},
        {
            "type": "process",
            "lines": proc_lines,
            "items": [
                {"title": "Cloudflare 開 tunnel", "desc": "攞 token，加 Public Hostname 同 Access policy"},
                {"title": "API 登入 gateway", "desc": "AES 加密密碼換 session cookie"},
                {"title": "開 Node-RED", "desc": "一個 /cgi RPC，唔需要㩒界面"},
                {"title": "上傳 flow 觸發安裝", "desc": "root 執行腳本：寫檔、起 procd、註冊 tunnel"},
                {"title": "驗收 + 斷電重開", "desc": "4 條 quic、rc.d、開機自己起返"},
            ],
        },
        {"type": "checklist", "lines": chk_lines},
        # ---- 圖 1：連線拓撲
        {
            "type": "embed",
            "line": find(lines, "## 原理：三個關鍵事實"),
            "label": "圖 1 · 連線拓撲",
            "svg": svg("overview.svg"),
            "caption": "gateway 自己走出去註冊，所以 SIM 網絡、冇公網 IP 都上得到；入嚟嘅請求行返同一條連線。",
        },
        # ---- 圖 2：提權路線
        {
            "type": "embed",
            "line": find(lines, "## 步驟一：Cloudflare 側準備"),
            "label": "圖 2 · admin 到 root 嘅唯一路線",
            "svg": svg("privilege.svg"),
            "caption": "六條常見提權路全部實測失敗；Node-RED 由 procd 以 root 啟動，所以 exec node 就係 root shell。",
        },
        # ---- aside：真係全人手
        {
            "type": "aside",
            "line": find(lines, "## 步驟四：生成並上傳 flows.json"),
            "title": "唔想用產生器？",
            "text": "可以唔用 make_flows.py，直接把附錄「完整安裝腳本」貼入 exec node 嘅 command，"
                    "再自己砌 inject(once) → exec(oldrc:true) → debug 三個 node。效果完全一樣。",
        },
        # ---- aside：唔好急住開 domain
        {
            "type": "aside",
            "line": find(lines, "## 步驟六：驗收"),
            "title": "唔好即刻開 domain",
            "text": "cloudflared 要 60–70 秒先完成註冊（排喺一堆 LoRaWAN 服務後面）。"
                    "重開機後太早開，見到 502 係正常，唔係部署失敗。",
        },
        # ---- 圖 3：開機時序
        {
            "type": "embed",
            "line": find(lines, "## 步驟七：收尾"),
            "label": "圖 3 · 開機時序實測",
            "svg": svg("boot-timeline.svg"),
            "caption": "同一隻正值係 71 秒通 443、107 秒通 22；cloudflared 期間重試幾次先註冊成功係正常。",
        },
        # ---- highlight
        {
            "type": "highlight",
            "line": find(lines, "**唯一行得通嘅提權路線就係 Node-RED exec node。**"),
            "label": "記住",
        },
        # ---- 排錯速查卡
        {
            "type": "summary-card",
            "line": find(lines, "## 排查：一定會撞到嘅錯誤"),
            "label": "三十秒自我檢查",
            "items": [
                {"title": "token 權限", "desc": "/etc/cloudflared/token 應該係 600 root:root"},
                {"title": "rc.d", "desc": "S95cloudflared 同 S96cfkeepalive 兩個 symlink 都要在"},
                {"title": "4 條 quic", "desc": "system.log 要有 4 條 Registered tunnel connection"},
                {"title": "ps 冇 token", "desc": "見到 cloudflared 但唔應該見到 TUNNEL_TOKEN"},
                {"title": "root shell 已清", "desc": "/node-red/cfrun 應該回 404"},
                {"title": "log 係今次", "desc": "### RUN <run_id> ### 要對得上今次部署"},
            ],
        },
        # ---- 驗收清單
        {"type": "checklist", "lines": acc_lines},
    ]

    doc = {
        "meta": {
            "title": "Milesight UG65 手動安裝 Cloudflare Tunnel",
            "author": "Louis FU",
            "date": "2026.11.24",
            "reading_time": "約 25 分鐘（跟住做約 40 分鐘）",
            "tags": ["Milesight", "UG65", "Cloudflare Tunnel", "LoRaWAN", "OpenWrt"],
            "subtitle": "唔需要 root 密碼、唔拆機、唔刷 firmware —— 由 admin 帳號一路做到開機自動連線",
            "lang": None,
        },
        "upgrades": upgrades,
    }

    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    print("已寫入 %s" % OUT)
    print("  def-card      lines %s" % def_lines)
    print("  process       lines %s–%s" % (proc_lines[0], proc_lines[-1]))
    print("  checklist     %d 行 × 2" % len(chk_lines))
    print("  embed ×3 / aside ×2 / highlight ×1 / summary-card ×1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
