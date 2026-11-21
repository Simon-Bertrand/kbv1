"""Generic detector adapter for Docling's standard PDF pipeline."""

import math
import re
from types import MappingProxyType
from collections.abc import Sequence
from pathlib import Path

from docling.datamodel.accelerator_options import AcceleratorOptions
from docling.datamodel.base_models import Cluster, LayoutPrediction, Page, Table, TableStructurePrediction
from docling.datamodel.document import ConversionResult
from docling.models.base_layout_model import BaseLayoutModel
from docling.models.base_layout_model import TEXT_ELEM_LABELS
from docling.models.stages.table_structure.table_structure_model import TableStructureModel
from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions, LayoutPostprocessorOptions
from docling.utils.layout_postprocessor import LayoutPostprocessor, SpatialClusterIndex
from docling_core.types.doc import BoundingBox, CoordOrigin, DocItemLabel, TableCell, TableData
from docling_core.types.doc.page import BoundingRectangle, TextCell

from .options import DoclingLayoutOptions, NativeOcrLayoutOptions, SpatialTableOptions
from .catalog import MODELS
from ..ocr import BaseSpatialOcrModel, OcrRegion
from ..paths import model_directory, require_model_files
from pdfparse.progress import Stage, report_progress


def _crop_predictions(model, operation, images):
    """Submit all independent crops before waiting, including legacy backends."""
    if not images:
        return []
    queued = getattr(model, '_inference_queue', None)
    name = 'predict_batch' if operation == 'predict' else operation + '_batch'
    if queued is not None:
        return queued.call(model, name, images, {})
    batched = getattr(model, name, None)
    if batched is not None:
        return batched(images)
    return [getattr(model, operation)(image) for image in images]


class DoclingLayoutAdapter(BaseLayoutModel):
    """Delegate inference and normalize predictions for Docling postprocessing."""

    requires_layout_postprocessing = True

    def __init__(
        self,
        artifacts_path: Path | None,
        accelerator_options: AcceleratorOptions,
        options: DoclingLayoutOptions,
        enable_remote_services: bool = False,
    ) -> None:
        self.options = options
        self._confidence_thresholds = MappingProxyType(dict.fromkeys(
            LayoutPostprocessor.CONFIDENCE_THRESHOLDS, options.confidence_threshold))
        if options.text_ocr is not None:
            if options.skip_cell_assignment:
                raise ValueError('Text-region OCR requires layout cell assignment')
            # Clean and deduplicate once here, before filling empty text regions.
            self.requires_layout_postprocessing = False
        if not options.model_path.exists():
            raise FileNotFoundError(options.model_path)
        self.detector = options.detector_instance if options.detector_instance is not None else options.detector_class(
            model_path=options.model_path,
            device=options.device,
            settings=options.detector_settings,
        )

    @classmethod
    def get_options_type(cls) -> type[DoclingLayoutOptions]:
        return DoclingLayoutOptions

    def predict_layout(
        self, conv_res: ConversionResult, pages: Sequence[Page]
    ) -> Sequence[LayoutPrediction]:
        results = [LayoutPrediction(clusters=[]) for _ in pages]
        inputs = []
        for index, page in enumerate(pages):
            if page.size is None:
                continue
            image = page.get_image(scale=1.0)
            if image is None:
                continue
            width, height = page.size.width, page.size.height
            if not all(math.isfinite(v) and v > 0 for v in (width, height)):
                raise ValueError(f"Invalid page dimensions: {width}, {height}")
            if image.width <= 0 or image.height <= 0:
                raise ValueError("Layout inference requires a non-empty image")
            inputs.append((index, image, width, height))
        if not inputs:
            return results
        predictions = self.detector.predict_batch([image for _, image, _, _ in inputs])
        for (index, image, width, height), detections in zip(inputs, predictions, strict=True):
            sx, sy = width / image.width, height / image.height
            clusters: list[Cluster] = []
            for det in detections:
                score = float(det.confidence)
                if not math.isfinite(score) or not 0 <= score <= 1:
                    raise ValueError(f"Invalid layout confidence: {score}")
                if not isinstance(det.label, DocItemLabel):
                    raise TypeError(f"Invalid Docling label: {det.label!r}")
                x1, y1, x2, y2 = det.xyxy
                if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
                    raise ValueError("Non-finite layout bounding box")
                if score < self.options.confidence_threshold:
                    continue
                l, r = (max(0.0, min(x * sx, width)) for x in (x1, x2))
                t, b = (max(0.0, min(y * sy, height)) for y in (y1, y2))
                if l >= r or t >= b:
                    continue
                clusters.append(
                    Cluster(
                        id=len(clusters),
                        label=det.label,
                        confidence=score,
                        bbox=BoundingBox(
                            l=l, t=t, r=r, b=b, coord_origin=CoordOrigin.TOPLEFT
                        ),
                        cells=[],
                    )
                )
            if self.options.text_ocr is not None:
                clusters = self._recognize_text(pages[index], clusters)
            results[index] = LayoutPrediction(clusters=clusters)
        return results

    def _recognize_text(self, page: Page, clusters: list[Cluster]) -> list[Cluster]:
        if not any(cell.text.strip() for cell in page.cells):
            # On scans, prefer one enclosing text crop over its line proposals.
            # Otherwise Docling can collapse the connected overlap group to one line.
            text_regions = [c for c in clusters if c.label in TEXT_ELEM_LABELS]
            index = SpatialClusterIndex(text_regions)
            covered = set()
            for region in sorted(text_regions, key=lambda c: (c.bbox.area(), c.confidence), reverse=True):
                if region.id in covered:
                    continue
                for candidate_id in index.find_candidates(region.bbox):
                    if candidate_id == region.id or candidate_id in covered:
                        continue
                    candidate = index.clusters_by_id[candidate_id]
                    if candidate.bbox.intersection_over_self(region.bbox) >= 0.95:
                        covered.add(candidate_id)
            clusters = [c for c in clusters if c.id not in covered]
        postprocessor = LayoutPostprocessor(page, clusters, LayoutPostprocessorOptions(
            keep_empty_clusters=True,
            create_orphan_clusters=self.options.create_orphan_clusters,
        ))
        postprocessor.CONFIDENCE_THRESHOLDS = self._confidence_thresholds
        clusters = postprocessor.postprocess()
        next_index = max((cell.index for cell in page.cells), default=-1) + 1
        candidates = []
        for cluster in clusters:
            if any(cell.text.strip() for cell in cluster.cells):
                continue
            spatial = (cluster.label in (DocItemLabel.TABLE, DocItemLabel.DOCUMENT_INDEX)
                       and isinstance(self.options.text_ocr, BaseSpatialOcrModel))
            if cluster.label not in TEXT_ELEM_LABELS and not spatial:
                continue
            candidates.append((cluster, spatial))
        # Keep crop buffering bounded even on a page containing many tiny regions.
        for offset in range(0, len(candidates), 16):
            group = []
            predictions = {}
            for cluster, spatial in candidates[offset:offset + 16]:
                image = page.get_image(scale=2.0, cropbox=cluster.bbox)
                if image is None:
                    raise ValueError(f'Missing text-region crop on page {page.page_no}')
                group.append((cluster, image, spatial))
            for spatial in (False, True):
                selected = [(cluster, image) for cluster, image, is_spatial in group if is_spatial == spatial]
                operation = 'predict_regions' if spatial else 'predict'
                outputs = _crop_predictions(self.options.text_ocr, operation, [image for _, image in selected])
                for (cluster, image), output in zip(selected, outputs, strict=True):
                    if spatial:
                        regions = output
                    else:
                        if not isinstance(output, str):
                            raise TypeError('Text-region OCR must return a string')
                        regions = [OcrRegion(output, (0., 0., float(image.width), float(image.height)))] if output.strip() else []
                    predictions[id(cluster)] = regions
            for cluster, image, spatial in group:
                regions = predictions[id(cluster)]
                cells = []
                sx, sy = cluster.bbox.width / image.width, cluster.bbox.height / image.height
                for region in regions:
                    if not isinstance(region, OcrRegion):
                        raise TypeError('Spatial OCR must return OcrRegion instances')
                    l, t, r, b = region.xyxy
                    if r > image.width or b > image.height:
                        raise ValueError('OCR region escapes its crop')
                    bbox = BoundingBox(l=cluster.bbox.l + l * sx, t=cluster.bbox.t + t * sy,
                                       r=cluster.bbox.l + r * sx, b=cluster.bbox.t + b * sy,
                                       coord_origin=CoordOrigin.TOPLEFT)
                    cell = TextCell(index=next_index, text=region.text, orig=region.text, from_ocr=True,
                                    rect=BoundingRectangle.from_bounding_box(bbox), confidence=0.0)
                    cells.append(cell)
                    next_index += 1
                cluster.cells = cells
                if page.parsed_page is not None and cells:
                    page.parsed_page.textline_cells.extend(cells)
                    page.parsed_page.has_lines = True
        # Empty proposals kept for OCR must not become empty Docling text nodes,
        # including the text children of figures handled by the figure OCR later.
        def keep(cluster: Cluster) -> bool:
            return (cluster.label not in TEXT_ELEM_LABELS or bool(cluster.children)
                    or any(cell.text.strip() for cell in cluster.cells))

        pending = list(clusters)
        for cluster in pending:
            pending.extend(cluster.children)
        for cluster in reversed(pending):
            cluster.children = [child for child in cluster.children if keep(child)]
        return [cluster for cluster in clusters if keep(cluster)]


def native_layout_files(options: LayoutObjectDetectionOptions) -> tuple[Path, tuple[str, ...]]:
    spec = options.model_spec
    override = spec.engine_overrides.get(options.engine_options.engine_type)
    resolved = override.merge_with(spec.repo_id, spec.revision) if override else None
    repo_id = resolved.repo_id if resolved else spec.repo_id
    for model in MODELS.values():
        if model.source.repo_id == repo_id:
            return model_directory('layout', model.name), model.source.files
    raise ValueError(f'Native layout model {repo_id!r} has no local provisioning script in models/.')


class NativeOcrLayoutAdapter(DoclingLayoutAdapter):
    """Run Docling's native layout engine with local weights."""

    requires_layout_postprocessing = False

    def __init__(self, artifacts_path, accelerator_options, options: NativeOcrLayoutOptions,
                 enable_remote_services=False):
        from docling.models.factories import get_layout_factory

        if options.text_ocr is not None and options.skip_cell_assignment:
            raise ValueError('Text-region OCR requires layout cell assignment')
        self.options = options
        self._confidence_thresholds = MappingProxyType(dict(LayoutPostprocessor.CONFIDENCE_THRESHOLDS))
        native_options = LayoutObjectDetectionOptions(**{
            name: getattr(options, name) for name in LayoutObjectDetectionOptions.model_fields
        })
        directory, files = native_layout_files(options)
        require_model_files(directory, files)
        # Docling uses repo_id as the leaf directory under artifacts_path.
        spec = native_options.model_spec
        engine = native_options.engine_options.engine_type
        overrides = dict(spec.engine_overrides)
        if engine in overrides:
            overrides[engine] = overrides[engine].model_copy(update={'repo_id': directory.name})
        native_options.model_spec = spec.model_copy(update={
            'repo_id': directory.name, 'engine_overrides': overrides,
        })
        self.native = get_layout_factory(allow_external_plugins=False).create_instance(
            options=native_options, artifacts_path=directory.parent, accelerator_options=accelerator_options,
            enable_remote_services=False,
        )
        if options.text_ocr is None:
            self.requires_layout_postprocessing = self.native.requires_layout_postprocessing

    @classmethod
    def get_options_type(cls) -> type[NativeOcrLayoutOptions]:
        return NativeOcrLayoutOptions

    def predict_layout(self, conv_res, pages):
        predictions = self.native.predict_layout(conv_res, pages)
        if self.options.text_ocr is None:
            return predictions
        return [LayoutPrediction(clusters=self._recognize_text(page, prediction.clusters))
                for page, prediction in zip(pages, predictions, strict=True)]


def layout_engines() -> dict[str, list[type[DoclingLayoutAdapter]]]:
    """Docling plugin entry point for the adapter defined in this module."""
    return {"layout_engines": [DoclingLayoutAdapter, NativeOcrLayoutAdapter]}


def _spatial_index_table(cluster: Cluster, page_no: int) -> Table | None:
    """Align OCR baselines and repeated numeric right edges in scanned indexes.

    Do not interpret, normalize or correct numeric strings. Text boxes stay in
    page coordinates; no cell locations are inferred from character counts.
    """
    cells = [cell for cell in cluster.cells if cell.text.strip()]
    if not cells or not all(cell.from_ocr for cell in cells):
        return None
    boxes = {cell.index: cell.rect.to_bounding_box() for cell in cells}
    numeric = {cell.index for cell in cells if re.fullmatch(r'[\s\d.,$€£¥%()+−-]+', cell.text)
               and any(char.isdigit() for char in cell.text)}
    tolerance = cluster.bbox.width * .02
    edges: list[list[float]] = []
    for cell in sorted((cell for cell in cells if cell.index in numeric), key=lambda c: boxes[c.index].r):
        right = boxes[cell.index].r
        if edges and right - edges[-1][0] <= tolerance:
            edges[-1].append(right)
        else:
            edges.append([right])
    # A repeated alignment is evidence of a column, not an isolated page number.
    if not edges or not any(len(edge) >= 2 for edge in edges):
        return None
    anchors = [sum(edge) / len(edge) for edge in edges]
    num_cols = len(anchors) + 1
    rows: list[list[TextCell]] = []
    for cell in sorted(cells, key=lambda c: (boxes[c.index].t, boxes[c.index].l)):
        box = boxes[cell.index]
        candidates = []
        for index, row in enumerate(rows):
            anchor = boxes[row[0].index]
            overlap = min(box.b, anchor.b) - max(box.t, anchor.t)
            if overlap >= .5 * min(box.height, anchor.height):
                candidates.append((overlap / min(box.height, anchor.height), index))
        if candidates:
            rows[max(candidates)[1]].append(cell)
        else:
            rows.append([cell])
    table_cells = []
    for row_index, row in enumerate(rows):
        columns: dict[int, list[TextCell]] = {}
        for cell in sorted(row, key=lambda c: boxes[c.index].l):
            box = boxes[cell.index]
            col = (min(range(len(anchors)), key=lambda i: abs(anchors[i] - box.r)) + 1
                   if cell.index in numeric else sum(edge < box.l for edge in anchors))
            columns.setdefault(col, []).append(cell)
        positions = sorted(columns)
        for col in positions:
            group = columns[col]
            group_boxes = [boxes[cell.index] for cell in group]
            table_cells.append(TableCell(
                text=' '.join(cell.text for cell in group),
                bbox=BoundingBox(l=min(b.l for b in group_boxes), t=min(b.t for b in group_boxes),
                                 r=max(b.r for b in group_boxes), b=max(b.b for b in group_boxes),
                                 coord_origin=CoordOrigin.TOPLEFT),
                start_row_offset_idx=row_index, end_row_offset_idx=row_index + 1,
                start_col_offset_idx=col, end_col_offset_idx=col + 1,
            ))
    return Table(id=cluster.id, page_no=page_no, cluster=cluster, label=cluster.label,
                 otsl_seq=[], table_cells=table_cells, num_rows=len(rows), num_cols=num_cols)


class SpatialTableAdapter(TableStructureModel):
    """Retain standard table inference except for spatial OCR document indexes."""

    @classmethod
    def get_options_type(cls) -> type[SpatialTableOptions]:
        return SpatialTableOptions

    def predict_tables(self, conv_res: ConversionResult, pages: Sequence[Page]) -> Sequence[TableStructurePrediction]:
        pages = list(pages)
        recognized = {}
        candidates = []
        if self.options.table_ocr is not None:
            for page in pages:
                if page.predictions.layout is None:
                    continue
                for cluster in page.predictions.layout.clusters:
                    if (cluster.label in (DocItemLabel.TABLE, DocItemLabel.DOCUMENT_INDEX)
                            and not any(cell.text.strip() for cell in cluster.cells)):
                        candidates.append((page, cluster))
            for offset in range(0, len(candidates), 16):
                group = candidates[offset:offset + 16]
                crops = []
                for page, cluster in group:
                    crop = page.get_image(scale=2.0, cropbox=cluster.bbox)
                    if crop is None:
                        raise ValueError(f'Missing table crop on page {page.page_no}')
                    crops.append(crop)
                outputs = _crop_predictions(self.options.table_ocr, 'predict_table', crops)
                for (page, cluster), data in zip(group, outputs, strict=True):
                    recognized[id(cluster)] = data
        prepared = []
        spatial_tables = []
        for page in pages:
            tables = {}
            layout = page.predictions.layout
            if layout is not None:
                next_index = max(
                    [cell.index for cell in page.cells] +
                    [cell.index for cluster in layout.clusters for cell in cluster.cells], default=-1) + 1
                for cluster in layout.clusters:
                    if (self.options.table_ocr is not None
                            and cluster.label in (DocItemLabel.TABLE, DocItemLabel.DOCUMENT_INDEX)
                            and not any(cell.text.strip() for cell in cluster.cells)):
                        report_progress(Stage.OCR, f'Tableau scanné · page {page.page_no}')
                        data = recognized[id(cluster)]
                        if not isinstance(data, TableData):
                            raise TypeError('Table OCR must return TableData')
                        text = '\n'.join(cell.text for cell in data.table_cells if cell.text.strip())
                        if not text.strip():
                            raise ValueError('Table OCR returned no recognized text')
                        cell = TextCell(index=next_index, text=text, orig=text, from_ocr=True,
                                        rect=BoundingRectangle.from_bounding_box(cluster.bbox), confidence=0.0)
                        cluster.cells = [cell]
                        next_index += 1
                        if page.parsed_page is not None:
                            page.parsed_page.textline_cells.append(cell)
                            page.parsed_page.has_lines = True
                        tables[cluster.id] = Table(
                            id=cluster.id, page_no=page.page_no, cluster=cluster, label=cluster.label,
                            otsl_seq=[], table_cells=data.table_cells, num_rows=data.num_rows, num_cols=data.num_cols,
                        )
                    elif cluster.label == DocItemLabel.DOCUMENT_INDEX:
                        table = _spatial_index_table(cluster, page.page_no)
                        if table is not None:
                            tables[cluster.id] = table
            spatial_tables.append(tables)
            copy = page.model_copy()
            copy.predictions = page.predictions.model_copy()
            if layout is not None:
                copy.predictions.layout = layout.model_copy(update={
                    'clusters': [cluster for cluster in layout.clusters if cluster.id not in tables],
                })
            prepared.append(copy)
        predictions = super().predict_tables(conv_res, prepared)
        for page, prediction, tables in zip(pages, predictions, spatial_tables, strict=True):
            prediction.table_map.update(tables)
            page.predictions.tablestructure = prediction
        return predictions


def table_structure_engines() -> dict[str, list[type[SpatialTableAdapter]]]:
    return {'table_structure_engines': [SpatialTableAdapter]}
