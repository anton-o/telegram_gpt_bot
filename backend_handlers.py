import asyncio
from textwrap import wrap
from telegram import Update
from telegram.ext import ContextTypes
from bot_secrets import (
    OPENAI_KEY, 
    ORGANIZATION,
    PROJECT,
    GEMINI_API_KEY
)

from antifraud import check_fraud

from google import genai
from openai import AsyncOpenAI  # Use the Async client

# Initialize clients
# AsyncOpenAI allows non-blocking network calls
openai_client = AsyncOpenAI(
    organization=ORGANIZATION,
    project=PROJECT,
    api_key=OPENAI_KEY
)

# google.genai supports async natively via the .aio property
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# Default model constants
DEFAULT_OAI_MODEL = "gpt-5.2"  # Updated to a more standard OpenAI model
DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview"


async def gpt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    check_fraud(update)
    user_req = update.message.text
    if not user_req:
        return

    # Retrieve user-specific preferences, falling back to defaults
    use_gemini = context.user_data.get("use_gemini", True)
    model = context.user_data.get("gemini_model", DEFAULT_GEMINI_MODEL) if use_gemini else context.user_data.get("oai_model", DEFAULT_OAI_MODEL)

    # Await the async function so the bot doesn't freeze
    resp = await _async_ask_llm(user_req, use_gemini, model)
    
    # Safe chunking for long messages
    for chunk in wrap(resp, width=4096, replace_whitespace=False):
        await update.message.reply_text(chunk)


async def bot_mentioned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    check_fraud(update)
    if not update.message.entities or not update.message.text:
        return

    text_offset = update.message.entities[0].offset + update.message.entities[0].length
    user_req = update.message.text[text_offset:].strip() 

    # Retrieve user-specific preferences
    use_gemini = context.user_data.get("use_gemini", True)
    model = context.user_data.get("gemini_model", DEFAULT_GEMINI_MODEL) if use_gemini else context.user_data.get("oai_model", DEFAULT_OAI_MODEL)

    resp = await _async_ask_llm(user_req, use_gemini, model)
    
    # Added chunking here to prevent Telegram API errors on long mentions
    for chunk in wrap(resp, width=4096, replace_whitespace=False):
        await update.message.reply_text(chunk)


async def set_current_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Determine the current backend and model from user data
    use_gemini = context.user_data.get("use_gemini", True)
    current_model = context.user_data.get("gemini_model", DEFAULT_GEMINI_MODEL) if use_gemini else context.user_data.get("oai_model", DEFAULT_OAI_MODEL)

    # USE CASE 1: Empty command -> Fetch and display the list of available models
    if not context.args:
        # Give the user immediate feedback since API calls can take a second
        await update.message.reply_text("Fetching available models... please wait ⏳")
        
        try:
            if use_gemini:
                available_models = []
                # Fetch Gemini models asynchronously
                pager = await gemini_client.aio.models.list()
                async for model in pager:
                    m_name = model.name.lstrip("models/") if model.name else None
                    if m_name and "gemini-" in m_name:
                        available_models.append(m_name)
                
                model_list_str = "\n".join(available_models)
                await update.message.reply_text(
                    f"Current Gemini model: **{current_model}**\n\n"
                    f"Available Models:\n{model_list_str}\n\n"
                    f"To change, use: `/smodel <model_name>`",
                    parse_mode="Markdown"
                )
            else:
                # Fetch OpenAI models asynchronously
                models_response = await openai_client.models.list()
                # Filter to keep only relevant text models (exclude whisper, dall-e, etc.)
                available_models = [
                    m.id for m in models_response.data 
                    if 'gpt' in m.id or 'o1' in m.id or 'o3' in m.id
                ]
                
                model_list_str = "\n".join(available_models)
                await update.message.reply_text(
                    f"Current OpenAI model: **{current_model}**\n\n"
                    f"Available Models:\n{model_list_str}\n\n"
                    f"To change, use: `/smodel <model_name>`",
                    parse_mode="Markdown"
                )
        except Exception as e:
            await update.message.reply_text(f"⚠️ Failed to fetch the model list: {str(e)}")
        
        return # Exit early since we just showed the list

    # USE CASE 2: User provided a model name -> Update the preference
    new_model = context.args[0].strip()
    
    if use_gemini:
        context.user_data["gemini_model"] = new_model
        await update.message.reply_text(f"✅ Gemini model changed from {current_model} to {new_model}")
    else:
        context.user_data["oai_model"] = new_model
        await update.message.reply_text(f"✅ OpenAI model changed from {current_model} to {new_model}")

async def set_backend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        use_gemini = context.user_data.get("use_gemini", True)
        current = "Gemini" if use_gemini else "OpenAI"
        await update.message.reply_text(f"Current backend is {current}. Use '/sbackend gemini' or '/sbackend openai' to change.")
        return

    backend_requested = context.args[0].lower()
    
    if backend_requested == "gemini":
        context.user_data["use_gemini"] = True
        await update.message.reply_text("Gemini is now your active backend.")
    elif backend_requested == "openai":
        context.user_data["use_gemini"] = False
        await update.message.reply_text("OpenAI is now your active backend.")
    else:
        await update.message.reply_text("Invalid backend. Please use 'gemini' or 'openai'.")


async def _async_ask_llm(user_request: str, use_gemini: bool, model: str) -> str:
    """
    Asynchronously gets the LLM response for a given query.
    Inputs: user query, backend flag, and specific model string.
    Returns: A string containing the API response or an error message.
    """
    try:
        if use_gemini:
            # Using the .aio property accesses the asynchronous methods in the new Gemini SDK
            response = await gemini_client.aio.models.generate_content(
                model=model, 
                contents=user_request,
            )
            return "gmn: " + (response.text if response.text else "[No text returned]")
            
        else:
            # Await the OpenAI async client
            response = await openai_client.chat.completions.create(
                model=model, 
                messages=[
                    {"role": "user", "content": user_request},
                ]
            )
            return "oai: " + (response.choices[0].message.content or "[No text returned]")
            
    except Exception as e:
        # Prevents the bot from crashing silently
        return f"⚠️ API Error: {str(e)}"