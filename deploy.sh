#!/bin/bash

CONFIG_FILE="./deploy_config.cfg"

# Read the IP address and the SSH key file path from the config file
{
    IFS= read -r REMOTE_IP
    IFS= read -r SSH_KEY_PATH
} < "$CONFIG_FILE"

# Expand the tilde (~) in the path if necessary, if it points to the user's home directory
# If the path in the config file starts with a tilde, this will expand it.
SSH_KEY_PATH=$(eval echo "$SSH_KEY_PATH")

echo "Deploying to IP: $REMOTE_IP using Key: $SSH_KEY_PATH"

# Execute the deployment steps using the variables
rsync -chavzP -e "ssh -i $SSH_KEY_PATH" /Users/anton/Documents/sandbox/telegram_gpt_bot/* root@"$REMOTE_IP":/root/python
ssh -i "$SSH_KEY_PATH" root@"$REMOTE_IP" /root/python/stop.sh
ssh -i "$SSH_KEY_PATH" root@"$REMOTE_IP" /root/python/start.sh