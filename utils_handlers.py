from telegram import ForceReply, Update
from telegram.ext import ContextTypes

from bot_secrets import BOT_NAME


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """get user's parameters"""
    user = update.effective_user
    await update.message.reply_html(
        f"Whoami handler: {update.message.to_json()}" \
        + f"\nuser: {update.effective_user.to_json()}", #\
        # + f"\nuser: {context}",

        reply_markup=ForceReply(selective=True),
        )


# Define a few command handlers. These usually take the two arguments update and
# context.
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(f"Hi {user.mention_html()}! \nSet context, then use @{BOT_NAME} to ask gpt about something. Type /help for the command list.", reply_markup=ForceReply(selective=True))
