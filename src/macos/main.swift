// Echo — macOS 原生播放器外壳
// 启动内置的 Python 服务，并在窗口内用 WKWebView 显示播放器页面。
// 编译：见 build-app.command（swiftc，无需 Xcode）

import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var server: Process?
    private var logBuffer = ""
    private var loaded = false
    private let startPort = 8756

    // MARK: - 生命周期

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        startServer()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        stopServer()
    }

    // MARK: - 界面

    private func buildWindow() {
        let config = WKWebViewConfiguration()
        // 允许音频自动播放（本页仍以点击播放为主）
        config.mediaTypesRequiringUserActionForPlayback = []
        // 右键可「检查元素」，便于调试
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")

        webView = WKWebView(frame: .zero, configuration: config)
        webView.autoresizingMask = [.width, .height]

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1120, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered,
                          defer: false)
        window.title = "Echo"
        window.minSize = NSSize(width: 680, height: 480)
        window.contentView = webView
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        showMessage(title: "🎧", detail: "正在启动播放器…")
    }

    private func showMessage(title: String, detail: String) {
        let html = """
        <!doctype html><html><head><meta charset="utf-8"></head>
        <body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
                     font-family:-apple-system,'PingFang SC',sans-serif;background:#f7f5f1;color:#1c1b18">
          <div style="text-align:center;max-width:640px;padding:0 24px">
            <div style="font-size:44px">\(title)</div>
            <div style="margin-top:14px;font-size:15px;line-height:1.7;color:#8a857a;
                        white-space:pre-wrap">\(detail)</div>
          </div>
        </body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    private func showError(_ text: String) {
        showMessage(title: "⚠️", detail: text)
    }

    // MARK: - 菜单

    private func buildMenu() {
        let mainMenu = NSMenu()

        let appItem = NSMenuItem()
        mainMenu.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关于 Echo",
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
                        keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "隐藏 Echo",
                        action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出 Echo",
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem()
        mainMenu.addItem(editItem)
        let editMenu = NSMenu(title: "编辑")
        editMenu.addItem(withTitle: "剪切", action: Selector(("cut:")), keyEquivalent: "x")
        editMenu.addItem(withTitle: "拷贝", action: Selector(("copy:")), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: Selector(("paste:")), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: Selector(("selectAll:")), keyEquivalent: "a")
        editItem.submenu = editMenu

        NSApp.mainMenu = mainMenu
    }

    // MARK: - 定位项目目录

    private func projectRoot() -> URL? {
        let fm = FileManager.default
        var candidates: [URL] = []
        // 1) Echo.app 所在目录（推荐把 .app 放在项目根目录）
        candidates.append(Bundle.main.bundleURL.deletingLastPathComponent())
        // 2) 常见位置
        candidates.append(fm.homeDirectoryForCurrentUser.appendingPathComponent("Projects/Echo"))
        candidates.append(URL(fileURLWithPath: "/Users/david/Projects/Echo"))

        for dir in candidates {
            let py = dir.appendingPathComponent(".venv/bin/python")
            let script = dir.appendingPathComponent("src/player.py")
            if fm.fileExists(atPath: py.path) && fm.fileExists(atPath: script.path) {
                return dir
            }
        }
        return nil
    }

    // MARK: - 启动 / 停止 Python 服务

    private func startServer() {
        guard let root = projectRoot() else {
            showError("找不到项目目录。\n\n请把 Echo.app 放回项目根目录（与 src/、.venv/ 同级），或把项目放在 ~/Projects/Echo。")
            return
        }

        let proc = Process()
        proc.executableURL = root.appendingPathComponent(".venv/bin/python")
        proc.arguments = [root.appendingPathComponent("src/player.py").path,
                          "--serve", "--port", "\(startPort)"]
        proc.currentDirectoryURL = root

        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let chunk = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.handleOutput(chunk) }
        }

        proc.terminationHandler = { [weak self] p in
            DispatchQueue.main.async {
                guard let self = self, !self.loaded else { return }
                let tail = String(self.logBuffer.suffix(800))
                self.showError("Python 服务启动失败（退出码 \(p.terminationStatus)）。\n\n\(tail)")
            }
        }

        do {
            try proc.run()
            server = proc
        } catch {
            showError("无法启动 Python 服务：\(error.localizedDescription)\n\n请先双击一次 launch.command 确认环境可用。")
        }
    }

    private func handleOutput(_ chunk: String) {
        logBuffer += chunk
        if !loaded, let url = extractURL(from: logBuffer) {
            loaded = true
            webView.load(URLRequest(url: url))
        }
    }

    /// 从 `▶️  已启动：http://127.0.0.1:8756/` 里取出地址
    private func extractURL(from text: String) -> URL? {
        guard let range = text.range(of: "http://127.0.0.1:") else { return nil }
        var digits = ""
        for ch in text[range.upperBound...] {
            if ch.isNumber { digits.append(ch) } else { break }
        }
        guard !digits.isEmpty else { return nil }
        return URL(string: "http://127.0.0.1:\(digits)/")
    }

    private func stopServer() {
        guard let proc = server, proc.isRunning else { return }
        proc.terminate()
        let deadline = Date().addingTimeInterval(3)
        while proc.isRunning && Date() < deadline {
            usleep(150_000)
        }
        if proc.isRunning {
            kill(proc.processIdentifier, SIGKILL)
        }
    }
}

// MARK: - 入口

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
