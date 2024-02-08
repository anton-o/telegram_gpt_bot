from textwrap import wrap
from telegram import ForceReply, Update
from telegram.ext import ContextTypes
from bot_secrets import OPENAI_KEY

from gpt_ctx_mgmt import get_effective_ctx
from antifraud import check_fraud
from dialog_dump import dump_dialog_turn

import openai
openai.api_key = OPENAI_KEY
current_model = 'gpt-4-1106-preview'

async def gpt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    check_fraud(update)
    user_req = update.message.text
    _, resp = _ask_openai(update.effective_user.id, user_req)
    # dump_dialog_turn(system_role, user_req, resp)
    [await update.message.reply_text(chunk) for chunk in wrap(resp, width=4096, replace_whitespace=False)]


async def bot_mentioned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    check_fraud(update)
    text_offset = update.message.entities[0].offset + update.message.entities[0].length
    user_req = update.message.text[text_offset:] # suppose mention is the first entity, don't care about others

    system_role, resp = _ask_openai(update.effective_user.id, user_req)
    dump_dialog_turn(system_role, user_req, resp)
    await update.message.reply_text(resp)


async def set_current_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global current_model
    new_model = update.message.text
    models = [i['id'] for i in openai.Model.list()['data'] if 'gpt' in i['id']]
    if new_model in models:
        response = f'Model is changed from {new_model} to {new_model}'
        current_model = new_model
    else:
        response = f'Current model is {current_model}. The suggested model is not in the available gpt-models list: ' + ', '.join(models)

    await update.message.reply_text(response)

def _ask_openai(chat_id: int, user_request: str) -> str:
    '''Gets gpt response for given query and current context.
        input:  user query
        output: a tuple: current context and gpt response 
    '''
    system_role = get_effective_ctx(chat_id)
    user_req = user_request
    response = openai.ChatCompletion.create(
        model=current_model, 
        messages= [
            {"role": "system", "content": system_role}, 
            {"role": "user", "content": user_req},
        ]
    )
    return system_role, response["choices"][0]["message"]["content"]
