"""Generate small, original PDF fixtures without third-party Python dependencies."""
from pathlib import Path
import textwrap

DOCUMENTS = [
    ('guide-marque.pdf', 'Guide de marque', 12, 'La simplicite guide les choix de notre identite. La typographie, les couleurs et les espacements doivent rester coherents dans toutes les interfaces.'),
    ('principes-produit.pdf', 'Principes produit', 5, 'Chaque ecran doit avoir un objectif clair. Les composants sont accessibles, reutilisables et adaptes a leur contexte.'),
    ('systeme-design.pdf', 'Systeme de design', 21, 'Les couleurs sont definies dans theme.css par des tokens semantiques. Les themes clair et sombre partagent les memes composants. Modifiez --color-accent pour changer la couleur principale.'),
]
def pdf_string(text):
    return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
def create_pdf(title, total, body):
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>']
    pages = []
    for number in range(1, total + 1):
        page_id = len(objects) + 1
        stream_id = page_id + 1
        pages.append(f'{page_id} 0 R')
        lines = textwrap.wrap(body, 80)
        commands = ['0.776 0.157 0.157 rg', 'BT /F1 14 Tf 50 785 Td (KM / DOCUMENT DE DEMONSTRATION) Tj ET', '0.15 0.15 0.15 rg', f'BT /F1 26 Tf 50 725 Td ({pdf_string(title)}) Tj ET', f'BT /F1 16 Tf 50 687 Td (Page {number} sur {total}) Tj ET', '0.85 G 50 663 m 545 663 l S', 'BT /F1 12 Tf 50 625 Td 21 TL']
        for index, line in enumerate(lines):
            commands.append(f'{"T* " if index else ""}({pdf_string(line)}) Tj')
        commands += ['ET', 'BT /F1 11 Tf 50 490 Td (Exemple local pour tester la lecture des sources et la navigation.) Tj ET', 'BT /F1 10 Tf 50 50 Td (Document fictif - aucune donnee confidentielle.) Tj ET', f'BT /F1 10 Tf 490 50 Td ({number} / {total}) Tj ET']
        stream = '\n'.join(commands).encode('ascii')
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>'.encode())
        objects.append(f'<< /Length {len(stream)} >>\nstream\n'.encode() + stream + b'\nendstream')
    objects[1] = f'<< /Type /Pages /Kids [{" ".join(pages)}] /Count {total} >>'.encode()
    output = bytearray(b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n')
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output)); output.extend(f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref = len(output)
    output.extend(f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]: output.extend(f'{offset:010} 00000 n \n'.encode())
    output.extend(f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return output
root = Path(__file__).resolve().parents[1] / 'public' / 'documents'
root.mkdir(parents=True, exist_ok=True)
for filename, title, total, body in DOCUMENTS:
    (root / filename).write_bytes(create_pdf(title, total, body))
