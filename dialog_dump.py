import json
from typing import OrderedDict

DATA_FILE = 'dialogs.txt'

def dump_dialog_turn(ctx: str, request: str, response: str) -> None:
    with open(DATA_FILE, '+a', encoding='utf8') as f:
        data_entry = OrderedDict(
            {
                'context':ctx, 
                'user_req': request, 
                'ai_resp': response
            }
        )
        json.dump(data_entry, f, ensure_ascii=False)