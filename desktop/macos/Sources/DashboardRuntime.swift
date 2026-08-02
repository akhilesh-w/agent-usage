import AppKit
import Combine
import Foundation

@MainActor
final class DashboardRuntime: ObservableObject {
    enum State: Equatable {
        case starting
        case running(URL)
        case failed(String)
    }

    @Published private(set) var state: State = .starting
    @Published private(set) var statusMessage: String?

    private var process: Process?
    private let host = "127.0.0.1"
    private let port = 7840
    private var terminatingObserver: NSObjectProtocol?

    init() {
        terminatingObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.stop()
            }
        }
    }

    deinit {
        if let terminatingObserver {
            NotificationCenter.default.removeObserver(terminatingObserver)
        }
    }

    func start() {
        guard process == nil else { return }
        state = .starting
        statusMessage = "Launching local scanner…"

        do {
            try spawnServer()
        } catch {
            state = .failed(error.localizedDescription)
            return
        }

        Task {
            do {
                let url = try await waitForServer(
                    url: URL(string: "http://\(host):\(port)/")!,
                    attempts: 80,
                    delayNs: 250_000_000
                )
                statusMessage = nil
                state = .running(url)
            } catch {
                stop()
                state = .failed(
                    error.localizedDescription
                        + "\n\nMake sure Python 3 is installed and the repo is intact."
                )
            }
        }
    }

    func restart() {
        stop()
        start()
    }

    func stop() {
        guard let process else { return }
        if process.isRunning {
            process.terminate()
            // Give it a moment; then force if needed
            DispatchQueue.global().asyncAfter(deadline: .now() + 1.5) {
                if process.isRunning {
                    process.interrupt()
                }
            }
        }
        self.process = nil
    }

    // MARK: - Private

    private func repoRoot() throws -> URL {
        // 1) AGENT_READOUT_ROOT
        if let env = ProcessInfo.processInfo.environment["AGENT_READOUT_ROOT"], !env.isEmpty {
            return URL(fileURLWithPath: env, isDirectory: true)
        }

        // 2) Next to the .app: AgentUsage.app/../../ (dev) or Resources
        let bundle = Bundle.main.bundleURL
        let resourceRoot = bundle.appendingPathComponent("Contents/Resources", isDirectory: true)
        if FileManager.default.fileExists(atPath: resourceRoot.appendingPathComponent("agent_usage").path) {
            return resourceRoot
        }

        // 3) Walk up from executable / source layout: desktop/macos → repo root
        var url = bundle
        for _ in 0 ..< 8 {
            let candidate = url
            if FileManager.default.fileExists(atPath: candidate.appendingPathComponent("agent_usage").path),
               FileManager.default.fileExists(atPath: candidate.appendingPathComponent("web/static").path)
            {
                return candidate
            }
            url.deleteLastPathComponent()
        }

        // 4) cwd
        let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
        if FileManager.default.fileExists(atPath: cwd.appendingPathComponent("agent_usage").path) {
            return cwd
        }

        throw RuntimeError(
            "Could not find Agent Usage project root (agent_usage/ + web/static/). "
                + "Set AGENT_READOUT_ROOT or run the app built from this repo."
        )
    }

    private func pythonPath() -> String {
        if let env = ProcessInfo.processInfo.environment["AGENT_READOUT_PYTHON"], !env.isEmpty {
            return env
        }
        // Prefer Homebrew python if present
        for candidate in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"] {
            if FileManager.default.isExecutableFile(atPath: candidate) {
                return candidate
            }
        }
        return "python3"
    }

    private func spawnServer() throws {
        let root = try repoRoot()
        let python = pythonPath()
        statusMessage = "Using \(python)"

        let process = Process()
        process.currentDirectoryURL = root
        process.executableURL = URL(fileURLWithPath: python)

        // -c keeps us from needing a separate runner script in PATH
        let py = """
        import os, sys
        root = os.environ.get("AGENT_READOUT_ROOT") or r"\(root.path)"
        sys.path.insert(0, root)
        os.environ["AGENT_READOUT_ROOT"] = root
        from agent_usage.server_app import run_browser
        run_browser(open_browser=False, port=\(port))
        """
        process.arguments = ["-c", py]

        var env = ProcessInfo.processInfo.environment
        env["AGENT_READOUT_ROOT"] = root.path
        let path = env["PATH"] ?? "/usr/bin:/bin"
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:\(path)"
        env["PYTHONPATH"] = root.path
        process.environment = env

        let err = Pipe()
        let out = Pipe()
        process.standardError = err
        process.standardOutput = out
        process.standardInput = FileHandle.nullDevice

        try process.run()
        self.process = process
    }

    private func waitForServer(url: URL, attempts: Int, delayNs: UInt64) async throws -> URL {
        var last: Error?
        for i in 0 ..< attempts {
            statusMessage = "Waiting for dashboard… (\(i + 1)/\(attempts))"
            var req = URLRequest(url: url)
            req.timeoutInterval = 1.5
            do {
                let (_, resp) = try await URLSession.shared.data(for: req)
                if let http = resp as? HTTPURLResponse, (200 ..< 500).contains(http.statusCode) {
                    return url
                }
            } catch {
                last = error
            }
            if let process, !process.isRunning {
                throw RuntimeError("Python process exited early. Is Python 3 installed?")
            }
            try await Task.sleep(nanoseconds: delayNs)
        }
        throw RuntimeError(
            "Dashboard did not become ready on \(url.absoluteString). \(last?.localizedDescription ?? "")"
        )
    }
}

struct RuntimeError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}
