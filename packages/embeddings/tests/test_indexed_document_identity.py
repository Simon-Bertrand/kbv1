from km.inventory import indexed_document_identity, standalone_document_metadata


def test_pdf_name_uses_csv_then_actual_filename_when_the_csv_is_absent(tmp_path):
    pdf = tmp_path / '2.pdf'
    pdf.touch()
    (tmp_path / 'infos.json').write_text('{"typeDocumentId": 7}')
    bundle = {'filepath': str(pdf)}
    assert indexed_document_identity({**bundle, 'standalone_metadata': standalone_document_metadata(tmp_path, pdf.name)}) == ('2.pdf', 7)

    csv = tmp_path / 'metadata.csv'
    csv.write_text('filename;orignal_path\n2.pdf;C:\\archives\\Notice originale.pdf\n')
    assert indexed_document_identity({**bundle, 'standalone_metadata': standalone_document_metadata(tmp_path, pdf.name)}) == ('Notice originale.pdf', 7)

    csv.write_text('filename;original_path\n1.pdf;/archives/Autre.pdf\n')
    assert indexed_document_identity({**bundle, 'standalone_metadata': standalone_document_metadata(tmp_path, pdf.name)}) == (None, 7)
