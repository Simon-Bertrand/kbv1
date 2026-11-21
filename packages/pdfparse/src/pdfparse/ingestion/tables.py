"""Exact table row/cell evidence and conservative continuation candidates."""
from docling_core.types.doc import TableData
from pdfparse.enrich import EnrichedTableItem
from pdfparse.enrich.handlers import table_rows


def table_evidence(prepared):
    rows, links = [], []
    previous = None
    for block in prepared.blocks:
        item = block.element
        if not isinstance(item, EnrichedTableItem):
            previous = None
            continue
        data = TableData.model_validate(item.table_data)
        headers = tuple((c.start_col_offset_idx, c.end_col_offset_idx, c.text.strip())
                        for c in data.table_cells if c.column_header and c.text.strip())
        pages = sorted({loc.page_no for loc in item.locations})
        section = tuple(s.element_ref for s in block.sections)
        if previous:
            before, before_headers, before_pages, before_section = previous
            if (headers and headers == before_headers and pages and before_pages
                    and pages[0] == before_pages[-1] + 1 and section == before_section
                    and item.num_cols == before.num_cols
                    and (not item.caption or not before.caption or item.caption == before.caption)):
                links.append({'from_ref': before.element_ref.cref, 'to_ref': item.element_ref.cref,
                              'relation': 'possible_continuation', 'basis': 'adjacent_pages_equal_headers'})
        previous = (item, headers, pages, section)
        rendered = table_rows(data)
        body = '\n'.join(text for _, text, _ in rendered)
        # Custom handlers may rewrite the table; never invent their row offsets.
        if body != item.content_for_embedding or not block.text.endswith(body):
            continue
        cursor = block.end - len(body)
        for row_index, text, cells in rendered:
            rows.append({'table_ref': item.element_ref.cref, 'row_index': row_index,
                         'start': cursor, 'end': cursor + len(text),
                         'cells': [cell.model_dump(mode='json') for cell in cells]})
            cursor += len(text) + 1
    return rows, links
