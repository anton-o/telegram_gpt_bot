#! /bin/bash

remote_ip=$(<deploy_ip.cfg)
rsync -chavzP /Users/anton/Documents/sandbox/telegram_gpt_bot/* root@"$remote_ip":/root/python
ssh root@"$remote_ip" /root/python/stop.sh
ssh root@"$remote_ip" /root/python/start.sh

