"""Privacy rendering: Anonymous Mode, face masking, strips and the skeleton wipe (task P4-05)."""

from rulecam.privacy.background_plate import BackgroundPlate
from rulecam.privacy.face_mask import blur_faces
from rulecam.privacy.skeleton_render import (
    PersonRender,
    canonical_pose,
    ppe_from_trace,
    render_skeletons,
    track_colour,
)
from rulecam.privacy.strip import FrameBundle, FrameSource, render_strip, strip_times
from rulecam.privacy.wipe import ConsentError, wipe_frame, write_wipe_video

__all__ = [
    "BackgroundPlate",
    "ConsentError",
    "FrameBundle",
    "FrameSource",
    "PersonRender",
    "blur_faces",
    "canonical_pose",
    "ppe_from_trace",
    "render_skeletons",
    "render_strip",
    "strip_times",
    "track_colour",
    "wipe_frame",
    "write_wipe_video",
]
