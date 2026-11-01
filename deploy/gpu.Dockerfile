# syntax=docker/dockerfile:1.7
# One GPU per site (WS3/WS5): Muse Glimmer (llama.cpp llama-server, :8080) and the SAM 3.1 image
# server (rulecam sam_server, :8090) on one 48 GB GPU, kept up by deploy/gpu_supervisor.py.
# Built as ghcr.io/jeevithg090/rulecam-gpu; docs/deploy.md "One GPU per site" has the memory
# budget and the deploy (`scripts/deploy/runpod_up.sh --one-gpu`).
#
# - SAM: the same venv as deploy/sam.Dockerfile (CUDA 12.8 base, torch 2.10 cu128 wheels, sam3 at
#   SAM3_COMMIT, which the workflow passes from sam_worker.SAM3_COMMIT, plus sam3's undeclared
#   deps; tests/deploy/test_images.py keeps both files in step).
# - Glimmer: llama-server and its ggml backends copied from the official ggml-org server image
#   (Muse Glimmer needs >= b10353). That image's server stage copies `/app/lib/` (the shared
#   libraries, CUDA backend included) and `/app/full/llama-server` into `/app`; we keep `/app`
#   together under /opt/llama, so the backends sit next to the binary as llama.cpp expects.
#   It links the CUDA runtime, cuBLAS and NCCL, which CUDA's minimal `base` image lacks: the
#   torch cu128 wheels already ship them (libcudart.so.12, libcublas(Lt).so.12, libnccl.so.2),
#   so /opt/llama/cuda links to those instead of adding the `runtime` image's second copy
#   (~3 GB). The build fails when any library of the binary or its backends is unresolved (libcuda.so.1, the driver, is
#   mounted by the NVIDIA container runtime and is checked against the image's compat copy).
#   The extra runtime packages are the ones the ggml-org image installs: libgomp1, curl, ffmpeg.
#
#   docker build -f deploy/gpu.Dockerfile -t rulecam-gpu .
#   docker run --gpus all -p 8080:8080 -p 8090:8090 -v gpu:/workspace \
#       -e LLAMA_API_KEY -e SAM_API_KEY -e HF_TOKEN rulecam-gpu
#
# Base tags are pinned and at least two weeks old when chosen (b11669: 2026-10-15).
ARG CUDA_IMAGE=nvidia/cuda:12.8.1-base-ubuntu24.04
ARG LLAMA_IMAGE=ghcr.io/ggml-org/llama.cpp:server-cuda-b11669

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM ${LLAMA_IMAGE} AS llama

FROM ${CUDA_IMAGE} AS build
ARG SAM3_COMMIT=f1647e8d0c1d280991f4d1b7d974c2704537fe66
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3.12-dev build-essential git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN uv venv --python /usr/bin/python3.12 /opt/sam-venv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/sam-venv/bin/python "torch==2.10.0" torchvision \
        --index-url https://download.pytorch.org/whl/cu128
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/sam-venv/bin/python \
        "sam3 @ git+https://github.com/facebookresearch/sam3.git@${SAM3_COMMIT}" \
        opencv-python-headless "setuptools<81" "numpy<2" "pydantic>=2.7" huggingface_hub \
        einops pycocotools psutil

FROM ${CUDA_IMAGE}
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.12 libglib2.0-0t64 tini ca-certificates libgomp1 curl ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin sam \
    && mkdir -p /workspace/hf-cache /workspace/llama-cache \
    && chown -R sam: /workspace
COPY --from=build /opt/sam-venv /opt/sam-venv
COPY --from=llama /app /opt/llama
# sam_server only needs rulecam's perception and contracts modules (stdlib + pydantic + numpy).
COPY src/rulecam /opt/rulecam/src/rulecam
COPY deploy/entrypoint-lib.sh deploy/sam-entrypoint.sh deploy/gpu-entrypoint.sh \
    deploy/gpu_supervisor.py /opt/rulecam/deploy/
ENV PATH=/opt/sam-venv/bin:$PATH \
    PYTHONPATH=/opt/rulecam/src \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/workspace/hf-cache \
    LLAMA_CACHE=/workspace/llama-cache \
    GLIMMER_PORT=8080 \
    GLIMMER_CTX=32768 \
    GLIMMER_PARALLEL=2 \
    SAM_PORT=8090 \
    SAM_MEMORY_GUARD_GB=8
# llama-server's CUDA runtime, cuBLAS and NCCL come from the torch wheels in the venv.
RUN set -eu; \
    mkdir -p /opt/llama/cuda; \
    for lib in libcudart.so.12 libcublas.so.12 libcublasLt.so.12 libnccl.so.2; do \
        path="$(find /opt/sam-venv -name "$lib" -print -quit)"; \
        if [ -z "$path" ]; then echo "no $lib in the torch wheels" >&2; exit 1; fi; \
        ln -s "$path" "/opt/llama/cuda/$lib"; \
    done; \
    missing="$(LD_LIBRARY_PATH=/opt/llama:/opt/llama/cuda:/usr/local/cuda/compat \
        ldd /opt/llama/llama-server /opt/llama/*.so* 2>&1 | grep 'not found' || true)"; \
    if [ -n "$missing" ]; then echo "llama-server: unresolved libraries:" >&2; \
        echo "$missing" >&2; exit 1; fi
# Fail the build, not the pod, when an import is missing (no GPU is needed to import).
RUN python -c "import sam3.model_builder, torch, cv2, einops, pycocotools, psutil, rulecam.perception.sam_server" \
    && python /opt/rulecam/deploy/gpu_supervisor.py --plan
EXPOSE 8080 8090
# The first start downloads Glimmer (about 18 GB) and the SAM checkpoint (about 3.5 GB).
HEALTHCHECK --interval=30s --timeout=10s --start-period=1800s --retries=3 \
    CMD ["/opt/rulecam/deploy/gpu-entrypoint.sh", "healthcheck"]
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/rulecam/deploy/gpu-entrypoint.sh"]
CMD ["serve"]
