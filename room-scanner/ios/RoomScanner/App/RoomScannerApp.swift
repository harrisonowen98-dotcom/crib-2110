import SwiftUI

@main
struct RoomScannerApp: App {
    @StateObject private var store = ScanStore()

    var body: some Scene {
        WindowGroup {
            LibraryView()
                .environmentObject(store)
                .preferredColorScheme(.dark)
        }
    }
}
