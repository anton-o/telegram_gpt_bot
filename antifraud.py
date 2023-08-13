from datetime import datetime
from telegram import Update

from white_lists import is_whitelisted

last_message_time = datetime.now()
low_delay_msg_cnt = 0
LOW_DELAY_SEC_TH = 10
LOW_DELAY_MSG_CNT_THRESHOLD = 3
ALERT_FILENAME = 'ALERT.txt'

def _append_alert(alert_txt: str) -> None:
    with open(ALERT_FILENAME, '+a') as alert_file:
        alert_file.write(alert_txt)

def check_fraud(update: Update) -> None:
    '''
        Checks for too frequent activity of unregistered (not whitelisted) users.
    '''
    if is_whitelisted(update.message.chat_id):
        pass
    else:
        global last_message_time, low_delay_msg_cnt
        cur_time = datetime.now()
        if (cur_time - last_message_time).seconds < LOW_DELAY_SEC_TH:
            low_delay_msg_cnt += 1
        else:
            low_delay_msg_cnt = 0
        if low_delay_msg_cnt > LOW_DELAY_MSG_CNT_THRESHOLD:
            _append_alert(f'Trigger time: {cur_time.isoformat()}\n{update.to_json}\n')
            low_delay_msg_cnt = 0
        last_message_time = cur_time
