from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import BadRequestError

import backend_handlers


@pytest.mark.parametrize("use_gemini", [False, True])
async def test_image_input_uses_provider_format_and_retains_context(
    monkeypatch, use_gemini
):
    response = SimpleNamespace(id="context-2", output_text="description", steps=[])
    create = AsyncMock(return_value=response)
    if use_gemini:
        monkeypatch.setattr(
            backend_handlers,
            "gemini_client",
            SimpleNamespace(
                aio=SimpleNamespace(interactions=SimpleNamespace(create=create))
            ),
        )
    else:
        monkeypatch.setattr(
            backend_handlers,
            "openai_client",
            SimpleNamespace(responses=SimpleNamespace(create=create)),
        )

    result = await backend_handlers._async_ask_llm(
        "Describe",
        use_gemini,
        "test-model",
        previous_context_id="context-1",
        store_context=True,
        image=backend_handlers.ImageInput(b"abc", "image/jpeg"),
    )

    payload = create.await_args.kwargs
    assert result.context_id == "context-2"
    assert result.succeeded
    assert payload["store"] is True
    assert payload["model"] == "test-model"
    if use_gemini:
        assert payload["input"] == [
            {"type": "text", "text": "Describe"},
            {"type": "image", "data": "YWJj", "mime_type": "image/jpeg"},
        ]
        assert payload["previous_interaction_id"] == "context-1"
        assert payload["response_modalities"] == ["text"]
        assert payload["tools"] == [{"type": "google_search"}]
    else:
        assert payload["input"] == [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Describe"},
                    {
                        "type": "input_image",
                        "image_url": "data:image/jpeg;base64,YWJj",
                        "detail": "auto",
                    },
                ],
            }
        ]
        assert payload["previous_response_id"] == "context-1"
        assert "tools" not in payload


async def test_image_survives_web_search_fallback(monkeypatch):
    error = BadRequestError(
        "private payload",
        response=httpx.Response(
            400, request=httpx.Request("POST", "https://example.test")
        ),
        body={"param": "tools[0].type"},
    )
    create = AsyncMock(
        side_effect=[error, SimpleNamespace(id="response", output_text="answer")]
    )
    monkeypatch.setattr(
        backend_handlers,
        "openai_client",
        SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    result = await backend_handlers._async_ask_llm(
        "Describe",
        False,
        "test-model",
        store_context=False,
        allow_web_search=True,
        image=backend_handlers.ImageInput(b"abc", "image/png"),
    )
    assert result.succeeded
    assert result.context_id is None
    assert create.await_count == 2
    first, second = [call.kwargs for call in create.await_args_list]
    assert first["input"] == second["input"]
    assert first["input"][0]["content"][1]["image_url"] == "data:image/png;base64,YWJj"
    assert first["store"] is False and second["store"] is False
    assert "tools" in first and "tools" not in second


@pytest.mark.parametrize("use_gemini", [False, True])
async def test_unsupported_image_request_is_not_retried_without_image(
    monkeypatch, caplog, use_gemini
):
    error = RuntimeError("SECRET_IMAGE_PAYLOAD")
    error.status_code = 400
    create = AsyncMock(side_effect=error)
    if use_gemini:
        monkeypatch.setattr(
            backend_handlers,
            "gemini_client",
            SimpleNamespace(
                aio=SimpleNamespace(interactions=SimpleNamespace(create=create))
            ),
        )
    else:
        monkeypatch.setattr(
            backend_handlers,
            "openai_client",
            SimpleNamespace(responses=SimpleNamespace(create=create)),
        )
    result = await backend_handlers._async_ask_llm(
        "Describe",
        use_gemini,
        "text-only-model",
        previous_context_id="last-good",
        store_context=True,
        image=backend_handlers.ImageInput(b"abc", "image/jpeg"),
    )
    assert not result.succeeded
    assert result.context_id is None
    assert "image" in result.text.lower() and "/smodel" in result.text
    assert "SECRET_IMAGE_PAYLOAD" not in result.text + caplog.text
    create.assert_awaited_once()
