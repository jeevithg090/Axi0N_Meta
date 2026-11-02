#!/usr/bin/env bash
# Download the pinned RTMPose-l body7 ONNX for evidence-strip pose (add-on 3.4) and check it.
#
#   scripts/fetch_rtmpose.sh [OUT_DIR]      (default: ./models)
#
# Writes OUT_DIR/rtmpose-l-body7.onnx (110,596,989 bytes) after checking the zip's and the ONNX
# file's sha256 against the values pinned in src/rulecam/pose/keypoints.py. Then set
# RULECAM_POSE_ONNX to that path (and install `onnxruntime`, e.g. `uv sync --extra ml` or
# `pip install onnxruntime`). Nothing is written if a hash does not match.
set -euo pipefail

OUT_DIR="${1:-models}"
NAME="rtmpose-l_simcc-body7_pt-body7_420e-256x192-4dba18fc_20230504"
URL="https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/${NAME}.zip"
ZIP_SHA256="121f42bf3f3e28f2cf254696ae649e58459513f65d2b4580959b688c0b2c8302"
ONNX_SHA256="cff059fd58a2c0d5fabaddcd66a96abcfb327563bcb0149ea59c9de4a8990fe2"

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
curl -fsSL -o "$work/rtmpose.zip" "$URL"
[[ "$(sha256 "$work/rtmpose.zip")" == "$ZIP_SHA256" ]] || { echo "zip sha256 mismatch" >&2; exit 1; }
unzip -q -j "$work/rtmpose.zip" "*/end2end.onnx" -d "$work"
[[ "$(sha256 "$work/end2end.onnx")" == "$ONNX_SHA256" ]] || { echo "onnx sha256 mismatch" >&2; exit 1; }
mkdir -p "$OUT_DIR"
mv "$work/end2end.onnx" "$OUT_DIR/rtmpose-l-body7.onnx"
echo "$OUT_DIR/rtmpose-l-body7.onnx"
