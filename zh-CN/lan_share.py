# -*- coding: utf-8 -*-
"""
局域网快传 LAN Quick Share
========================
同一个 Wi-Fi 下，免登录、免云端，直接在设备之间互传文字与任意文件。

运作原理：
  1. UDP 广播（Discovery）：每台设备定时向局域网广播自己的存在，
     同时监听其他设备的广播，自动建立线上设备清单。
  2. HTTP 直连（Transfer）：找到目标后，直接对对方的内建 HTTP
     服务器建立 TCP 连接，把文字或文件的二进制资料流过去，
     不经过任何云端服务器。
  3. 网页服务器（Web UI）：手机浏览器输入 http://<电脑IP>:8080 即可
     上传文件、传文字，并接收电脑推播的内容（SSE）。
"""
import ctypes
import json
import os
import platform
import queue
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote, unquote

import requests

from webui import WebServer, ExclusiveHTTPServer

APP_NAME = "局域网快传"
APP_NAME_EN = "LAN Quick Share"
APP_ID = "LANQuickShare"
VERSION = "1.1.0"

DISCOVERY_PORT = 45678          # UDP 广播探索用
HTTP_PORT_START = 45679         # HTTP 传输用（被占用时自动往后找）
HTTP_PORT_END = 45720
BUFFER = 65536                  # 64KB 流分块
PEER_TIMEOUT = 12               # 超过几秒没收到广播就视为离线
ANNOUNCE_INTERVAL = 3           # 广播间隔（秒）


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def resource_path(rel):
    """取得打包后（PyInstaller _MEIPASS）或开发环境下的资源路径。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def get_lan_ip():
    """取得本机在局域网中的 IP（连不出外网时回退 127.0.0.1）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def broadcast_addresses():
    """同时返回全域广播位址与依本机 IP 推导的 /24 子网广播位址，提高发现率。"""
    addrs = {"255.255.255.255"}
    ip = get_lan_ip()
    parts = ip.split(".")
    if len(parts) == 4 and ip != "127.0.0.1":
        parts[3] = "255"
        addrs.add(".".join(parts))
    return addrs


def sanitize_filename(name):
    """移除路径与 Windows 不合法字符，避免写档出问题。"""
    name = os.path.basename(name).strip().strip(".")
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name)
    return name or "received_file"


def unique_path(path):
    """若档名已存在，自动加上 (1)、(2)… 后缀。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists("%s (%d)%s" % (base, i, ext)):
        i += 1
    return "%s (%d)%s" % (base, i, ext)


def human_size(n):
    try:
        n = float(n)
    except Exception:
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return "%d %s" % (int(n), unit)
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%d B" % int(n)


def parse_ip_port(s):
    """把 'IP' 或 'IP:port' 拆成 (host, port)。"""
    s = s.strip()
    if not s:
        return None, None
    if ":" in s:
        host, _, port = s.rpartition(":")
        try:
            return host, int(port)
        except ValueError:
            return s, None
    return s, None


def probe_app_peer(ip, port, timeout=3):
    """探测某 IP:port 是否为另一台运行本软件的电脑，是则返回其设备资讯。"""
    try:
        r = requests.get("http://%s:%d/api/info" % (ip, port), timeout=timeout)
        if r.status_code == 200:
            j = r.json()
            if j.get("app") == APP_ID:
                return {"name": str(j.get("name") or "未知设备"),
                        "ip": ip, "port": port}
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 设备发现（UDP 广播）
# ---------------------------------------------------------------------------

class Discovery:
    """定时广播自己、监听别人，维护一份线上设备清单。"""

    def __init__(self, name, http_port):
        self.name = name
        self.http_port = http_port
        self.peers = {}           # (ip, port) -> dict
        self.lock = threading.Lock()
        self.running = False
        self.local_ips = {get_lan_ip(), "127.0.0.1"}
        self._announce_event = threading.Event()

    def start(self):
        self.running = True
        threading.Thread(target=self._announce_loop, daemon=True).start()
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def stop(self):
        self.running = False
        self._announce_event.set()

    def announce_once(self):
        """手动触发一次立即广播（刷新用）。"""
        self._announce_event.set()

    def _announce_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        msg = json.dumps({
            "app": APP_ID,
            "name": self.name,
            "port": self.http_port,
            "version": VERSION,
        }).encode("utf-8")
        while self.running:
            for addr in broadcast_addresses():
                try:
                    sock.sendto(msg, (addr, DISCOVERY_PORT))
                except Exception:
                    pass
            self._announce_event.wait(ANNOUNCE_INTERVAL)
            self._announce_event.clear()
        sock.close()

    def _listen_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", DISCOVERY_PORT))
        except OSError:
            sock.close()
            return
        sock.settimeout(1.0)
        while self.running:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                break
            try:
                info = json.loads(data.decode("utf-8", "ignore"))
            except Exception:
                continue
            if info.get("app") != APP_ID:
                continue
            port = info.get("port")
            if addr[0] in self.local_ips and port == self.http_port:
                continue
            if not isinstance(port, int) or not (0 < port < 65536):
                continue
            key = (addr[0], port)
            with self.lock:
                self.peers[key] = {
                    "name": str(info.get("name") or "未知设备"),
                    "ip": addr[0],
                    "port": port,
                    "last": time.time(),
                }
        sock.close()

    def get_peers(self):
        """返回目前线上的设备清单，顺便清掉超时的设备。"""
        now = time.time()
        with self.lock:
            stale = [k for k, v in self.peers.items() if now - v["last"] > PEER_TIMEOUT]
            for k in stale:
                del self.peers[k]
            return sorted(self.peers.values(), key=lambda p: (p["name"].lower(), p["ip"]))


# ---------------------------------------------------------------------------
# 文件 / 文字接收（HTTP 服务器，电脑 ↔ 电脑）
# ---------------------------------------------------------------------------

class _TransferHandler(BaseHTTPRequestHandler):
    server_version = "LANQuickShare/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静默存取日志
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

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/info":
            self._send_json({
                "app": APP_ID,
                "name": self.server.device_name,
                "version": VERSION,
            })
        elif path == "/":
            body = ("LAN Quick Share 运作中：" + self.server.device_name).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send_json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/text":
            self._handle_text()
        elif parsed.path == "/api/file":
            self._handle_file(parsed)
        else:
            self._send_json({"ok": False, "error": "not found"}, 404)

    def _handle_text(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0 or length > 10 * 1024 * 1024:   # 文字上限 10MB
            self._send_json({"ok": False, "error": "bad length"}, 400)
            return
        try:
            data = self.rfile.read(length)
            msg = json.loads(data.decode("utf-8"))
            text = str(msg.get("text", ""))
            sender = str(msg.get("from", "未知设备"))
            self.server.events.put({
                "type": "recv-text",
                "from": sender,
                "ip": self._client_ip(),
                "text": text,
                "time": time.time(),
            })
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 400)

    def _handle_file(self, parsed):
        qs = parse_qs(parsed.query)
        filename = sanitize_filename(qs.get("filename", ["file"])[0])
        raw_from = self.headers.get("X-From")   # 传送端以 URL 编码，避免非 ASCII 标头
        sender = unquote(raw_from) if raw_from else "未知设备"
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length < 0:
            self._send_json({"ok": False, "error": "bad length"}, 400)
            return
        save_dir = self.server.save_dir
        os.makedirs(save_dir, exist_ok=True)
        path = unique_path(os.path.join(save_dir, filename))
        received = 0
        try:
            with open(path, "wb") as f:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(BUFFER, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
                    received += len(chunk)
            if received != length:
                try:
                    os.remove(path)
                except Exception:
                    pass
                self._send_json({"ok": False, "error": "incomplete"}, 500)
                return
            self.server.events.put({
                "type": "recv-file",
                "from": sender,
                "ip": self._client_ip(),
                "name": os.path.basename(path),
                "size": received,
                "path": path,
                "time": time.time(),
            })
            self._send_json({"ok": True, "saved": os.path.basename(path)})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 500)


class TransferServer:
    """每台设备内建的轻量 HTTP 服务器，负责接收文字与文件。"""

    def __init__(self, device_name, save_dir, events):
        self.device_name = device_name
        self.save_dir = save_dir
        self.events = events
        self.httpd = None
        self.port = None

    def start(self):
        last_err = None
        for port in range(HTTP_PORT_START, HTTP_PORT_END + 1):
            try:
                httpd = ExclusiveHTTPServer(("", port), _TransferHandler)
                httpd.daemon_threads = True
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
            raise RuntimeError("找不到可用的连接端口：%s" % last_err)
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.5},
                         daemon=True).start()

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 传送端（HTTP 客户端，电脑 ↔ 电脑）
# ---------------------------------------------------------------------------

def send_text(ip, port, from_name, text, timeout=10):
    r = requests.post(
        "http://%s:%d/api/text" % (ip, port),
        json={"from": from_name, "text": text},
        timeout=timeout,
    )
    return r.json().get("ok", False)


def send_file(ip, port, from_name, file_path, progress_cb=None):
    """以流方式传送任意大小文件；progress_cb(sent, total) 回报进度。"""
    size = os.path.getsize(file_path)
    filename = os.path.basename(file_path)
    state = {"sent": 0, "last_report": -1}

    def gen():
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(BUFFER)
                if not chunk:
                    break
                state["sent"] += len(chunk)
                if progress_cb:
                    pct = int(state["sent"] * 100 / max(size, 1))
                    if pct != state["last_report"]:
                        state["last_report"] = pct
                        progress_cb(state["sent"], size)
                yield chunk

    r = requests.post(
        "http://%s:%d/api/file?filename=%s" % (ip, port, quote(filename)),
        data=gen(),
        headers={"Content-Length": str(size),
                 "X-From": quote(from_name, safe="")},   # HTTP 标头只支援 latin-1，中文需编码
        timeout=None,
    )
    return r.json().get("ok", False)


def default_save_dir():
    """接收文件的默认文件夹（“文件”底下的 LANQuickShare Received）。"""
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    if not os.path.isdir(docs):
        docs = os.path.expanduser("~")
    path = os.path.join(docs, "LANQuickShare Received")
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# 图形界面（全屏幕、无传统选单）
# ---------------------------------------------------------------------------

def run_app():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    HAS_DND = False
    try:
        from tkinterdnd2 import TkinterDnD, DND_FILES
        HAS_DND = True
    except Exception:
        pass

    # 设置 AppUserModelID，让工作列正确显示 EXE 图示（而不是默认 Python 图示）
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID + ".App")
    except Exception:
        pass

    device_name = platform.node() or "我的电脑"
    save_dir = default_save_dir()
    events = queue.Queue()
    lan_ip = get_lan_ip()

    server = TransferServer(device_name, save_dir, events)
    server.start()

    discovery = Discovery(device_name, server.port)
    discovery.start()

    web = WebServer(device_name, save_dir, events)
    web.start()
    web_url = "http://%s:%d" % (lan_ip, web.port)

    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    root.title("%s %s" % (APP_NAME, APP_NAME_EN))
    try:
        root.iconbitmap(default=resource_path("icon.ico"))   # 窗口左上角图示
    except Exception:
        pass
    root.configure(bg="#eef1f6")
    root.minsize(1080, 660)
    try:
        root.state("zoomed")        # 全屏幕最大化（Windows）
    except Exception:
        root.geometry("1280x800")
    # 注意：刻意不设置任何选单列（没有“文件”“说明”等传统选项），界面只有本软件内容。

    # ---- 字型 -------------------------------------------------------------
    FONT = "Microsoft JhengHei UI"
    f_title = (FONT, 17, "bold")
    f_sub = (FONT, 10)
    f_h2 = (FONT, 12, "bold")
    f_body = (FONT, 10)
    f_btn = (FONT, 10, "bold")

    C_BG = "#eef1f6"
    C_CARD = "#ffffff"
    C_BORDER = "#d9dee7"
    C_PRIMARY = "#2563eb"
    C_PRIMARY_D = "#1d4ed8"
    C_TEXT = "#111827"
    C_MUTED = "#6b7280"

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Treeview", font=f_body, rowheight=28,
                    background=C_CARD, fieldbackground=C_CARD)
    style.configure("Treeview.Heading", font=(FONT, 10, "bold"))
    style.configure("TProgressbar", thickness=10, background=C_PRIMARY)

    # ---- 共用状态 ---------------------------------------------------------
    state = {
        "peers": [],            # 合并后的设备清单（电脑 + 手机 + 手动）
        "selected_key": None,   # 目前选取设备的 key
        "manual": [],           # 手动加入的设备
        "files": [],            # 待传文件路径
        "history": [],          # 传输记录
        "sending": False,
    }

    def make_button(parent, text, command, primary=True, padx=18, pady=8):
        return tk.Button(
            parent, text=text, command=command,
            font=f_btn, bd=0, relief="flat", cursor="hand2",
            padx=padx, pady=pady,
            bg=C_PRIMARY if primary else "#e5e7eb",
            fg="#ffffff" if primary else C_TEXT,
            activebackground=C_PRIMARY_D if primary else "#d1d5db",
            activeforeground="#ffffff" if primary else C_TEXT,
        )

    def card(parent):
        return tk.Frame(parent, bg=C_CARD, highlightbackground=C_BORDER,
                        highlightthickness=1, bd=0)

    # ---- 顶部标题列 -------------------------------------------------------
    header = tk.Frame(root, bg=C_PRIMARY, height=64)
    header.pack(fill="x")
    header.pack_propagate(False)

    header_icon_img = None
    try:
        from PIL import Image, ImageTk
        _img = Image.open(resource_path("icon.ico"))
        _img = _img.resize((40, 40), Image.LANCZOS)
        header_icon_img = ImageTk.PhotoImage(_img)
        tk.Label(header, image=header_icon_img, bg=C_PRIMARY).pack(
            side="left", padx=(18, 10), pady=12)
    except Exception:
        pass

    title_box = tk.Frame(header, bg=C_PRIMARY)
    title_box.pack(side="left", pady=8)
    tk.Label(title_box, text=APP_NAME, font=f_title, bg=C_PRIMARY,
             fg="#ffffff").pack(anchor="w")
    tk.Label(title_box,
             text="免登录・免云端・同一个 Wi-Fi 即可互传文字与文件",
             font=f_sub, bg=C_PRIMARY, fg="#dbeafe").pack(anchor="w")

    info_text = "本机：%s　IP：%s" % (device_name, lan_ip)
    tk.Label(header, text=info_text, font=f_sub, bg=C_PRIMARY,
             fg="#ffffff").pack(side="right", padx=20)

    # ---- 手机连接列 -------------------------------------------------------
    web_bar = tk.Frame(root, bg="#1e3a8a")
    web_bar.pack(fill="x")
    tk.Label(web_bar, text="手机连接网址：", font=f_btn, bg="#1e3a8a",
             fg="#ffffff").pack(side="left", padx=(18, 6), pady=8)
    url_label = tk.Label(web_bar, text=web_url, font=(FONT, 11, "bold"),
                         bg="#ffffff", fg="#1e3a8a", padx=12, pady=3,
                         cursor="hand2")
    url_label.pack(side="left", pady=8)
    url_label.bind("<Button-1>", lambda e: copy_url())
    make_button(web_bar, "复制网址", lambda: copy_url(),
                primary=False, padx=12, pady=4).pack(side="left", padx=8, pady=7)
    make_button(web_bar, "手机使用说明", lambda: show_phone_help(),
                primary=False, padx=12, pady=4).pack(side="left", pady=7)

    def copy_url():
        root.clipboard_clear()
        root.clipboard_append(web_url)
        set_status("已复制网址：%s" % web_url)

    # ---- 主体 -------------------------------------------------------------
    body = tk.Frame(root, bg=C_BG)
    body.pack(fill="both", expand=True, padx=16, pady=16)
    body.columnconfigure(0, minsize=340, weight=0)
    body.columnconfigure(1, weight=1)
    body.rowconfigure(0, weight=1)

    # ===== 左栏：线上设备 =====
    left = card(body)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

    dev_head = tk.Frame(left, bg=C_CARD)
    dev_head.pack(fill="x", padx=14, pady=(12, 4))
    peers_title = tk.Label(dev_head, text="线上设备（0）", font=f_h2,
                           bg=C_CARD, fg=C_TEXT)
    peers_title.pack(side="left")
    make_button(dev_head, "刷新", lambda: (discovery.announce_once(),
                                               set_status("已重新广播，搜寻设备中…")),
                primary=False, padx=12, pady=4).pack(side="right")

    tk.Label(left, text="选择要传送到的设备", font=f_sub,
             bg=C_CARD, fg=C_MUTED).pack(anchor="w", padx=14)

    peer_list = tk.Listbox(left, font=(FONT, 11), bd=0, relief="flat",
                           highlightthickness=1, highlightbackground=C_BORDER,
                           selectbackground="#dbeafe", selectforeground=C_TEXT,
                           activestyle="none", exportselection=False)
    peer_list.pack(fill="both", expand=True, padx=14, pady=(6, 6))

    # 手动输入 IP
    ip_row = tk.Frame(left, bg=C_CARD)
    ip_row.pack(fill="x", padx=14, pady=(0, 6))
    tk.Label(ip_row, text="手动输入 IP：", font=f_sub, bg=C_CARD,
             fg=C_MUTED).pack(side="left")
    ip_entry = tk.Entry(ip_row, font=f_body, highlightthickness=1,
                        highlightbackground=C_BORDER, bd=0, width=14)
    ip_entry.pack(side="left", fill="x", expand=True, padx=(6, 6), ipady=3)
    make_button(ip_row, "连接", lambda: on_connect_ip(),
                primary=False, padx=10, pady=3).pack(side="left")

    peer_hint = tk.Label(left, text="找不到电脑时，可在上方手动输入对方 IP。\n"
                                    "手机请用上方网址连接，会自动出现在此清单。",
                         font=f_sub, bg=C_CARD, fg=C_MUTED, justify="left")
    peer_hint.pack(anchor="w", padx=14, pady=(2, 12))

    # ===== 右栏 =====
    right = tk.Frame(body, bg=C_BG)
    right.grid(row=0, column=1, sticky="nsew")
    right.rowconfigure(1, weight=1)
    right.columnconfigure(0, weight=1)

    # ---- 传送区 ----
    send_card = card(right)
    send_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))

    tk.Label(send_card, text="传送", font=f_h2, bg=C_CARD,
             fg=C_TEXT).pack(anchor="w", padx=14, pady=(12, 6))

    # 传文字
    text_row = tk.Frame(send_card, bg=C_CARD)
    text_row.pack(fill="x", padx=14, pady=(0, 8))
    text_input = tk.Text(text_row, height=3, font=f_body, wrap="word",
                         highlightthickness=1, highlightbackground=C_BORDER, bd=0)
    text_input.pack(side="left", fill="x", expand=True)
    btn_send_text = make_button(text_row, "传送文字", lambda: on_send_text())
    btn_send_text.pack(side="left", padx=(10, 0))

    # 传文件
    file_row = tk.Frame(send_card, bg=C_CARD)
    file_row.pack(fill="x", padx=14, pady=(0, 4))
    make_button(file_row, "选择文件", lambda: on_pick_files(),
                primary=False).pack(side="left")
    make_button(file_row, "清空", lambda: on_clear_files(),
                primary=False).pack(side="left", padx=(8, 0))
    files_label = tk.Label(file_row, text="尚未选择文件", font=f_sub,
                           bg=C_CARD, fg=C_MUTED, anchor="w")
    files_label.pack(side="left", padx=12, fill="x", expand=True)
    btn_send_file = make_button(file_row, "传送文件", lambda: on_send_files())
    btn_send_file.pack(side="right")

    drop_hint = tk.Label(send_card, text="", font=f_sub, bg=C_CARD, fg=C_MUTED,
                         anchor="w")
    drop_hint.pack(fill="x", padx=14, pady=(0, 2))
    if HAS_DND:
        drop_hint.config(text="提示：也可以直接把文件从“文件资源管理器”拖放到这个区块")

    # 进度列
    prog_row = tk.Frame(send_card, bg=C_CARD)
    prog_row.pack(fill="x", padx=14, pady=(0, 12))
    progress = ttk.Progressbar(prog_row, mode="determinate", maximum=1000)
    progress.pack(side="left", fill="x", expand=True)
    prog_label = tk.Label(prog_row, text="", font=f_sub, bg=C_CARD,
                          fg=C_MUTED, width=22, anchor="e")
    prog_label.pack(side="right", padx=(8, 0))

    # ---- 传输记录 ----
    hist_card = card(right)
    hist_card.grid(row=1, column=0, sticky="nsew")
    hist_card.rowconfigure(1, weight=1)
    hist_card.columnconfigure(0, weight=1)

    hist_head = tk.Frame(hist_card, bg=C_CARD)
    hist_head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
    tk.Label(hist_head, text="传输记录", font=f_h2, bg=C_CARD,
             fg=C_TEXT).pack(side="left")
    make_button(hist_head, "打开接收文件夹", lambda: open_save_dir(),
                primary=False, padx=12, pady=4).pack(side="right")
    make_button(hist_head, "清除记录", lambda: clear_history(),
                primary=False, padx=12, pady=4).pack(side="right", padx=(0, 8))

    cols = ("time", "dir", "peer", "kind", "content", "size")
    tree = ttk.Treeview(hist_card, columns=cols, show="headings")
    tree.heading("time", text="时间")
    tree.heading("dir", text="方向")
    tree.heading("peer", text="设备")
    tree.heading("kind", text="类型")
    tree.heading("content", text="内容")
    tree.heading("size", text="大小")
    tree.column("time", width=90, anchor="center", stretch=False)
    tree.column("dir", width=60, anchor="center", stretch=False)
    tree.column("peer", width=150, stretch=False)
    tree.column("kind", width=60, anchor="center", stretch=False)
    tree.column("content", width=420)
    tree.column("size", width=90, anchor="e", stretch=False)
    tree.grid(row=1, column=0, sticky="nsew", padx=(14, 0), pady=(0, 12))
    scroll = ttk.Scrollbar(hist_card, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    scroll.grid(row=1, column=1, sticky="ns", padx=(0, 14), pady=(0, 12))
    tree.bind("<Double-1>", lambda e: on_history_double_click())

    tk.Label(hist_card, text="收到的文件存放在：%s（双击记录可打开）" % save_dir,
             font=f_sub, bg=C_CARD, fg=C_MUTED, anchor="w").grid(
        row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 10))

    # ---- 底部状态列 -------------------------------------------------------
    status_var = tk.StringVar(value="就绪。等待同一 Wi-Fi 下的设备上线…")
    status_bar = tk.Label(root, textvariable=status_var, font=f_sub,
                          bg="#f9fafb", fg=C_MUTED, anchor="w",
                          highlightthickness=1, highlightbackground=C_BORDER)
    status_bar.pack(fill="x", side="bottom")

    # ---- 行为函数 ---------------------------------------------------------

    def set_status(msg):
        status_var.set(msg)

    def peer_label(p):
        tag = "[手机]" if p["kind"] == "web" else "[电脑]"
        return "  %s %s（%s）" % (tag, p["name"], p["ip"])

    def build_device_list():
        peers = []
        for p in discovery.get_peers():
            peers.append({"key": "app:%s:%d" % (p["ip"], p["port"]),
                          "kind": "app", "name": p["name"],
                          "ip": p["ip"], "port": p["port"]})
        for c in web.list_clients():
            peers.append({"key": "web:%s" % c["id"], "kind": "web",
                          "name": c["name"], "ip": c["ip"], "id": c["id"]})
        for m in state["manual"]:
            if not any(x["key"] == m["key"] for x in peers):
                peers.append(dict(m))
        return peers

    def selected_peer():
        key = state["selected_key"]
        for p in state["peers"]:
            if p["key"] == key:
                return p
        return None

    def on_peer_select(_evt=None):
        sel = peer_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(state["peers"]):
            p = state["peers"][idx]
            state["selected_key"] = p["key"]
            set_status("已选择设备：%s（%s）" % (p["name"], p["ip"]))

    peer_list.bind("<<ListboxSelect>>", on_peer_select)

    def refresh_peers():
        peers = build_device_list()
        old_keys = [p["key"] for p in state["peers"]]
        new_keys = [p["key"] for p in peers]
        state["peers"] = peers
        peers_title.config(text="线上设备（%d）" % len(peers))
        if old_keys != new_keys:
            peer_list.delete(0, "end")
            for p in peers:
                peer_list.insert("end", peer_label(p))
            if state["selected_key"] in new_keys:
                peer_list.selection_set(new_keys.index(state["selected_key"]))
            elif peers:
                peer_list.selection_set(0)
                on_peer_select()
            else:
                state["selected_key"] = None

    def on_connect_ip():
        raw = ip_entry.get().strip()
        if not raw:
            messagebox.showwarning(APP_NAME, "请输入对方电脑的 IP。")
            return
        host, port = parse_ip_port(raw)
        if port is None:
            port = HTTP_PORT_START
        set_status("正在连接 %s:%d …" % (host, port))
        info = probe_app_peer(host, port)
        if not info:
            messagebox.showerror(
                APP_NAME,
                "找不到 %s:%d 上的设备。\n\n请确认：\n"
                "・对方已打开本软件\n・IP 与连接端口正确\n・双方在同一个 Wi-Fi"
                % (host, port))
            set_status("连接失败：%s:%d" % (host, port))
            return
        key = "app:%s:%d" % (info["ip"], info["port"])
        if not any(m["key"] == key for m in state["manual"]):
            state["manual"].append({"key": key, "kind": "app",
                                    "name": info["name"],
                                    "ip": info["ip"], "port": info["port"]})
        state["selected_key"] = key
        refresh_peers()
        set_status("已连接：%s（%s）" % (info["name"], info["ip"]))

    def on_pick_files():
        paths = filedialog.askopenfilenames(title="选择要传送的文件（可复选）")
        if not paths:
            return
        for p in paths:
            if p not in state["files"]:
                state["files"].append(p)
        update_files_label()

    def add_files(paths):
        n = 0
        for p in paths:
            if p and os.path.isfile(p) and p not in state["files"]:
                state["files"].append(p)
                n += 1
        if n:
            update_files_label()
            set_status("已加入 %d 个文件" % n)
        return n

    def on_drop_files(event):
        try:
            paths = root.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        add_files(paths)

    if HAS_DND:
        send_card.drop_target_register(DND_FILES)
        send_card.dnd_bind("<<Drop>>", on_drop_files)

    def on_clear_files():
        state["files"] = []
        update_files_label()

    def update_files_label():
        files = state["files"]
        if not files:
            files_label.config(text="尚未选择文件")
        else:
            total = sum(os.path.getsize(p) for p in files if os.path.exists(p))
            names = "、".join(os.path.basename(p) for p in files[:3])
            if len(files) > 3:
                names += "…等 %d 个文件" % len(files)
            files_label.config(text="%s（共 %s）" % (names, human_size(total)))

    def add_history(direction, peer, kind, content, size, path=None, text=None):
        item = {
            "time": time.strftime("%H:%M:%S"),
            "dir": direction,
            "peer": peer,
            "kind": kind,
            "content": content,
            "size": human_size(size) if size is not None else "",
            "path": path,
            "text": text,
        }
        state["history"].append(item)
        tree.insert("", 0, values=(item["time"], item["dir"], item["peer"],
                                   item["kind"], item["content"], item["size"]))

    def clear_history():
        state["history"] = []
        for i in tree.get_children():
            tree.delete(i)

    def open_save_dir():
        try:
            os.startfile(save_dir)
        except Exception as e:
            messagebox.showerror(APP_NAME, "无法打开文件夹：%s" % e)

    def on_history_double_click():
        sel = tree.selection()
        if not sel:
            return
        idx = len(state["history"]) - 1 - tree.index(sel[0])
        if not (0 <= idx < len(state["history"])):
            return
        item = state["history"][idx]
        if item["kind"] == "文件" and item["path"] and os.path.exists(item["path"]):
            try:
                import subprocess
                subprocess.run(["explorer", "/select,", os.path.normpath(item["path"])])
            except Exception:
                open_save_dir()
        elif item["kind"] == "文字" and item["text"] is not None:
            show_text_popup(item)

    def show_text_popup(item):
        win = tk.Toplevel(root)
        win.title("文字消息 — 来自 %s" % item["peer"])
        try:
            win.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass
        win.geometry("520x320")
        box = tk.Text(win, font=f_body, wrap="word")
        box.pack(fill="both", expand=True, padx=10, pady=10)
        box.insert("1.0", item["text"])
        box.config(state="disabled")

        def copy_text():
            root.clipboard_clear()
            root.clipboard_append(item["text"])
            set_status("已复制文字到剪贴板")

        make_button(win, "复制文字", copy_text, primary=False).pack(pady=(0, 10))

    def show_phone_help():
        win = tk.Toplevel(root)
        win.title("手机使用说明")
        try:
            win.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass
        win.geometry("560x460")
        win.configure(bg="#ffffff")
        head = tk.Frame(win, bg=C_PRIMARY, height=52)
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(head, text="手机使用说明", font=(FONT, 13, "bold"),
                 bg=C_PRIMARY, fg="#ffffff").pack(side="left", padx=18, pady=12)

        content = (
            "用手机上传 / 接收文件（免安装 App、免登录）\n\n"
            "1. 让手机与这台电脑连上“同一个 Wi-Fi”。\n\n"
            "2. 在手机浏览器（Chrome、Safari 皆可）输入网址：\n\n"
            "        %s\n\n"
            "3. 网页上可以：\n"
            "  ・上传照片或文件 → 会存到这台电脑的接收文件夹\n"
            "  ・传送文字到电脑\n"
            "  ・查看并下载电脑传给你的文件（会变成下载连结）\n\n"
            "4. 手机打开网页后，会自动出现在左边“线上设备”清单\n"
            "    （标记 [手机]），电脑就能直接选它传文字或文件。\n\n"
            "提醒：\n"
            "・若手机连不上，请确认 Windows 防火墙已允许本程序\n"
            "   （勾选“专用网络”）。\n"
            "・这个网页只在局域网内有效，手机和电脑需在同一个 Wi-Fi。"
        ) % web_url

        box = tk.Text(win, font=(FONT, 11), wrap="word", bd=0, padx=18, pady=14,
                      bg="#ffffff", fg=C_TEXT)
        box.pack(fill="both", expand=True)
        box.insert("1.0", content)
        box.config(state="disabled")

        def copy():
            root.clipboard_clear()
            root.clipboard_append(web_url)
            set_status("已复制网址：%s" % web_url)

        make_button(win, "复制网址", copy, primary=False).pack(pady=(0, 14))

    # ---- 传送逻辑（背景运行绪） -------------------------------------------

    def on_send_text():
        peer = selected_peer()
        if not peer:
            messagebox.showwarning(APP_NAME, "请先在左边选择一台线上设备。")
            return
        text = text_input.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning(APP_NAME, "请先输入要传送的文字。")
            return
        threading.Thread(target=_send_text_worker,
                         args=(peer, text), daemon=True).start()

    def _send_text_worker(peer, text):
        try:
            if peer["kind"] == "web":
                ok = web.push_text(peer["id"], device_name, text)
            else:
                ok = send_text(peer["ip"], peer["port"], device_name, text)
            events.put({"type": "send-text-done", "ok": ok,
                        "peer": peer["name"], "text": text})
        except Exception as e:
            events.put({"type": "send-error", "what": "文字",
                        "peer": peer["name"], "error": str(e)})

    def on_send_files():
        peer = selected_peer()
        if not peer:
            messagebox.showwarning(APP_NAME, "请先在左边选择一台线上设备。")
            return
        files = [p for p in state["files"] if os.path.exists(p)]
        if not files:
            messagebox.showwarning(APP_NAME, "请先选择要传送的文件。")
            return
        if state["sending"]:
            messagebox.showinfo(APP_NAME, "目前正在传送中，请稍候。")
            return
        state["sending"] = True
        btn_send_file.config(state="disabled")
        btn_send_text.config(state="disabled")
        threading.Thread(target=_send_files_worker,
                         args=(peer, files), daemon=True).start()

    def _send_files_worker(peer, files):
        total_all = sum(os.path.getsize(p) for p in files)
        sent_before = 0
        try:
            for path in files:
                name = os.path.basename(path)
                size = os.path.getsize(path)
                if peer["kind"] == "web":
                    ok = web.push_file(peer["id"], device_name, path)
                else:
                    def cb(sent, total, _base=sent_before, _total=total_all,
                           _name=name):
                        events.put({"type": "send-progress",
                                    "sent": _base + sent, "total": _total,
                                    "name": _name})
                    ok = send_file(peer["ip"], peer["port"], device_name, path, cb)
                sent_before += size
                events.put({"type": "send-file-done", "ok": ok,
                            "peer": peer["name"], "name": name, "size": size})
                if not ok:
                    break
            events.put({"type": "send-all-done", "ok": True, "peer": peer["name"]})
        except Exception as e:
            events.put({"type": "send-error", "what": "文件",
                        "peer": peer["name"], "error": str(e)})
            events.put({"type": "send-all-done", "ok": False, "peer": peer["name"]})

    # ---- 事件循环（把背景运行绪的事件安全地更新到 UI） ----------------------

    def poll_events():
        try:
            while True:
                ev = events.get_nowait()
                handle_event(ev)
        except queue.Empty:
            pass
        refresh_peers()
        root.after(500, poll_events)

    def handle_event(ev):
        t = ev["type"]
        if t == "recv-text":
            preview = ev["text"].replace("\n", " ")
            if len(preview) > 60:
                preview = preview[:60] + "…"
            add_history("收到", "%s（%s）" % (ev["from"], ev["ip"]), "文字",
                        preview, len(ev["text"].encode("utf-8")), text=ev["text"])
            set_status("收到来自 %s 的文字消息" % ev["from"])
        elif t == "recv-file":
            add_history("收到", "%s（%s）" % (ev["from"], ev["ip"]), "文件",
                        ev["name"], ev["size"], path=ev["path"])
            set_status("收到文件：%s（%s），已存到接收文件夹"
                       % (ev["name"], human_size(ev["size"])))
        elif t == "send-progress":
            pct = ev["sent"] * 1000 // max(ev["total"], 1)
            progress["value"] = pct
            prog_label.config(text="%s　%s / %s" % (ev["name"],
                              human_size(ev["sent"]), human_size(ev["total"])))
        elif t == "send-text-done":
            if ev["ok"]:
                preview = ev["text"].replace("\n", " ")
                if len(preview) > 60:
                    preview = preview[:60] + "…"
                add_history("送出", ev["peer"], "文字", preview,
                            len(ev["text"].encode("utf-8")), text=ev["text"])
                text_input.delete("1.0", "end")
                set_status("文字已传送给 %s" % ev["peer"])
            else:
                set_status("传送失败：对方已离线或回应异常")
        elif t == "send-file-done":
            add_history("送出", ev["peer"], "文件", ev["name"], ev["size"])
            set_status("文件已传送：%s" % ev["name"])
        elif t == "send-all-done":
            state["sending"] = False
            btn_send_file.config(state="normal")
            btn_send_text.config(state="normal")
            if ev["ok"]:
                progress["value"] = 1000
                prog_label.config(text="全部完成")
                set_status("所有文件已成功传送给 %s" % ev["peer"])
                state["files"] = []
                update_files_label()
            else:
                prog_label.config(text="传送中断")
        elif t == "send-error":
            progress["value"] = 0
            prog_label.config(text="")
            set_status("传送%s失败：%s" % (ev["what"], ev["error"]))
            messagebox.showerror(
                APP_NAME,
                "传送%s给 %s 失败：\n%s\n\n"
                "请确认：\n・对方程序仍在运行\n・双方在同一个 Wi-Fi\n"
                "・Windows 防火墙已允许本程序（专用网络）"
                % (ev["what"], ev["peer"], ev["error"]))

    def on_close():
        discovery.stop()
        server.stop()
        web.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(500, poll_events)
    root.mainloop()


if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        # 打包成无主控台 EXE 时，sys.stdout/stderr 可能为 None；
        # 任何写入都会让程序闪退。统一导向记录档，顺便留下当机堆叠。
        import faulthandler
        _log = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")),
                            "lanqs_debug.log")
        try:
            _lf = open(_log, "w", encoding="utf-8")
            faulthandler.enable(_lf)
            sys.stdout = _lf
            sys.stderr = _lf
        except Exception:
            pass
    run_app()
