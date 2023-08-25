from telegram import Update
from telegram.ext import ContextTypes


# contexts are user-specific
assistant_context_history = {}
assistant_context = {}

def get_effective_ctx(chat_id: int) -> str:
    global assistant_context
    c_ctx = assistant_context[chat_id] if chat_id in assistant_context else ''
    
    return c_ctx + '. Explain your thoughts step by step.'

async def set_assistant_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    global assistant_context, assistant_context_history
    chat_id = update.effective_user.id
    assistant_context[chat_id] = update.message.text.lstrip('/sctx')
    if chat_id not in assistant_context_history:
        assistant_context_history[chat_id] = set()

    assistant_context_history[chat_id].add(assistant_context[chat_id])
    await update.message.reply_text(f"Assistant context is set to: {assistant_context[chat_id]}")


async def get_assistant_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    global assistant_context, assistant_context_history

    chat_id = update.effective_user.id
    await update.message.reply_text(f"Assistant context is set to: {assistant_context.get(chat_id)}")


async def get_assistant_ctx_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global assistant_context_history

    chat_id = update.effective_user.id
    if chat_id in assistant_context_history:
        a_contexts = list(assistant_context_history.get(chat_id))
    else:
        a_contexts = []
    await update.message.reply_text(f"{len(a_contexts)} context(s).")
    [await update.message.reply_text(ctx) for ctx in a_contexts]

async def clear_assistant_context_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    global assistant_context, assistant_context_history

    chat_id = update.effective_user.id
    await update.message.reply_text(f"Assistant contexts history has been cleared.")
