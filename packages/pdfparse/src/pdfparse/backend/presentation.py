"""Read-only projections of pipeline data; never serialize runtimes."""
from dataclasses import asdict
import json
from .schemas import Asset, ChunkView, Geometry, Item, Result, Settings


from pdfparse.ingestion.preparation import describe, qualified


def settings_view(pipeline, capacity: int) -> Settings:
    config = pipeline.configuration
    options = None
    converter = getattr(pipeline.extractor, '_converter', None)
    if converter is not None:
        from docling.datamodel.base_models import InputFormat
        options = converter.format_to_options[InputFormat.PDF].pipeline_options
    return Settings(pipeline_class=qualified(pipeline), cache_capacity=capacity,
                    configuration={name: describe(getattr(config, name)) for name in type(config).model_fields},
                    extractor=describe(pipeline.extractor),
                    codecs={key: {'class': qualified(registration.codec), 'schema_version': registration.version}
                            for key, registration in pipeline.codec.wrappers._by_id.items()},
                    extraction_options=describe(options))


def result_view(pipeline, result) -> Result:
    projection = result.projection
    snapshot = projection.index if projection else result.prepare_index()
    models = json.loads(snapshot.model_provenance_json)
    encoded = json.loads(projection.document_json) if projection else pipeline.codec.to_dict(result.document, validate=False)
    items = []
    for element, entry in zip(result.document.elements.values(), encoded['elements']):
        classification = getattr(element, 'classification', None)
        items.append(Item(
            ref=element.element_ref.cref, type=element.element_type.value,
            source_class=element.source_class, label=element.source_label,
            parent_ref=element.parent_ref.cref if element.parent_ref else None,
            children_refs=[ref.cref for ref in element.children_refs],
            locations=[Geometry.model_validate(loc, from_attributes=True) for loc in (
                *element.locations, *getattr(element, 'content_locations', ()))],
            original_text=getattr(element, 'original_text', None),
            content_for_embedding=getattr(element, 'content_for_embedding', None),
            content_for_llm=getattr(element, 'content_for_llm', None),
            caption=getattr(element, 'caption', None), description=getattr(element, 'description', None),
            ocr_text=classification.get('ocr_text') if isinstance(classification, dict) else None,
            classification=classification, data=entry['data'], models=models[element.element_ref.cref],
        ))
    markdown = projection.markdown if projection else result.document.to_markdown()
    native_pictures = sum(item.type == 'picture' and (
        not isinstance(item.classification, dict) or item.classification.get('status') not in ('enriched', 'unhandled')
    ) for item in items)
    unhandled = sum(item.type == 'picture' and isinstance(item.classification, dict)
                    and item.classification.get('status') == 'unhandled' for item in items)
    notices = []
    if native_pictures:
        notices.append(f'{native_pictures} figure(s) conservée(s) sans classification ni OCR/VLM. '
                       'Configurez le routeur de figures pour produire leur contenu textuel.')
    if unhandled:
        notices.append(f'{unhandled} figure(s) classifiée(s) sans route d’enrichissement ; '
                       'consultez leur catégorie dans Éléments.')
    if not markdown:
        notices.append('Aucun texte disponible pour le Markdown. L’OCR global du PDF est désactivé.')
    if not result.chunks:
        notices.append('Aucun chunk généré : aucun contenu indexable après enrichissement.')
    return Result(document_id=result.document.document_id, items=items, markdown=markdown, notices=notices,
                  document_path=str(result.document.pdfpath),
                  assets=[Asset(**{key: record[key] for key in (
                      'id', 'ref', 'asset_path', 'caption', 'description', 'locations', 'models', 'classification', 'chunk_ids')},
                      available=record['asset_path'] is not None and (result.document.assets_dir is not None or pipeline.configuration.asset_store is not None))
                      for record in snapshot.visual_records()],
                  extraction_regions=[describe(asdict(region)) for region in result.extraction],
                  quality=describe(asdict(snapshot.quality)), manifest=result.preparation_manifest(),
                  metrics=asdict(result.metrics) if result.metrics else {},
                  index_records=list(snapshot.index_records()), chunks=[
        ChunkView(id=chunk.chunk_id, text=chunk.text, context=chunk.context,
                  contextualized_text=chunk.contextualized_text,
                  source_refs=[ref.cref for ref in chunk.source_refs],
                  locations=[Geometry.model_validate(loc, from_attributes=True) for loc in chunk.locations],
                  transformation_id=chunk.transformation_id) for chunk in result.chunks
    ])
