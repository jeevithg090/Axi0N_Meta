# SAM 3.1 transport fixtures

Replies in the transport envelope `{status, headers, json}` used by `rulecam.clients.transport`.

## `live/`: real replies (Oct 30 2026, issue #5)

These were recorded from the live API with `HostedSam31` on a 3 s, 640×360 clip made by
`ffmpeg -f lavfi -i testsrc2=size=640x360:rate=10 -t 3`. Each **reply** (`status`, `headers`,
`json`) is verbatim. The `request` is trimmed: the uploaded bytes and the image data URL are
replaced by their sizes. There are no secrets in these files; the API key was never written to
them.

| File | Prompt | Result |
|---|---|---|
| `upload.json` | `POST /files` | `id` used as `file_id` |
| `video_rectangle.json` | `rectangle` | 46 detections, 2 objects; the first is `<2f>0`, box 122..197 × 262..277, `data=16,76` |
| `video_number.json` | `number` | 37 detections |
| `video_circle_empty.json`, `video_colored_square_empty.json` | `circle`, `colored square` | empty completion → `[]` |
| `video_backward_negative_point.json` | `<29f>1<|box;…|>-<|point;…|>`, `backward` | 20 detections, frames returned in the order 29 → 0 |
| `image_rectangle_empty.json` | image probe | empty, `usage.images_processed: 1` |
| `error_400_no_media.json` | prompt with no media | 400 `invalid_request_error` |

## Top level: recorded-style replies

These are hand-written replies in the same grammar. They cover cases the live run did not produce
(429 with `Retry-After`, and masks run through a fake decoder). Their mask payloads (`~FIXTURE-A`,
…) are placeholders.
