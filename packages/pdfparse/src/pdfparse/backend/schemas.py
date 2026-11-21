"""Explicit HTTP contracts used to generate the browser SDK."""
from enum import StrEnum
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, computed_field, model_validator

from pdfparse.models.base import Device
from pdfparse.models.catalog import ModelCategory
from pdfparse.progress import Stage


class ApiModel(BaseModel):
    model_config = ConfigDict(extra='forbid', json_schema_serialization_defaults_required=True)


class Status(StrEnum):
    IDLE = 'idle'
    QUEUED = 'queued'
    PROCESSING = 'processing'
    READY = 'ready'
    ERROR = 'error'


class JobEvent(ApiModel):
    stage: Stage
    detail: str | None = None
    at: datetime


class Job(ApiModel):
    id: str
    kind: Literal['document', 'batch', 'runtime']
    name: str
    document_id: str | None = None
    status: Literal['queued', 'running', 'succeeded', 'failed', 'cancelled'] = 'queued'
    stop_requested: bool = False
    stage: Stage | None = None
    detail: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    events: list[JobEvent] = Field(default_factory=list)
    inferences: list[dict[str, JsonValue]] = Field(default_factory=list)
    inference_count: int = 0
    stage_seconds: dict[str, float] = Field(default_factory=dict)

    @computed_field
    @property
    def elapsed_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        return max(0.0, ((self.finished_at or datetime.now(timezone.utc)) - self.started_at).total_seconds())


class Folder(ApiModel):
    name: str
    path: str


class SourceRoot(ApiModel):
    root: str | None


class SourceRootSelection(ApiModel):
    path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class FolderListing(ApiModel):
    root: str
    path: str
    parent: str | None
    folders: list[Folder]


class Document(ApiModel):
    id: str
    name: str
    path: str
    size: int
    version: str
    status: Status = Status.IDLE
    error: str | None = None


class Documents(ApiModel):
    folder: str
    documents: list[Document]
    total: int
    page: int
    page_size: int


class Geometry(ApiModel):
    page_no: int
    bbox: tuple[float, float, float, float]
    coord_origin: Literal['TOPLEFT', 'BOTTOMLEFT']
    charspan: tuple[int, int] | None = None


class Item(ApiModel):
    ref: str
    type: str
    source_class: str
    label: str | None
    parent_ref: str | None
    children_refs: list[str]
    locations: list[Geometry]
    original_text: str | None
    content_for_embedding: str | None
    content_for_llm: str | None
    caption: str | None
    description: str | None
    ocr_text: str | None
    classification: JsonValue
    data: dict[str, JsonValue]
    models: list[dict[str, JsonValue]] = Field(default_factory=list)


class Asset(ApiModel):
    id: str
    ref: str
    asset_path: str | None
    caption: str
    description: str | None
    locations: list[Geometry]
    models: list[dict[str, JsonValue]] = Field(default_factory=list)
    classification: JsonValue = None
    chunk_ids: list[str] = Field(default_factory=list)
    available: bool
    url: str | None = None


class ChunkView(ApiModel):
    id: str
    text: str
    context: str
    contextualized_text: str
    source_refs: list[str]
    locations: list[Geometry]
    transformation_id: str


class Result(ApiModel):
    document_id: str
    items: list[Item]
    chunks: list[ChunkView]
    markdown: str
    notices: list[str] = []
    extraction_regions: list[dict[str, JsonValue]] = Field(default_factory=list)
    quality: dict[str, JsonValue] = Field(default_factory=dict)
    manifest: dict[str, JsonValue] = Field(default_factory=dict)
    metrics: dict[str, JsonValue] = Field(default_factory=dict)
    index_records: list[dict[str, JsonValue]] = Field(default_factory=list)
    document_path: str | None = None
    assets: list[Asset] = Field(default_factory=list)


class Settings(ApiModel):
    pipeline_class: str
    configuration: dict[str, JsonValue]
    extractor: JsonValue
    codecs: JsonValue
    extraction_options: JsonValue
    worker_count: int = 1
    cache_capacity: int
    read_only: bool = True


class ModelSelection(ApiModel):
    name: str = Field(min_length=1)
    device: Device = 'cpu'
    settings: dict[str, JsonValue] = Field(default_factory=dict)


class TokenChunking(ApiModel):
    kind: Literal['token'] = 'token'
    chunk_size: int = Field(default=512, ge=1, le=100_000, strict=True)
    chunk_overlap: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode='after')
    def overlap_budget(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError('Le chevauchement doit être inférieur à la taille cible.')
        return self


class SentenceChunking(TokenChunking):
    kind: Literal['sentence'] = 'sentence'
    min_sentences_per_chunk: int = Field(default=1, ge=1, strict=True)
    min_characters_per_sentence: int = Field(default=12, ge=1, strict=True)


class RecursiveChunking(ApiModel):
    kind: Literal['recursive'] = 'recursive'
    chunk_size: int = Field(default=512, ge=1, le=100_000, strict=True)
    min_characters_per_chunk: int = Field(default=24, ge=1, strict=True)


class StructuralChunking(ApiModel):
    kind: Literal['structural'] = 'structural'
    chunk_size: int = Field(default=512, ge=1, le=512, strict=True)
    target_tokens: int = Field(default=384, ge=1, le=512, strict=True)
    min_tokens: int = Field(default=128, ge=1, le=512, strict=True)
    respect_page_boundaries: bool = Field(default=False, strict=True)


ChunkingSelection = Annotated[TokenChunking | SentenceChunking | RecursiveChunking | StructuralChunking, Field(discriminator='kind')]


FigureMode = Literal['keep', 'ocr', 'vlm', 'ocr_vlm', 'table']


class EnrichmentSelection(ApiModel):
    figures: FigureMode = 'ocr_vlm'
    class_routes: dict[str, FigureMode] = Field(default_factory=dict)
    fallback: FigureMode = 'ocr_vlm'
    confidence_threshold: float = Field(default=0.8, ge=0, le=1, allow_inf_nan=False)
    text_ocr: bool = False
    page_vlm: bool = False


class RuntimeConfiguration(ApiModel):
    layout: ModelSelection | None = None
    picture_classifier: ModelSelection | None = None
    ocr: ModelSelection | None = None
    vlm: ModelSelection | None = None
    chunking: ChunkingSelection = Field(default_factory=StructuralChunking)
    enrichment: EnrichmentSelection = Field(default_factory=EnrichmentSelection)

    def required_services(self) -> set[str]:
        required = {name for name in ('layout','picture_classifier') if getattr(self,name) is not None}
        if self.picture_classifier:
            modes = [self.enrichment.figures, self.enrichment.fallback, *self.enrichment.class_routes.values()]
            if any(mode in ('ocr', 'ocr_vlm') for mode in modes):
                required.add('ocr')
            if any(mode in ('vlm', 'ocr_vlm') for mode in modes):
                required.add('vlm')
            if any(mode == 'table' for mode in modes):
                if self.ocr is None and self.vlm is None:
                    required.add('ocr')
                if self.ocr is not None:
                    required.add('ocr')
                if self.vlm is not None:
                    required.add('vlm')
        if self.enrichment.text_ocr:
            required.add('ocr')
        if self.enrichment.page_vlm:
            required.add('vlm')
        return required

    @model_validator(mode='after')
    def figure_services(self):
        if self.enrichment.class_routes and not self.picture_classifier:
            raise ValueError('Les routes par classe nécessitent un classifieur DFC.')
        missing = [name for name in sorted(self.required_services()) if getattr(self,name) is None]
        if missing:
            raise ValueError('Services requis par le routage : ' + ', '.join(missing))
        return self


class ModelOption(ApiModel):
    category: ModelCategory
    name: str
    prepared: bool
    defaults: dict[str, JsonValue]
    settings_schema: dict[str, JsonValue]


class DeviceOption(ApiModel):
    id: str
    label: str


class RuntimeStatus(ApiModel):
    status: Literal['unloaded', 'loading', 'loaded', 'unloading', 'error'] = 'unloaded'
    configuration: RuntimeConfiguration | None = None
    requested: RuntimeConfiguration | None = None
    error: str | None = None
    revision: int = 0


class RuntimeOptions(ApiModel):
    models: list[ModelOption]
    devices: list[DeviceOption]
    runtime: RuntimeStatus
    defaults: RuntimeConfiguration
