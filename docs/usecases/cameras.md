# Connect any camera in 30 seconds

Most small sites already own CCTV: an NVR or a few IP cameras from Hikvision, Dahua, Reolink,
Axis or a similar maker. Rulecam connects to those cameras directly, so nobody has to buy new
cameras or call an installer. Paste the camera's address, or let Rulecam find it on the network,
and in about half a minute the camera is live: Muse Spark names it and says what it sees, SAM 3.1
checks what it can detect, and Spark says which Rulecam use cases it suits.

Open **Connect Cameras** in the web app (`/cameras`). It has four ways in:

| Way in | When to use it |
|---|---|
| Paste a URL | You know the camera's address (RTSP, HLS, MJPEG, a YouTube live link). |
| Scan the network | Rulecam runs on the site's network and you want it to find the cameras. |
| Pair a phone | No camera at the spot yet: any phone becomes one (Pair-a-Phone). |
| Upload a clip | No live camera at all: a recording loops forever as a "virtual camera". |

Every connected camera turns into the same 3-second video chunks as every other Rulecam camera,
so rules, Ask, the planner and evidence work on it with no extra setup.

## What happens when you connect

1. Rulecam starts reading the stream and cutting it into chunks (10 frames a second; streams
   wider than 1280 pixels are scaled down). The tile shows the first frame within a few seconds.
2. From the first frame, Muse Spark 1.3 gives the camera a name a site owner would recognise
   ("Supermarket checkout lanes"), describes what it sees and from where, notes image problems,
   and lists up to 12 things worth looking for.
3. SAM 3.1 looks for each of those things on the same frame. The tile shows each one as a chip:
   how many it found and its best confidence. Things it could not find are crossed out.
4. Muse Spark looks again, now with SAM's numbers, and rates the camera for each use case
   (supermarket, warehouse, crowd) as strong, possible or poor, says why, suggests the first
   rules it could run, and gives one tip on placement.

If Spark sees that the camera looks into a bathroom, bedroom, changing room or staff break room,
Rulecam stops the camera for good and says why. Rulecam never watches those spaces.

You can type a name yourself; then Spark's name is not used. "Look again" re-runs steps 2 to 4
on a fresh frame. Phones and other cameras listed on the page have the same "First look" button.

## Connecting each kind of camera

**IP cameras and NVRs (RTSP).** Pick the maker in the form, type the camera's IP address, and
the usual address fills in. Put the camera account's user name and password in their own
boxes (or inside the URL; Rulecam takes them out). Usual addresses:

| Maker | Main stream | Lower-resolution stream |
|---|---|---|
| Hikvision | `rtsp://<ip>:554/Streaming/Channels/101` | `.../Channels/102` (NVR channel N: N01, N02) |
| Dahua, Amcrest, Lorex | `rtsp://<ip>:554/cam/realmonitor?channel=1&subtype=0` | `subtype=1` |
| Reolink | `rtsp://<ip>:554/h264Preview_01_main` | `h264Preview_01_sub` (H.265 models: `h265Preview_01_main`) |
| Axis | `rtsp://<ip>/axis-media/media.amp` | `?resolution=640x360` |
| Uniview | `rtsp://<ip>:554/unicast/c1/s0/live` | `.../s1/live` |
| TP-Link Tapo | `rtsp://<ip>:554/stream1` | `stream2` |
| UniFi Protect | the RTSPS link copied from Protect | |

H.264 and H.265 (HEVC) both work. Rulecam reads RTSP over TCP, so it works through most routers
and VPNs. Reolink and Tapo need RTSP switched on in their app first.

**ONVIF (scan the network).** "Scan the network" sends the standard ONVIF discovery message and
also checks every address on the local network for an open RTSP port (554, 8554). ONVIF cameras
are listed with the name and model they advertise. Type the camera account's user name and
password and press "List streams": Rulecam asks the camera for its streams and you press
"Connect" on the one you want. Cameras with ONVIF switched off show up as "RTSP port open"; "Use
this address" starts the URL for you. The scan only finds cameras when Rulecam runs on the same
network as them (a site laptop or small box), never from a cloud server.

**HLS.** Paste the `.m3u8` address. Rulecam starts at the newest part of a live playlist.

**MJPEG and other HTTP streams.** Paste the address (for example `http://<ip>/mjpg/video.mjpg`).
Rulecam lets ffmpeg recognise the format.

**Public live streams.** Paste a YouTube live (or Twitch, Vimeo...) page link. Rulecam asks
yt-dlp for the stream each time it connects, because those links expire after a few hours. These
cameras are marked `public`.

**A clip as a virtual camera.** Upload a video (up to 500 MB) or give the path of one inside the
Rulecam data or working folder. It loops forever at its normal speed and is marked `upload`.

## Staying connected

Each camera has its own reader. When the stream drops, stalls (no new frame for 20 seconds) or
never starts (nothing after 30 seconds), Rulecam reconnects after 1, 2, 4, 8, 15 and then every
30 seconds, with a little randomness so many cameras do not retry at once. A camera that stayed up
for a minute starts again from the short wait. Connected cameras come back by themselves when the
Rulecam server restarts. Each tile shows its health: live or reconnecting, frames per second over
the last 10 seconds, how many chunks, when the last frame arrived, what the camera sends (codec,
size, frame rate), how many reconnects, and the last error in plain words (for example "Server
returned 401 Unauthorized" for a wrong password).

Each camera keeps its newest 1,200 chunks on disk (one hour).

## Passwords

The user name and password are kept only on the Rulecam server, in `data/connectors/secrets.json`,
readable only by the server's user. The web page never gets them back, and they are removed from
every error message and log line (`rtsp://***@...`). Removing a camera removes its password.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `RULECAM_CONNECTORS_FPS` | 10 | Frames per second in each chunk |
| `RULECAM_CONNECTORS_MAX_WIDTH` | 1280 | Wider streams are scaled down to this width |
| `RULECAM_CONNECTORS_KEEP_CHUNKS` | 1200 | Chunk files kept per camera |

The first look needs `META_API_KEY` (Muse Spark and SAM 3.1). Without it, cameras still connect
and stream; the first look says it is unavailable.

## API

Every route is owner-only, like the other owner routes (this machine, or a signed-in owner).

| Route | What it does |
|---|---|
| `GET /connectors/cameras` | Every connected camera with its health and first look |
| `POST /connectors/cameras` | Add a camera: `{url, name?, username?, password?, loop?, survey?}` → 201 with the camera |
| `POST /connectors/cameras/upload` | Multipart `clip` (+ `name`): a looped virtual camera |
| `GET /connectors/cameras/{id}` | One camera |
| `DELETE /connectors/cameras/{id}` | Stop and forget it (its chunks stay for evidence) |
| `POST /connectors/cameras/{id}/restart` | Reconnect now |
| `GET /connectors/snapshot/{camera_id}.jpg` | Newest frame of any camera, phones too |
| `POST /connectors/survey/{camera_id}` | Run the first look again, on any camera |
| `GET /connectors/survey/{camera_id}` | That camera's first look |
| `GET /connectors/images/{name}` | A snapshot or the frame with SAM 3.1's boxes drawn on it |
| `POST /connectors/scan` | `{timeout_s?, sweep?}` → devices found on the local network |
| `POST /connectors/onvif` | `{target, username?, password?}` → the device's streams (addresses without passwords) |
| `GET /connectors/brands` | The usual RTSP addresses per maker |

A camera answer carries `camera_id`, `name` (and `named_by`: owner, spark or source), `kind`
(rtsp, hls, http, rtmp, public, file), `display_url` (never with a password), `has_credentials`,
`provenance`, `blocked`, `health` and `survey`. The first look's `status` goes `waiting`,
`describing`, `detecting`, `matching`, then `done` (or `failed`, `blocked`, `unavailable`), with
`timings` in seconds for each step.

## Demo steps

You need ffmpeg and [mediamtx](https://github.com/bluenviron/mediamtx) (`brew install mediamtx`).

1. Serve a clip as a pretend CCTV camera (mediamtx with its default settings listens for RTSP on
   8554 and HLS on 8888):

   ```bash
   mediamtx &
   ffmpeg -re -stream_loop -1 -i data/testclips/market2/p39221981.mp4 -an -c:v copy \
     -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/nvr/ch1 &
   ```

2. Start the API and the web app (`make api`, `make ui`) with `META_API_KEY` set and
   `RULECAM_MODE=live`, and open `/cameras`.
3. Paste `rtsp://127.0.0.1:8554/nvr/ch1` and press Connect. Within about 5 seconds the tile shows
   the live frame; within about 30 seconds it is named "Supermarket checkout lanes" with SAM 3.1's
   chips and a strong supermarket fit.
4. Stop the ffmpeg command: the tile turns "reconnecting" and shows the error. Start it again:
   the tile is live again within about 30 seconds.
5. Paste a YouTube live link (a town-square webcam works well) to show a public stream, or upload
   a clip to show a virtual camera.
6. With a plan approved, the new camera's chunks go to the runtime like any other camera's.
