import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import backend_handlers
from state_store import FileStateStore


def make_update(
    text="question",
    entities=None,
    *,
    user_id=1001,
    chat_id=None,
):
    message = SimpleNamespace(
        text=text,
        entities=entities,
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=user_id if chat_id is None else chat_id),
    )


def make_context(args=None):
    return SimpleNamespace(args=[] if args is None else args, user_data={})


@pytest.fixture(autouse=True)
def isolated_state_store(tmp_path, monkeypatch):
    store = FileStateStore(tmp_path / "user-state.json")
    monkeypatch.setattr(backend_handlers, "state_store", store)
    backend_handlers._user_locks.clear()
    return store


def test_default_settings_use_openai_terra():
    assert backend_handlers._default_user_settings() == {
        "use_gemini": False,
        "gemini_model": backend_handlers.DEFAULT_GEMINI_MODEL,
        "oai_model": "gpt-5.6-terra",
    }


async def test_first_private_prompt_starts_persistent_openai_context(
    monkeypatch, isolated_state_store
):
    ask = AsyncMock(
        return_value=backend_handlers.LLMResult("oai: answer", "response-1")
    )
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    update = make_update("What is a black hole?")

    await backend_handlers.gpt(update, make_context())

    ask.assert_awaited_once_with(
        "What is a black hole?",
        False,
        backend_handlers.DEFAULT_OAI_MODEL,
        previous_context_id=None,
        store_context=True,
        allow_web_search=True,
    )
    assert update.message.reply_text.await_args_list[0].args == (
        backend_handlers.NEW_CONVERSATION_NOTICE,
    )
    assert update.message.reply_text.await_args_list[1].args == ("oai: answer",)
    conversation = await isolated_state_store.get_conversation(1001, 1001)
    assert conversation["remote_context_id"] == "response-1"
    assert conversation["suppress_next_start_notice"] is False


async def test_second_private_prompt_continues_without_notice(monkeypatch):
    ask = AsyncMock(
        side_effect=[
            backend_handlers.LLMResult("oai: first", "context-1"),
            backend_handlers.LLMResult("oai: second", "context-2"),
        ]
    )
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)

    await backend_handlers.gpt(make_update("first"), make_context())
    second_update = make_update("second")
    await backend_handlers.gpt(second_update, make_context())

    assert ask.await_args_list[1].kwargs["previous_context_id"] == "context-1"
    second_update.message.reply_text.assert_awaited_once_with(
        "oai: second", parse_mode="Markdown"
    )


async def test_private_context_is_isolated_between_users(monkeypatch):
    ask = AsyncMock(
        side_effect=[
            backend_handlers.LLMResult("first user", "user-1-context"),
            backend_handlers.LLMResult("second user", "user-2-context"),
            backend_handlers.LLMResult("first user again", "user-1-next"),
            backend_handlers.LLMResult("second user again", "user-2-next"),
        ]
    )
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)

    await backend_handlers.gpt(make_update(user_id=1001), make_context())
    await backend_handlers.gpt(make_update(user_id=2002), make_context())
    await backend_handlers.gpt(make_update(user_id=1001), make_context())
    await backend_handlers.gpt(make_update(user_id=2002), make_context())

    assert [call.kwargs["previous_context_id"] for call in ask.await_args_list] == [
        None,
        None,
        "user-1-context",
        "user-2-context",
    ]


async def test_private_turns_for_one_user_are_serialized(monkeypatch):
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    previous_ids = []

    async def ask(*args, previous_context_id=None, **kwargs):
        previous_ids.append(previous_context_id)
        if len(previous_ids) == 1:
            first_started.set()
            await release_first.wait()
            return backend_handlers.LLMResult("first", "context-1")
        return backend_handlers.LLMResult("second", "context-2")

    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)

    first = asyncio.create_task(
        backend_handlers.gpt(make_update("first"), make_context())
    )
    await first_started.wait()
    second = asyncio.create_task(
        backend_handlers.gpt(make_update("second"), make_context())
    )
    await asyncio.sleep(0)
    assert previous_ids == [None]

    release_first.set()
    await asyncio.gather(first, second)

    assert previous_ids == [None, "context-1"]


async def test_private_prompt_uses_persisted_openai_selection(
    monkeypatch, isolated_state_store
):
    await isolated_state_store.save_user(
        1001,
        {
            "use_gemini": False,
            "gemini_model": backend_handlers.DEFAULT_GEMINI_MODEL,
            "oai_model": "gpt-test",
        },
    )
    ask = AsyncMock(
        return_value=backend_handlers.LLMResult("oai: answer", "response-1")
    )
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)

    await backend_handlers.gpt(make_update(), make_context())

    assert ask.await_args.args[:3] == ("question", False, "gpt-test")
    assert ask.await_args.kwargs["allow_web_search"] is True


async def test_persisted_gemini_selection_overrides_openai_default(
    monkeypatch, isolated_state_store
):
    await isolated_state_store.save_user(
        1001,
        {
            "use_gemini": True,
            "gemini_model": "gemini-test",
            "oai_model": backend_handlers.DEFAULT_OAI_MODEL,
        },
    )
    ask = AsyncMock(
        return_value=backend_handlers.LLMResult("gmn: answer", "interaction-1")
    )
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)

    await backend_handlers.gpt(make_update(), make_context())

    assert ask.await_args.args[:3] == ("question", True, "gemini-test")
    assert ask.await_args.kwargs["allow_web_search"] is False


async def test_private_empty_message_is_ignored(monkeypatch):
    ask = AsyncMock()
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    update = make_update("")

    await backend_handlers.gpt(update, make_context())

    ask.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()


async def test_group_mention_is_stateless(monkeypatch, isolated_state_store):
    ask = AsyncMock(
        return_value=backend_handlers.LLMResult("oai: answer", context_id=None)
    )
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    mention = SimpleNamespace(offset=0, length=len("@test_bot"))
    update = make_update(
        "@test_bot   explain this",
        [mention],
        chat_id=-2001,
    )

    await backend_handlers.bot_mentioned(update, make_context())

    ask.assert_awaited_once_with(
        "explain this",
        False,
        backend_handlers.DEFAULT_OAI_MODEL,
        store_context=False,
    )
    assert await isolated_state_store.get_conversation(-2001, 1001) is None


@pytest.mark.parametrize(
    ("text", "entities"),
    [
        ("@test_bot", [SimpleNamespace(offset=0, length=len("@test_bot"))]),
        ("ordinary group message", []),
    ],
)
async def test_group_message_without_prompt_is_ignored(monkeypatch, text, entities):
    ask = AsyncMock()
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    update = make_update(text, entities, chat_id=-2001)

    await backend_handlers.bot_mentioned(update, make_context())

    ask.assert_not_awaited()


async def test_response_is_split_at_telegram_limit(monkeypatch):
    monkeypatch.setattr(
        backend_handlers,
        "_async_ask_llm",
        AsyncMock(return_value=backend_handlers.LLMResult("x" * 5000, "context-1")),
    )
    update = make_update()

    await backend_handlers.gpt(update, make_context())

    chunks = [call.args[0] for call in update.message.reply_text.await_args_list[1:]]
    assert [len(chunk) for chunk in chunks] == [4096, 904]


async def test_inactive_conversation_expires_and_announces(
    monkeypatch, isolated_state_store
):
    old_timestamp = (
        datetime.now(timezone.utc) - backend_handlers.CONVERSATION_TIMEOUT
    ).isoformat()
    await isolated_state_store.save_conversation(
        1001,
        1001,
        {
            "provider": "openai",
            "model": backend_handlers.DEFAULT_OAI_MODEL,
            "remote_context_id": "old-context",
            "last_successful_turn_at": old_timestamp,
            "suppress_next_start_notice": False,
        },
    )
    ask = AsyncMock(
        return_value=backend_handlers.LLMResult("oai: answer", "new-context")
    )
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    update = make_update()

    await backend_handlers.gpt(update, make_context())

    assert ask.await_args.kwargs["previous_context_id"] is None
    assert update.message.reply_text.await_args_list[0].args == (
        backend_handlers.NEW_CONVERSATION_NOTICE,
    )


async def test_reset_suppresses_next_start_notice(monkeypatch, isolated_state_store):
    update = make_update()
    await backend_handlers.reset_conversation(update, make_context())
    assert "Conversation reset" in update.message.reply_text.await_args.args[0]

    ask = AsyncMock(
        return_value=backend_handlers.LLMResult("oai: answer", "new-context")
    )
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    prompt_update = make_update()
    await backend_handlers.gpt(prompt_update, make_context())

    prompt_update.message.reply_text.assert_awaited_once_with(
        "oai: answer", parse_mode="Markdown"
    )
    conversation = await isolated_state_store.get_conversation(1001, 1001)
    assert conversation["suppress_next_start_notice"] is False


async def test_backend_change_resets_context_and_same_value_preserves_it(
    isolated_state_store,
):
    await isolated_state_store.save_conversation(
        1001,
        1001,
        {
            "provider": "openai",
            "model": backend_handlers.DEFAULT_OAI_MODEL,
            "remote_context_id": "existing-context",
            "last_successful_turn_at": datetime.now(timezone.utc).isoformat(),
            "suppress_next_start_notice": False,
        },
    )
    update = make_update()

    await backend_handlers.set_backend(update, make_context(["openai"]))
    preserved = await isolated_state_store.get_conversation(1001, 1001)
    assert preserved["remote_context_id"] == "existing-context"

    await backend_handlers.set_backend(update, make_context(["gemini"]))
    reset = await isolated_state_store.get_conversation(1001, 1001)
    assert reset["remote_context_id"] is None
    assert reset["suppress_next_start_notice"] is True
    assert "Conversation reset" in update.message.reply_text.await_args.args[0]


async def test_set_backend_reports_current_and_rejects_unknown():
    update = make_update()

    await backend_handlers.set_backend(update, make_context())
    assert "Current backend is OpenAI" in update.message.reply_text.await_args.args[0]

    await backend_handlers.set_backend(update, make_context(["other"]))
    assert "Invalid backend" in update.message.reply_text.await_args.args[0]


async def test_model_change_resets_context_and_same_value_preserves_it(
    isolated_state_store,
):
    await isolated_state_store.save_conversation(
        1001,
        1001,
        {
            "provider": "openai",
            "model": backend_handlers.DEFAULT_OAI_MODEL,
            "remote_context_id": "existing-context",
            "last_successful_turn_at": datetime.now(timezone.utc).isoformat(),
            "suppress_next_start_notice": False,
        },
    )
    update = make_update()

    await backend_handlers.set_current_model(
        update, make_context([backend_handlers.DEFAULT_OAI_MODEL])
    )
    preserved = await isolated_state_store.get_conversation(1001, 1001)
    assert preserved["remote_context_id"] == "existing-context"

    await backend_handlers.set_current_model(update, make_context(["gpt-test"]))
    reset = await isolated_state_store.get_conversation(1001, 1001)
    assert reset["remote_context_id"] is None
    assert reset["model"] == "gpt-test"
    assert "Conversation reset" in update.message.reply_text.await_args.args[0]


async def test_openai_model_listing_filters_unrelated_models(monkeypatch):
    models_response = SimpleNamespace(
        data=[
            SimpleNamespace(id="gpt-test"),
            SimpleNamespace(id="o3-test"),
            SimpleNamespace(id="embedding-test"),
        ]
    )
    client = SimpleNamespace(
        models=SimpleNamespace(list=AsyncMock(return_value=models_response))
    )
    monkeypatch.setattr(backend_handlers, "openai_client", client)
    await backend_handlers.state_store.save_user(
        1001,
        {
            "use_gemini": False,
            "gemini_model": backend_handlers.DEFAULT_GEMINI_MODEL,
            "oai_model": "gpt-test",
        },
    )
    update = make_update()

    await backend_handlers.set_current_model(update, make_context())

    result = update.message.reply_text.await_args_list[1].args[0]
    assert "gpt-test" in result
    assert "o3-test" in result
    assert "embedding-test" not in result


async def test_gemini_model_listing_filters_unrelated_models(monkeypatch):
    async def model_pager():
        for model_name in (
            "models/gemini-test",
            "models/embedding-test",
            None,
        ):
            yield SimpleNamespace(name=model_name)

    client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(list=AsyncMock(return_value=model_pager()))
        )
    )
    monkeypatch.setattr(backend_handlers, "gemini_client", client)
    await backend_handlers.state_store.save_user(
        1001,
        {
            "use_gemini": True,
            "gemini_model": backend_handlers.DEFAULT_GEMINI_MODEL,
            "oai_model": backend_handlers.DEFAULT_OAI_MODEL,
        },
    )
    update = make_update()

    await backend_handlers.set_current_model(update, make_context())

    result = update.message.reply_text.await_args_list[1].args[0]
    assert "gemini-test" in result
    assert "embedding-test" not in result


async def test_model_listing_reports_provider_error(monkeypatch):
    client = SimpleNamespace(
        models=SimpleNamespace(
            list=AsyncMock(side_effect=RuntimeError("provider unavailable"))
        )
    )
    monkeypatch.setattr(backend_handlers, "openai_client", client)
    await backend_handlers.state_store.save_user(
        1001,
        {
            "use_gemini": False,
            "gemini_model": backend_handlers.DEFAULT_GEMINI_MODEL,
            "oai_model": "gpt-test",
        },
    )
    update = make_update()

    await backend_handlers.set_current_model(update, make_context())

    result = update.message.reply_text.await_args_list[1].args[0]
    assert result == "⚠️ Failed to fetch the model list. Please try again."
    assert "provider unavailable" not in result


async def test_openai_request_uses_responses_continuation(monkeypatch):
    response = SimpleNamespace(id="response-2", output_text="answer")
    create = AsyncMock(return_value=response)
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(backend_handlers, "openai_client", client)

    result = await backend_handlers._async_ask_llm(
        "question",
        False,
        "gpt-test",
        previous_context_id="response-1",
        store_context=True,
        allow_web_search=True,
    )

    assert result == backend_handlers.LLMResult("oai: answer", "response-2")
    create.assert_awaited_once_with(
        model="gpt-test",
        input="question",
        store=True,
        previous_response_id="response-1",
        tools=[{"type": "web_search"}],
        tool_choice="auto",
    )


async def test_openai_group_request_does_not_enable_web_search(monkeypatch):
    response = SimpleNamespace(id="response-1", output_text="answer")
    create = AsyncMock(return_value=response)
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(backend_handlers, "openai_client", client)

    result = await backend_handlers._async_ask_llm(
        "question",
        False,
        "gpt-test",
        store_context=False,
    )

    assert result == backend_handlers.LLMResult("oai: answer", None)
    create.assert_awaited_once_with(
        model="gpt-test",
        input="question",
        store=False,
    )


async def test_openai_response_formats_valid_citations(monkeypatch):
    text = "First CIT1, second CIT2, bad BAD and BROKEN."
    first_start = text.index("CIT1")
    second_start = text.index("CIT2")
    bad_start = text.index("BAD")
    broken_start = text.index("BROKEN")
    annotations = [
        SimpleNamespace(
            type="url_citation",
            start_index=first_start,
            end_index=first_start + len("CIT1"),
            title="Paper_One",
            url="https://example.com/one",
        ),
        SimpleNamespace(
            type="url_citation",
            start_index=first_start,
            end_index=first_start + len("CIT1"),
            title="Duplicate",
            url="https://example.com/duplicate",
        ),
        SimpleNamespace(
            type="url_citation",
            start_index=second_start,
            end_index=second_start + len("CIT2"),
            title="Paper [Two]",
            url="https://example.com/two",
        ),
        SimpleNamespace(
            type="url_citation",
            start_index=bad_start,
            end_index=bad_start + len("BAD"),
            title="Unsafe",
            url="ftp://example.com/bad",
        ),
        SimpleNamespace(
            type="url_citation",
            start_index=999,
            end_index=1000,
            title="Malformed",
            url="https://example.com/malformed",
        ),
        SimpleNamespace(
            type="url_citation",
            start_index=broken_start,
            end_index=broken_start + len("BROKEN"),
            title="Malformed URL",
            url="https://[",
        ),
        SimpleNamespace(type="file_citation"),
    ]
    response = SimpleNamespace(
        id="response-1",
        output_text=text,
        output=[
            SimpleNamespace(type="web_search_call"),
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(
                        type="output_text",
                        text=text,
                        annotations=annotations,
                    )
                ],
            ),
        ],
    )
    create = AsyncMock(return_value=response)
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(backend_handlers, "openai_client", client)

    result = await backend_handlers._async_ask_llm(
        "question",
        False,
        "gpt-test",
        store_context=True,
        allow_web_search=True,
    )

    assert result == backend_handlers.LLMResult(
        "oai: First [Paper\\_One](https://example.com/one), "
        "second [Paper \\[Two\\]](https://example.com/two), "
        "bad BAD and BROKEN.",
        "response-1",
    )
    assert "duplicate" not in result.text.lower()


async def test_openai_search_retries_without_tool_when_model_rejects_it(
    monkeypatch,
):
    class FakeBadRequestError(Exception):
        def __init__(self, param):
            self.param = param

    response = SimpleNamespace(id="response-2", output_text="fallback answer")
    create = AsyncMock(side_effect=[FakeBadRequestError("tools[0].type"), response])
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(backend_handlers, "BadRequestError", FakeBadRequestError)
    monkeypatch.setattr(backend_handlers, "openai_client", client)

    result = await backend_handlers._async_ask_llm(
        "question",
        False,
        "gpt-test",
        previous_context_id="response-1",
        store_context=True,
        allow_web_search=True,
    )

    assert result == backend_handlers.LLMResult(
        f"oai: {backend_handlers.OPENAI_SEARCH_FALLBACK_NOTICE}\n\nfallback answer",
        "response-2",
    )
    assert create.await_count == 2
    assert create.await_args_list[0].kwargs == {
        "model": "gpt-test",
        "input": "question",
        "store": True,
        "previous_response_id": "response-1",
        "tools": [{"type": "web_search"}],
        "tool_choice": "auto",
    }
    assert create.await_args_list[1].kwargs == {
        "model": "gpt-test",
        "input": "question",
        "store": True,
        "previous_response_id": "response-1",
    }


async def test_openai_search_does_not_retry_unrelated_bad_request(monkeypatch):
    class FakeBadRequestError(Exception):
        param = "model"

    create = AsyncMock(side_effect=FakeBadRequestError())
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(backend_handlers, "BadRequestError", FakeBadRequestError)
    monkeypatch.setattr(backend_handlers, "openai_client", client)

    result = await backend_handlers._async_ask_llm(
        "question",
        False,
        "gpt-test",
        store_context=True,
        allow_web_search=True,
    )

    assert result == backend_handlers.LLMResult(
        "⚠️ API request failed. Please try again.",
        None,
        succeeded=False,
    )
    assert create.await_count == 1


async def test_gemini_interaction_adds_deduplicated_sources(monkeypatch):
    annotations = [
        SimpleNamespace(
            type="url_citation", url="https://example.com", title="Example"
        ),
        SimpleNamespace(
            type="url_citation", url="https://example.com", title="Duplicate"
        ),
    ]
    interaction = SimpleNamespace(
        id="interaction-2",
        output_text="answer",
        steps=[
            SimpleNamespace(
                type="model_output",
                content=[SimpleNamespace(annotations=annotations)],
            )
        ],
    )
    create = AsyncMock(return_value=interaction)
    client = SimpleNamespace(
        aio=SimpleNamespace(
            interactions=SimpleNamespace(create=create),
        )
    )
    monkeypatch.setattr(backend_handlers, "gemini_client", client)

    result = await backend_handlers._async_ask_llm(
        "question",
        True,
        "gemini-test",
        previous_context_id="interaction-1",
        store_context=True,
    )

    assert result.text.startswith("gmn: answer")
    assert result.text.count("https://example.com") == 1
    assert "Duplicate" in result.text
    assert result.context_id == "interaction-2"
    create.assert_awaited_once_with(
        model="gemini-test",
        input="question",
        generation_config={"temperature": 0.0},
        tools=[{"type": "google_search"}],
        store=True,
        previous_interaction_id="interaction-1",
    )


async def test_missing_provider_context_retries_once_and_announces(
    monkeypatch, isolated_state_store
):
    await isolated_state_store.save_conversation(
        1001,
        1001,
        {
            "provider": "openai",
            "model": backend_handlers.DEFAULT_OAI_MODEL,
            "remote_context_id": "missing-context",
            "last_successful_turn_at": datetime.now(timezone.utc).isoformat(),
            "suppress_next_start_notice": False,
        },
    )
    ask = AsyncMock(
        side_effect=[
            backend_handlers.ContextUnavailableError(),
            backend_handlers.LLMResult("oai: answer", "replacement-context"),
        ]
    )
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    update = make_update()

    await backend_handlers.gpt(update, make_context())

    assert ask.await_count == 2
    assert ask.await_args_list[1].kwargs.get("previous_context_id") is None
    assert update.message.reply_text.await_args_list[0].args == (
        backend_handlers.NEW_CONVERSATION_NOTICE,
    )


async def test_provider_failure_does_not_start_or_persist_conversation(
    monkeypatch, isolated_state_store
):
    ask = AsyncMock(
        return_value=backend_handlers.LLMResult(
            "⚠️ API request failed. Please try again.",
            None,
            succeeded=False,
        )
    )
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    update = make_update()

    await backend_handlers.gpt(update, make_context())

    update.message.reply_text.assert_awaited_once_with(
        "⚠️ API request failed. Please try again.", parse_mode="Markdown"
    )
    assert await isolated_state_store.get_conversation(1001, 1001) is None


async def test_provider_404_with_context_is_classified(monkeypatch):
    error = RuntimeError("missing")
    error.status_code = 404
    create = AsyncMock(side_effect=error)
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(backend_handlers, "openai_client", client)

    with pytest.raises(backend_handlers.ContextUnavailableError):
        await backend_handlers._async_ask_llm(
            "question",
            False,
            "gpt-test",
            previous_context_id="response-1",
            store_context=True,
        )


async def test_provider_error_is_returned_without_internal_details(monkeypatch):
    create = AsyncMock(side_effect=RuntimeError("secret provider payload"))
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(backend_handlers, "openai_client", client)

    result = await backend_handlers._async_ask_llm(
        "question",
        False,
        "gpt-test",
        store_context=True,
    )

    assert result == backend_handlers.LLMResult(
        "⚠️ API request failed. Please try again.",
        None,
        succeeded=False,
    )
    assert "secret" not in result.text


def test_expiration_rejects_missing_malformed_and_naive_timestamps():
    now = datetime.now(timezone.utc)
    base = {"remote_context_id": "context"}

    assert backend_handlers._conversation_is_expired(base, now)
    assert backend_handlers._conversation_is_expired(
        {**base, "last_successful_turn_at": "invalid"}, now
    )
    assert backend_handlers._conversation_is_expired(
        {**base, "last_successful_turn_at": datetime.now().isoformat()}, now
    )
    assert not backend_handlers._conversation_is_expired(
        {
            **base,
            "last_successful_turn_at": (
                now - backend_handlers.CONVERSATION_TIMEOUT + timedelta(seconds=1)
            ).isoformat(),
        },
        now,
    )
