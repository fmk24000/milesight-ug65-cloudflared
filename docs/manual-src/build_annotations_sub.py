#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由 sub-manual.md 自動計升格行號 → annotations-sub.json（連 3 張內聯 SVG）。"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD = os.path.join(HERE, "sub-manual.md")
OUT = os.path.join(HERE, "annotations-sub.json")


def load():
    with io.open(MD, encoding="utf-8") as f:
        return f.read().split("\n")


def find(lines, needle, nth=1):
    hits = [i + 1 for i, l in enumerate(lines) if needle in l]
    if len(hits) < nth:
        raise SystemExit("✗ anchor 搵唔到：%r" % needle)
    return hits[nth - 1]


def svg(name):
    with io.open(os.path.join(HERE, "svgs", name), encoding="utf-8") as f:
        return f.read().strip()


def block(lines, first_anchor, stop_re):
    """由 anchor 開始收集連續項目（用嚟做 checklist／process）。"""
    start = find(lines, first_anchor) - 1
    out = []
    i = start
    while i < len(lines) and re.match(stop_re, lines[i]):
        out.append(i + 1)
        i += 1
    if not out:
        raise SystemExit("✗ block 收集唔到：%r" % first_anchor)
    return out


def main():
    L = load()

    defs = [
        find(L, "> **Gateway Fleet**："),
        find(L, "> **cloudflared access tcp**："),
        find(L, "> **loopback 判別**："),
    ]

    ask_lines = block(L, "- ❌ 唔准當 `username` 一定係 `admin`", r"^- ")
    proc_lines = block(L, "1. 開 Node-RED（", r"^\d+\. ")
    acc_lines = block(L, "- 子機 `10.99.99.1:28883` LISTEN", r"^- ")

    upgrades = [
        {"type": "def-card", "lines": defs},
        {"type": "checklist", "lines": ask_lines},
        {
            "type": "embed",
            "line": find(L, "## 原理：一個填錯就靜靜死嘅位"),
            "label": "圖 1 · Part A 同 Part B 嘅分別",
            "svg": svg("sub-part-ab.svg"),
            "caption": "先確定目的，再決定要唔要子機自己嘅 token；兩件事可以並存。",
        },
        {
            "type": "embed",
            "line": find(L, "## Part A：主機側前置（做一次）"),
            "label": "圖 2 · loopback 陷阱",
            "svg": svg("sub-loopback-trap.svg"),
            "caption": "同一個欄位填唔同值，結果一個 connected: false、一個 connected: true，而且冇任何 error。",
        },
        {
            "type": "embed",
            "line": find(L, "## Part A：部署"),
            "label": "圖 3 · Fleet 資料流",
            "svg": svg("sub-fleet-topology.svg"),
            "caption": "子機主動連出去，借主機嘅 hostname 出街；主機 tunnel 接住再送落本機 mosquitto。",
        },
        {
            "type": "process",
            "lines": proc_lines,
            "items": [
                {"title": "開 Node-RED", "desc": "一個 RPC，唔需要㩒界面"},
                {"title": "攞 root shell", "desc": "上傳臨時 ctl flow，重啟 Node-RED"},
                {"title": "推 binary", "desc": "本機開 http.server，子機 wget + 驗 md5"},
                {"title": "寫 cfagent", "desc": "procd、START=95、釘 10.99.99.1 落 lo"},
                {"title": "改 destination", "desc": "serv_addr / port / enabled → apply"},
                {"title": "清 root shell", "desc": "上傳空 flow，Node-RED 熄返"},
                {"title": "存底", "desc": "snapshots/conf_IP.json（只第一次）"},
            ],
        },
        {
            "type": "aside",
            "line": find(L, "## Part A：重開機驗證（建議每台做一次）"),
            "title": "唔好只睇 domain 返 200",
            "text": "有舊安裝嘅機，外網 200 唔代表今次部署成功。一定要睇 destination 嘅 connected、"
                    "子機 toml 嘅 server=，同主機 Fleet 嘅 lastSeen 有冇更新。",
        },
        {
            "type": "highlight",
            "line": find(L, "**硬規矩：token 要問用戶攞，而且一定係子機自己嗰條。**"),
            "label": "記住",
        },
        {
            "type": "aside",
            "line": find(L, "## Part B：執行"),
            "title": "先驗 token，再碰機",
            "text": "新 token 一到，先跑 --check-token。佢唔會連任何 gateway，純粹驗格式同撞唔撞主機，"
                    "錯就 exit 2，唔會改到部機。",
        },
        {
            "type": "summary-card",
            "line": find(L, "## 排查"),
            "label": "三十秒自我檢查",
            "items": [
                {"title": "listener", "desc": "10.99.99.1:28883 有冇 LISTEN"},
                {"title": "toml", "desc": "server= 係 ssl://10.99.99.1:28883"},
                {"title": "connected", "desc": "destination id=1 connected=1"},
                {"title": "Fleet", "desc": "主機見到 connected=True + 新 lastSeen"},
                {"title": "root shell", "desc": "/node-red/cfrun 回 404/502"},
                {"title": "兩個進程", "desc": "做埋 Part B 就要兩個 cloudflared 並存"},
            ],
        },
        {"type": "checklist", "lines": acc_lines},
    ]

    doc = {
        "meta": {
            "title": "Milesight UG65 子機接入 Gateway Fleet",
            "author": "Louis FU",
            "date": "2026.11.24",
            "reading_time": "約 20 分鐘（跟住做約 30 分鐘／台）",
            "tags": ["Milesight", "UG65", "Gateway Fleet", "MQTT", "Cloudflare Tunnel"],
            "subtitle": "子機冇固定 IP、行 SIM 都可以上主機 —— Part A 做 Fleet agent，Part B 子機自己一條 tunnel",
            "lang": None,
        },
        "upgrades": upgrades,
    }

    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    print("已寫入 %s" % OUT)
    print("  def-card lines %s" % defs)
    print("  checklist 問齊 %d 行 / 驗收 %d 行" % (len(ask_lines), len(acc_lines)))
    print("  process lines %s–%s" % (proc_lines[0], proc_lines[-1]))
    print("  embed ×3 / aside ×2 / highlight ×1 / summary-card ×1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
