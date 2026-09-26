# -*- coding: utf-8 -*-
"""
區網快傳 — 手機網頁伺服器
=========================
在 8080 埠（被佔用自動往後找）提供一個給手機瀏覽器使用的網頁：
  - 手機上傳照片 / 檔案 → 存到電腦的接收資料夾
  - 手機傳送文字 → 出現在電腦端傳輸記錄
  - 電腦端可把文字 / 檔案推給正在連線的手機（SSE 推播，檔案變成下載連結）
"""
import json
import os
import queue
import re
import socket as _socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

WEB_PORT_START = 8080
WEB_PORT_END = 8090


class ExclusiveHTTPServer(ThreadingHTTPServer):
    """綁定前設定 SO_EXCLUSIVEADDRUSE，避免 Windows 上兩個實例
    同時成功綁定同一埠（連線去向不可預期）的問題；
    第二個實例會綁定失敗而自動改用下一個埠。
    注意：SO_REUSEADDR 與 SO_EXCLUSIVEADDRUSE 互斥，必須關閉前者。"""

    allow_reuse_address = False

    def server_bind(self):
        try:
            self.socket.setsockopt(_socket.SOL_SOCKET, _socket.SO_EXCLUSIVEADDRUSE, 1)
        except Exception:
            pass
        super().server_bind()


# ---------------------------------------------------------------------------
# 手機網頁（單一 HTML，內嵌 CSS / JS）
# ---------------------------------------------------------------------------

WEB_HTML = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>區網快傳</title>
<style>
 :root{--bg:#eef1f6;--primary:#2563eb;--text:#111827;--muted:#6b7280}
 *{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
 body{font-family:-apple-system,BlinkMacSystemFont,"Microsoft JhengHei","Segoe UI",Roboto,sans-serif;
      background:var(--bg);color:var(--text);padding:16px;max-width:560px;margin:0 auto;min-height:100vh}
 .head{background:linear-gradient(135deg,#2563eb,#1e40af);color:#fff;border-radius:18px;padding:20px;
       margin-bottom:16px;display:flex;align-items:center;gap:12px;box-shadow:0 4px 14px rgba(37,99,235,.25)}
 .logo{width:46px;height:46px;border-radius:14px;background:rgba(255,255,255,.18);display:flex;
       align-items:center;justify-content:center;font-size:24px;flex:none}
 .head h1{font-size:20px;font-weight:700}
 .head .sub{font-size:13px;opacity:.85;margin-top:2px}
 .dot{width:11px;height:11px;border-radius:50%;background:#9ca3af;margin-left:auto;flex:none}
 .dot.on{background:#4ade80;box-shadow:0 0 0 4px rgba(74,222,128,.2)}
 .card{background:#fff;border-radius:18px;padding:18px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
 .card h2{font-size:15px;margin-bottom:12px}
 label{font-size:13px;color:var(--muted)}
 input[type=text],textarea{width:100%;border:1.5px solid #d9dee7;border-radius:12px;padding:11px 13px;
       font-size:15px;margin:6px 0 12px;font-family:inherit;background:#fbfcfe}
 input[type=text]:focus,textarea:focus{outline:none;border-color:var(--primary)}
 textarea{min-height:84px;resize:vertical}
 .btn{display:block;width:100%;background:var(--primary);color:#fff;border:none;border-radius:13px;
      padding:13px;font-size:15px;font-weight:600;cursor:pointer}
 .btn:active{transform:scale(.98);opacity:.9}
 .filezone{border:2px dashed #c7d2fe;border-radius:14px;padding:24px;text-align:center;
           color:var(--muted);font-size:14px;cursor:pointer;margin-bottom:12px;background:#f8faff}
 .filezone:active{background:#eef2ff}
 .bar{height:8px;background:#e5e7eb;border-radius:99px;overflow:hidden;margin-top:6px;display:none}
 .bar i{display:block;height:100%;width:0;background:var(--primary);transition:width .2s}
 .item{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:13px;margin-bottom:9px}
 .item .who{font-size:12px;color:var(--muted);margin-bottom:6px}
 .item a{color:var(--primary);font-weight:600;text-decoration:none;word-break:break-all}
 .item pre{white-space:pre-wrap;word-break:break-word;font-size:14px;line-height:1.5}
 .empty{color:var(--muted);font-size:13px;text-align:center;padding:16px}
 .toast{position:fixed;left:50%;bottom:32px;transform:translateX(-50%);background:rgba(17,24,39,.92);
        color:#fff;padding:11px 20px;border-radius:99px;font-size:14px;opacity:0;transition:opacity .25s;
        pointer-events:none;max-width:86%;text-align:center}
 .toast.show{opacity:1}
</style>
</head>
<body>
<div class="head">
  <div class="logo">&#128230;</div>
  <div>
    <h1>區網快傳</h1>
    <div class="sub" id="peer">連線中…</div>
  </div>
  <div class="dot" id="dot"></div>
</div>

<div class="card">
  <h2>我的名稱</h2>
  <label>這部手機在電腦端顯示的名稱</label>
  <input type="text" id="name" placeholder="例如：Alan 的手機">
</div>

<div class="card">
  <h2>傳送文字</h2>
  <textarea id="text" placeholder="輸入要傳到電腦的文字…"></textarea>
  <button class="btn" onclick="sendText()">傳送到電腦</button>
</div>

<div class="card">
  <h2>上傳檔案</h2>
  <div class="filezone" onclick="document.getElementById('file').click()">
    <div style="font-size:30px;margin-bottom:4px">&#128228;</div>
    點此選擇照片或檔案<br><span style="font-size:12px">可複選，會存到電腦的接收資料夾</span>
  </div>
  <input type="file" id="file" multiple style="display:none" onchange="uploadFiles()">
  <div class="bar" id="bar"><i id="barfill"></i></div>
</div>

<div class="card">
  <h2>電腦傳給我的</h2>
  <div id="inbox"><div class="empty">尚無內容</div></div>
</div>

<div class="toast" id="toast"></div>

<script>
var clientId = localStorage.getItem('lanqs_id');
if(!clientId){ clientId = Math.random().toString(36).slice(2,10); localStorage.setItem('lanqs_id', clientId); }

var nameEl = document.getElementById('name');
nameEl.value = localStorage.getItem('lanqs_name') || '';
nameEl.addEventListener('change', function(){
  localStorage.setItem('lanqs_name', nameEl.value.trim());
  connect();
});

function myName(){ return nameEl.value.trim() || '我的手機'; }

var es = null;
function connect(){
  if(es){ try{ es.close(); }catch(e){} }
  es = new EventSource('/events?name=' + encodeURIComponent(myName()) + '&id=' + clientId);
  es.onopen = function(){ setStatus(true); };
  es.onerror = function(){ setStatus(false); };
  es.onmessage = function(e){
    var m;
    try{ m = JSON.parse(e.data); }catch(err){ return; }
    if(m.type === 'hello'){ document.getElementById('peer').textContent = '已連線至 ' + m.name; }
    else if(m.type === 'text'){ addText(m); }
    else if(m.type === 'file'){ addFile(m); }
  };
}

function setStatus(on){
  var d = document.getElementById('dot');
  d.className = 'dot' + (on ? ' on' : '');
  if(!on) document.getElementById('peer').textContent = '連線中斷，重新連線中…';
}

function toast(msg){
  var t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._h);
  t._h = setTimeout(function(){ t.classList.remove('show'); }, 2200);
}

function sendText(){
  var v = document.getElementById('text').value.trim();
  if(!v){ toast('請先輸入文字'); return; }
  fetch('/web/text', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ from: myName(), text: v })
  }).then(function(r){ return r.json(); }).then(function(j){
    if(j && j.ok){ document.getElementById('text').value = ''; toast('已傳送到電腦'); }
    else toast('傳送失敗');
  }).catch(function(){ toast('傳送失敗（連線問題）'); });
}

function uploadFiles(){
  var inp = document.getElementById('file');
  var files = inp.files;
  if(!files.length) return;
  var fd = new FormData();
  fd.append('from', myName());
  for(var i=0;i<files.length;i++){ fd.append('files', files[i]); }
  var bar = document.getElementById('bar');
  var fill = document.getElementById('barfill');
  bar.style.display = 'block'; fill.style.width = '0%';
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '/upload');
  xhr.upload.onprogress = function(e){
    if(e.lengthComputable){ fill.style.width = Math.round(e.loaded/e.total*100) + '%'; }
  };
  xhr.onload = function(){
    bar.style.display = 'none'; fill.style.width = '0%';
    var j;
    try{ j = JSON.parse(xhr.responseText); }catch(err){ j = null; }
    if(j && j.ok){ toast('已上傳 ' + (j.saved || []).length + ' 個檔案'); }
    else toast('上傳失敗');
    inp.value = '';
  };
  xhr.onerror = function(){ bar.style.display='none'; toast('上傳失敗（連線問題）'); };
  xhr.send(fd);
}

function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function human(n){
  if(n == null) return '';
  var u = ['B','KB','MB','GB','TB'];
  var i = 0;
  while(n >= 1024 && i < u.length-1){ n /= 1024; i++; }
  return (i===0 ? n : n.toFixed(1)) + ' ' + u[i];
}

function addText(m){
  var inbox = document.getElementById('inbox');
  var empty = inbox.querySelector('.empty');
  if(empty) empty.remove();
  var d = document.createElement('div');
  d.className = 'item';
  d.innerHTML = '<div class="who">來自 ' + esc(m.from) + '</div><pre>' + esc(m.text) + '</pre>';
  inbox.insertBefore(d, inbox.firstChild);
}

function addFile(m){
  var inbox = document.getElementById('inbox');
  var empty = inbox.querySelector('.empty');
  if(empty) empty.remove();
  var d = document.createElement('div');
  d.className = 'item';
  d.innerHTML = '<div class="who">來自 ' + esc(m.from) + ' &#183; ' + human(m.size) + '</div>' +
                '<a href="' + m.url + '" download>' + esc(m.name) + '</a>';
  inbox.insertBefore(d, inbox.firstChild);
}

connect();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 工具（獨立複製，避免與主程式互相 import）
# ---------------------------------------------------------------------------

def _sanitize(name):
    name = os.path.basename(name).strip().strip(".")
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name)
    return name or "received_file"


def _unique(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists("%s (%d)%s" % (base, i, ext)):
        i += 1
    return "%s (%d)%s" % (base, i, ext)


def parse_multipart(body, boundary):
    """極簡 multipart/form-data 解析，回傳 [{name, filename, content}]。"""
    parts = []
    delim = b"--" + boundary.encode("utf-8")
    for seg in body.split(delim):
        if not seg or seg in (b"--", b"--\r\n") or seg.startswith(b"--"):
            continue
        if seg.startswith(b"\r\n"):
            seg = seg[2:]
        if seg.endswith(b"\r\n"):
            seg = seg[:-2]
        if b"\r\n\r\n" in seg:
            head, content = seg.split(b"\r\n\r\n", 1)
        else:
            head, content = seg, b""
        headers = {}
        for line in head.split(b"\r\n"):
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.strip().lower()] = v.strip()
        cd = headers.get(b"content-disposition", b"").decode("utf-8", "ignore")
        name = None
        filename = None
        for piece in cd.split(";"):
            piece = piece.strip()
            if piece.startswith("filename="):
                filename = piece[len("filename="):].strip().strip('"')
            elif piece.startswith("name="):
                name = piece[len("name="):].strip().strip('"')
        parts.append({"name": name, "filename": filename, "content": content})
    return parts


# ---------------------------------------------------------------------------
# 網頁伺服器
# ---------------------------------------------------------------------------

class WebServer:
    """對外（手機瀏覽器）提供網頁與上傳，並管理連線中的手機客戶端。"""

    def __init__(self, device_name, save_dir, events):
        self.device_name = device_name
        self.save_dir = save_dir
        self.events = events
        self.httpd = None
        self.port = None
        self.clients = {}      # client_id -> {id, ip, name, q, last}
        self.outgoing = {}     # 檔下載 id -> {path, name, size}
        self.lock = threading.Lock()

    # -- 生命週期 ----------------------------------------------------------
    def start(self):
        last_err = None
        for port in range(WEB_PORT_START, WEB_PORT_END + 1):
            try:
                httpd = ExclusiveHTTPServer(("", port), _WebHandler)
                httpd.daemon_threads = True
                httpd.web = self
                httpd.device_name = self.device_name
                httpd.save_dir = self.save_dir
                httpd.events = self.events
                self.httpd = httpd
                self.port = port
                break
            except OSError as e:
                last_err = e
                continue
        if self.httpd is None:
            raise RuntimeError("找不到可用的網頁連接埠：%s" % last_err)
        threading.Thread(target=self.httpd.serve_forever,
                         kwargs={"poll_interval": 0.5}, daemon=True).start()

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass

    # -- 手機客戶端清單 ----------------------------------------------------
    def register_client(self, cid, ip, name, q):
        with self.lock:
            self.clients[cid] = {"id": cid, "ip": ip, "name": name,
                                 "q": q, "last": time.time()}

    def unregister_client(self, cid):
        with self.lock:
            self.clients.pop(cid, None)

    def touch_client(self, cid):
        with self.lock:
            c = self.clients.get(cid)
            if c:
                c["last"] = time.time()

    def list_clients(self):
        now = time.time()
        with self.lock:
            stale = [k for k, v in self.clients.items() if now - v["last"] > 180]
            for k in stale:
                self.clients.pop(k, None)
            return [{"id": v["id"], "name": v["name"], "ip": v["ip"]}
                    for v in self.clients.values()]

    def _get_subscriber(self, client_id):
        with self.lock:
            c = self.clients.get(client_id)
            return c if c else None

    # -- 電腦 → 手機推播 ---------------------------------------------------
    def push_text(self, client_id, from_name, text):
        sub = self._get_subscriber(client_id)
        if not sub:
            return False
        try:
            sub["q"].put({"type": "text", "from": from_name, "text": text})
            return True
        except Exception:
            return False

    def push_file(self, client_id, from_name, path):
        sub = self._get_subscriber(client_id)
        if not sub:
            return False
        fid = uuid.uuid4().hex[:12]
        name = os.path.basename(path)
        size = os.path.getsize(path)
        with self.lock:
            self.outgoing[fid] = {"path": path, "name": name, "size": size}
            # 只保留最近 200 個下載連結，避免無謂累積
            if len(self.outgoing) > 200:
                for k in list(self.outgoing.keys())[:len(self.outgoing) - 200]:
                    self.outgoing.pop(k, None)
        try:
            sub["q"].put({"type": "file", "from": from_name, "name": name,
                          "size": size, "url": "/download/%s" % fid})
            return True
        except Exception:
            return False

    def get_outgoing(self, fid):
        with self.lock:
            return self.outgoing.get(fid)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class _WebHandler(BaseHTTPRequestHandler):
    server_version = "LANQuickShare-Web/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _client_ip(self):
        return self.client_address[0] if self.client_address else "?"

    # -- GET --------------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path == "/":
            self._serve_html()
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif path == "/info":
            self._send_json({
                "app": "LANQuickShare", "type": "web",
                "name": self.server.device_name, "version": "1.0.0",
            })
        elif path == "/events":
            self._handle_events(qs)
        elif path.startswith("/download/"):
            self._handle_download(path)
        else:
            self._send_json({"ok": False, "error": "not found"}, 404)

    def _serve_html(self):
        body = WEB_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _handle_events(self, qs):
        ip = self._client_ip()
        cid = str(qs.get("id", ["0"])[0])
        name = str(qs.get("name", ["手機"])[0])[:40]
        client_id = "%s#%s" % (ip, cid)
        q = queue.Queue(maxsize=1000)
        web = self.server.web
        web.register_client(client_id, ip, name, q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        hello = json.dumps({"type": "hello", "id": client_id,
                            "name": self.server.device_name}, ensure_ascii=False)
        try:
            self.wfile.write(("data: %s\n\n" % hello).encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    item = q.get(timeout=15)
                    payload = json.dumps(item, ensure_ascii=False)
                    self.wfile.write(("data: %s\n\n" % payload).encode("utf-8"))
                    self.wfile.flush()
                    web.touch_client(client_id)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    web.touch_client(client_id)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            web.unregister_client(client_id)

    def _handle_download(self, path):
        fid = os.path.basename(path)
        item = self.server.web.get_outgoing(fid)
        if not item or not os.path.exists(item["path"]):
            self._send_json({"ok": False, "error": "gone"}, 404)
            return
        try:
            f = open(item["path"], "rb")
            size = item["size"]
        except OSError:
            self._send_json({"ok": False, "error": "unreadable"}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition",
                         'attachment; filename="%s"; filename*=UTF-8\'\'%s'
                         % (quote(item["name"], safe=""), quote(item["name"], safe="")))
        self.end_headers()
        try:
            remaining = size
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            f.close()

    # -- POST -------------------------------------------------------------
    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/upload":
            self._handle_upload()
        elif parsed.path == "/web/text":
            self._handle_web_text()
        else:
            self._send_json({"ok": False, "error": "not found"}, 404)

    def _handle_upload(self):
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        if not ctype.startswith("multipart/form-data") or not m:
            self._send_json({"ok": False, "error": "need multipart/form-data"}, 400)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0 or length > 2 * 1024 * 1024 * 1024:   # 上限 2GB
            self._send_json({"ok": False, "error": "too large"}, 413)
            return
        body = self.rfile.read(length)
        parts = parse_multipart(body, m.group(1))
        sender = "手機"
        file_parts = []
        for p in parts:
            if p["filename"]:
                file_parts.append(p)
            elif p["name"] in ("from", "name", "deviceName"):
                v = p["content"].decode("utf-8", "ignore").strip()
                if v:
                    sender = v[:40]
        if not file_parts:
            self._send_json({"ok": False, "error": "no file"}, 400)
            return
        os.makedirs(self.server.save_dir, exist_ok=True)
        saved = []
        for p in file_parts:
            fname = _sanitize(p["filename"])
            path = _unique(os.path.join(self.server.save_dir, fname))
            with open(path, "wb") as f:
                f.write(p["content"])
            self.server.events.put({
                "type": "recv-file",
                "from": sender + "（網頁）",
                "ip": self._client_ip(),
                "name": os.path.basename(path),
                "size": len(p["content"]),
                "path": path,
                "time": time.time(),
            })
            saved.append(os.path.basename(path))
        self._send_json({"ok": True, "saved": saved})

    def _handle_web_text(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0 or length > 10 * 1024 * 1024:
            self._send_json({"ok": False, "error": "bad length"}, 400)
            return
        try:
            data = self.rfile.read(length)
            msg = json.loads(data.decode("utf-8"))
            sender = str(msg.get("from", "手機"))[:40]
            text = str(msg.get("text", ""))
            self.server.events.put({
                "type": "recv-text",
                "from": sender + "（網頁）",
                "ip": self._client_ip(),
                "text": text,
                "time": time.time(),
            })
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 400)
