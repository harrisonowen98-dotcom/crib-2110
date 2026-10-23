# Build & install the Room Scanner iPhone app

**Status:** the app compiles (GitHub's macOS runner builds it with Xcode 16 and the 6 unit tests pass — see the Actions tab). It has **never been run on a real iPhone**, so expect runtime surprises (the RoomPlan-shares-ARSession design below is the main one).
Windows can't build iOS apps, but you don't need a Mac: Route D builds in the cloud and installs from Windows.

Requirements: iPhone with LiDAR (16 Pro Max ✔), iOS 17+, a Mac with Xcode 15+ (or a cloud Mac), an Apple ID (free works).

## Route A — you have a Mac (or borrow one for 20 minutes)
1. On the Mac, copy the whole `room-scanner/ios/` folder somewhere (e.g. Desktop).
2. Open Terminal. Install XcodeGen: `brew install xcodegen` (no Homebrew? install it from https://brew.sh first).
3. In Terminal: `cd ~/Desktop/ios && xcodegen generate`. This creates `RoomScanner.xcodeproj`.
4. Double-click `RoomScanner.xcodeproj` to open Xcode.
5. In the left sidebar click the blue **RoomScanner** project → under TARGETS click **RoomScanner** → tab **Signing & Capabilities**.
6. Tick **Automatically manage signing**. Under **Team** pick your Apple ID (Xcode → Settings → Accounts → **+** → Apple ID if none is listed).
7. Change **Bundle Identifier** to something unique, e.g. `com.yourname.roomscanner` (do the same for the RoomScannerTests target).
8. Plug the iPhone in with a cable. Unlock it, tap **Trust This Computer**.
9. iPhone: **Settings → Privacy & Security → Developer Mode → On** (phone restarts). 
10. In Xcode's top bar pick your iPhone as the run destination (next to the ▶ button).
11. Press **▶ Run** (Cmd+R). First launch: on the iPhone open **Settings → General → VPN & Device Management → your Apple ID → Trust**.
12. Open **Room Scanner** on the phone, tap **+**, name the room, scan.
13. **Free Apple ID limit:** the app stops launching after 7 days — plug in and press ▶ again (your scans in Documents/Scans stay).

Fix-it tips if the build shows red errors: copy the error text to Claude together with the file name — most are one-line fixes.
Run the unit tests: **Cmd+U** (ExporterTests: schema keys, matrices, versioning, keyframe thresholds, floor polygon).

## Route B — no Mac: rent a cloud Mac
1. Rent a macOS machine by the hour (e.g. MacinCloud, AWS EC2 Mac, or a GitHub Actions `macos-14` runner).
2. Follow Route A steps 1–7 on that machine. You cannot plug your phone into a cloud Mac, so instead **Product → Archive** and
   distribute with **Ad Hoc / Development** using your Apple Developer account (paid, 99 USD/yr) — or use TestFlight.
   With a *free* Apple ID, cloud Macs can't install to your phone; borrow a real Mac for step 8–11 instead.

## Route D — no Mac at all (recommended for you): cloud build + Sideloadly from Windows
1. Open the repo on GitHub → **Actions** tab → latest green **Build iOS app (unsigned)** run → scroll to **Artifacts** → click **RoomScanner-unsigned-ipa** (downloads a zip). Unzip it → `RoomScanner-unsigned.ipa`.
   (Re-run any time: Actions → Build iOS app (unsigned) → **Run workflow**.)
2. Install **iTunes** and **iCloud** from apple.com (the direct downloads, NOT the Microsoft Store versions) — Sideloadly needs their drivers.
3. Install **Sideloadly** from https://sideloadly.io and open it.
4. Plug the iPhone into the PC with a cable, unlock it, tap **Trust This Computer**.
5. Drag `RoomScanner-unsigned.ipa` onto Sideloadly. Pick your iPhone in the device box. Type your Apple ID email.
6. Click **Start**, enter your Apple ID password (and the 2FA code if asked).
7. iPhone: **Settings → Privacy & Security → Developer Mode → On**, restart when asked. (If the switch isn't there yet, try the install in step 6 first, then it appears.)
8. iPhone: **Settings → General → VPN & Device Management → your Apple ID → Trust**.
9. Open **Room Scanner**. Allow the camera. Scan.
10. A free Apple ID app expires after **7 days** — repeat steps 4–6 to refresh (scans stay in the app).

## Route C — start capturing today (no app yet)
Scan with Polycam / Scaniverse / 3D Scanner App, export `.obj`/`.ply`/`.glb` + photos, and import with
`python -m pipeline import <file> --out data/` (see README quick start).

## Getting scans to the PC
1. In the app's library, tap the share icon next to a scan → **AirDrop** (to a Mac) or **Save to Files** → OneDrive/iCloud Drive/USB.
   On Windows: iCloud for Windows, or Files → "On My iPhone → Room Scanner → Scans" over a USB cable with iTunes/Apple Devices app.
2. Unzip into `room-scanner\data\` so you get `data\Office_v4_2026-10-09\manifest.json`.

## The one design risk to check first
The app runs **RoomPlan on the same ARSession** as the depth/photo capture (`RoomCaptureSession(arSession:)`, iOS 17) so walls and photos
share one coordinate frame. If depth stops arriving after RoomPlan starts, the app shows "No LiDAR depth arriving…". Then switch off RoomPlan
(`useRoomPlan = false` in `ScanSession.swift`) — you still get photos + depth + mesh, but no `rooms/` data (walls/objects); building the shell from the
classified mesh instead is **not implemented yet**.

## What's in the app
Camera + green/red LiDAR wireframe, automatic keyframes (0.25 m / 15° / sharpness filter), manual shutter, pause/resume,
photo-detail ring, walls/rooms/photos card, top-down mini-map, low-light / move-slower / heat / battery warnings, exposure lock,
multi-room (Finish room & start another), versioned library with thumbnails/rename/delete, `.zip` export via the share sheet.
Tune thresholds in `config.yaml` (`capture:`) and run `python tools/gen_swift_config.py`.
