"""Deterministic local app for browser tests; never loads model weights."""
import argparse
from pathlib import Path

import uvicorn

from pdfparse import PdfParseConfiguration, PdfParsePipeline
from pdfparse.backend.app import create_app
from tests.test_backend import Extractor
from tests.test_layout_pipeline import make_pdf


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    root = Path('/tmp/pdfparse-studio-fixture')
    root.mkdir(exist_ok=True)
    (root / 'Rapports').mkdir(exist_ok=True)
    make_pdf(root / 'Guide.pdf', pages=2)
    make_pdf(root / 'Rapports' / 'Annexe.pdf')
    make_pdf(root / 'ZLong.pdf', pages=20)
    from pypdf import PdfReader, PdfWriter
    with (root / 'Guide.pdf').open('rb') as stream:
        source = PdfReader(stream)
        rotated = PdfWriter()
        rotated.add_page(source.pages[0]).rotate(90)
        with (root / 'ZRotation.pdf').open('wb') as output:
            rotated.write(output)
    encrypted = PdfWriter()
    encrypted.add_blank_page(width=300, height=400)
    encrypted.encrypt('fixture-password')
    with (root / 'ZProtected.pdf').open('wb') as output:
        encrypted.write(output)
    # Exercise PDF.js auxiliary resources with a CID font and JPEG2000 image.
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from PIL import Image
    from io import BytesIO
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    auxiliary = canvas.Canvas(str(root / 'ZAuxiliary.pdf'))
    auxiliary.setFont('STSong-Light', 20)
    auxiliary.drawString(30, 750, '中文离线字体')
    auxiliary.save()
    pixels = BytesIO()
    Image.new('RGB', (128, 128), 'green').save(pixels, format='JPEG2000')
    # ReportLab converts JPEG2000; use pypdf to retain the JPXDecode stream.
    from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=400)
    image = DecodedStreamObject()
    image.set_data(pixels.getvalue())
    image.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                  NameObject('/Width'): NumberObject(128), NameObject('/Height'): NumberObject(128),
                  NameObject('/ColorSpace'): NameObject('/DeviceRGB'), NameObject('/BitsPerComponent'): NumberObject(8),
                  NameObject('/Filter'): NameObject('/JPXDecode')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/Im'): writer._add_object(image)})})
    contents = DecodedStreamObject()
    contents.set_data(b'q 128 0 0 128 20 20 cm /Im Do Q')
    page[NameObject('/Contents')] = writer._add_object(contents)
    with (root / 'ZJpeg2000.pdf').open('wb') as output:
        writer.write(output)
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False), extractor=Extractor())
    uvicorn.run(create_app(root=root, pipeline=pipeline), host='127.0.0.1', port=args.port, log_level='info')


if __name__ == '__main__':
    main()
