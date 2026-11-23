"""Search request shared by KM API and the internal reranker."""
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator


DocumentTypeId = Annotated[int, Field(strict=True, gt=0, le=9007199254740991)]
DocumentTypeLabel = Annotated[str, Field(min_length=1, max_length=160)]


class DateRange(BaseModel):
    model_config = ConfigDict(extra='forbid')

    start: str | None = Field(default=None, alias='from', pattern=r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
    to: str | None = Field(default=None, pattern=r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')

    @field_validator('start', 'to')
    @classmethod
    def validate_calendar_date(cls, value):
        if value is None:
            raise ValueError('Empty date bound')
        date.fromisoformat(value)
        return value

    @model_validator(mode='after')
    def validate_range(self):
        if not (self.start or self.to) or (self.start and self.to and self.start > self.to):
            raise ValueError('Invalid date range')
        return self


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    mode: Literal['lexical', 'semantic', 'hybrid'] = 'hybrid'
    filters: dict[str, str | bool] = Field(default_factory=dict)
    metadata_filters: list[dict] = Field(default_factory=list, max_length=16)
    document_ids: list[str] = Field(default_factory=list, max_length=200)
    type_document_ids: list[DocumentTypeId] = Field(default_factory=list, max_length=100)
    perimeter_code: str | None = Field(default=None, min_length=1, max_length=80)
    type_document_labels: list[DocumentTypeLabel] = Field(default_factory=list, max_length=100)
    date_filters: dict[Literal['edition', 'approval', 'repeal'], DateRange] = Field(default_factory=dict)
    limit: int = Field(default=10, ge=1, le=100)
    rerank: bool = False


class DirectSearchRequest(SearchRequest):
    model_config = ConfigDict(extra='forbid')

    candidates: int = Field(ge=1, le=100)
    vector: Annotated[list[FiniteFloat], Field(min_length=1024, max_length=1024)] | None = None
    fingerprint: str | None = Field(default=None, min_length=1, max_length=256)
    failure_mode: Literal['fallback', 'error'] = 'fallback'

    @model_validator(mode='after')
    def validate_retrieval(self):
        if self.candidates < self.limit:
            raise ValueError('Fewer candidates than requested results')
        if self.mode == 'lexical':
            if self.vector is not None or self.fingerprint is not None:
                raise ValueError('Unexpected vector for lexical search')
        elif self.vector is None or self.fingerprint is None:
            raise ValueError('Vector and encoder fingerprint required')
        return self


def search_scope(request):
    scope = {field: value for field in ('metadata_filters', 'document_ids', 'type_document_ids', 'perimeter_code', 'type_document_labels')
            if (value := getattr(request, field, None))}
    if date_filters := getattr(request, 'date_filters', None):
        scope['date_filters'] = {key: bounds.model_dump(by_alias=True, exclude_none=True)
                                 for key, bounds in date_filters.items()}
    return scope
