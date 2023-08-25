from telegram import Update
from telegram.ext import ContextTypes


help_message = ("Commands:\n"
                + "/start\n"
                + "/sctx - set user's gpt-system context\n"
                + "/gctx - get user's current gpt-system context\n"
                + "/clear_ctx - clear user's gpt-system contexts history\n"
                + "/gctxhsit - get user's history of gpt-system contexts"
                )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""

    await update.message.reply_text(help_message)