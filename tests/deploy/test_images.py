"""Container images and deploy defaults (WS5) stay in step with the code and docs/deploy.md."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

from rulecam.perception import sam_worker

ROOT = Path(__file__).resolve().parents[2]
API_DOCKERFILE = (ROOT / "deploy" / "api.Dockerfile").read_text(encoding="utf-8")
SAM_DOCKERFILE = (ROOT / "deploy" / "sam.Dockerfile").read_text(encoding="utf-8")
GPU_DOCKERFILE = (ROOT / "deploy" / "gpu.Dockerfile").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "images.yml").read_text(encoding="utf-8")
DEPLOY_DOC = (ROOT / "docs" / "deploy.md").read_text(encoding="utf-8")


def load_deploy_script() -> object:
    spec = importlib.util.spec_from_file_location(
        "runpod_deploy_images", ROOT / "scripts/deploy/runpod.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def from_images(dockerfile: str) -> list[str]:
    args = dict(re.findall(r"^ARG (\w+)=(\S+)$", dockerfile, flags=re.MULTILINE))
    images = re.findall(r"^FROM (\S+)", dockerfile, flags=re.MULTILINE)
    return [args.get(image.strip("${}"), image) for image in images]


def test_base_images_are_pinned_to_exact_tags() -> None:
    for dockerfile in (API_DOCKERFILE, SAM_DOCKERFILE, GPU_DOCKERFILE):
        for image in from_images(dockerfile):
            assert ":" in image, image
            assert not image.endswith((":latest", ":3.12", ":12.8")), image
    assert "python:3.12.14-slim-trixie" in from_images(API_DOCKERFILE)
    assert "nvidia/cuda:12.8.1-base-ubuntu24.04" in from_images(SAM_DOCKERFILE)


def test_api_image_installs_the_lock_and_runs_the_entrypoint() -> None:
    assert "uv sync --frozen --no-dev --no-editable" in API_DOCKERFILE
    assert "ffmpeg" in API_DOCKERFILE
    assert "useradd" in API_DOCKERFILE
    assert "HEALTHCHECK" in API_DOCKERFILE
    assert '"/opt/rulecam/deploy/api-entrypoint.sh"' in API_DOCKERFILE


def test_sam_image_pins_match_the_worker() -> None:
    assert f"ARG SAM3_COMMIT={sam_worker.SAM3_COMMIT}" in SAM_DOCKERFILE
    assert f'"{sam_worker.TORCH_PACKAGES[0]}"' in SAM_DOCKERFILE
    assert sam_worker.TORCH_INDEX_URL in SAM_DOCKERFILE
    # sam3 imports these without declaring them; the pod venv failed without them.
    for package in ("einops", "pycocotools", "psutil", '"numpy<2"', '"pydantic>=2.7"'):
        assert package in SAM_DOCKERFILE
    assert "rulecam.perception.sam_server" in SAM_DOCKERFILE


def test_workflow_builds_both_images_and_reads_the_pinned_commit() -> None:
    for name in ("ghcr.io/jeevithg090/rulecam-api", "ghcr.io/jeevithg090/rulecam-sam"):
        assert name in WORKFLOW
    for dockerfile in ("deploy/api.Dockerfile", "deploy/sam.Dockerfile"):
        assert dockerfile in WORKFLOW
    assert "SAM3_COMMIT" in WORKFLOW
    assert "packages: write" in WORKFLOW


def test_glimmer_pod_matches_the_documented_args() -> None:
    rp = load_deploy_script()
    doc = " ".join(DEPLOY_DOC.split())
    assert f"Image `{rp.GLIMMER_IMAGE}`" in doc  # type: ignore[attr-defined]
    assert " ".join(rp.GLIMMER_ARGS) in doc  # type: ignore[attr-defined]
    assert "LLAMA_API_KEY" in doc


def test_gpu_image_has_the_sam_venv_and_the_pinned_llama_server() -> None:
    rp = load_deploy_script()
    images = from_images(GPU_DOCKERFILE)
    assert "nvidia/cuda:12.8.1-base-ubuntu24.04" in images
    assert rp.GLIMMER_IMAGE in images  # type: ignore[attr-defined]
    assert "COPY --from=llama /app /opt/llama" in GPU_DOCKERFILE
    # The same SAM venv as the SAM image, pin for pin.
    assert f"ARG SAM3_COMMIT={sam_worker.SAM3_COMMIT}" in GPU_DOCKERFILE
    assert f'"{sam_worker.TORCH_PACKAGES[0]}"' in GPU_DOCKERFILE
    assert sam_worker.TORCH_INDEX_URL in GPU_DOCKERFILE
    for package in ("einops", "pycocotools", "psutil", '"numpy<2"', '"pydantic>=2.7"'):
        assert package in GPU_DOCKERFILE
    # llama-server's CUDA runtime and cuBLAS come from the torch wheels; the build checks them.
    for lib in ("libcudart.so.12", "libcublas.so.12", "libcublasLt.so.12", "libnccl.so.2"):
        assert lib in GPU_DOCKERFILE
    assert "ldd /opt/llama/llama-server" in GPU_DOCKERFILE
    assert "SAM_MEMORY_GUARD_GB=8" in GPU_DOCKERFILE
    assert "GLIMMER_CTX=32768" in GPU_DOCKERFILE
    assert "EXPOSE 8080 8090" in GPU_DOCKERFILE
    assert '"/opt/rulecam/deploy/gpu-entrypoint.sh"' in GPU_DOCKERFILE
    for name in (
        "gpu-entrypoint.sh",
        "gpu_supervisor.py",
        "sam-entrypoint.sh",
        "entrypoint-lib.sh",
    ):
        assert f"deploy/{name}" in GPU_DOCKERFILE


def test_workflow_builds_the_gpu_image() -> None:
    assert "ghcr.io/jeevithg090/rulecam-gpu" in WORKFLOW
    assert "deploy/gpu.Dockerfile" in WORKFLOW
    assert "needs.changes.outputs.gpu" in WORKFLOW
    rp = load_deploy_script()
    assert rp.GPU_IMAGE == "ghcr.io/jeevithg090/rulecam-gpu"  # type: ignore[attr-defined]


def test_one_gpu_section_is_documented() -> None:
    doc = " ".join(DEPLOY_DOC.split())
    assert "## One GPU per site" in DEPLOY_DOC
    assert "runpod_up.sh --one-gpu" in doc
    assert "SAM_MEMORY_GUARD_GB=8" in doc
    assert "--cameras-per-gpu" in doc
