#!/bin/sh
# UG65 cloudflared 驗收腳本 — 由 deploy.py 自動餵入 gateway 執行
# （經 SSH：sh -s；或者經 rootctl 臨時 root shell）。
# 亦可以人手：ssh admin@<ip> 之後貼入去。

echo "--- 0. 身分 ---"
id
echo

echo "--- 1. 安裝 log（最後 40 行）---"
tail -n 40 /tmp/cfinstall.log 2>&1 || echo "（冇 /tmp/cfinstall.log —— tmpfs，重開機就冇；或者 flow 未跑）"
echo

echo "--- 2. cloudflared process ---"
ps 2>/dev/null | grep '[c]loudflared' || echo "*** cloudflared NOT RUNNING ***"
echo

echo "--- 3. 檔案 ---"
ls -l /usr/bin/cloudflared /etc/init.d/cloudflared /etc/cloudflared/token 2>&1
echo

echo "--- 4. 開機連結 ---"
ls -l /etc/rc.d/ 2>/dev/null | grep cloudflared || echo "*** 冇 rc.d link（唔會開機自啟）***"
echo

echo "--- 5. cron 保活（best-effort）---"
cat /etc/crontabs/root 2>&1
echo "⚠️ Milesight 開機／yruo_apply 會重寫呢個檔，所以唔可以只靠 cron。"
echo

echo "--- 5b. procd 常駐保活（真正嘅保活）---"
ls -l /usr/bin/cf-maintain.sh /usr/bin/cf-maintain-loop.sh /etc/init.d/cfkeepalive 2>&1
ls -l /etc/rc.d/ 2>/dev/null | grep -E 'cfkeepalive|cfsshfw'
ps 2>/dev/null | grep '[c]f-maintain' || echo "*** cf-maintain loop 冇跑 ***"
echo

echo "--- 5c. SSH（如已開）---"
netstat -ltn 2>/dev/null | grep -E ':22 ' || echo "（22 未 listen —— 即 SSH 未開）"
if [ "$(id -u)" = "0" ]; then
  echo "root shell：可以睇 firewall 規則"
  export LD_LIBRARY_PATH=/usr/lib/iptables
  iptables -S INPUT 2>&1 | head -8
else
  echo "（admin 身分睇唔到 iptables —— 需要 root，例如經 rootctl 跑就會見到）"
fi
echo

echo "--- 6. Tunnel 註冊紀錄 ---"
grep -i cloudflared /etc/urlog/system.log 2>/dev/null \
  | grep -E 'Registered tunnel connection|Starting tunnel|Updated to new configuration' \
  | tail -12 || echo "（system.log 冇 cloudflared 紀錄）"
echo

echo "--- 7. ingress 設定 ---"
grep -o 'ingress[^]]*]' /etc/urlog/system.log 2>/dev/null | tail -1
echo

echo "--- 8. 資源 ---"
uptime
free -m 2>/dev/null
echo

echo "--- 9. 版本 ---"
/usr/bin/cloudflared --version 2>&1
echo

echo "--- 10. Node-RED ---"
ps 2>/dev/null | grep '[n]ode-red' || echo "node-red 冇跑"
echo

echo "=== 驗收完 ==="
