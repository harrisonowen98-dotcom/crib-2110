import SwiftUI

struct ScanView: View {
    @EnvironmentObject var store: ScanStore
    @StateObject private var session = ScanSession()
    @Environment(\.dismiss) private var dismiss
    let name: String
    @State private var confirmExit = false
    @State private var startError: String?

    var body: some View {
        ZStack {
            ARScanView(session: session).ignoresSafeArea()
            VStack(spacing: 0) {
                topBar
                Spacer()
                warningStrip
                bottomBar
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
            if case .saving = session.state { savingOverlay }
            if case .saved(let folder) = session.state { savedOverlay(folder) }
            if case .failed(let msg) = session.state { Text(msg).padding().background(.red.opacity(0.8), in: RoundedRectangle(cornerRadius: 12)).foregroundStyle(.white) }
        }
        .statusBarHidden()
        .task {
            do { try session.start(store: store, name: name) } catch { startError = error.localizedDescription }
        }
        .alert("Can’t start scan", isPresented: .constant(startError != nil)) { Button("OK") { dismiss() } } message: { Text(startError ?? "") }
        .confirmationDialog("Discard this scan?", isPresented: $confirmExit, titleVisibility: .visible) {
            Button("Discard", role: .destructive) { session.cancel(); dismiss() }
            Button("Keep scanning", role: .cancel) {}
        } message: { Text("Photos captured so far stay on disk as an unfinished folder; nothing is overwritten.") }
    }

    // MARK: top
    private var topBar: some View {
        HStack(alignment: .top) {
            Button { confirmExit = true } label: { Image(systemName: "xmark").font(.headline).padding(12).background(.ultraThinMaterial, in: Circle()) }
            Spacer()
            VStack(alignment: .trailing, spacing: 8) { statusCard; MiniMap(session: session).frame(width: 110, height: 110) }
        }
    }

    private var statusCard: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle().stroke(.white.opacity(0.2), lineWidth: 6)
                Circle().trim(from: 0, to: CGFloat(session.detail)).stroke(session.detail > 0.8 ? Color.green : Color.blue, style: StrokeStyle(lineWidth: 6, lineCap: .round)).rotationEffect(.degrees(-90))
                Text("\(Int(session.detail * 100))%").font(.caption.bold())
            }.frame(width: 52, height: 52)
            VStack(alignment: .leading, spacing: 2) {
                Text(name).font(.headline)
                Text("photo detail").font(.caption).foregroundStyle(.secondary)
                Text("\(session.wallCount) walls measured · \(session.roomCount) rooms · \(session.photoCount) photos").font(.caption2).foregroundStyle(.secondary)
            }
        }
        .padding(12).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
    }

    // MARK: bottom
    private var warningStrip: some View {
        VStack(spacing: 6) {
            ForEach(Array(session.warnings).sorted(), id: \.self) { w in
                Label(w, systemImage: "exclamationmark.triangle.fill").font(.footnote.bold()).padding(.horizontal, 12).padding(.vertical, 6)
                    .background(.orange.opacity(0.9), in: Capsule()).foregroundStyle(.black)
            }
            if let h = session.hint {
                Text(h).font(.footnote).multilineTextAlignment(.center).padding(10).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
            }
        }.padding(.bottom, 8)
    }

    private var bottomBar: some View {
        HStack(spacing: 18) {
            Menu {
                Toggle("Show LiDAR mesh", isOn: $session.showMesh)
                Toggle("Lock exposure & white balance", isOn: $session.lockExposure)
                Button("Finish room & start another") { Task { await session.finishRoom(startNext: true) } }
            } label: { Image(systemName: "slider.horizontal.3").font(.title3).frame(width: 48, height: 48).background(.ultraThinMaterial, in: Circle()) }

            Button { session.requestManualShutter() } label: { Image(systemName: "camera.fill").font(.title2).frame(width: 60, height: 60).background(.ultraThinMaterial, in: Circle()) }

            Button { session.state == .paused ? session.resume() : session.pause() } label: {
                Image(systemName: session.state == .paused ? "play.fill" : "pause.fill").font(.title3).frame(width: 48, height: 48).background(.ultraThinMaterial, in: Circle())
            }

            Button { Task { await session.save(store: store) } } label: {
                Text("Save room").font(.headline).padding(.horizontal, 22).padding(.vertical, 14).background(Color.blue, in: Capsule()).foregroundStyle(.white)
            }
        }
    }

    // MARK: overlays
    private var savingOverlay: some View {
        VStack(spacing: 12) { ProgressView().scaleEffect(1.4); Text("Saving…") }.padding(28).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18))
    }
    private func savedOverlay(_ folder: String) -> some View {
        VStack(spacing: 14) {
            Image(systemName: "checkmark.circle.fill").font(.system(size: 44)).foregroundStyle(.green)
            Text("Saved").font(.title3.bold()); Text(folder).font(.footnote).foregroundStyle(.secondary)
            Button("Done") { dismiss() }.buttonStyle(.borderedProminent)
        }.padding(26).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18))
    }
}

/// Top-down mini-map of surfaces seen so far: green = well covered, red = needs more photos. Blue arrow = you.
struct MiniMap: View {
    @ObservedObject var session: ScanSession
    var body: some View {
        Canvas { ctx, size in
            let pts = session.mapPoints
            guard !pts.isEmpty else { return }
            let xs = pts.map(\.x) + [session.cameraXZ.x], zs = pts.map(\.z) + [session.cameraXZ.y]
            let minX = xs.min()!, maxX = xs.max()!, minZ = zs.min()!, maxZ = zs.max()!
            let span = max(maxX - minX, maxZ - minZ, 1), pad: CGFloat = 8
            let scale = (min(size.width, size.height) - pad * 2) / CGFloat(span)
            func map(_ x: Float, _ z: Float) -> CGPoint { CGPoint(x: pad + CGFloat(x - minX) * scale, y: pad + CGFloat(z - minZ) * scale) }
            for p in pts {
                let c = map(p.x, p.z)
                ctx.fill(Path(ellipseIn: CGRect(x: c.x - 1.4, y: c.y - 1.4, width: 2.8, height: 2.8)), with: .color(Color(red: Double(1 - p.progress), green: Double(0.25 + 0.65 * p.progress), blue: 0.35)))
            }
            let me = map(session.cameraXZ.x, session.cameraXZ.y)
            var arrow = Path(); arrow.move(to: CGPoint(x: 0, y: -7)); arrow.addLine(to: CGPoint(x: 5, y: 5)); arrow.addLine(to: CGPoint(x: -5, y: 5)); arrow.closeSubpath()
            ctx.translateBy(x: me.x, y: me.y); ctx.rotate(by: .radians(Double(session.cameraYaw)))
            ctx.fill(arrow, with: .color(.cyan))
        }
        .background(.black.opacity(0.45), in: RoundedRectangle(cornerRadius: 12))
    }
}
