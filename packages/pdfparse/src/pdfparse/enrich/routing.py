"""Single-pass enrichment, source identity and graph assembly."""
from __future__ import annotations

import hashlib
from pathlib import Path

from docling_core.types.doc import ContentLayer, DocItem, DoclingDocument, PictureItem

from .abstraction import EnrichedDocument, EnrichedElement, EnrichedPictureItem, WrappingContext
from .assets import AssetStore
from .model import (
    BaseEnrichModel, BasePictureEnrichModel, HandlerEnrichModel, WrappingPictureEnrichModel,
)
from .serialization import WrapperRegistry
from .pages import BasePageEnrichModel, append_page_descriptions_steps


def _context(pdfpath, doc, asset_store, source_path=None):
    from pdfparse.ingestion.contracts import logical_pdf_path

    path = logical_pdf_path(pdfpath, source_path)
    with (source_path if source_path is not None else path).open('rb') as stream:
        document_id = hashlib.file_digest(stream, 'sha256').hexdigest()
    return WrappingContext(pdfpath=path, document_id=document_id, doc=doc, asset_store=asset_store)


def _document(ctx):
    return EnrichedDocument(document_id=ctx.document_id, pdfpath=ctx.pdfpath,
        assets_dir=ctx.asset_store.local_directory if ctx.asset_store is not None else None)


def _add_element(result, item, element, ctx, wrappers):
    if not isinstance(element, EnrichedElement):
        raise TypeError(f"Handler for {type(item).__name__} must return an EnrichedElement")
    if isinstance(item, PictureItem) and not isinstance(element, EnrichedPictureItem):
        raise TypeError("Picture enrichment must return an EnrichedPictureItem")
    if element.element_ref != ctx.ref(item.self_ref):
        raise ValueError("Handler changed the source element reference")
    if element.document_id != ctx.document_id or element.pdfpath != ctx.pdfpath:
        raise ValueError("Handler changed the source document identity")
    if ((element.parent_ref.cref if element.parent_ref else None)
            != (item.parent.cref if item.parent else None)
            or len(element.children_refs) != len(item.children)
            or any(ref.cref != source.cref
                   for ref, source in zip(element.children_refs, item.children))):
        raise ValueError("Handler changed the source parent/child relationships")
    if isinstance(item, DocItem) and (
        len(element.locations) != len(item.prov) or any(
            (loc.page_no, loc.bbox, loc.coord_origin, loc.charspan)
            != (prov.page_no, prov.bbox.as_tuple(), prov.bbox.coord_origin.value,
                tuple(prov.charspan) if prov.charspan is not None else None)
            for loc, prov in zip(element.locations, item.prov)
        )
    ):
        raise ValueError("Handler changed the source geometry")
    if wrappers is not None:
        wrappers.type_id_for(type(element))
    result.add(element)


def wrap_document_steps(
    pdfpath: Path | str,
    doc: DoclingDocument,
    *,
    enrich: BaseEnrichModel | None = None,
    picture_enrich: BasePictureEnrichModel | None = None,
    page_enrich: BasePageEnrichModel | None = None,
    asset_store: AssetStore | None = None,
    wrappers: WrapperRegistry | None = None,
    source_path: Path | None = None,
) -> EnrichedDocument:
    """Visit every node once; pictures have their own independent strategy.

    Validates each output at its source boundary and the completed graph once.
    Services and frozen handler registries are reused without per-document copies.
    """
    from pdfparse.execution import Call
    from .pictures import PictureEnrichModel
    ctx = _context(pdfpath, doc, asset_store, source_path)
    ordinary = enrich if enrich is not None else HandlerEnrichModel()
    pictures = picture_enrich if picture_enrich is not None else WrappingPictureEnrichModel()
    result = _document(ctx)
    for root in (doc.body, doc.furniture):
        for item, _ in doc.iterate_items(root=root, with_groups=True, traverse_pictures=True,
                                         included_content_layers=set(ContentLayer)):
            if isinstance(item, PictureItem) and isinstance(pictures, PictureEnrichModel):
                element = yield from pictures.enrich_steps(item, ctx=ctx)
            elif isinstance(item, PictureItem):
                element = yield Call(pictures, (item,), {'ctx': ctx})
            else:
                element = ordinary(item, ctx=ctx)
            _add_element(result, item, element, ctx, wrappers)
    if page_enrich is not None:
        yield from append_page_descriptions_steps(result, doc, page_enrich, wrappers=wrappers)
    result.validate()
    return result


def wrap_document(pdfpath, doc, **kwargs):
    from pdfparse.execution import run_steps
    return run_steps(wrap_document_steps(pdfpath, doc, **kwargs))


async def wrap_document_async(pdfpath, doc, *, scheduler, item_concurrency=8, **kwargs):
    import asyncio
    from pdfparse.execution import bounded_map, run_steps_async
    from .pictures import PictureEnrichModel
    from .pages import append_page_descriptions_async

    def prepare():
        ctx = _context(pdfpath, doc, kwargs.get('asset_store'), kwargs.get('source_path'))
        items = [item for root in (doc.body, doc.furniture)
                 for item, _ in doc.iterate_items(root=root, with_groups=True, traverse_pictures=True,
                                                  included_content_layers=set(ContentLayer))]
        return ctx, items

    ctx, items = await scheduler.run_cpu(prepare)
    pictures = kwargs.get('picture_enrich') or WrappingPictureEnrichModel()
    ordinary = kwargs.get('enrich')
    if ordinary is None:
        ordinary = HandlerEnrichModel()
    picture_items = [item for item in items if isinstance(item, PictureItem)]
    async def picture(item):
        if isinstance(pictures, PictureEnrichModel):
            return await run_steps_async(pictures.enrich_steps(item, ctx=ctx), scheduler)
        return await scheduler.run_cpu_serial(('picture-handler', id(pictures)), pictures, item, ctx=ctx)

    async def ordinary_elements():
        # Yield between bounded slices so a long document does not keep a CPU
        # worker away from urgent input preparation for its entire traversal.
        sources = [item for item in items if not isinstance(item, PictureItem)]
        elements = {}
        def wrap(group):
            return {item.self_ref: ordinary(item, ctx=ctx) for item in group}
        for offset in range(0, len(sources), 64):
            elements.update(await scheduler.run_cpu(wrap, sources[offset:offset + 64]))
        return elements

    async with asyncio.TaskGroup() as group:
        picture_task = group.create_task(bounded_map(picture, picture_items, item_concurrency))
        ordinary_task = group.create_task(ordinary_elements())
    saved = ordinary_task.result()
    saved.update((item.self_ref, element) for item, element in
                 zip(picture_items, picture_task.result(), strict=True))
    def assemble():
        result = _document(ctx)
        for item in items:
            _add_element(result, item, saved[item.self_ref], ctx, kwargs.get('wrappers'))
        result.validate()
        return result
    result = await scheduler.run_cpu(assemble)
    if kwargs.get('page_enrich') is not None:
        await append_page_descriptions_async(result, doc, kwargs['page_enrich'], scheduler=scheduler,
            wrappers=kwargs.get('wrappers'), concurrency=item_concurrency)
        await scheduler.run_cpu(result.validate)
    return result
