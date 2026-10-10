from telegram import Update
from telegram.ext import ContextTypes

from bot_secrets import BOT_NAME, _admins

help_admin_message = (
    "/whoami - get user info, debug\n"
    + "/smodel - set current model, lists models if no fit\n"
    + "/sbackend - set backend, 'gemini' or 'openai'\n"
    + "/reset - start a new private conversation\n"
    + "Private photos require a question in the caption and reuse your private "
    + "conversation context."
)

help_message = (
    "Commands:\n"
    + "/start\n"
    + "/src - github repo URL\n"
    + "Photos (OpenAI/Gemini): send one Telegram photo (up to 10 MiB) with a "
    + "question in its caption. In allowed groups, start the caption with "
    + f"@{BOT_NAME} and a space before the question.\n"
    + "Albums, image documents, and image generation are not supported. "
    + "Photos are processed in memory; group requests are stateless.\n"
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    message = (
        help_message + help_admin_message
        if update.effective_user.id in _admins
        else help_message
    )

    await update.message.reply_text(message)
