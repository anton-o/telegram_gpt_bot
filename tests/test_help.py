from types import SimpleNamespace
from unittest.mock import AsyncMock

import help as help_handlers


def make_update(user_id):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )


async def test_regular_user_gets_public_help_only():
    update = make_update(9999)

    await help_handlers.help_command(update, SimpleNamespace())

    message = update.message.reply_text.await_args.args[0]
    assert "/start" in message
    assert "/src" in message
    assert "/whoami" not in message
    assert "/smodel" not in message


async def test_admin_gets_admin_help():
    update = make_update(1001)

    await help_handlers.help_command(update, SimpleNamespace())

    message = update.message.reply_text.await_args.args[0]
    assert "/whoami" in message
    assert "/smodel" in message
    assert "/sbackend" in message
