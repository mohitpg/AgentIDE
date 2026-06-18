"""Image extraction / encoding helpers — pure functions, no model or network."""

from __future__ import annotations

from agent_vision.images import (
    build_multimodal_content,
    bytes_to_data_uri,
    extract_image_sources,
)

_DATA = "data:image/png;base64,AAAABBBB"


def test_extract_data_uri() -> None:
    sources, residual = extract_image_sources(f"look at {_DATA} please")
    assert sources == [_DATA]
    assert residual == "look at please"


def test_extract_http_image_url_with_query() -> None:
    url = "https://example.com/path/diagram.PNG?v=2"
    sources, residual = extract_image_sources(f"convert {url} to code")
    assert sources == [url]
    assert residual == "convert to code"


def test_non_image_link_is_not_extracted() -> None:
    text = "plain text with a link https://example.com/page"
    sources, residual = extract_image_sources(text)
    assert sources == []
    assert residual == text


def test_local_image_path_inlined_as_data_uri(tmp_path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake-bytes")
    sources, residual = extract_image_sources(f"ocr {img}")
    assert len(sources) == 1
    assert sources[0].startswith("data:image/png;base64,")
    assert residual == "ocr"


def test_missing_local_path_is_left_as_text() -> None:
    sources, residual = extract_image_sources("ocr nonexistent-file.png now")
    assert sources == []
    assert residual == "ocr nonexistent-file.png now"


def test_build_multimodal_content_shape() -> None:
    blocks = build_multimodal_content("describe", [_DATA])
    assert blocks[0] == {"type": "text", "text": "describe"}
    assert blocks[1] == {"type": "image_url", "image_url": {"url": _DATA}}


def test_bytes_to_data_uri() -> None:
    uri = bytes_to_data_uri(b"hello", "image/jpeg")
    assert uri.startswith("data:image/jpeg;base64,")
