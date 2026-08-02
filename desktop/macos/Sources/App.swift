import SwiftUI

@main
struct AgentReadoutApp: App {
    @StateObject private var runtime = DashboardRuntime()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(runtime)
                .frame(minWidth: 960, minHeight: 640)
        }
        .defaultSize(width: 1320, height: 900)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }
}
