# -*- coding: utf-8 -*-
"""LAN Quick Share / LANクイックシェア

同じ Wi-Fi 内でパソコン・スマホ間のテキストとファイルを送受信するローカルツール。
ログイン不要・クラウド不要・インストール不要でも利用可能。"""
import json
import os
import queue
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
import webui

FAILED = []


def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), "-", name, extra)
    if not cond:
        FAILED.append(name)


tmp = tempfile.mkdtemp(prefix="lanqs_web_")
events = queue.Queue()
web = webui.WebServer("測試パソコン", tmp, events)
web.start()
print("web port:", web.port)
base = "http://127.0.0.1:%d" % web.port

# 1. 首頁
r = requests.get(base + "/", timeout=5)
check("GET / returns HTML", r.status_code == 200 and "LANクイックシェア" in r.text)

# 2. info 探測
info = requests.get(base + "/info", timeout=5).json()
check("GET /info web marker", info.get("type") == "web", str(info))

# 3. 網頁傳テキスト
requests.post(base + "/web/text",
              json={"from": "マイスマホ", "text": "哈囉パソコン！"}, timeout=5)
ev = events.get(timeout=5)
check("web text event", ev["type"] == "recv-text"
      and ev["from"] == "マイスマホ（Web）" and ev["text"] == "哈囉パソコン！", str(ev))

# 4. multipart 上傳
B = "----LANQSTESTBOUNDARY"


def mpart(name, filename, data, ctype=None):
    cd = 'Content-Disposition: form-data; name="%s"' % name
    if filename is not None:
        cd += '; filename="%s"' % filename
    head = cd + "\r\n"
    if ctype:
        head += "Content-Type: %s\r\n" % ctype
    return ("--%s\r\n" % B).encode() + head.encode() + b"\r\n" + data + b"\r\n"


body = (mpart("from", None, "マイスマホ".encode("utf-8"))
        + mpart("files", "照片.jpg", b"\xff\xd8\xff\xd9", "image/jpeg")
        + ("--%s--\r\n" % B).encode())
r = requests.post(base + "/upload", data=body,
                  headers={"Content-Type": "multipart/form-data; boundary=%s" % B},
                  timeout=10)
check("upload returns ok", r.json().get("ok") is True, r.text)
ev = events.get(timeout=5)
check("upload file event", ev["type"] == "recv-file"
      and ev["name"] == "照片.jpg" and ev["size"] == 4
      and ev["from"] == "マイスマホ（Web）", str({k: ev[k] for k in ("type", "name", "size", "from")}))
check("upload file saved", os.path.exists(ev["path"])
      and open(ev["path"], "rb").read() == b"\xff\xd8\xff\xd9")

# 5. SSE 推播（用 raw socket，requests 對無 Content-Length 串流會緩衝）
import socket as _socket

s = _socket.create_connection(("127.0.0.1", web.port), timeout=10)
s.sendall(("GET /events?name=%s&id=abc123 HTTP/1.1\r\n"
           "Host: 127.0.0.1\r\nConnection: close\r\n\r\n"
           % requests.utils.quote("スマホ")).encode())
time.sleep(0.5)
clients = web.list_clients()
check("client registered", len(clients) == 1 and clients[0]["name"] == "スマホ",
      str(clients))
cid = clients[0]["id"] if clients else None

if cid:
    check("push_text ok", web.push_text(cid, "パソコン", "你好，スマホ！") is True)
    src = os.path.join(tmp, "給スマホ.txt")
    with open(src, "wb") as f:
        f.write("這是パソコン傳來的ファイル".encode("utf-8"))
    check("push_file ok", web.push_file(cid, "パソコン", src) is True)

time.sleep(1.0)
raw = s.recv(65536)
s.close()
sse_text = raw.decode("utf-8", "ignore")
check("SSE hello received", "hello" in sse_text, sse_text[:200])
check("SSE text received", "你好，スマホ！" in sse_text, sse_text[:200])
check("SSE file received", "給スマホ.txt" in sse_text and "/download/" in sse_text,
      sse_text[:300])
m = __import__("re").search(r'"url":\s*"([^"]+)"', sse_text)
if m:
    r = requests.get(base + m.group(1), timeout=5)
    check("download file correct", r.content == "這是パソコン傳來的ファイル".encode("utf-8"))
else:
    check("download url found", False)

# 6. 中斷後從清單移除
web.stop()
print()
if FAILED:
    print("FAILED:", FAILED)
    sys.exit(1)
print("ALL WEB TESTS PASSED")
