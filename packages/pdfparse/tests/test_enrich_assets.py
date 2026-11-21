from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest
from docling_core.types.doc import DoclingDocument, ImageRef
from PIL import Image

from pdfparse.enrich import (
    AssetStore, EnrichedPictureItem, FileSystemAssetStore,
    MemoryAssetStore, wrap_document,
)

from tests.enrich_fixtures import enrichment_pipeline


@pytest.mark.parametrize("kind", ["disk", "memory"])
def test_asset_store_contract(tmp_path, kind):
    store = FileSystemAssetStore(tmp_path / "assets") if kind == "disk" else MemoryAssetStore()
    assert store.put("images/a.png", b"png bytes", media_type="image/png") == "images/a.png"
    assert store.put("images/a.png", b"png bytes", media_type="image/png") == "images/a.png"
    assert store.get("images/a.png") == b"png bytes"
    with pytest.raises(ValueError, match="Different content"):
        store.put("images/a.png", b"other bytes", media_type="image/png")
    assert store.get("images/a.png") == b"png bytes"
    with pytest.raises((FileNotFoundError, KeyError)):
        store.get("missing.png")


@pytest.mark.parametrize("key", ["", ".", "../escape", "/absolute", "nested/../escape", "a\\b"])
def test_stores_reject_invalid_keys(tmp_path, key):
    for store in [FileSystemAssetStore(tmp_path), MemoryAssetStore()]:
        with pytest.raises(ValueError):
            store.put(key, b"data", media_type="image/png")


def test_two_filesystem_stores_publish_identical_assets_concurrently(tmp_path):
    first = FileSystemAssetStore(tmp_path)
    second = FileSystemAssetStore(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda store: store.put("image.png", b"same bytes", media_type="image/png"),
            [first, second],
        ))
    assert results == ["image.png", "image.png"]
    assert first.get("image.png") == b"same bytes"
    assert list(tmp_path.iterdir()) == [tmp_path / "image.png"]


def test_image_handler_uses_injected_store_and_default_files_are_compatible(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    doc = DoclingDocument(name="picture")
    doc.add_picture(image=ImageRef.from_pil(Image.new("RGB", (20, 30), "blue"), dpi=72))
    memory = MemoryAssetStore()
    pipeline = enrichment_pipeline(asset_store=memory)
    result = pipeline.enrich_document(source, doc)
    picture = next(item for item in result.elements.values() if isinstance(item, EnrichedPictureItem))
    assert result.assets_dir is None
    assert Image.open(BytesIO(memory.get(picture.asset_path))).size == (20, 30)
    disk_dir = tmp_path / "assets"
    disk_result = wrap_document(source, doc, asset_store=FileSystemAssetStore(disk_dir))
    disk_picture = disk_result.get(picture.element_ref)
    assert disk_picture.asset_path == picture.asset_path
    assert (disk_dir / picture.asset_path).read_bytes() == memory.get(picture.asset_path)
    assert disk_result.assets_dir == disk_dir
    assert len(list(disk_dir.glob("*.png"))) == 1
    without_assets = enrichment_pipeline().enrich_document(source, doc)
    assert without_assets.get(picture.element_ref).asset_path is None


def test_asset_store_errors_are_not_silenced(tmp_path):
    class BrokenStore(AssetStore):
        def put(self, key, data, *, media_type):
            raise OSError("storage unavailable")
        def get(self, locator):
            raise OSError("storage unavailable")
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    doc = DoclingDocument(name="picture")
    doc.add_picture(image=ImageRef.from_pil(Image.new("RGB", (20, 20)), dpi=72))
    with pytest.raises(OSError, match="storage unavailable"):
        enrichment_pipeline(asset_store=BrokenStore()).enrich_document(source, doc)
