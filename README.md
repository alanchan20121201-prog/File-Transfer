# 區網快傳 LAN Quick Share

> 免登入、免雲端、同一個 Wi-Fi 即可互傳文字與任意檔案的輕量工具。
> A lightweight, login-free and cloud-free tool for sharing text and any files over the same Wi-Fi.

<details open>
<summary>中文</summary>

## 功能

### 核心
- 點對點（P2P）直連傳輸：文字 + 任意類型檔案，64KB 串流，支援大檔
- 免登入、免雲端：不經任何第三方伺服器
- 角色合一：開啟即同時是傳送端也是接收端

### 裝置發現
- UDP 廣播自動發現同網段裝置（含 /24 子網廣播）
- 自動排除自己、逾時自動剔除離線裝置
- 手動輸入 IP／IP:埠 連線（廣播被擋時的備援）

### 電腦 ↔ 電腦
- 傳送文字訊息
- 傳送任意檔案（可複選、進度條、內容一致）
- 接收檔案自動存入「文件／LANQuickShare Received」，重名自動加 (1)(2)…
- 傳輸記錄（方向／裝置／類型／內容／大小），雙擊開啟、可複製文字

### 手機網頁
- 內建網頁伺服器（中文版 :8080／英文版 :8181）
- 手機瀏覽器輸入網址即可用，免裝 App、免登入
- 上傳照片／檔案到電腦（可複選、顯示進度）
- 傳送文字到電腦
- 接收電腦推播的文字；檔案變成下載連結（SSE 即時推播）
- 手機自動出現在電腦端裝置清單

### 介面
- 全螢幕最大化、無傳統選單列
- 自訂圖示（視窗標題列 + 工作列）
- 拖放傳檔（從檔案總管拖進傳送區）
- 中英雙語介面（兩個獨立版本，網頁埠不同）

### 安裝
- 免安裝版：單一 EXE 直接執行
- 安裝程式：自選路徑、桌面／開始功能表捷徑、卸載項目，免系統管理員

## 技術架構
- 語言：Python 3.12 + tkinter（GUI）
- 發現：UDP 廣播（埠 45678）
- 傳輸：HTTP（埠 45679 起自動避讓）
- 網頁：內建 HTTP 伺服器（8080／8181）+ SSE 推播
- 打包：PyInstaller（--onefile --noconsole）

## 常見問題
1. **找不到其他裝置**：確認雙方同一個 Wi-Fi、允許防火牆（私人網路），或用手動輸入 IP。
2. **手機打不開網址**：確認網址就是電腦上方顯示的那個（含埠號）、允許防火牆、同一個 Wi-Fi。
3. **第一次開啟被擋（Device Guard）**&#8203;：未簽名程式受組織原則攔截，等幾分鐘重試、用安裝程式，或請 IT 加入允許清單。
4. **傳檔失敗或中斷**：確認對方程式仍在執行、雙方仍在同一個 Wi-Fi、防火牆已允許。
5. **雙開衝突**：不會，第二個程式會自動改用下一個埠。
6. **檔案存哪**：文件／LANQuickShare Received，可在程式內開啟資料夾。
7. **重名覆蓋**：不會，自動改成「檔名 (1)」「檔名 (2)」…。
8. **手機上傳上限**：單次 2GB，更大請用電腦端互傳（無上限）。
9. **手機要裝 App 嗎**：不用，瀏覽器開網址即可。

</details>

<details>
<summary>English</summary>

## Features

### Core
- Peer-to-peer direct transfer: text + files of any type, 64KB streaming, large files supported
- No login, no cloud: no third-party servers involved
- Dual role: every instance is both a sender and a receiver

### Discovery
- UDP broadcast auto-discovers devices on the same subnet (incl. /24 subnet broadcast)
- Auto-excludes self and prunes offline devices on timeout
- Manual IP / IP:port connection (fallback when broadcast is blocked)

### PC to PC
- Send text messages
- Send any files (multi-select, progress bar, content-verified)
- Received files saved to "Documents/LANQuickShare Received"; duplicates auto-renamed (1)(2)…
- Transfer history (direction/device/type/content/size); double-click to open, copy text

### Mobile Web
- Built-in web server (Chinese :8080 / English :8181)
- Open the URL in a mobile browser — no app, no login
- Upload photos/files to the computer (multi-select, progress shown)
- Send text to the computer
- Receive pushed text; files appear as download links (SSE real-time push)
- The phone appears in the computer's device list automatically

### Interface
- Maximized fullscreen, no traditional menu bar
- Custom icon (title bar + taskbar)
- Drag-and-drop sending (drag from File Explorer into the send area)
- Chinese & English UI (two separate builds, different web ports)

### Installation
- Portable: single EXE, run directly
- Installer: custom path, desktop/Start Menu shortcuts, uninstall entry, no admin needed

## Tech Stack
- Language: Python 3.12 + tkinter (GUI)
- Discovery: UDP broadcast (port 45678)
- Transfer: HTTP (port 45679, auto-increment if busy)
- Web: built-in HTTP server (8080/8181) + SSE push
- Packaging: PyInstaller (--onefile --noconsole)

## FAQ
1. **Can't find other devices**: same Wi-Fi, allow the firewall (Private), or connect via manual IP.
2. **Phone can't open the URL**: use the exact URL shown in the app (with port), allow the firewall, same Wi-Fi.
3. **Blocked on first launch (Device Guard)**&#8203;: unsigned app blocked by org policy; wait and retry, use the installer, or ask IT to allowlist it.
4. **Transfer fails or drops**: keep both apps open on the same Wi-Fi with the firewall allowed.
5. **Two instances conflict?**&#8203; No — the second auto-switches to the next port.
6. **Where are files saved?**&#8203; Documents/LANQuickShare Received (open it from the app).
7. **Same-named files overwritten?**&#8203; No — auto-renamed "name (1)", "name (2)", …
8. **Web upload limit**: 2GB per upload; use PC-to-PC (unlimited) for larger files.
9. **Does the phone need an app?**&#8203; No — just open the URL in the browser.

</details>
