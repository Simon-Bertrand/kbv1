"""Granite Vision 4.1 4B with native Transformers support."""
from .transformers import TransformersVlmModel


class GraniteVision41Vlm(TransformersVlmModel):
    """Structured extraction; accepts tags such as <tables_html> and <chart2csv>."""

    model_type = "granite4_vision"
