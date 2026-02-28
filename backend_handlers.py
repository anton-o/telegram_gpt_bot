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
from google.genai.types import GenerateContentConfig, Tool, GoogleSearch
from openai import AsyncOpenAI 

# Initialize clients
openai_client = AsyncOpenAI(
    organization=ORGANIZATION,
    project=PROJECT,
    api_key=OPENAI_KEY
)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

DEFAULT_OAI_MODEL = "gpt-5.2"
DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview" 

async def gpt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    check_fraud(update)
    user_req = update.message.text
    if not user_req:
        return

    use_gemini = context.user_data.get("use_gemini", True)
    model = context.user_data.get("gemini_model", DEFAULT_GEMINI_MODEL) if use_gemini else context.user_data.get("oai_model", DEFAULT_OAI_MODEL)

    resp = await _async_ask_llm(user_req, use_gemini, model)
    
    for chunk in wrap(resp, width=4096, replace_whitespace=False):
        # Added Markdown parsing so the extracted URLs render as clickable links
        await update.message.reply_text(chunk, parse_mode="Markdown")

async def bot_mentioned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    check_fraud(update)
    if not update.message.entities or not update.message.text:
        return

    text_offset = update.message.entities[0].offset + update.message.entities[0].length
    user_req = update.message.text[text_offset:].strip() 

    use_gemini = context.user_data.get("use_gemini", True)
    model = context.user_data.get("gemini_model", DEFAULT_GEMINI_MODEL) if use_gemini else context.user_data.get("oai_model", DEFAULT_OAI_MODEL)

    resp = await _async_ask_llm(user_req, use_gemini, model)
    
    for chunk in wrap(resp, width=4096, replace_whitespace=False):
        await update.message.reply_text(chunk, parse_mode="Markdown")

async def set_current_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    use_gemini = context.user_data.get("use_gemini", True)
    current_model = context.user_data.get("gemini_model", DEFAULT_GEMINI_MODEL) if use_gemini else context.user_data.get("oai_model", DEFAULT_OAI_MODEL)

    if not context.args:
        await update.message.reply_text("Fetching available models... please wait ⏳")
        try:
            if use_gemini:
                available_models = []
                pager = await gemini_client.aio.models.list()
                async for model_obj in pager:
                    m_name = model_obj.name.lstrip("models/") if model_obj.name else None
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
                models_response = await openai_client.models.list()
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
        return 

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
    try:
        if use_gemini:
            # 1. Initialize the Search Tool
            search_tool = Tool(google_search=GoogleSearch())
            
            # 2. Attach the tool to the generation config
            config = GenerateContentConfig(
                tools=[search_tool],
                temperature=0.0, # Lower temp forces higher fidelity when pulling facts
            )

            response = await gemini_client.aio.models.generate_content(
                model=model, 
                contents=user_request,
                config=config
            )
            
            resp_text = response.text if response.text else "[No text returned]"
            
            # 3. Extract and Deduplicate URLs from Grounding Metadata
            source_links = {}
            if response.candidates and response.candidates[0].grounding_metadata:
                metadata = response.candidates[0].grounding_metadata
                
                # Iterate through the chunks of data the model retrieved
                if metadata.grounding_chunks:
                    for chunk in metadata.grounding_chunks:
                        # Ensure the chunk contains a web URI
                        if hasattr(chunk, 'web') and chunk.web and chunk.web.uri:
                            # Using a dictionary with URI as the key deduplicates identical URLs
                            source_links[chunk.web.uri] = chunk.web.title or "Source Link"
            
            # 4. Format and append the sources if any were found
            if source_links:
                resp_text += "\n\n**Sources:**\n"
                for uri, title in source_links.items():
                    resp_text += f"- [{title}]({uri})\n"

            return "gmn: " + resp_text
            
        else:
            response = await openai_client.chat.completions.create(
                model=model, 
                messages=[
                    {"role": "user", "content": user_request},
                ]
            )
            return "oai: " + (response.choices[0].message.content or "[No text returned]")
            
    except Exception as e:
        return f"⚠️ API Error: {str(e)}"