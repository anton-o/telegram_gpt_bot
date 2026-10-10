import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import backend_handlers
from state_store import FileStateStore

PHOTO_BYTES = b"\xff\xd8\xffsynthetic-photo\xff\xd9"


def photo_update(caption="Explain this photo", *, chat_type="private", **changes):
    download = AsyncMock(return_value=bytearray(PHOTO_BYTES))
    remote = SimpleNamespace(file_size=len(PHOTO_BYTES), download_as_bytearray=download)
    photo = SimpleNamespace(
        file_size=len(PHOTO_BYTES), get_file=AsyncMock(return_value=remote)
    )
    message = SimpleNamespace(
        text=None,
        caption=caption,
        photo=[photo],
        media_group_id=None,
        reply_text=AsyncMock(),
    )
    for name, value in changes.items():
        setattr(message, name, value)
    return (
        SimpleNamespace(
            message=message,
            effective_chat=SimpleNamespace(
                id=1001 if chat_type == "private" else -2001, type=chat_type
            ),
            effective_user=SimpleNamespace(id=1001),
        ),
        photo,
        remote,
    )


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    store = FileStateStore(tmp_path / "state.json")
    monkeypatch.setattr(backend_handlers, "state_store", store)
    backend_handlers._user_locks.clear()
    return store


def mock_provider(monkeypatch, **kwargs):
    ask = AsyncMock(
        **(
            kwargs
            or {"return_value": backend_handlers.LLMResult("answer", "photo-context")}
        )
    )
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    return ask


async def test_private_photo_passes_caption_and_largest_image_and_saves_only_context(
    monkeypatch, isolated_state
):
    ask = mock_provider(monkeypatch)
    update, photo, remote = photo_update("  Read the sign 📷  ")
    thumbnail = SimpleNamespace(get_file=AsyncMock())
    update.message.photo.insert(0, thumbnail)

    await backend_handlers.image_request(update, SimpleNamespace())

    assert ask.await_args.args == (
        "Read the sign 📷",
        False,
        backend_handlers.DEFAULT_OAI_MODEL,
    )
    image = ask.await_args.kwargs["image"]
    assert image.data == PHOTO_BYTES
    assert image.mime_type == "image/jpeg"
    assert ask.await_args.kwargs["store_context"] is True
    assert ask.await_args.kwargs["allow_web_search"] is True
    thumbnail.get_file.assert_not_awaited()
    photo.get_file.assert_awaited_once()
    remote.download_as_bytearray.assert_awaited_once()
    assert update.message.reply_text.await_args.args == ("answer",)
    record = await isolated_state.get_conversation(1001, 1001)
    assert record["remote_context_id"] == "photo-context"
    assert set(record) == {
        "provider",
        "model",
        "remote_context_id",
        "last_successful_turn_at",
        "suppress_next_start_notice",
    }
    persisted = isolated_state.path.read_text()
    assert "synthetic-photo" not in persisted
    assert "Read the sign" not in persisted


@pytest.mark.parametrize("caption", [None, "", " \n "])
async def test_private_photo_requires_caption_before_downloading(
    monkeypatch, isolated_state, caption
):
    ask = mock_provider(monkeypatch)
    update, photo, _ = photo_update(caption)
    await backend_handlers.image_request(update, SimpleNamespace())
    assert "caption" in update.message.reply_text.await_args.args[0].lower()
    photo.get_file.assert_not_awaited()
    ask.assert_not_awaited()
    assert not isolated_state.path.exists()


async def test_album_rejected_before_downloading(monkeypatch):
    ask = mock_provider(monkeypatch)
    update, photo, _ = photo_update(media_group_id="album")
    await backend_handlers.image_request(update, SimpleNamespace())
    assert "one photo" in update.message.reply_text.await_args.args[0].lower()
    photo.get_file.assert_not_awaited()
    ask.assert_not_awaited()


@pytest.mark.parametrize(
    "caption",
    [
        None,
        "ordinary caption",
        "@other_bot explain",
        "@test_bot_extra explain",
        "@test_bot, explain",
        "explain @test_bot",
    ],
)
async def test_group_photo_without_leading_exact_mention_is_ignored(
    monkeypatch, isolated_state, caption
):
    ask = mock_provider(monkeypatch)
    update, photo, _ = photo_update(caption, chat_type="supergroup")
    await backend_handlers.image_request(update, SimpleNamespace())
    update.message.reply_text.assert_not_awaited()
    photo.get_file.assert_not_awaited()
    ask.assert_not_awaited()
    assert not isolated_state.path.exists()


async def test_group_mention_only_asks_for_caption(monkeypatch):
    ask = mock_provider(monkeypatch)
    update, photo, _ = photo_update("@test_bot", chat_type="group")
    await backend_handlers.image_request(update, SimpleNamespace())
    assert "caption" in update.message.reply_text.await_args.args[0].lower()
    photo.get_file.assert_not_awaited()
    ask.assert_not_awaited()


async def test_group_photo_uses_user_model_but_never_conversation_state(
    monkeypatch, isolated_state
):
    await isolated_state.save_user(
        1001, {"use_gemini": True, "gemini_model": "gemini-test"}
    )
    ask = mock_provider(monkeypatch)
    for name in ("get_conversation", "save_conversation", "reset_conversation"):
        monkeypatch.setattr(
            isolated_state,
            name,
            AsyncMock(side_effect=AssertionError("group accessed conversation")),
        )
    update, _, _ = photo_update("@TEST_BOT  Explain 🐈", chat_type="supergroup")
    await backend_handlers.image_request(update, SimpleNamespace())
    assert ask.await_args.args == ("Explain 🐈", True, "gemini-test")
    assert ask.await_args.kwargs["store_context"] is False
    assert "previous_context_id" not in ask.await_args.kwargs
    assert update.message.reply_text.await_count == 1


@pytest.mark.parametrize(
    "failure",
    [
        "declared-size",
        "remote-size",
        "actual-size",
        "empty",
        "invalid",
        "get-file",
        "download",
    ],
)
async def test_photo_failure_preserves_context_and_hides_secrets(
    monkeypatch, isolated_state, caplog, failure
):
    record = {
        "provider": "openai",
        "model": backend_handlers.DEFAULT_OAI_MODEL,
        "remote_context_id": "last-good",
        "last_successful_turn_at": datetime.now(timezone.utc).isoformat(),
    }
    await isolated_state.save_conversation(1001, 1001, record)
    ask = mock_provider(monkeypatch)
    update, photo, remote = photo_update()
    monkeypatch.setattr(backend_handlers, "MAX_IMAGE_BYTES", 100)
    if failure == "declared-size":
        photo.file_size = 101
    elif failure == "remote-size":
        remote.file_size = 101
    elif failure == "actual-size":
        remote.download_as_bytearray.return_value = bytearray(b"x" * 101)
    elif failure == "empty":
        remote.download_as_bytearray.return_value = bytearray()
    elif failure == "invalid":
        remote.download_as_bytearray.return_value = bytearray(b"not an image")
    elif failure == "get-file":
        photo.get_file.side_effect = RuntimeError("SECRET_TOKEN")
    else:
        remote.download_as_bytearray.side_effect = RuntimeError("SECRET_TOKEN")
    await backend_handlers.image_request(update, SimpleNamespace())
    assert update.message.reply_text.await_count == 1
    assert "SECRET_TOKEN" not in update.message.reply_text.await_args.args[0]
    assert "SECRET_TOKEN" not in caplog.text
    ask.assert_not_awaited()
    assert await isolated_state.get_conversation(1001, 1001) == record
    if failure == "declared-size":
        photo.get_file.assert_not_awaited()
    if failure == "remote-size":
        remote.download_as_bytearray.assert_not_awaited()


async def test_text_photo_text_context_survives_restart(monkeypatch, isolated_state):
    ask = mock_provider(
        monkeypatch,
        side_effect=[
            backend_handlers.LLMResult("first", "text-1"),
            backend_handlers.LLMResult("image", "photo-2"),
            backend_handlers.LLMResult("third", "text-3"),
        ],
    )
    await backend_handlers._ask_private_llm("first", chat_id=1001, user_id=1001)
    monkeypatch.setattr(
        backend_handlers, "state_store", FileStateStore(isolated_state.path)
    )
    update, _, _ = photo_update()
    await backend_handlers.image_request(update, SimpleNamespace())
    await backend_handlers._ask_private_llm("follow up", chat_id=1001, user_id=1001)
    assert [call.kwargs["previous_context_id"] for call in ask.await_args_list] == [
        None,
        "text-1",
        "photo-2",
    ]
    assert "image" not in ask.await_args_list[0].kwargs
    assert "image" not in ask.await_args_list[2].kwargs


async def test_image_is_reused_when_provider_context_is_missing(
    monkeypatch, isolated_state
):
    await isolated_state.save_conversation(
        1001,
        1001,
        {
            "provider": "openai",
            "model": backend_handlers.DEFAULT_OAI_MODEL,
            "remote_context_id": "unavailable-context",
            "last_successful_turn_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    ask = mock_provider(
        monkeypatch,
        side_effect=[
            backend_handlers.ContextUnavailableError(),
            backend_handlers.LLMResult("answer", "new-context"),
        ],
    )
    update, photo, remote = photo_update()
    await backend_handlers.image_request(update, SimpleNamespace())
    assert ask.await_count == 2
    assert ask.await_args_list[0].kwargs["previous_context_id"] == "unavailable-context"
    assert "previous_context_id" not in ask.await_args_list[1].kwargs
    assert (
        ask.await_args_list[0].kwargs["image"] is ask.await_args_list[1].kwargs["image"]
    )
    photo.get_file.assert_awaited_once()
    remote.download_as_bytearray.assert_awaited_once()
    assert (await isolated_state.get_conversation(1001, 1001))[
        "remote_context_id"
    ] == "new-context"


async def test_slow_photo_download_cannot_be_overtaken_by_text(monkeypatch):
    ask = mock_provider(
        monkeypatch,
        side_effect=[
            backend_handlers.LLMResult("image", "photo-1"),
            backend_handlers.LLMResult("text", "text-2"),
        ],
    )
    started, release = asyncio.Event(), asyncio.Event()
    update, _, remote = photo_update()

    async def download():
        started.set()
        await release.wait()
        return bytearray(PHOTO_BYTES)

    remote.download_as_bytearray.side_effect = download
    photo_task = asyncio.create_task(
        backend_handlers.image_request(update, SimpleNamespace())
    )
    try:
        await asyncio.wait_for(started.wait(), 1)
        text_task = asyncio.create_task(
            backend_handlers._ask_private_llm("later", chat_id=1001, user_id=1001)
        )
        await asyncio.sleep(0)
        ask.assert_not_awaited()
    finally:
        release.set()
        await photo_task
    await text_task
    assert [call.args[0] for call in ask.await_args_list] == [
        "Explain this photo",
        "later",
    ]
    assert ask.await_args_list[1].kwargs["previous_context_id"] == "photo-1"


@pytest.mark.parametrize(
    ("data", "mime_type"),
    [(b"\x89PNG\r\n\x1a\n", "image/png"), (b"RIFF\x04\x00\x00\x00WEBP", "image/webp")],
)
async def test_photo_format_detected_and_missing_size_metadata_is_supported(
    monkeypatch, data, mime_type
):
    ask = mock_provider(monkeypatch)
    update, photo, remote = photo_update()
    photo.file_size = remote.file_size = None
    remote.download_as_bytearray.return_value = bytearray(data)
    monkeypatch.setattr(backend_handlers, "MAX_IMAGE_BYTES", len(data))
    await backend_handlers.image_request(update, SimpleNamespace())
    assert ask.await_args.kwargs["image"].data == data
    assert ask.await_args.kwargs["image"].mime_type == mime_type


async def test_provider_failure_on_photo_preserves_last_success(
    monkeypatch, isolated_state
):
    record = {
        "provider": "openai",
        "model": backend_handlers.DEFAULT_OAI_MODEL,
        "remote_context_id": "last-good",
        "last_successful_turn_at": datetime.now(timezone.utc).isoformat(),
    }
    await isolated_state.save_conversation(1001, 1001, record)
    mock_provider(
        monkeypatch,
        return_value=backend_handlers.LLMResult("API failed", None, succeeded=False),
    )
    update, _, _ = photo_update()
    await backend_handlers.image_request(update, SimpleNamespace())
    assert await isolated_state.get_conversation(1001, 1001) == record
    update.message.reply_text.assert_awaited_once_with(
        "API failed", parse_mode="Markdown"
    )


@pytest.mark.parametrize("reset_reason", ["timeout", "reset", "model", "provider"])
async def test_photo_turn_respects_context_resets(
    monkeypatch, isolated_state, reset_reason
):
    record = {
        "provider": "openai",
        "model": backend_handlers.DEFAULT_OAI_MODEL,
        "remote_context_id": "old-context",
        "last_successful_turn_at": datetime.now(timezone.utc).isoformat(),
    }
    if reset_reason == "timeout":
        record["last_successful_turn_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=31)
        ).isoformat()
    await isolated_state.save_conversation(1001, 1001, record)
    ask = mock_provider(monkeypatch)
    update, _, _ = photo_update()
    if reset_reason == "reset":
        await backend_handlers.reset_conversation(update, SimpleNamespace())
    elif reset_reason == "model":
        await backend_handlers.set_current_model(
            update, SimpleNamespace(args=["new-model"])
        )
    elif reset_reason == "provider":
        await backend_handlers.set_backend(update, SimpleNamespace(args=["gemini"]))
    update.message.reply_text.reset_mock()
    await backend_handlers.image_request(update, SimpleNamespace())
    assert ask.await_args.kwargs["previous_context_id"] is None
    assert ask.await_args.kwargs["image"].data == PHOTO_BYTES
    if reset_reason == "provider":
        assert ask.await_args.args[1] is True
    if reset_reason == "model":
        assert ask.await_args.args[2] == "new-model"
    assert update.message.reply_text.await_count == (
        2 if reset_reason == "timeout" else 1
    )


async def test_photo_context_is_isolated_by_chat_and_user(monkeypatch, isolated_state):
    ask = mock_provider(
        monkeypatch,
        side_effect=[
            backend_handlers.LLMResult("first", "first-context"),
            backend_handlers.LLMResult("other user", "other-user-context"),
            backend_handlers.LLMResult("other chat", "other-chat-context"),
            backend_handlers.LLMResult("follow up", "continued-context"),
        ],
    )
    first, _, _ = photo_update()
    other_user, _, _ = photo_update()
    other_user.effective_user.id = 2002
    other_chat, _, _ = photo_update()
    other_chat.effective_chat.id = 3003
    for update in (first, other_user, other_chat, first):
        await backend_handlers.image_request(update, SimpleNamespace())
    assert [call.kwargs["previous_context_id"] for call in ask.await_args_list] == [
        None,
        None,
        None,
        "first-context",
    ]
    assert (await isolated_state.get_conversation(1001, 2002))[
        "remote_context_id"
    ] == "other-user-context"
    assert (await isolated_state.get_conversation(3003, 1001))[
        "remote_context_id"
    ] == "other-chat-context"


@pytest.mark.parametrize(
    "missing", ["message", "effective_chat", "effective_user", "photo"]
)
async def test_incomplete_photo_update_is_ignored(monkeypatch, missing):
    ask = mock_provider(monkeypatch)
    update, photo, _ = photo_update()
    if missing == "photo":
        update.message.photo = []
    else:
        setattr(update, missing, None)
    await backend_handlers.image_request(update, SimpleNamespace())
    ask.assert_not_awaited()
    photo.get_file.assert_not_awaited()
