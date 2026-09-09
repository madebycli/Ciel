# Compute Backend

Compute priority is AMD first:

1. ROCm-enabled PyTorch on a supported AMD GPU
2. CPU fallback

Ciel detects ROCm through `torch.version.hip`. PyTorch on ROCm still exposes the familiar `torch.cuda` device API, so the runtime must not equate a `cuda` device string with NVIDIA hardware.

Model and TTS components should receive the selected accelerator through runtime configuration instead of re-detecting hardware independently.
