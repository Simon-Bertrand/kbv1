from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace

import pytest
from docling_core.types.doc import DocItemLabel, DoclingDocument, TextItem

from pdfparse.enrich import (
    EnrichedTextItem, EnrichedTitleItem, HandlerRegistry,
    UnsupportedDoclingItem, create_default_handler_registry, create_default_wrapper_registry,
    handlers,
)
from tests.enrich_fixtures import SummarySettings, SummaryTextItem, enrichment_pipeline


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.pdf"
    path.write_bytes(b"unit-test PDF source identity")
    document = DoclingDocument(name="fixture")
    document.add_text(label=DocItemLabel.TEXT, text="A paragraph to enrich")
    document.add_text(label=DocItemLabel.TITLE, text="A heading")
    return path, document


class SummaryHandler:
    """A stand-in for an injected model-backed enrichment handler."""

    def __init__(self, summarize):
        self.summarize = summarize

    def __call__(self, item, *, ctx):
        base = handlers.handle_text(item, ctx=ctx)
        values = {field.name: getattr(base, field.name) for field in fields(base)}
        return SummaryTextItem(
            **values, summary=self.summarize(item.text),
            settings=SummarySettings("experiment", (base.element_ref,)),
        )


def experiment(prefix):
    registry = create_default_handler_registry()
    registry.register(TextItem, SummaryHandler(lambda text: prefix + text), replace=True)
    wrappers = create_default_wrapper_registry()
    wrappers.register("experiment.summary_text", SummaryTextItem)
    return enrichment_pipeline(handlers=registry, wrappers=wrappers), registry, wrappers


def test_two_experiments_are_isolated_and_custom_wrappers_roundtrip(source, tmp_path):
    first, original_handlers, original_wrappers = experiment("first: ")
    second, _, _ = experiment("second: ")
    original_handlers.register(TextItem, handlers.handle_text, replace=True)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outputs = list(executor.map(lambda pipeline: pipeline.enrich_document(*source), [first, second]))
    left = next(item for item in outputs[0].elements.values() if type(item) is SummaryTextItem)
    right = outputs[1].get(left.element_ref)
    assert left.summary.startswith("first: ")
    assert right.summary.startswith("second: ")
    assert left.element_ref == right.element_ref
    assert left.locations == right.locations
    assert left.document_id == right.document_id
    # Exact TitleItem registration still takes precedence over TextItem.
    assert any(type(item) is EnrichedTitleItem for item in outputs[0].elements.values())
    with pytest.raises(RuntimeError, match="frozen"):
        first.configuration.enrich.handlers.register(TextItem, handlers.handle_text, replace=True)
    with pytest.raises(RuntimeError, match="frozen"):
        first.codec.wrappers.register("experiment.other", SummaryTextItem)
    path = tmp_path / "experiment.json"
    first.codec.save(outputs[0], path)
    restored = first.codec.load(path)
    restored_item = restored.get(left.element_ref)
    assert restored_item == left
    assert isinstance(restored_item.settings, SummarySettings)
    assert isinstance(restored_item.settings.source_refs, tuple)
    assert restored_item.settings.source_refs[0].resolve(restored) == restored_item
    plain = enrichment_pipeline().enrich_document(*source)
    assert type(plain.get(left.element_ref)) is EnrichedTextItem
    assert not hasattr(plain.get(left.element_ref), "summary")


def test_register_policy_decorator_and_copy(source):
    registry = create_default_handler_registry()
    with pytest.raises(ValueError, match="replace=True"):
        registry.register(TextItem, handlers.handle_text)
    copied = registry.copy()
    @copied.register(TextItem, replace=True)
    def custom(item, *, ctx):
        return replace(handlers.handle_text(item, ctx=ctx), content_for_llm="changed")
    output = enrichment_pipeline(handlers=copied).enrich_document(*source)
    assert any(getattr(item, "content_for_llm", None) == "changed" for item in output.elements.values())
    empty = HandlerRegistry()
    with pytest.raises(UnsupportedDoclingItem):
        enrichment_pipeline(handlers=empty).enrich_document(*source)
    with pytest.raises(TypeError, match="NodeItem"):
        registry.register(str, custom)


def test_unregistered_wrapper_rejected_at_pipeline_boundary(source):
    registry = create_default_handler_registry()
    registry.register(TextItem, SummaryHandler(str.upper), replace=True)
    with pytest.raises(ValueError, match="Unregistered wrapper"):
        enrichment_pipeline(handlers=registry).enrich_document(*source)


@pytest.mark.parametrize("failure", ["return_type", "identity", "reference", "inference"])
def test_invalid_handler_results_and_model_failures_propagate(source, failure):
    registry = create_default_handler_registry()
    def custom(item, *, ctx):
        if failure == "return_type":
            return None
        if failure == "inference":
            raise RuntimeError("enrichment model failed")
        result = handlers.handle_text(item, ctx=ctx)
        if failure == "identity":
            return replace(result, document_id="different document")
        return replace(result, element_ref=ctx.ref("#/texts/unknown"))
    registry.register(TextItem, custom, replace=True)
    with pytest.raises((TypeError, ValueError, RuntimeError)):
        enrichment_pipeline(handlers=registry).enrich_document(*source)
