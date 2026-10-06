#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""唔連子機，直接睇主機 Gateway Fleet 全表（connected / lastSeen）。

用法：
  python fleet_status.py --main 192.168.68.106 --password '<ADMIN_PASSWORD>'
"""
import argparse
import os
import sys

import urllib3

urllib3.disable_warnings()

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ug65_lib as L  # noqa: E402
from sub_install import fleet_api  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", required=True)
    ap.add_argument("--password", required=True)
    args = ap.parse_args()

    s, tok = fleet_api(args.main, args.password)
    if not tok:
        print("拎唔到 JWT")
        return
    r = s.get("https://%s/api/gateways" % args.main,
              params={"limit": 9999, "offset": 0, "organizationID": 1},
              headers={"Authorization": "Bearer " + tok}, timeout=20)
    j = r.json()
    print("localNS=%s totalCount=%s" % (j.get("localNS"), j.get("totalCount")))
    for gw in j.get("result", []):
        print("  %-14s %-20s connected=%-6s firstSeen=%-20s lastSeen=%s" %
              (gw["mac"], gw.get("name"), gw.get("connected"),
               gw.get("firstSeenAt"), gw.get("lastSeenAt")))


if __name__ == "__main__":
    main()
