# Rulecam glasses app (native, with Meta's Wearables Device Access Toolkit)

**Status: not built.** The working glasses experience today is the phone page `/glasses`
(`ui/src/app/glasses`), where the phone's rear camera stands in for the glasses' camera and answers
play in paired Ray-Ban Meta or Oakley Meta glasses as ordinary Bluetooth audio. This folder holds
the plan for the native app that uses the glasses' own camera. See `docs/usecases/glasses.md`.

## Why it is not built yet

Checked on Nov 4 2026:

- The SDK is obtainable with no blockers. Meta's Wearables Device Access Toolkit (DAT) for iOS is a
  public Swift package, `https://github.com/meta/meta-wearables-dat-ios`, version 1.0.1 (developer
  preview, Oct 9 2026). It needs no sign-up while you develop: turn on Developer Mode in the Meta
  AI app (Settings, App Info, tap the version 5 times) and the app ID can stay empty.
- It needs Xcode 26.4 or newer and iOS 17.2 or newer. **The machine this was built on has only
  the Command Line Tools, no Xcode**, so neither the app nor the iOS Simulator could be built or
  run. Writing Swift we could not compile or run would have been a guess, so we stopped here.
- Without glasses, the SDK's Mock Device (`MWDATMockDevice`) can stream a video file as if it came
  from the glasses' camera, including in the Simulator. The team has no glasses on hand, so the mock
  device would have been the only test, and it would have to be labelled as simulated.

## What the app would do

It uses the same API as the phone page, so nothing on the server changes:

1. Register with the Meta AI app (`Wearables.shared.startRegistration()`, the app's URL scheme
   handles the callback), and ask for the camera permission.
2. Open a session on the glasses (`createSession(deviceSelector: AutoDeviceSelector(...))`), add a
   camera stream (`StreamConfiguration(videoCodec: .raw, resolution: .medium, frameRate: 2)`,
   504 by 896 at 2 frames a second is plenty) and keep the newest frame (`frame.makeUIImage()`).
3. Patrol: every 5 seconds, when nothing is in flight, send the newest frame as JPEG to
   `POST /wearables/look` with `source=meta-glasses` (or `mock-device` under the Mock Device) and
   `wait=false`, then poll `GET /wearables/jobs/{id}`.
4. Push to talk: record the microphone with `AVAudioSession` (`.playAndRecord`, Bluetooth HFP
   allowed, so the glasses' microphones are used) only while the on-screen button is held, encode
   16 kHz mono WAV, and send it as `audio` with the newest frame.
5. Speak: play the answer's WAV (`GET /wearables/audio/{name}`) or `AVSpeechSynthesizer` with the
   `spoken` text. The audio session routes it to the glasses.
6. Safety walk: `POST /wearables/walks`, send frames with `walk_id`, then
   `POST /wearables/walks/{id}/finish`.

Info.plist needs the `MWDAT` dictionary (`AppLinkURLScheme`, `MetaAppID`, `ClientToken`,
`TeamID`), a URL scheme, `UISupportedExternalAccessoryProtocols` = `com.meta.ar.wearable`,
background modes (`bluetooth-central`, `bluetooth-peripheral`, `external-accessory`, `audio`) and
the Bluetooth, local network, camera and microphone usage strings.

The SDK's own experimental audio stream (16 kHz PCM from the glasses) needs an approval in the
Wearables Developer Center, so plain Bluetooth audio routing is the safer path for questions.

## To build it later

1. Install Xcode 26.4 or newer.
2. Create a SwiftUI app here (`apps/glasses/ios`), add the package
   `https://github.com/meta/meta-wearables-dat-ios` pinned to `1.0.1` (the `main` branch has no
   `Package.swift`), with the products `MWDATCore`, `MWDATCamera` and, for development,
   `MWDATMockDevice`.
3. In debug builds enable the Mock Device (`MockDeviceKit.shared.enable()`, pair `.rayBanMeta`,
   `powerOn()`, `unfold()`, `don()`, then `setCameraFeed(fileURL:)` with an HEVC clip of a store
   aisle), and label everything it produces as simulated.
4. Point the app at the Rulecam API with an owner session and run it in the Simulator, then on a
   phone paired with the glasses (Developer Mode on).

The Android SDK is `https://github.com/meta/meta-wearables-dat-android`.
