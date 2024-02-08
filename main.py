
import logging

from telegram import __version__ as TG_VER
from bot_secrets import BOT_TOKEN, BOT_NAME
from openai_handlers import bot_mentioned, gpt, set_current_model
from gpt_ctx_mgmt import (get_assistant_context, get_assistant_ctx_history, 
                          set_assistant_context, clear_assistant_context_history)

from help import help_command
from white_lists import  admins_filter, groups_filter
from utils_handlers import start, whoami, get_repo_address

try:
    from telegram import __version_info__
except ImportError:
    __version_info__ = (0, 0, 0, 0, 0)  # type: ignore[assignment]

if __version_info__ < (20, 0, 0, "alpha", 1):
    raise RuntimeError(
        f"This example is not compatible with your current PTB version {TG_VER}. To view the "
        f"{TG_VER} version of this example, "
        f"visit https://docs.python-telegram-bot.org/en/v{TG_VER}/examples.html"
    )
from telegram import Update, User
from telegram.ext import Application, CommandHandler, MessageHandler, PrefixHandler, filters

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

def main() -> None:
    """Start the bot."""
    application = Application.builder().token(BOT_TOKEN).build()

    universal_filters = admins_filter | groups_filter
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("sctx", set_assistant_context, filters=universal_filters))
    application.add_handler(CommandHandler("gctx", get_assistant_context, filters=universal_filters))
    application.add_handler(CommandHandler("gctxhist", get_assistant_ctx_history, filters=universal_filters))
    application.add_handler(CommandHandler("clear_ctx", clear_assistant_context_history, filters=universal_filters))
    application.add_handler(CommandHandler("src", get_repo_address, filters=universal_filters))
    application.add_handler(PrefixHandler('@', BOT_NAME,  bot_mentioned, filters=groups_filter))

    
    # direct message & whoami only for admins
    message_filters = filters.TEXT & ~filters.COMMAND & admins_filter & filters.ChatType.PRIVATE
    application.add_handler(MessageHandler(message_filters, gpt))
    application.add_handler(CommandHandler("whoami", whoami, filters=admins_filter))
    application.add_handler(CommandHandler("smodel", set_current_model, filters=admins_filter))

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
