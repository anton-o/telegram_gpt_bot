from telegram import Update
from telegram.ext import ContextTypes


help_message = ("Commands:\n"
                + "/start\n"
                + "/sctx - set system context\n"
                + "/gctx - get current system context\n"
                + "/gctxhsit - get history of system contexts"
                )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""

    await update.message.reply_text(help_message)