# Quality disclaimer
This is a weekend home project, provided as is with no any guaranty or support

# Capabilities
  1. communicates with the openAI gpt-models
  2. can be used in telegram groups with many members
  3. supports  "system role" for every member of a group
  4. saves history of messages being sent with @botname

# usage scenario
## Example
  1. /help
  2. /sctx you're a theoretical physicist Leonard Susskind
  3. @yourbotname what is the expected time for the black hole to disappear
  .... some time
  4. /gctx
  5. /gctxhist
## What's going on above
  1. get list of commands
  2. set "system role", which will be given to gpt as a context with your question and a request to think step-by-step
  3. your request
  4. get current system context, just in case you've forgotten it
  5. list of historical contexts, just in case you'd like to reuse


# Environment 
  python > 3.8
  
  openai
  python-telegram-bot

# Deploy and run
  1. ensure you have ssh configured to access your server
  2. create deploy_ip.cfg with an IP address of the server
  3. create secret.py based on the example
  4. run ./deploy.sh