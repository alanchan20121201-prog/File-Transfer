# -*- coding: utf-8 -*-
"""網路核心煙霧測試：HTTP 伺服器、文字傳送、大檔案串流、UDP 發現。"""
import hashlib
import json
import os
import queue
import socket
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lan_share as ls

FAILED = []


def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), "-", name, extra)
    if not cond:
        FAILED.append(name)


# ---- 1. HTTP 伺服器 + 文字傳送 -------------------------------------------
tmp = tempfile.mkdtemp(prefix="lanqs_test_")
events = queue.Queue()
server = ls.TransferServer("測試機A", tmp, events)
server.start()
print("server port:", server.port)
check("server started", server.port is not None)

import requests
info = requests.get("http://127.0.0.1:%d/api/info" % server.port, timeout=5).json()
check("GET /api/info", info.get("name") == "測試機A", json.dumps(info, ensure_ascii=False))

ok = ls.send_text("127.0.0.1", server.port, "測試機B", "你好，世界！Hello LAN 🚀")
check("send_text returns ok", ok)
try:
    ev = events.get(timeout=5)
    check("text event received", ev["type"] == "recv-text" and ev["text"] == "你好，世界！Hello LAN 🚀" and ev["from"] == "測試機B", str(ev))
except queue.Empty:
    check("text event received", False, "timeout")

# ---- 2. 大檔案串流傳送（6MB 隨機資料，驗證雜湊一致） -----------------------
# 來源檔放在另一個目錄，避免與接收目錄重疊
src_dir = tempfile.mkdtemp(prefix="lanqs_src_")
src = os.path.join(src_dir, "big 測試檔.bin")
with open(src, "wb") as f:
    f.write(os.urandom(6 * 1024 * 1024))
md5_src = hashlib.md5(open(src, "rb").read()).hexdigest()

prog = []
ok = ls.send_file("127.0.0.1", server.port, "測試機B", src,
                  lambda s, t: prog.append((s, t)))
check("send_file returns ok", ok)
try:
    ev = events.get(timeout=10)
    check("file event received", ev["type"] == "recv-file" and ev["size"] == 6 * 1024 * 1024, str({k: ev[k] for k in ('type', 'name', 'size', 'from')}))
    md5_dst = hashlib.md5(open(ev["path"], "rb").read()).hexdigest()
    check("file bytes identical", md5_src == md5_dst)
    check("filename sanitized+kept", "big" in ev["name"] and ev["name"].endswith(".bin"), ev["name"])
except queue.Empty:
    check("file event received", False, "timeout")
check("progress callback fired", len(prog) > 0 and prog[-1][0] == prog[-1][1], "callbacks=%d" % len(prog))

# ---- 3. 重複檔名自動改名 ---------------------------------------------------
ok2 = ls.send_file("127.0.0.1", server.port, "測試機B", src)
ev2 = events.get(timeout=10)
check("duplicate renamed", "(1)" in ev2["name"], ev2["name"])

# ---- 4. UDP 發現（同機廣播測試） -------------------------------------------
disc = ls.Discovery("測試機A", server.port)
disc.start()
time.sleep(4.5)   # 等一輪以上廣播
peers = disc.get_peers()
# 同機廣播在部分系統上收不到自己的封包（這是正常的），但只要 listener 綁定成功、不噴錯即通過
print("peers seen (self excluded):", peers)
check("discovery listener running without crash", isinstance(peers, list))

# 模擬另一台裝置手動廣播，驗證 listener 真的收得到
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
msg = json.dumps({"app": ls.APP_ID, "name": "模擬裝置", "port": 49999}).encode()
for addr in ls.broadcast_addresses():
    s.sendto(msg, (addr, ls.DISCOVERY_PORT))
time.sleep(2)
peers = disc.get_peers()
check("discovery receives external announce", any(p["name"] == "模擬裝置" for p in peers), str(peers))

disc.stop()
server.stop()
print()
if FAILED:
    print("FAILED:", FAILED)
    sys.exit(1)
print("ALL TESTS PASSED")
