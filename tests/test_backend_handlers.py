from types import SimpleNamespace
from unittest.mock import AsyncMock

import backend_handlers


def make_update(text="question", entities=None):
    message = SimpleNamespace(
        text=text,
        entities=entities,
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(message=message)


def make_context(args=None, user_data=None):
    return SimpleNamespace(
        args=[] if args is None else args,
        user_data={} if user_data is None else user_data,
    )


async def test_private_prompt_uses_default_gemini_backend(monkeypatch):
    ask = AsyncMock(return_value="gmn: answer")
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    update = make_update("What is a black hole?")

    await backend_handlers.gpt(update, make_context())

    ask.assert_awaited_once_with(
        "What is a black hole?",
        True,
        backend_handlers.DEFAULT_GEMINI_MODEL,
    )
    update.message.reply_text.assert_awaited_once_with(
        "gmn: answer",
        parse_mode="Markdown",
    )


async def test_private_prompt_uses_selected_openai_model(monkeypatch):
    ask = AsyncMock(return_value="oai: answer")
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    update = make_update("question")
    context = make_context(
        user_data={"use_gemini": False, "oai_model": "gpt-test"}
    )

    await backend_handlers.gpt(update, context)

    ask.assert_awaited_once_with("question", False, "gpt-test")


async def test_private_empty_message_is_ignored(monkeypatch):
    ask = AsyncMock()
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    update = make_update("")

    await backend_handlers.gpt(update, make_context())

    ask.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()


async def test_group_mention_extracts_prompt(monkeypatch):
    ask = AsyncMock(return_value="gmn: answer")
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    mention = SimpleNamespace(offset=0, length=len("@test_bot"))
    update = make_update("@test_bot   explain this", [mention])

    await backend_handlers.bot_mentioned(update, make_context())

    ask.assert_awaited_once_with(
        "explain this",
        True,
        backend_handlers.DEFAULT_GEMINI_MODEL,
    )


async def test_group_mention_without_prompt_is_ignored(monkeypatch):
    ask = AsyncMock()
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    mention = SimpleNamespace(offset=0, length=len("@test_bot"))
    update = make_update("@test_bot", [mention])

    await backend_handlers.bot_mentioned(update, make_context())

    ask.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()


async def test_group_message_without_entities_is_ignored(monkeypatch):
    ask = AsyncMock()
    monkeypatch.setattr(backend_handlers, "_async_ask_llm", ask)
    update = make_update("ordinary group message", [])

    await backend_handlers.bot_mentioned(update, make_context())

    ask.assert_not_awaited()


async def test_response_is_split_at_telegram_limit(monkeypatch):
    monkeypatch.setattr(
        backend_handlers,
        "_async_ask_llm",
        AsyncMock(return_value="x" * 5000),
    )
    update = make_update()

    await backend_handlers.gpt(update, make_context())

    chunks = [call.args[0] for call in update.message.reply_text.await_args_list]
    assert [len(chunk) for chunk in chunks] == [4096, 904]


async def test_set_backend_reports_and_changes_backend():
    context = make_context()
    update = make_update()

    await backend_handlers.set_backend(update, context)
    assert "Current backend is Gemini" in update.message.reply_text.await_args.args[0]

    context.args = ["openai"]
    await backend_handlers.set_backend(update, context)
    assert context.user_data["use_gemini"] is False

    context.args = ["gemini"]
    await backend_handlers.set_backend(update, context)
    assert context.user_data["use_gemini"] is True


async def test_set_backend_rejects_unknown_value():
    context = make_context(args=["other"])
    update = make_update()

    await backend_handlers.set_backend(update, context)

    assert "Invalid backend" in update.message.reply_text.await_args.args[0]
    assert "use_gemini" not in context.user_data


async def test_set_model_saves_model_for_active_backend():
    update = make_update()
    gemini_context = make_context(args=["gemini-test"])

    await backend_handlers.set_current_model(update, gemini_context)
    assert gemini_context.user_data["gemini_model"] == "gemini-test"

    openai_context = make_context(
        args=["gpt-test"],
        user_data={"use_gemini": False},
    )
    await backend_handlers.set_current_model(update, openai_context)
    assert openai_context.user_data["oai_model"] == "gpt-test"


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
    update = make_update()
    context = make_context(user_data={"use_gemini": False})

    await backend_handlers.set_current_model(update, context)

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
    update = make_update()
    context = make_context(user_data={"use_gemini": False})

    await backend_handlers.set_current_model(update, context)

    result = update.message.reply_text.await_args_list[1].args[0]
    assert result == "⚠️ Failed to fetch the model list: provider unavailable"


async def test_openai_request_uses_single_user_message(monkeypatch):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))]
    )
    create = AsyncMock(return_value=response)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(backend_handlers, "openai_client", client)

    result = await backend_handlers._async_ask_llm(
        "question",
        False,
        "gpt-test",
    )

    assert result == "oai: answer"
    create.assert_awaited_once_with(
        model="gpt-test",
        messages=[{"role": "user", "content": "question"}],
    )


async def test_gemini_request_adds_deduplicated_sources(monkeypatch):
    first_web = SimpleNamespace(uri="https://example.com", title="Example")
    duplicate_web = SimpleNamespace(uri="https://example.com", title="Duplicate")
    metadata = SimpleNamespace(
        grounding_chunks=[
            SimpleNamespace(web=first_web),
            SimpleNamespace(web=duplicate_web),
        ]
    )
    response = SimpleNamespace(
        text="answer",
        candidates=[SimpleNamespace(grounding_metadata=metadata)],
    )
    generate = AsyncMock(return_value=response)
    client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=generate),
        )
    )
    monkeypatch.setattr(backend_handlers, "gemini_client", client)

    result = await backend_handlers._async_ask_llm(
        "question",
        True,
        "gemini-test",
    )

    assert result.startswith("gmn: answer")
    assert result.count("https://example.com") == 1
    assert "Duplicate" in result
    generate.assert_awaited_once()


async def test_provider_error_is_returned_as_controlled_message(monkeypatch):
    create = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(backend_handlers, "openai_client", client)

    result = await backend_handlers._async_ask_llm(
        "question",
        False,
        "gpt-test",
    )

    assert result == "⚠️ API Error: provider unavailable"
