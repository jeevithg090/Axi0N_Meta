# syntax=docker/dockerfile:1.7
# Self-hosted SAM 3.1 image (WS5): CUDA 12.8, torch 2.10 (cu128 wheels), sam3 at the commit
# pinned in src/rulecam/perception/sam_worker.py (SAM3_COMMIT; the workflow passes it as a build
# arg and tests/deploy/test_images.py keeps the default in step), plus the deps sam3 imports but
# does not declare (einops, pycocotools, psutil) and what rulecam's sam_server needs (pydantic,
# opencv, numpy < 2, huggingface_hub). Built as ghcr.io/jeevithg090/rulecam-sam.
#
# The base is CUDA's minimal `base` image: the torch cu128 wheels bring their own cuBLAS, cuDNN
# and NCCL, so the `runtime` image would only add a second copy (~3 GB). The host driver must
# support CUDA 12.8 (the deploy script asks RunPod for minCudaVersion 12.8).
#
#   docker build -f deploy/sam.Dockerfile -t rulecam-sam .
#   docker run --gpus all -p 8090:8090 -v sam:/workspace -e SAM_API_KEY -e HF_TOKEN rulecam-sam
#
# The gated checkpoint is downloaded at first start into /workspace/hf-cache (see
# deploy/sam-entrypoint.sh). Base tags are pinned and at least two weeks old when chosen.
ARG CUDA_IMAGE=nvidia/cuda:12.8.1-base-ubuntu24.04

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

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
    && apt-get install -y --no-install-recommends python3.12 libglib2.0-0t64 tini ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin sam \
    && mkdir -p /workspace/hf-cache \
    && chown -R sam: /workspace
COPY --from=build /opt/sam-venv /opt/sam-venv
# sam_server only needs rulecam's perception and contracts modules (stdlib + pydantic + numpy).
COPY src/rulecam /opt/rulecam/src/rulecam
COPY deploy/entrypoint-lib.sh deploy/sam-entrypoint.sh /opt/rulecam/deploy/
ENV PATH=/opt/sam-venv/bin:$PATH \
    PYTHONPATH=/opt/rulecam/src \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/workspace/hf-cache \
    SAM_PORT=8090
# Fail the build, not the pod, when an import is missing (no GPU is needed to import).
RUN python -c "import sam3.model_builder, torch, cv2, einops, pycocotools, psutil, rulecam.perception.sam_server"
EXPOSE 8090
# The first start downloads the checkpoint and loading SAM 3.1 takes about a minute.
HEALTHCHECK --interval=30s --timeout=6s --start-period=900s --retries=3 \
    CMD ["/opt/rulecam/deploy/sam-entrypoint.sh", "healthcheck"]
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/rulecam/deploy/sam-entrypoint.sh"]
CMD ["serve"]
