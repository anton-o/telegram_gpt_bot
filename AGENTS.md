# Repository guide for coding agents

## Purpose

This repository contains an asynchronous Telegram bot that routes prompts to
OpenAI or Google Gemini. Keep changes small, testable, and compatible with the
allow-list restrictions in `white_lists.py`.

## Architecture

- `main.py` builds the `python-telegram-bot` application and registers handlers.
- `backend_handlers.py` selects providers and models, calls provider SDKs, and
  formats responses for Telegram.
- `white_lists.py` builds Telegram filters from configured administrator and
  group IDs.
- `help.py` and `utils_handlers.py` implement utility commands.
- `tests/` contains isolated unit tests. Tests must never call Telegram, OpenAI,
  Gemini, Google Search, or any other network service.

## Per-user state contract

`state_store.py` persists user preferences by `user_id` and private conversation
references by `(chat_id, user_id)`. Conversation records contain only provider
context IDs and metadata; prompt and response text is not stored locally. Group
requests are stateless and must never read or write conversation records.

Private turns for one user are serialized. A successful turn replaces the saved
provider context ID atomically. Provider errors leave the last successful context
unchanged. Conversation context resets after 30 minutes of inactivity, on
`/reset`, and whenever the active provider or model changes. State survives
process restarts and deployments in `/var/lib/tlggptbot/user-state.json`.

Do not treat user-level settings as conversation-level data. Preserve isolation
by at least `(chat_id, user_id)` and, if group or forum conversations are ever
added, by Telegram message thread. Never allow context to leak between users or
chats. Any state schema change must define migration behavior and include
restart, reset, concurrency, malformed-file, and isolation tests.

## Local setup and validation

Use the repository targets rather than ad-hoc commands:

```bash
make bootstrap
make check
```

- `make bootstrap` installs pinned Python and syncs the locked uv environment.
- `make format` applies Ruff safe fixes and formatting.
- `make lint` and `make format-check` run the non-mutating style gates.
- `make scripts-check` validates deployment shell syntax.
- `make test` runs the unit suite.
- `make coverage` runs the same coverage command used by CI.
- `make check` verifies the lock, formatting, lint, syntax, tests, and coverage.

Use `uv add` or `uv add --dev` for dependency changes and commit both
`pyproject.toml` and `uv.lock`. Do not edit `uv.lock` directly.

Run `make check` before handing off code changes.

## Change guidelines

- Support the Python version configured in CI.
- Keep provider calls asynchronous.
- Add or update tests for behavior changes and error paths.
- Keep provider-specific request/response formats out of Telegram-facing tests.
- Do not expose exception details containing credentials or full provider
  payloads to logs or users.
- Preserve unrelated working-tree changes.

## Secrets and external effects

`bot_secrets.py` and deployment configuration are intentionally ignored. Never
commit real tokens, API keys, administrator IDs, group IDs, server addresses, or
generated persistence databases. Use fake values and mocked clients in tests.
Do not deploy, contact external APIs, push branches, or open pull requests unless
the user explicitly requests it.

## Server deployment

`deploy.sh` is the local orchestrator and `deploy-remote.sh` performs the
staged, root-only server cutover. Routine deployments must come from a clean
commit equal to `origin/main`, must preserve the server-side `bot_secrets.py`,
and must abort on uv or Python pin drift. Keep
`deploy/runtime-versions.conf`, `.python-version`, and the CI uv pin aligned;
the deployment tests enforce this contract. Preserve atomic cutover, health
checks, automatic rollback, and one-release retention when changing deployment
behavior.
