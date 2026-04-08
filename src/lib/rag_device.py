"""CUDA device selection for RAG embeddings (sentence-transformers / PyTorch).

With multiple GPUs, ``device="cuda"`` follows PyTorch's default device, which can
point at a non-ideal adapter. This module pins the embedding pipeline to a single
visible index (default ``0``, typically the primary GPU such as an RTX 4070).
"""

from __future__ import annotations

import os

from src.utils.logger import LOGGER


def embedding_torch_device() -> str:
    """Return ``cuda:N`` for the RAG embedding model, or ``cpu``.

    Uses GPU index *N* from env ``MTG_RAG_CUDA_DEVICE`` if set; otherwise ``0``
    (first visible CUDA device). Calls :func:`torch.cuda.set_device` so default
    CUDA ops match. If CUDA is unavailable, returns ``cpu``.
    """
    import torch

    if not torch.cuda.is_available():
        return "cpu"
    if "MTG_RAG_CUDA_DEVICE" in os.environ:
        idx = int(os.environ["MTG_RAG_CUDA_DEVICE"])
    else:
        idx = 0
    n: int = torch.cuda.device_count()
    if idx < 0 or idx >= n:
        LOGGER.error(
            "embedding_torch_device: MTG_RAG_CUDA_DEVICE=%s invalid (visible CUDA devices=%s)",
            idx,
            n,
        )
        raise ValueError(
            f"embedding_torch_device: MTG_RAG_CUDA_DEVICE={idx} invalid; only {n} CUDA device(s) visible"
        )
    torch.cuda.set_device(idx)
    return f"cuda:{idx}"
