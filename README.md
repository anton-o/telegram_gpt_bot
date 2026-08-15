# Quality disclaimer
This is a weekend home project, provided as is with no any guaranty or support.
Code based on the echobot example 
https://docs.python-telegram-bot.org/en/latest/examples.echobot.html

# Capabilities
  1. communicates with Google Gemini and OpenAI models
  2. can be used in Telegram groups by mentioning the bot
  3. supports private conversations for configured administrators
  4. allows administrators to select the active backend and model

# usage scenario
## Example
  1. /help
  2. @yourbotname what is the expected time for a black hole to disappear
  3. Wait for the response
## What's going on above
  1. get list of commands
  2. send a request to the configured AI backend by mentioning the bot
  3. receive the response, split into multiple Telegram messages when necessary


# Environment 
  python > 3.8
  
  openai
  python-telegram-bot
  google-genai

# setup script as a service
  nano /lib/systemd/system/tlggptbot.service
  put the following into the file
  ```
  [Unit]
  Description=Telegram GPT bot
  After=network.target

  [Service]
  Type=idle
  Restart=on-failure
  User=root
  ExecStart=/bin/bash -c 'source /root/ve_tlg/bin/activate && nohup python /root/python/main.py'

  [Install]
WantedBy=multi-user.target
  ```
  sudo chmod 644 /lib/systemd/system/tlggptbot.service
  sudo systemctl daemon-reload
  sudo systemctl enable tlggptbot.service

# Deploy and run
  1. ensure you have ssh configured to access your server
  2. change /lib/systemd/system/tlggptbot.service according to your virtual env and location for the bot source code
  3. create deploy_ip.cfg with an IP address of the server
  4. create secret.py based on the example
  5. run ./deploy.sh
