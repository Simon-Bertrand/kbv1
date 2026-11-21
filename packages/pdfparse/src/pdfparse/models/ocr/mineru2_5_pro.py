"""MinerU2.5-Pro text recognition on an already extracted crop."""
from .transformers import TransformersOcrModel
from . import BaseTableOcrModel


class MinerU25ProOcr(TransformersOcrModel, BaseTableOcrModel):
    """Use the native recognition prompt; never run MinerU's document parser."""

    model_type = "qwen2_vl"
    default_prompt = "\nText Recognition:"

    table_prompt = '\nTable Recognition:'

    def _decode_kwargs(self, prompt):
        return {**super()._decode_kwargs(prompt), 'skip_special_tokens': prompt != self.table_prompt}

    def predict_table(self, image):
        return self.predict_table_batch([image])[0]

    def predict_table_batch(self, images):
        texts = self._predict_batch(images, self.table_prompt)
        return [self._parse_table(text) for text in texts]

    def _parse_table(self, text):
        from docling.utils.otsl import parse_otsl_output
        from docling_core.types.doc import TableData

        text = text.strip()
        eos = self.processor.tokenizer.eos_token
        if eos:
            text = text.removesuffix(eos).strip()
        if not text.endswith('<nl>'):
            raise ValueError('Incomplete MinerU table output; increase max_new_tokens')
        _, cells, rows, cols = parse_otsl_output(text)
        if not rows or not cols or not any(cell.text.strip() for cell in cells):
            raise ValueError('MinerU did not return a recognized table')
        return TableData(num_rows=rows, num_cols=cols, table_cells=cells)
