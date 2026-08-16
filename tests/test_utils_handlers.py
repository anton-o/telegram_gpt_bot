from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import ForceReply

import utils_handlers


def make_update():
    user = SimpleNamespace(
        mention_html=lambda: '<a href="tg://user?id=1001">Test User</a>',
        to_json=lambda: '{"id": 1001}',
    )
    message = SimpleNamespace(
        reply_html=AsyncMock(),
        to_json=lambda: '{"message_id": 1}',
    )
    return SimpleNamespace(
        effective_user=user,
        message=message,
    )


async def test_start_mentions_user_and_bot():
    update = make_update()

    await utils_handlers.start(update, SimpleNamespace())

    message = update.message.reply_html.await_args.args[0]
    assert "Test User" in message
    assert "@test_bot" in message
    assert isinstance(
        update.message.reply_html.await_args.kwargs["reply_markup"],
        ForceReply,
    )


async def test_repo_address_returns_project_url():
    update = make_update()

    await utils_handlers.get_repo_address(update, SimpleNamespace())

    message = update.message.reply_html.await_args.args[0]
    assert message == "https://github.com/anton-o/telegram_gpt_bot"


async def test_whoami_includes_message_and_user_json():
    update = make_update()

    await utils_handlers.whoami(update, SimpleNamespace())

    message = update.message.reply_html.await_args.args[0]
    assert '"message_id": 1' in message
    assert '"id": 1001' in message
