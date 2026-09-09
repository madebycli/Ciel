from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Accelerator:
    backend: str
    torch_device: str
    name: str
    accelerated: bool
    detail: str = ""


def detect_accelerator(preference: str | None = None) -> Accelerator:
    """AMD-first compute selection.

    PyTorch intentionally exposes ROCm devices through the ``cuda`` device
    API. ``torch.version.hip`` is therefore the reliable discriminator between
    AMD ROCm and NVIDIA CUDA. Ciel's automatic policy is ROCm first, CPU second.
    """
    preference = (preference or os.environ.get("CIEL_ACCELERATOR", "auto")).lower()
    if preference not in {"auto", "rocm", "cpu"}:
        raise ValueError("CIEL_ACCELERATOR must be auto, rocm or cpu")
    if preference == "cpu":
        return Accelerator("cpu", "cpu", "CPU", False, "forced by configuration")

    try:
        import torch
    except ImportError:
        if preference == "rocm":
            raise RuntimeError("ROCm requested but PyTorch is not installed")
        return Accelerator("cpu", "cpu", "CPU", False, "PyTorch not installed")

    hip_version = getattr(getattr(torch, "version", None), "hip", None)
    gpu_available = bool(getattr(torch, "cuda", None) and torch.cuda.is_available())
    if hip_version and gpu_available:
        try:
            name = torch.cuda.get_device_name(0)
        except Exception:
            name = "AMD GPU"
        return Accelerator("rocm", "cuda", name, True, f"ROCm/HIP {hip_version}")

    if preference == "rocm":
        raise RuntimeError("ROCm requested but no usable AMD ROCm device was detected")

    detail = "ROCm not detected"
    cuda_version = getattr(getattr(torch, "version", None), "cuda", None)
    if cuda_version and gpu_available:
        detail = "NVIDIA CUDA detected but ignored by the AMD-first auto policy"
    return Accelerator("cpu", "cpu", "CPU", False, detail)
