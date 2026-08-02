import AppKit
import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var runtime: DashboardRuntime

    var body: some View {
        ZStack {
            Color(nsColor: NSColor(calibratedRed: 0.10, green: 0.10, blue: 0.11, alpha: 1))
                .ignoresSafeArea()

            switch runtime.state {
            case .starting:
                VStack(spacing: 14) {
                    ProgressView()
                        .controlSize(.large)
                    Text("Starting Agent Readout…")
                        .font(.system(size: 15, weight: .medium))
                        .foregroundStyle(.secondary)
                    if let msg = runtime.statusMessage {
                        Text(msg)
                            .font(.system(size: 12))
                            .foregroundStyle(.tertiary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 40)
                    }
                }
            case .running(let url):
                DashboardWebView(url: url)
                    .ignoresSafeArea()
            case .failed(let message):
                VStack(spacing: 12) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 28))
                        .foregroundStyle(.orange)
                    Text("Couldn’t start")
                        .font(.headline)
                    Text(message)
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: 420)
                    Button("Retry") { runtime.restart() }
                        .keyboardShortcut(.defaultAction)
                }
                .padding(24)
            }
        }
        .onAppear { runtime.start() }
        .onDisappear { /* keep server for window reopen; stopped on app terminate */ }
    }
}
