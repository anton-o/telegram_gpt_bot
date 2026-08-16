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
development dependencies. Commit both `pyproject.toml` and `uv.lock`. The
requirements files remain temporarily for rollback of the legacy server setup.

The unit tests use fake credentials and mocked API clients. They do not call
Telegram, OpenAI, Gemini, or Google Search, and do not require repository
secrets. GitHub Actions runs the suite for pull requests and pushes to `main`.
It can also be started manually for another branch from the Actions tab.

# Legacy service setup (before migration)
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

The existing `deploy.sh` and `/root/ve_tlg` environment are the legacy rollback
path. Do not remove them during the initial uv migration.

Create `deploy_config.cfg` from the tracked example. It must use the named
configuration format and continue targeting `/root/python` and
`tlggptbot.service`. The migration never uploads the local `bot_secrets.py`; it
copies the existing server file into the staged application.

First run the read-only checks:

```bash
./deploy-migrate.sh --preflight
```

The preflight verifies local tests and lock state, SSH access, the active legacy
service, `/root/ve_tlg`, the server secret, required tools, and free disk space.
It does not upload files or stop the service.

Run the migration only after preflight passes:

```bash
./deploy-migrate.sh
```

The migration downloads the pinned uv release and checksum on the server,
installs the pinned Python version, creates a locked production `.venv` in a
staged application, and performs a short systemd cutover. If the new service
does not remain healthy, it automatically restores the previous application
and service unit. Successful migrations retain the old application under
`/root/tlggptbot-backups/<migration-id>` and leave `/root/ve_tlg` untouched.

The new service executes `/root/python/.venv/bin/python` directly. Dependency
synchronization happens during deployment, never during a service restart.

## Routine deployments

Routine deployments use the same named `deploy_config.cfg`. They are accepted
only from a clean local checkout whose `HEAD` exactly matches `origin/main`.
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

## Post-migration cleanup

Migration rollback assets become eligible for cleanup only after seven healthy
days and one successful routine deployment. Audit the exact targets first:

```bash
./deploy-cleanup.sh --preflight
```

After reviewing that output, explicitly execute cleanup:

```bash
./deploy-cleanup.sh --execute
```

Cleanup removes `/root/ve_tlg`, the initial migration backup, an unchanged
shadowed legacy unit, and validated migration staging remnants. It does not
stop the active service and retains the latest routine rollback. The migration
scripts and legacy requirements remain in the repository until this server
cleanup has completed successfully; remove them in a final cleanup commit.
