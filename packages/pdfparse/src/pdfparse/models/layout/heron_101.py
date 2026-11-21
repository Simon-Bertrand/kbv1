"""Heron-101's local model implementation."""

from .transformers import TransformersLayoutDetector


class Heron101LayoutDetector(TransformersLayoutDetector):
    """RT-DETR v2 ResNet-101 layout detector using a local Heron-101 bundle."""
