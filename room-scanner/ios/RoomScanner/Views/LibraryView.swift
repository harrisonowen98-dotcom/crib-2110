import SwiftUI

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController { UIActivityViewController(activityItems: items, applicationActivities: nil) }
    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}

struct LibraryView: View {
    @EnvironmentObject var store: ScanStore
    @State private var showNew = false
    @State private var newName = "Room"
    @State private var scanning: String?
    @State private var renaming: ScanItem?
    @State private var renameText = ""
    @State private var shareURL: URL?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            List {
                if store.scans.isEmpty {
                    ContentUnavailableView("No scans yet", systemImage: "camera.metering.matrix", description: Text("Tap + to scan a room. Every save creates a new version; nothing is overwritten."))
                }
                ForEach(store.scans) { s in
                    HStack(spacing: 12) {
                        Group {
                            if let img = store.thumbnail(for: s) { Image(uiImage: img).resizable().scaledToFill() } else { Color.gray.opacity(0.3) }
                        }.frame(width: 80, height: 60).clipShape(RoundedRectangle(cornerRadius: 8))
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(s.name) v\(s.version)").font(.headline)
                            Text("\(s.date) · \(s.frames) photos · \(s.rooms) room\(s.rooms == 1 ? "" : "s")").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button { export(s) } label: { Image(systemName: "square.and.arrow.up") }.buttonStyle(.borderless)
                    }
                    .swipeActions { Button(role: .destructive) { store.delete(s) } label: { Label("Delete", systemImage: "trash") } }
                    .contextMenu {
                        Button("Rename") { renaming = s; renameText = s.name }
                        Button("Export .zip") { export(s) }
                        Button("Delete", role: .destructive) { store.delete(s) }
                    }
                }
            }
            .navigationTitle("Room Scanner")
            .toolbar { Button { showNew = true } label: { Image(systemName: "plus") } }
            .alert("New scan", isPresented: $showNew) {
                TextField("Room name", text: $newName)
                Button("Start") { scanning = newName }
                Button("Cancel", role: .cancel) {}
            } message: { Text("Next version number is added automatically (e.g. Office v4).") }
            .alert("Rename", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
                TextField("Name", text: $renameText)
                Button("Save") { if let r = renaming { store.rename(r, to: renameText) }; renaming = nil }
                Button("Cancel", role: .cancel) { renaming = nil }
            }
            .alert("Export failed", isPresented: .constant(error != nil)) { Button("OK") { error = nil } } message: { Text(error ?? "") }
            .fullScreenCover(item: Binding(get: { scanning.map { Wrapper(name: $0) } }, set: { if $0 == nil { scanning = nil; store.reload() } })) { w in
                ScanView(name: w.name).environmentObject(store)
            }
            .sheet(item: Binding(get: { shareURL.map { Wrapper(name: $0.path) } }, set: { if $0 == nil { shareURL = nil } })) { _ in
                if let u = shareURL { ShareSheet(items: [u]) }
            }
        }
    }
    struct Wrapper: Identifiable { let name: String; var id: String { name } }

    private func export(_ s: ScanItem) {
        do { shareURL = try store.zip(s) } catch { self.error = error.localizedDescription }
    }
}
