from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from pdfparse.enrich import (
    ElementLocation, EnrichedCodeItem, EnrichedDocument, EnrichedDocumentCodec,
    EnrichedElementType, EnrichedGroupItem, EnrichedPictureItem, EnrichedRef,
    EnrichedTableItem, EnrichedTextItem, WrapperCodec, WrapperRegistry,
    create_default_wrapper_registry,
)
from tests.enrich_fixtures import SummarySettings, SummaryTextItem


def base_fields(kind):
    return dict(
        pdfpath=Path("/tmp/source.pdf"), document_id="source-sha", element_type=kind,
        element_ref=EnrichedRef("source-sha", "#/items/0"), source_class="SourceItem",
        source_label=kind.value, content_layer="body", parent_ref=None,
        locations=(ElementLocation(1, (1, 2, 3, 4), "TOPLEFT", (0, 4)),),
    )


def text_item():
    return EnrichedTextItem(**base_fields(EnrichedElementType.TEXT), original_text="text",
                            content_for_embedding="text", content_for_llm="text")


def document(item):
    result = EnrichedDocument(document_id=item.document_id, pdfpath=item.pdfpath)
    result.add(item)
    return result


@pytest.mark.parametrize("item", [
    text_item(),
    EnrichedCodeItem(**base_fields(EnrichedElementType.CODE), original_text="print(1)",
                     content_for_embedding="print(1)", content_for_llm="print(1)", code_language="Python"),
    EnrichedGroupItem(**base_fields(EnrichedElementType.GROUP), name="section"),
    EnrichedTableItem(**base_fields(EnrichedElementType.TABLE), caption="table", caption_refs=(),
                      num_rows=1, num_cols=1, table_data={"cells": ["text"]},
                      content_for_embedding="text", content_for_llm="text"),
    EnrichedPictureItem(**base_fields(EnrichedElementType.PICTURE), caption="figure", caption_refs=(),
                        description=None, classification={"label": "chart"}, asset_path="image.png",
                        content_for_embedding="figure", content_for_llm="figure"),
])
def test_builtin_wrappers_roundtrip_with_stable_ids(item, tmp_path):
    source = document(item)
    payload = source.to_dict()
    assert payload["format"] == "pdfparse.enriched_document"
    assert payload["schema_version"] == 1
    entry = payload["elements"][0]
    assert entry["type"] == "pdfparse." + item.element_type.value
    assert entry["schema_version"] == 1
    assert "wrapper" not in entry
    target = tmp_path / "nested" / "document.json"
    source.save_json(target)
    restored = EnrichedDocument.load_json(target)
    assert restored.get(item.element_ref) == item
    assert isinstance(restored.get(item.element_ref).element_ref, EnrichedRef)
    assert isinstance(restored.get(item.element_ref).locations[0], ElementLocation)
    assert isinstance(restored.get(item.element_ref).pdfpath, Path)


def test_unversioned_documents_are_rejected():
    payload = document(text_item()).to_dict()
    payload.pop("schema_version")
    with pytest.raises(ValueError, match="Schema version"):
        EnrichedDocument.from_dict(payload)


def summary_item():
    return SummaryTextItem(**base_fields(EnrichedElementType.TEXT), original_text="text",
                           content_for_embedding="summary", content_for_llm="text", summary="summary",
                           settings=SummarySettings("custom", (EnrichedRef("source-sha", "#/items/0"),)))


def test_custom_wrapper_migration_and_nested_fields():
    registry = create_default_wrapper_registry()
    def migrate(payload):
        payload["summary"] = payload.pop("old_summary")
        return payload
    registry.register("experiment.summary", SummaryTextItem, version=2, migrations={1: migrate})
    codec = EnrichedDocumentCodec(registry)
    payload = codec.to_dict(document(summary_item()))
    entry = payload["elements"][0]
    assert entry["schema_version"] == 2
    entry["schema_version"] = 1
    entry["data"]["old_summary"] = entry["data"].pop("summary")
    original = deepcopy(payload)
    restored = codec.from_dict(payload)
    assert restored.get("#/items/0") == summary_item()
    assert payload == original
    assert codec.to_dict(restored)["elements"][0]["schema_version"] == 2
    with pytest.raises(ValueError, match="Unknown wrapper"):
        EnrichedDocument.from_dict(payload)


def test_custom_python_class_rename_does_not_change_persisted_id():
    registry = create_default_wrapper_registry()
    registry.register("experiment.summary", SummaryTextItem)
    codec = EnrichedDocumentCodec(registry)
    payload = codec.to_dict(document(summary_item()))
    original_name = SummaryTextItem.__name__
    try:
        SummaryTextItem.__name__ = "RenamedPythonClass"
        assert codec.from_dict(payload).get("#/items/0").summary == "summary"
        assert codec.to_dict(codec.from_dict(payload))["elements"][0]["type"] == "experiment.summary"
    finally:
        SummaryTextItem.__name__ = original_name


@pytest.mark.parametrize("change,match", [
    (lambda p: p.update(schema_version=99), "Unsupported document"),
    (lambda p: p.update(format="other.format"), "Unsupported document"),
    (lambda p: p.update(schema_version=True), "positive integer"),
    (lambda p: p["elements"][0].update(type="unknown.type"), "Unknown wrapper"),
    (lambda p: p["elements"][0].update(schema_version=99), "future wrapper"),
    (lambda p: p["elements"][0]["data"].update(extra_field="lost data"), "Unknown wrapper fields"),
])
def test_unknown_schemas_and_fields_are_rejected(change, match):
    payload = document(text_item()).to_dict()
    change(payload)
    with pytest.raises(ValueError, match=match):
        EnrichedDocument.from_dict(payload)


def test_missing_wrapper_migration_is_explicit():
    registry = create_default_wrapper_registry()
    registry.register("experiment.summary", SummaryTextItem, version=2)
    codec = EnrichedDocumentCodec(registry)
    payload = codec.to_dict(document(summary_item()))
    payload["elements"][0]["schema_version"] = 1
    with pytest.raises(ValueError, match="Missing migration"):
        codec.from_dict(payload)


def test_registry_snapshot_duplicates_and_unregistered_subclass():
    source = create_default_wrapper_registry()
    codec = EnrichedDocumentCodec(source)
    source.register("experiment.summary", SummaryTextItem)
    with pytest.raises(ValueError, match="Unregistered"):
        codec.to_dict(document(summary_item()))
    with pytest.raises(ValueError, match="Duplicate"):
        source.register("experiment.another", SummaryTextItem)
    with pytest.raises(ValueError, match="Duplicate"):
        source.register("pdfparse.text", SummaryTextItem)


def test_persistence_errors_preserve_existing_file_and_check_custom_references(tmp_path):
    registry = create_default_wrapper_registry()
    registry.register("experiment.summary", SummaryTextItem)
    codec = EnrichedDocumentCodec(registry)
    target = tmp_path / "document.json"
    target.write_text("original")
    item = replace(summary_item(), settings=SummarySettings("custom", (EnrichedRef("other-document", "#/items/0"),)))
    with pytest.raises(ValueError, match="different document"):
        codec.save(document(item), target)
    assert target.read_text() == "original"
    assert not list(tmp_path.glob("*.tmp"))


def test_non_finite_numbers_and_live_objects_are_not_persisted():
    for invalid in [float("nan"), object()]:
        item = replace(text_item(), formatting={"invalid": invalid})
        with pytest.raises((ValueError, TypeError)):
            document(item).to_dict()


def test_cyclic_wrapper_payload_fails_explicitly():
    payload = {}
    payload["cycle"] = payload
    with pytest.raises(ValueError, match="Cycle"):
        document(replace(text_item(), formatting=payload)).to_dict()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), object()])
def test_decode_rejects_non_durable_payloads(value):
    payload = document(text_item()).to_dict()
    payload["elements"][0]["data"]["formatting"] = {"invalid": value}
    with pytest.raises((ValueError, TypeError)):
        EnrichedDocument.from_dict(payload)


def test_loaded_nan_json_is_rejected(tmp_path):
    import json

    payload = document(text_item()).to_dict()
    payload["elements"][0]["data"]["formatting"] = {"invalid": float("nan")}
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Non-finite"):
        EnrichedDocument.load_json(path)


def test_decode_checks_migration_and_custom_codec_results():
    registry = WrapperRegistry()
    def migrate(payload):
        return {**payload, "formatting": {"invalid": float("nan")}}
    registry.register("experiment.migrated", EnrichedTextItem, version=2, migrations={1: migrate})
    codec = EnrichedDocumentCodec(registry)
    payload = codec.to_dict(document(text_item()))
    payload["elements"][0]["schema_version"] = 1
    with pytest.raises(ValueError, match="Non-finite"):
        codec.from_dict(payload)

    class InvalidCodec(WrapperCodec[EnrichedTextItem]):
        def encode(self, item):
            return {"text": item.original_text}
        def decode(self, payload):
            return replace(text_item(), formatting={"runtime": object()})
    registry = WrapperRegistry()
    registry.register("experiment.invalid", EnrichedTextItem, codec=InvalidCodec())
    codec = EnrichedDocumentCodec(registry)
    with pytest.raises(TypeError, match="Unsupported persistent"):
        codec.from_dict(codec.to_dict(document(text_item())))


def test_document_pdf_paths_must_agree_on_load_and_save():
    source = document(text_item())
    payload = source.to_dict()
    payload["elements"][0]["data"]["pdfpath"] = "/tmp/other.pdf"
    with pytest.raises(ValueError, match="PDF path mismatch"):
        EnrichedDocument.from_dict(payload)
    source.get("#/items/0").pdfpath = Path("/tmp/other.pdf")
    with pytest.raises(ValueError, match="PDF path mismatch"):
        source.to_dict()


def test_convenience_methods_reuse_builtin_adapters(tmp_path, monkeypatch):
    from pdfparse.enrich import serialization

    source = document(text_item())
    source.to_dict()  # initialize the shared built-in codecs
    def unexpected(*args, **kwargs):
        pytest.fail("Built-in adapters must not be rebuilt for each document")
    monkeypatch.setattr(serialization, "TypeAdapter", unexpected)
    assert EnrichedDocument.from_dict(source.to_dict()) == source
    path = tmp_path / "source.json"
    source.save_json(path)
    assert EnrichedDocument.load_json(path) == source


def test_custom_codec_is_an_explicit_extension_point():
    class CompactCodec(WrapperCodec[EnrichedTextItem]):
        def encode(self, item):
            return {"text": item.original_text}
        def decode(self, payload):
            return replace(text_item(), original_text=payload["text"])
    registry = WrapperRegistry()
    registry.register("experiment.compact", EnrichedTextItem, codec=CompactCodec())
    codec = EnrichedDocumentCodec(registry)
    payload = codec.to_dict(document(text_item()))
    assert payload["elements"][0]["data"] == {"text": "text"}
    assert codec.from_dict(payload).get("#/items/0") == text_item()
