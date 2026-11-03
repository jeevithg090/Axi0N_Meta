# ruff: noqa
"""Ping SAM and Glimmer, find people in a photo, embed two crops, ask Glimmer about the photo."""

import base64
import os
import sys
import time

import cv2
import httpx
import numpy as np
from dotenv import load_dotenv

load_dotenv()

edge = os.environ["RUNPOD_API_KEY"]
sam_url, glimmer_url = os.environ["SAM_URL"], os.environ["GLIMMER_URL"]
sam = {"Authorization": f"Bearer {edge}", "X-Rulecam-Key": os.environ["SAM_API_KEY"]}
glimmer = {"Authorization": f"Bearer {edge}", "X-Rulecam-Key": os.environ["GLIMMER_API_KEY"]}
client = httpx.Client(timeout=300)

image = cv2.imread(sys.argv[1]) if len(sys.argv) > 1 else np.full((540, 960, 3), 128, np.uint8)
height, width = image.shape[:2]
jpeg = base64.b64encode(cv2.imencode(".jpg", image)[1].tobytes()).decode()


def timed(label, call):
    start = time.perf_counter()
    reply = call()
    print(f"{label}: HTTP {reply.status_code} in {time.perf_counter() - start:.1f} s")
    reply.raise_for_status()
    return reply.json()


timed("SAM ready", lambda: client.get(f"{sam_url}/ping", headers=sam))
found = timed(
    "SAM segment_image",
    lambda: client.post(
        f"{sam_url}/segment_image",
        headers=sam,
        json={
            "request": {
                "concepts": ["person", "shopping cart"],
                "n_images": 1,
                "width": width,
                "height": height,
                "include_confidence": True,
                "masks": False,
            },
            "images_b64": [jpeg],
        },
    ),
)
print(f"    {len(found['objects'])} objects; first ones (box is x1, y1, x2, y2 in pixels):")
for obj in found["objects"][:5]:
    box = [round(v) for v in obj["box_xyxy"]]
    print(f"    {obj['concept']} #{obj['object_id']} box={box} confidence={obj.get('confidence')}")

crops = [image[: height // 2, : width // 2], image[height // 2 :, width // 2 :]]
crops_b64 = [base64.b64encode(cv2.imencode(".jpg", c)[1].tobytes()).decode() for c in crops]
vectors = timed(
    "SAM embed",
    lambda: client.post(f"{sam_url}/embed", headers=sam, json={"images_b64": crops_b64}),
)
print("    embedding size:", len(vectors["embeddings"][0]), "model:", vectors["model"])

answer = timed(
    "Glimmer chat",
    lambda: client.post(
        f"{glimmer_url}/chat/completions",
        headers=glimmer,
        json={
            "model": "muse-glimmer-30b",
            "max_tokens": 3000,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "How many people are in this photo? One sentence.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{jpeg}"},
                        },
                    ],
                }
            ],
        },
    ),
)
message = answer["choices"][0]["message"]
print("    Glimmer:", (message.get("content") or message.get("reasoning_content") or "")[:300])
