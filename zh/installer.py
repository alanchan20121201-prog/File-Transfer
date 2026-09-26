# -*- coding: utf-8 -*-
"""
區網快傳 安裝程式
=================
內嵌免安裝版主程式，執行後：
  1. 解出主程式到使用者選擇的安裝目錄（預設 %LOCALAPPDATA%\Programs\LANQuickShare）
  2. 建立桌面 / 開始功能表捷徑（含圖示）
  3. 寫入 Windows「應用程式與功能」卸載登錄與 uninstall.cmd
全程不需要系統管理員權限。
"""
import ctypes
import os
import queue
import shutil
import subprocess
import sys
import threading
import winreg

APP_NAME = "區網快傳"
APP_NAME_EN = "LAN Quick Share"
APP_ID = "LANQuickShare"
VERSION = "1.0.0"
EXE_NAME = "LANQuickShare.exe"
UNINSTALL_REG = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\LANQuickShare"


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def default_install_dir():
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, "Programs", APP_ID)


def desktop_dir():
    """相容 OneDrive 桌面重新導向的情況。"""
    cands = [os.path.join(os.path.expanduser("~"), "Desktop"),
             os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")]
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0]


def make_shortcut(lnk_path, target, workdir, icon, description):
    """透過 PowerShell COM（WScript.Shell）建立 .lnk 捷徑。"""
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$s = $ws.CreateShortcut('%s'); "
        "$s.TargetPath = '%s'; "
        "$s.WorkingDirectory = '%s'; "
        "$s.IconLocation = '%s,0'; "
        "$s.Description = '%s'; "
        "$s.Save()" % (lnk_path, target, workdir, icon, description)
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        check=True, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def write_uninstall_reg(install_dir, exe_path):
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REG)
    values = [
        ("DisplayName", winreg.REG_SZ, "%s %s" % (APP_NAME, APP_NAME_EN)),
        ("DisplayVersion", winreg.REG_SZ, VERSION),
        ("Publisher", winreg.REG_SZ, APP_NAME_EN),
        ("InstallLocation", winreg.REG_SZ, install_dir),
        ("DisplayIcon", winreg.REG_SZ, exe_path),
        ("UninstallString", winreg.REG_SZ,
         'cmd.exe /c "%s"' % os.path.join(install_dir, "uninstall.cmd")),
        ("NoModify", winreg.REG_DWORD, 1),
        ("NoRepair", winreg.REG_DWORD, 1),
    ]
    for name, typ, val in values:
        winreg.SetValueEx(key, name, 0, typ, val)
    winreg.CloseKey(key)


def write_uninstall_cmd(install_dir, desktop_lnk):
    """卸載腳本：以 %~dp0 取得自身目錄，內容維持純 ASCII。"""
    content = "\r\n".join([
        "@echo off",
        'set "DIR=%~dp0"',
        'if "%DIR:~-1%"=="\\" set "DIR=%DIR:~0,-1%"',
        'del /f /q "%s" 2>nul' % desktop_lnk,
        r'rmdir /s /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\LANQuickShare" 2>nul',
        'reg delete "HKCU\\%s" /f 2>nul' % UNINSTALL_REG,
        'start "" /b cmd /c "timeout /t 1 /nobreak >nul & rmdir /s /q ""%DIR%"""',
        "exit",
        "",
    ])
    with open(os.path.join(install_dir, "uninstall.cmd"), "w", encoding="ascii") as f:
        f.write(content)


def run_installer():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID + ".Setup")
    except Exception:
        pass

    root = tk.Tk()
    root.title("%s 安裝程式" % APP_NAME)
    try:
        root.iconbitmap(default=resource_path("icon.ico"))
    except Exception:
        pass
    root.resizable(False, False)
    root.configure(bg="#eef1f6")
    # 視窗置中
    w, h = 620, 470
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))

    FONT = "Microsoft JhengHei UI"
    C_PRIMARY = "#2563eb"
    C_TEXT = "#111827"
    C_MUTED = "#6b7280"
    C_CARD = "#ffffff"
    C_BORDER = "#d9dee7"

    events = queue.Queue()

    # ---- 頁首 ----
    header = tk.Frame(root, bg=C_PRIMARY, height=86)
    header.pack(fill="x")
    header.pack_propagate(False)
    icon_img = None
    try:
        from PIL import Image, ImageTk
        _img = Image.open(resource_path("icon.ico")).resize((48, 48), Image.LANCZOS)
        icon_img = ImageTk.PhotoImage(_img)
        tk.Label(header, image=icon_img, bg=C_PRIMARY).pack(side="left", padx=(20, 12))
    except Exception:
        pass
    hb = tk.Frame(header, bg=C_PRIMARY)
    hb.pack(side="left")
    tk.Label(hb, text="%s %s" % (APP_NAME, APP_NAME_EN),
             font=(FONT, 16, "bold"), bg=C_PRIMARY, fg="#ffffff").pack(anchor="w")
    tk.Label(hb, text="安裝程式  v%s　｜　免登入・免雲端・同 Wi-Fi 即傳" % VERSION,
             font=(FONT, 10), bg=C_PRIMARY, fg="#dbeafe").pack(anchor="w")

    # ---- 內容 ----
    body = tk.Frame(root, bg=C_CARD, highlightbackground=C_BORDER, highlightthickness=1)
    body.pack(fill="both", expand=True, padx=18, pady=18)

    tk.Label(body, text="本程式會將「%s」安裝到你的電腦，並建立卸載項目。\n"
                        "安裝後在任何裝置上開啟主程式，就能與同一個 Wi-Fi 下的"
                        "電腦、手機互傳文字與檔案。" % APP_NAME,
             font=(FONT, 10), bg=C_CARD, fg=C_TEXT, justify="left").pack(
        anchor="w", padx=16, pady=(14, 10))

    tk.Label(body, text="安裝位置：", font=(FONT, 10, "bold"),
             bg=C_CARD, fg=C_TEXT).pack(anchor="w", padx=16)
    path_row = tk.Frame(body, bg=C_CARD)
    path_row.pack(fill="x", padx=16, pady=(2, 8))
    path_var = tk.StringVar(value=default_install_dir())
    path_entry = tk.Entry(path_row, textvariable=path_var, font=(FONT, 10),
                          highlightthickness=1, highlightbackground=C_BORDER, bd=0)
    path_entry.pack(side="left", fill="x", expand=True, ipady=4)

    def browse():
        d = filedialog.askdirectory(initialdir=path_var.get() or os.path.expanduser("~"))
        if d:
            path_var.set(os.path.join(d, APP_ID) if os.path.basename(d) != APP_ID else d)

    tk.Button(path_row, text="瀏覽…", command=browse, font=(FONT, 9), bd=0,
              bg="#e5e7eb", fg=C_TEXT, padx=12, pady=4, cursor="hand2").pack(
        side="left", padx=(8, 0))

    var_desktop = tk.BooleanVar(value=True)
    var_startmenu = tk.BooleanVar(value=True)
    var_launch = tk.BooleanVar(value=True)
    for text, var in (("建立桌面捷徑", var_desktop),
                      ("建立開始功能表捷徑", var_startmenu),
                      ("安裝完成後立即啟動", var_launch)):
        tk.Checkbutton(body, text=text, variable=var, font=(FONT, 10),
                       bg=C_CARD, fg=C_TEXT, activebackground=C_CARD,
                       selectcolor=C_CARD).pack(anchor="w", padx=16, pady=1)

    prog = ttk.Progressbar(body, mode="determinate", maximum=100)
    prog.pack(fill="x", padx=16, pady=(14, 4))
    status_var = tk.StringVar(value="準備就緒")
    tk.Label(body, textvariable=status_var, font=(FONT, 9), bg=C_CARD,
             fg=C_MUTED, anchor="w").pack(fill="x", padx=16)

    btn_row = tk.Frame(body, bg=C_CARD)
    btn_row.pack(fill="x", padx=16, pady=(12, 14))
    btn_install = tk.Button(btn_row, text="立即安裝", font=(FONT, 11, "bold"),
                            bd=0, bg=C_PRIMARY, fg="#ffffff", padx=26, pady=9,
                            cursor="hand2", activebackground="#1d4ed8",
                            activeforeground="#ffffff")
    btn_install.pack(side="right")

    # ---- 安裝流程（背景執行緒） ---------------------------------------------
    def do_install(target, mk_desktop, mk_startmenu, launch):
        try:
            events.put(("progress", 10, "建立安裝目錄…"))
            os.makedirs(target, exist_ok=True)

            events.put(("progress", 35, "解出主程式…"))
            exe_path = os.path.join(target, EXE_NAME)
            shutil.copyfile(resource_path("app_payload.exe"), exe_path)
            shutil.copyfile(resource_path("icon.ico"),
                            os.path.join(target, "icon.ico"))

            desktop_lnk = os.path.join(desktop_dir(), APP_ID + ".lnk")
            if mk_desktop:
                events.put(("progress", 60, "建立桌面捷徑…"))
                make_shortcut(desktop_lnk, exe_path, target, exe_path,
                              "%s %s" % (APP_NAME, APP_NAME_EN))
            if mk_startmenu:
                events.put(("progress", 75, "建立開始功能表捷徑…"))
                sm_dir = os.path.join(os.environ["APPDATA"], "Microsoft",
                                      "Windows", "Start Menu", "Programs", APP_ID)
                os.makedirs(sm_dir, exist_ok=True)
                make_shortcut(os.path.join(sm_dir, APP_ID + ".lnk"),
                              exe_path, target, exe_path,
                              "%s %s" % (APP_NAME, APP_NAME_EN))

            events.put(("progress", 90, "寫入卸載資訊…"))
            write_uninstall_cmd(target, desktop_lnk)
            write_uninstall_reg(target, exe_path)

            if launch:
                subprocess.Popen([exe_path], cwd=target)

            events.put(("progress", 100, ""))
            events.put(("done", True, target))
        except Exception as e:
            events.put(("done", False, str(e)))

    def on_install():
        target = path_var.get().strip()
        if not target:
            messagebox.showwarning(APP_NAME, "請選擇安裝位置。")
            return
        btn_install.config(state="disabled", text="安裝中…")
        threading.Thread(target=do_install,
                         args=(target, var_desktop.get(),
                               var_startmenu.get(), var_launch.get()),
                         daemon=True).start()

    btn_install.config(command=on_install)

    def poll():
        try:
            while True:
                ev = events.get_nowait()
                if ev[0] == "progress":
                    prog["value"] = ev[1]
                    if ev[2]:
                        status_var.set(ev[2])
                elif ev[0] == "done":
                    if ev[1]:
                        status_var.set("安裝完成！")
                        btn_install.config(text="完成", state="normal",
                                           command=root.destroy)
                        messagebox.showinfo(
                            APP_NAME,
                            "安裝完成！\n\n安裝位置：%s\n\n"
                            "首次啟動若 Windows 防火牆詢問，\n"
                            "請勾選「私人網路」並按允許，\n"
                            "區網內的其他裝置才找得到你。" % ev[2])
                    else:
                        status_var.set("安裝失敗")
                        btn_install.config(state="normal", text="重試")
                        messagebox.showerror(APP_NAME, "安裝失敗：\n%s" % ev[2])
        except queue.Empty:
            pass
        root.after(200, poll)

    root.after(200, poll)
    root.mainloop()


if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        # 無主控台 EXE 的 stdout/stderr 可能為 None，統一導向記錄檔
        import faulthandler
        _log = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")),
                            "lanqs_setup_debug.log")
        try:
            _lf = open(_log, "w", encoding="utf-8")
            faulthandler.enable(_lf)
            sys.stdout = _lf
            sys.stderr = _lf
        except Exception:
            pass
    run_installer()
