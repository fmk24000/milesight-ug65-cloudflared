# -*- coding: utf-8 -*-
"""渲染後補丁：修正 code block 被欄寬切走、inline code 拆字、同螢幕邊距唔自適應。

為咗唔手改 render.mjs 產生嘅 CSS（會被下次 render 蓋走），呢一步獨立成一個
可重複執行嘅 post-process：喺 </head> 前注入一段 <style>。

三個修正：
  1. pre 換行（原本 overflow-x:auto，長命令喺欄寬下被右邊切走）
  2. inline code 唔再 break-all（uid=0(root) 會斷成 uid=0(r / oot)）
  3. 螢幕邊距自適應（原本 --margin 固定 80px，手機只剩 215px 內容寬）
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
  /* 補丁 3：螢幕邊距自適應（側邊留白跟螢幕收窄，唔好固定 80px） */
  @media (max-width: 1180px) { :root { --margin: 56px; } }
  @media (max-width: 980px)  { :root { --margin: 40px; } }
  @media (max-width: 820px)  { :root { --margin: 28px; } }
  @media (max-width: 640px)  { :root { --margin: 20px; }
                               .embed-body { padding: 16px; }
                               .embed-body > svg { min-width: 430px; } }
  @media (max-width: 460px)  { :root { --margin: 14px; }
                               .section-header h2 { font-size: 1.5rem; }
                               .section-number { font-size: 40px; } }
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
    print("  已注入補丁（code-fit + 自適應 margin）：%s" % path)
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
