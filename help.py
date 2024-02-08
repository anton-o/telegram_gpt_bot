from telegram import Update
from telegram.ext import ContextTypes

from bot_secrets import _admins

help_admin_message = ("/whoami - get user info, debug\n"
                      + "/smodel - set current model, lists models if no fit"

)

help_message = ("Commands:\n"
                + "/start\n"
                + "/sctx - set user's gpt-system context\n"
                + "/gctx - get user's current gpt-system context\n"
                + "/clear_ctx - clear user's gpt-system contexts history\n"
                + "/gctxhsit - get user's history of gpt-system contexts\n"
                + "/src - github repo URL\n"
                )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    message = help_message + help_admin_message if update.effective_user.id in _admins else ""

    await update.message.reply_text(message)