# Camera connectors: live end-to-end results (4 Nov 2026)

What was run, on one MacBook between 00:41 and 01:15 UTC on 4 Nov 2026, against a local Rulecam
API (`RULECAM_MODE=live`, `RULECAM_RUNTIME=true`) using the hosted Muse Spark 1.3 and SAM 3.1
APIs. Branch `p1/connectors`. Every number below was read from the API's own answers
(`/connectors/cameras`, `/runtime/metrics`) or a headless browser, not estimated.

## What the cameras were

No real CCTV camera was available, so the cameras were real streaming servers fed with recorded
clips from `data/testclips`:

- **RTSP H.264 and H.265**: mediamtx 1.x serving two clips (the H.265 one re-encoded with x265),
  read by Rulecam over RTSP/TCP. One more path on a second mediamtx with a user name and password
  required, as on a real NVR.
- **HLS**: the same mediamtx stream as an HLS playlist.
- **MJPEG over HTTP**: ffmpeg serving a CAVIAR clip as `multipart/x-mixed-replace`.
- **Public live stream**: a real YouTube live webcam (Jackson Hole town square), resolved with
  yt-dlp 2026.10.26.
- **Virtual camera**: a clip uploaded through `POST /connectors/cameras/upload`, looped.
- **ONVIF**: a small **simulated** NVR (a Python script, not a real device) that answers the
  real WS-Discovery multicast probe and the three ONVIF media calls, and checks each request's
  WS-Security password digest itself. Its streams point at the mediamtx paths above.

## Results

Time to first chunk is from Rulecam starting ffmpeg to the first 3-second chunk being published.
The first look's steps: Spark describes, SAM 3.1 detects, Spark matches use cases.

| Camera | Input | First chunk (s) | Describe (s) | Detect (s) | Match (s) | First look total (s) | Name Spark gave |
|---|---|---|---|---|---|---|---|
| RTSP H.264 | h264 1280x720 25 fps | 4.6 | 5.5 | 8.1 | 7.2 | 22.7 | Checkout Counter |
| RTSP H.265 | hevc 768x576 25 fps | 9.4 | 4.3 | 4.4 | 13.0 | 23.6 | (owner named it) |
| RTSP with password | h264 640x360 25 fps | 5.9 | 7.7 | 2.9 | 11.5 | 24.2 | Supermarket checkout lanes |
| HLS | h264 1280x720 25 fps | 3.9 to 7.0 | (first look not asked) | | | | |
| MJPEG over HTTP | mjpeg | 3.0 | 6.5 | 3.5 | 12.3 | 24.2 | Mall hallway corridor |
| YouTube live | h264 1280x720 30 fps | 1.1 | 7.3 | 4.3 | 9.1 | 25.1 | Town Square - Yeah Buddy Pizza |
| Uploaded clip | h264 640x360 | 2.8 | 6.2 | 3.6 | 8.6 | 19.6 | Checkout lane till |
| ONVIF (simulated NVR) | h264 640x360 25 fps | 4.5 | 7.4 | 3.7 | 9.4 | 23.6 | none: the page passed the NVR's advertised name ("Simulated NVR"); it now leaves naming to Spark |

From pressing Connect to a named, surveyed camera took 23 to 33 seconds in every case (first
chunk plus the first look). In the browser, the whole "Scan the network" path on the simulated
NVR (scan, type the password, list streams, connect, wait for the first look) took 33.2 s; the
scan itself 3.4 s.

The H.265 stream's first chunk took longest (9.4 s) because the test encoder put a keyframe only
every 10 seconds and ffmpeg waits for one; real cameras usually send one every 1 to 4 seconds.

**Use-case fit, as the models decided it.** The checkout close-ups were rated supermarket
"possible" (too tight to see queues), the wide checkout view supermarket "strong", the mall
corridor crowd "strong", and the town-square webcam "other: strong, crowd: possible" (a traffic
intersection). Every camera got a placement tip, for example "Pull back and raise the camera to
show the full lane plus a few metres behind the customer so queues and lane status are visible."
SAM 3.1's counts were plausible on the snapshots we looked at (17 shoppers, 2 carts on the wide
checkout view; 15 cars, 7 traffic lights on the town square), but nobody checked them against
ground truth, so they are not accuracy numbers.

**Into the runtime.** With the repository's sample forklift plan activated (its one rule runs on
every camera), the runtime processed 19 chunks from four connector cameras in about a minute,
with 228 SAM calls, a segmentation p50 of 9.4 s and a lag of 6.2 s (hosted SAM, no GPU pod).
`GET /cameras` listed the new cameras from their chunks. The plan was switched off again after
the check.

**Steady state.** Over several minutes each live camera averaged 10.0 frames per second and 20
chunks a minute (exactly real time). The 10-second fps reading jumps (9 to 26) on HLS and YouTube
because those streams arrive in segments of 1 to 5 seconds at a time.

**Reconnects.** Stopping the RTSP publisher turned both cameras reading it (RTSP and HLS)
"reconnecting" with the error in plain words ("Server returned 404 Not Found"). After the
publisher came back, they were live again in 24 and 27 seconds, after waits had grown to the
30-second cap. Restarting the API brought all saved cameras back by themselves, with chunk numbers
continuing (no repeated chunk ids), and stopping the API left no ffmpeg running.

**Passwords.** A wrong password showed "Server returned 401 Unauthorized (authorization failed)";
the right one went live. The ONVIF simulator accepted Rulecam's password digest and refused a
wrong password. In API answers and in the API's whole log, neither password appeared once (the
URL is shown as `rtsp://***@...`). `cameras.json` and `secrets.json` were written with mode 0600.

**Network scan on a real network.** On this office network (10.2.63.0/24) the scan took 3.4 s
and found no cameras, which is correct: there are none.

## What was not tested

- A real Hikvision, Dahua, Reolink or Axis camera or NVR, and a real ONVIF device. The ONVIF calls
  were checked only against the simulator and recorded-style replies in the unit tests.
- Many cameras at once for a long time (CPU per camera, disk use over a day).
- The deployed RunPod API: nothing was deployed. A scan from a cloud pod finds nothing by design.
- Accuracy of SAM 3.1's counts and of Spark's use-case ratings against human labels.

## Follow-up the same night (01:35 to 01:55 UTC)

**No orphan ffmpeg after a crash.** ffmpeg now runs in its own process group, with a
parent-death signal on Linux and a pipe watchdog elsewhere, and a previous run's ffmpeg for the
same camera is killed on start. Checked on macOS (the watchdog path): with a YouTube camera live,
`kill -9` of the API left its ffmpeg running at 2 s and gone by 6 s (ffmpeg finishes its current
segment on SIGTERM). A test does the same on every CI run (Linux, the parent-death path): it
SIGKILLs a process that owns a camera and requires its ffmpeg to be gone within 10 s. After the
restart the camera came back by itself and, because the crash had cut its first look short, got
a new one (named "Shibuya Scramble Crossing", crowd: strong).

**Public live webcam presets.** Four candidates were connected and surveyed; three became
presets. Times are first chunk / first look.

| Webcam (publisher) | First chunk (s) | First look (s) | Fit the models gave |
|---|---|---|---|
| Shibuya Scramble Crossing (ANN News, TV Asahi) | 1.5 | 28.4 | crowd: strong |
| Times Square Crossroads (EarthCam) | 1.3 (0.5 from the page) | 26.8 (27.8 from the page) | crowd: possible, other: strong |
| Fisherman's Village shop street, Koh Samui (The Real Samui Webcam) | 1.6 | 30.2 | supermarket: possible, crowd: possible |
| Leavenworth main street (Kris Kringl shop), not kept | 1.2 | 24.5 | crowd: possible (night, few people) |

Clicking the Times Square preset in the page (headless browser) showed the tile in under a
second and the finished first look 27.8 s later. At night SAM 3.1 found 0 "pedestrians" on Times
Square from that height while Shibuya, in daytime, showed 10, which is why the fits differ.

## Known limits

- A crash now stops ffmpeg, but a camera that was mid-first-look loses that look until the new
  one finishes (about 30 s after the restart).
- Public webcam links can end without notice; the presets will need checking before a demo.
- The 10-second fps reading jumps on HLS and YouTube streams (segment bursts); the long-run rate
  is right.
