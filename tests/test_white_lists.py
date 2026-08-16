from datetime import datetime, timezone

from telegram import Chat, Message, Update, User

import white_lists


def make_update(user_id, chat_id, chat_type):
    user = User(id=user_id, first_name="Test", is_bot=False)
    chat = Chat(id=chat_id, type=chat_type)
    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=user,
        text="message",
    )
    return Update(update_id=1, message=message)


def test_admin_filter_accepts_configured_admin():
    update = make_update(1001, 1001, Chat.PRIVATE)
    assert white_lists.admins_filter.check_update(update) is True


def test_admin_filter_rejects_regular_user():
    update = make_update(9999, 9999, Chat.PRIVATE)
    assert white_lists.admins_filter.check_update(update) is False


def test_group_filter_accepts_configured_group():
    update = make_update(9999, -2001, Chat.GROUP)
    assert white_lists.groups_filter.check_update(update) is True


def test_group_filter_rejects_unknown_group():
    update = make_update(9999, -9999, Chat.GROUP)
    assert white_lists.groups_filter.check_update(update) is False
