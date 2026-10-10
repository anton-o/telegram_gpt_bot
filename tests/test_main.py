from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from telegram import Chat, Document, Message, MessageEntity, PhotoSize, Update, User
from telegram.ext import CommandHandler, MessageHandler, PrefixHandler

import main


@pytest.fixture
def registered_handlers(monkeypatch):
    application = MagicMock()
    builder = MagicMock()
    builder.token.return_value.build.return_value = application
    monkeypatch.setattr(main.Application, "builder", MagicMock(return_value=builder))
    monkeypatch.setattr(main.state_store, "initialize", MagicMock())

    main.main()
    return [call.args[0] for call in application.add_handler.call_args_list]


def make_update(chat_id, chat_type, user_id, **message_fields):
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            chat=Chat(id=chat_id, type=chat_type),
            from_user=User(id=user_id, first_name="Test", is_bot=False),
            **message_fields,
        ),
    )


@pytest.mark.parametrize(
    ("chat_id", "chat_type", "user_id", "allowed"),
    [
        (1001, "private", 1001, True),
        (9999, "private", 9999, False),
        (-2001, "group", 9999, True),
        (-2001, "supergroup", 9999, True),
        (-9999, "group", 1001, False),
        (-9999, "supergroup", 9999, False),
        (-2001, "channel", 1001, False),
        (-2001, "private", 9999, False),
    ],
)
def test_photo_routing_enforces_allowed_users_and_chat_types(
    registered_handlers, chat_id, chat_type, user_id, allowed
):
    update = make_update(
        chat_id,
        chat_type,
        user_id,
        photo=[PhotoSize("photo-id", "photo-unique-id", 640, 480)],
        caption="@test_bot Describe this photo",
    )

    matching = [
        handler.callback
        for handler in registered_handlers
        if isinstance(handler, MessageHandler) and handler.check_update(update)
    ]

    assert matching == ([main.image_request] if allowed else [])


@pytest.mark.parametrize(
    "message_fields",
    [
        {"text": "Describe this photo"},
        {
            "text": "/help",
            "entities": [MessageEntity(MessageEntity.BOT_COMMAND, 0, 5)],
        },
        {
            "text": "/reset",
            "entities": [MessageEntity(MessageEntity.BOT_COMMAND, 0, 6)],
        },
        {
            "document": Document("file-id", "file-unique-id", mime_type="image/jpeg"),
            "caption": "Describe this photo",
        },
    ],
)
def test_photo_route_ignores_text_commands_and_image_documents(
    registered_handlers, message_fields
):
    update = make_update(1001, "private", 1001, **message_fields)

    assert not any(
        handler.callback is main.image_request and handler.check_update(update)
        for handler in registered_handlers
        if isinstance(handler, MessageHandler)
    )


@pytest.mark.parametrize(
    ("chat_id", "chat_type", "user_id", "text", "callback"),
    [
        (1001, "private", 1001, "Explain this", "gpt"),
        (-2001, "group", 9999, "@test_bot Explain this", "bot_mentioned"),
    ],
)
def test_photo_registration_preserves_text_routes(
    registered_handlers, chat_id, chat_type, user_id, text, callback
):
    update = make_update(chat_id, chat_type, user_id, text=text)

    matching = [
        handler.callback
        for handler in registered_handlers
        if isinstance(handler, (MessageHandler, PrefixHandler))
        and handler.check_update(update)
    ]

    assert matching == [getattr(main, callback)]


def test_main_registers_handlers_and_starts_polling(monkeypatch):
    application = MagicMock()
    builder = MagicMock()
    builder.token.return_value.build.return_value = application
    monkeypatch.setattr(main.Application, "builder", MagicMock(return_value=builder))
    initialize = MagicMock()
    monkeypatch.setattr(main.state_store, "initialize", initialize)

    main.main()

    initialize.assert_called_once_with()
    builder.token.assert_called_once_with("test-telegram-token")
    handlers = [call.args[0] for call in application.add_handler.call_args_list]
    assert len(handlers) == 10
    assert sum(isinstance(handler, CommandHandler) for handler in handlers) == 7
    assert sum(isinstance(handler, PrefixHandler) for handler in handlers) == 1
    assert sum(isinstance(handler, MessageHandler) for handler in handlers) == 2
    application.run_polling.assert_called_once()
