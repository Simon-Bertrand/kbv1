"""Heron's local model implementation; native Docling remains the default."""

from .transformers import TransformersLayoutDetector


class HeronLayoutDetector(TransformersLayoutDetector):
    """RT-DETR v2 ResNet-50 layout detector using a local Heron bundle."""
