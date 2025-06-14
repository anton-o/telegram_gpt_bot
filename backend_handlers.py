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
from openai import OpenAI

openai_client = OpenAI(
    organization=ORGANIZATION,
    project=PROJECT,
    api_key=OPENAI_KEY
)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

current_oai_model = "gpt-4.1-mini"
current_gemini_model = "gemini-2.5-flash-preview-05-20"
use_gemini = True


async def gpt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    check_fraud(update)
    user_req = update.message.text
    resp = _ask_llm(user_req)
    [await update.message.reply_text(chunk) for chunk in wrap(resp, width=4096, replace_whitespace=False)]


async def bot_mentioned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    check_fraud(update)
    text_offset = update.message.entities[0].offset + update.message.entities[0].length
    user_req = update.message.text[text_offset:] # suppose mention is the first entity, don't care about others

    resp = _ask_llm(user_req)
    await update.message.reply_text(resp)

async def set_current_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global current_oai_model, current_gemini_model

    def _get_gemini_models() -> list:
        models = []
        for model in gemini_client.models.list():
            m_name = model.name.lstrip("models/") if model.name else None
            if m_name and "gemini-" in m_name:
                models.append(m_name)
        return models
    def _get_openai_models() -> list:
        return [i.id for i in openai_client.models.list().data if ('gpt' in i.id) or ('o3' in i.id)]

    new_model = update.message.text.lstrip('/smodel').strip()
    if use_gemini:
        models = _get_gemini_models()
        if new_model in models:
            response = f'Model is changed from {current_gemini_model} to {new_model}'
            current_gemini_model = new_model
        else:
            response = f'Current model is {current_gemini_model}. The suggested model is not in the available gpt-models list: ' + '\n'.join(models)
    else:
        models = _get_openai_models()
        if new_model in models:
            response = f'Model is changed from {current_oai_model} to {new_model}'
            current_oai_model = new_model
        else:
            response = f'Current model is {current_oai_model}. The suggested model is not in the available gpt-models list: ' + '\n'.join(models)

    await update.message.reply_text(response)

async def set_backend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global use_gemini
    backend_requested = update.message.text.lstrip('/sbackend').strip()
    if backend_requested == "gemini":
        response = "Gemini is current backend now"
        use_gemini = True
    elif backend_requested == "openai":
        response = "Openai is current backend now"
        use_gemini = False
    else:
        be = "Gemini" if use_gemini else "Openai"
        response = f"Current backend is {be}, use 'gemini' or 'openai' to change"

    await update.message.reply_text(response)

def _ask_llm(user_request: str) -> str:
    '''Gets gpt response for given query and current context.
        input:  user query
        output: a tuple: current context and gpt response 
    '''
    user_req = user_request
    global use_gemini
    
    if use_gemini:
        response = gemini_client.models.generate_content(
            model=current_gemini_model, 
            contents= user_req,
        )
        txt_resp = "gmn: " + response.text
    else:
        response = openai_client.chat.completions.create(
            model=current_oai_model, 
            messages= [
                {"role": "user", "content": user_req},
            ]
        )
        txt_resp = "oai: " + response.choices[0].message.content

    return txt_resp
