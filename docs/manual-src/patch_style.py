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
  /* 補丁 5：間距用 --k 做倍率（預設 1 = 完全等於原本數值，唔會影響細螢幕）。
     高解析度放大字級時，padding／gap 一齊放大，避免「字大咗但留白冇變」而擠埋。 */
  :root {
    --k: 1;
    --sp-1: calc(2px * var(--k));   --sp-2: calc(4px * var(--k));
    --sp-3: calc(8px * var(--k));   --sp-4: calc(12px * var(--k));
    --sp-5: calc(16px * var(--k));  --sp-6: calc(24px * var(--k));
    --sp-7: calc(32px * var(--k));  --sp-8: calc(40px * var(--k));
    --sp-9: calc(48px * var(--k));  --sp-10: calc(64px * var(--k));
    --sp-11: calc(80px * var(--k)); --sp-12: calc(96px * var(--k));
    --sp-13: calc(160px * var(--k));
  }
  /* 補丁 3：手機 / 平板邊距自適應（原本 --margin 固定 80px，375px 手機只剩 215px 內容） */
  @media (max-width: 1180px) { :root { --margin: 56px; } }
  @media (max-width: 980px)  { :root { --margin: 40px; } }
  @media (max-width: 820px)  { :root { --margin: 28px; } }
  @media (max-width: 640px)  { :root { --margin: 20px; }
                               .embed-body { padding: 16px; }
                               .embed-body > svg { min-width: 430px; } }
  @media (max-width: 460px)  { :root { --margin: 14px; }
                               .section-header h2 { font-size: 1.5rem; }
                               .section-number { font-size: 40px; } }
  /* 補丁 4：2K / 4K 用盡畫面 —— 框架貼邊（margin 收窄、量度 100%），
     再用 body zoom 整體放大。用 zoom 而唔係逐個字級／padding 放大，係因為
     hero、chrome bar 呢啲組件用 px 定尺寸，只放大字級會令佢哋擠爆；
     zoom 會連 px 一齊縮放，比例完全唔變。 */
  @media (min-width: 1500px) { body { zoom: 1.10; }
                               :root { --measure: 100%; --margin: 40px; --media: 100%;
                                       --col: calc(40 * 1.0625rem); } }
  @media (min-width: 1800px) { body { zoom: 1.20; }
                               :root { --measure: 100%; --margin: 40px; --media: 100%;
                                       --col: calc(44 * 1.0625rem); } }
  @media (min-width: 2100px) { body { zoom: 1.30; }
                               :root { --measure: 100%; --margin: 36px; --media: 100%;
                                       --col: calc(48 * 1.0625rem); } }
  @media (min-width: 2560px) { body { zoom: 1.45; }
                               :root { --measure: 100%; --margin: 36px; --media: 100%;
                                       --col: calc(52 * 1.0625rem); } }
  @media (min-width: 3200px) { body { zoom: 1.55; }
                               :root { --measure: 100%; --margin: 36px; --media: 100%;
                                       --col: calc(58 * 1.0625rem); } }
  @media (min-width: 3700px) { body { zoom: 1.60; }
                               :root { --measure: 100%; --margin: 36px; --media: 100%;
                                       --col: calc(62 * 1.0625rem); } }
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
