# -*- coding: utf-8 -*-
"""渲染後補丁：修正 code block 被欄寬切走、同 inline code 拆字嘅問題。

為咗唔手改 render.mjs 產生嘅 CSS（會被下次 render 蓋走），呢一步獨立成一個
可重複執行嘅 post-process：喺 </head> 前注入一段 <style>。

兩個修正：
  1. pre 換行（原本 overflow-x:auto，長命令喺 544px 欄寬下被右邊切走，睇唔到又唔知要滾動）
  2. inline code 唔再 break-all（原本 uid=0(root) 會斷成 uid=0(r / oot)）
"""
import io
import sys

MARK = 'id="manual-code-fit"'

CSS = """<style id="manual-code-fit">
  /* 補丁 1：code block 一律換行，長命令唔會被欄寬切走 */
  pre { white-space: pre-wrap; overflow-wrap: anywhere; word-break: normal; }
  pre code { white-space: inherit; }
  /* 補丁 2：inline code 唔好逐個字母斷開，真係唔夠位先斷 */
  code { word-break: normal; overflow-wrap: anywhere; }
</style>"""


def patch(path):
    with io.open(path, encoding="utf-8") as f:
        html = f.read()

    if MARK in html:
        print("  已有補丁，略過：%s" % path)
        return 0

    if "</head>" not in html:
        print("✗ 搵唔到 </head>：%s" % path, file=sys.stderr)
        return 1

    html = html.replace("</head>", CSS + "\n</head>", 1)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print("  已注入 code-fit 補丁：%s" % path)
    return 0


def main():
    targets = sys.argv[1:]
    if not targets:
        print("用法：python patch_style.py <html> [html...]", file=sys.stderr)
        return 2
    rc = 0
    for t in targets:
        rc |= patch(t)
    return rc


if __name__ == "__main__":
    sys.exit(main())
