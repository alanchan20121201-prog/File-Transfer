# -*- coding: utf-8 -*-
"""英文版煙霧測試：埠 8181、英文網頁、上傳與文字事件、transfer server。"""
import ast
import os
import queue
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED = []


def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), "-", name, extra)
    if not cond:
        FAILED.append(name)


# 1. 語法檢查
for fn in ["lan_share.py", "webui.py", "installer.py"]:
    src = open(fn, "r", encoding="utf-8").read()
    try:
        ast.parse(src)
        check("syntax %s" % fn, True)
    except SyntaxError as e:
        check("syntax %s" % fn, False, str(e))

import requests
import webui
import lan_share

check("web port is 8181", webui.WEB_PORT_START == 8181, str(webui.WEB_PORT_START))

tmp = tempfile.mkdtemp(prefix="lanqs_en_")
events = queue.Queue()
web = webui.WebServer("Test PC", tmp, events)
web.start()
check("web server started on 8181", web.port == 8181, str(web.port))
base = "http://127.0.0.1:%d" % web.port

r = requests.get(base + "/", timeout=5)
check("English HTML", "LAN Quick Share" in r.text and "區網快傳" not in r.text)
check("English labels", "Send Text" in r.text and "Upload Files" in r.text)

info = requests.get(base + "/info", timeout=5).json()
check("web /info", info.get("type") == "web" and info.get("name") == "Test PC")

requests.post(base + "/web/text", json={"from": "My Phone", "text": "Hello PC!"}, timeout=5)
ev = events.get(timeout=5)
check("web text event", ev["type"] == "recv-text"
      and ev["from"] == "My Phone (web)" and ev["text"] == "Hello PC!", str(ev))

# multipart 上傳
B = "----ENTESTBOUNDARY"


def mpart(name, filename, data, ctype=None):
    cd = 'Content-Disposition: form-data; name="%s"' % name
    if filename is not None:
        cd += '; filename="%s"' % filename
    head = cd + "\r\n"
    if ctype:
        head += "Content-Type: %s\r\n" % ctype
    return ("--%s\r\n" % B).encode() + head.encode() + b"\r\n" + data + b"\r\n"


body = (mpart("from", None, "My Phone".encode())
        + mpart("files", "photo.jpg", b"\xff\xd8\xff\xd9", "image/jpeg")
        + ("--%s--\r\n" % B).encode())
r = requests.post(base + "/upload", data=body,
                  headers={"Content-Type": "multipart/form-data; boundary=%s" % B}, timeout=10)
check("upload ok", r.json().get("ok") is True, r.text)
ev = events.get(timeout=5)
check("upload event", ev["type"] == "recv-file" and ev["name"] == "photo.jpg"
      and ev["from"] == "My Phone (web)")

# transfer server
server = lan_share.TransferServer("Test PC", tmp, events)
server.start()
info = requests.get("http://127.0.0.1:%d/api/info" % server.port, timeout=5).json()
check("transfer /api/info", info.get("name") == "Test PC")

# 送文字（app 協定）
ok = lan_share.send_text("127.0.0.1", server.port, "Peer PC", "hi")
check("send_text", ok)
ev = events.get(timeout=5)
check("app text event", ev["type"] == "recv-text" and ev["from"] == "Peer PC")

web.stop()
server.stop()
print()
if FAILED:
    print("FAILED:", FAILED)
    sys.exit(1)
print("ALL ENGLISH SMOKE TESTS PASSED")
