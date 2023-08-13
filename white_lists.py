from telegram.ext import filters
from bot_secrets import _admins, _groups


admins_filter = filters.User(user_id=_admins)
groups_filter = filters.Chat(chat_id=_groups)

def is_whitelisted(chat_id: int) -> bool:
    return id in (_admins + _groups)