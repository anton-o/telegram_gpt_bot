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
  Python 3.11.15

  uv 0.11.7

# Development and tests
Install uv 0.11.7, then run:

Install the pinned Python version and synchronize the locked development
environment:
  ```bash
  make bootstrap
  ```

Run the unit tests:
  ```bash
  make check
  ```

`make check` verifies the uv lockfile, Ruff formatting and lint, Python syntax,
and the same coverage-enabled test suite used by CI. Environment activation is
optional; use `uv run python main.py` to run the bot.

Use `uv add <package>` for runtime dependencies and `uv add --dev <package>` for
development dependencies. Commit both `pyproject.toml` and `uv.lock`.

The unit tests use fake credentials and mocked API clients. They do not call
Telegram, OpenAI, Gemini, or Google Search, and do not require repository
secrets. GitHub Actions runs the suite for pull requests and pushes to `main`.
It can also be started manually for another branch from the Actions tab.

# Deployment

Create `deploy_config.cfg` from `deploy_config.cfg.EXAMPLE`. Deployments target
`/root/python` and `tlggptbot.service`, and never upload the local
`bot_secrets.py`; the existing server secret is copied into each staged
release.

Deployments are accepted only from a clean local checkout whose `HEAD` exactly
matches `origin/main`.
Start with the read-only validation:

```bash
git switch main
git pull --ff-only
./deploy.sh --preflight
```

Deploy after preflight succeeds:

```bash
./deploy.sh
```

The deployment uploads a checksummed runtime-only bundle, copies the existing
server secret into a staged `/root/python.new.<deployment-id>`, synchronizes the
locked production dependencies, and validates imports before stopping the
service. It then performs a short systemd cutover and automatically restores
the previous application and unit if health checks fail. Only the immediately
previous successful release is retained under
`/root/tlggptbot-backups/releases`.

Routine deployments never upgrade uv or Python. A mismatch with
`deploy/runtime-versions.conf` aborts and requires a separately reviewed runtime
upgrade.
