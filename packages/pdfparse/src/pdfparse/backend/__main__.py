"""Run Studio with the shared batch profile and load inference models on demand."""
import argparse
import json
import os
from pathlib import Path

from .app import create_app
from .defaults import default_runtime_configuration


def main():
    from pdfparse.logging import setup_logging
    setup_logging("pdfparse")
    profile = default_runtime_configuration()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, help='PDF root (or PDFPARSE_SOURCE_ROOT); otherwise chosen in Studio')
    parser.add_argument('--host', default='127.0.0.1', help='Bind address (use 0.0.0.0 inside Docker)')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--cache-capacity', type=int, default=os.getenv('PDFPARSE_CACHE_CAPACITY', '8'))
    parser.add_argument('--cache-max-bytes', type=int, default=os.getenv('PDFPARSE_CACHE_MAX_BYTES', str(128 * 1024 * 1024)))
    parser.add_argument('--export-openapi', type=Path)
    parser.add_argument('--assets-dir', type=Path, help='Extracted images (default: PDFPARSE_ASSETS_DIR or pdfparse/.assets)')
    parser.add_argument('--device', help='Override registered model devices (default: CPU layout/DFC, cuda:0 OCR/VLM)')
    parser.add_argument('--native', action='store_true', help='Start with native processing only instead of the shared batch profile')
    parser.add_argument('--layout', help='Optional registered layout model')
    parser.add_argument('--picture-classifier', help='Registered DFC model; required services depend on figure routing')
    parser.add_argument('--ocr', help='Registered crop OCR model')
    parser.add_argument('--vlm', help='Registered VLM model')
    parser.add_argument('--ocr-settings', type=json.loads)
    parser.add_argument('--vlm-settings', type=json.loads)
    parser.add_argument('--figure-mode', choices=('keep','ocr','vlm','ocr_vlm','table'), default=profile.enrichment.figures)
    parser.add_argument('--fallback-mode', choices=('keep','ocr','vlm','ocr_vlm','table'), default=profile.enrichment.fallback)
    parser.add_argument('--class-routes', type=json.loads, default=profile.enrichment.class_routes, help='JSON mapping native DFC labels to modes')
    parser.add_argument('--dfc-threshold', type=float, default=profile.enrichment.confidence_threshold)
    parser.add_argument('--text-ocr', action=argparse.BooleanOptionalAction, default=None, help='Enable OCR on scanned text blocks')
    parser.add_argument('--page-vlm', action='store_true', default=profile.enrichment.page_vlm, help='Enable VLM description of every page')
    args = parser.parse_args()
    if not isinstance(args.class_routes, dict):
        parser.error('--class-routes doit être un objet JSON {classe: mode}')
    if args.export_openapi:
        args.export_openapi.write_text(json.dumps(create_app(root=args.root).openapi(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return
    selected = (args.layout, args.picture_classifier, args.ocr, args.vlm)
    if args.native and (any(selected) or args.text_ocr or args.page_vlm or args.class_routes):
        parser.error('--native ne se combine pas avec une sélection de modèles')
    figures = (args.picture_classifier, args.ocr, args.vlm)
    if not args.picture_classifier and not args.text_ocr and not args.page_vlm and (args.ocr or args.vlm):
        parser.error('Activer --text-ocr, --page-vlm ou une classification de figures pour utiliser ces modèles.')
    from pdfparse import ModelRegistry
    from .schemas import ModelSelection, RuntimeConfiguration, EnrichmentSelection

    models = ModelRegistry()
    use_default = not args.native and not any(selected)
    if use_default:
        args.picture_classifier = profile.picture_classifier.name
        args.ocr = profile.ocr.name
        args.vlm = profile.vlm.name
    try:
        selection = RuntimeConfiguration(chunking=profile.chunking, enrichment=EnrichmentSelection(
            figures=args.figure_mode, fallback=args.fallback_mode, class_routes=args.class_routes,
            confidence_threshold=args.dfc_threshold,
            text_ocr=profile.enrichment.text_ocr if use_default and args.text_ocr is None else bool(args.text_ocr),
            page_vlm=args.page_vlm), **{
            category: ModelSelection(name=name,
                                     device=args.device or (getattr(profile, category).device if getattr(profile, category) else 'cpu'),
                                     settings=settings) if name else None
            for category, name, settings in (
                ('layout', args.layout, {}), ('picture_classifier', args.picture_classifier, {}),
                ('ocr', args.ocr, args.ocr_settings if args.ocr_settings is not None else profile.ocr.settings if use_default else {}),
                ('vlm', args.vlm, args.vlm_settings if args.vlm_settings is not None else profile.vlm.settings if use_default else {}),
            )
        })
    except ValueError as error:
        parser.error(str(error))
    app = create_app(root=args.root, models=models, assets_dir=args.assets_dir,
                     cache_capacity=args.cache_capacity, cache_max_bytes=args.cache_max_bytes,
                     runtime_configuration=selection if use_default or any((args.layout,*figures)) else None,
                     load_models_on_startup=not use_default)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_config=None, proxy_headers=False)


if __name__ == '__main__':
    main()
