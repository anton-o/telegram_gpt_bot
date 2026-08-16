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

`context.user_data` currently contains only in-memory user preferences:
`use_gemini`, `gemini_model`, and `oai_model`. It does not preserve conversation
history and it does not survive a process restart.

Do not treat user-level data as conversation-level data. New conversation state
must be isolated by at least `(chat_id, user_id)` and, where applicable, by
Telegram message thread. Never allow context to leak between users or chats.
Any persistent conversation feature must define retention, reset, concurrency,
and migration behavior and include isolation tests.

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
`pyproject.toml` and `uv.lock`. Do not edit `uv.lock` directly. The requirements
files are retained only for legacy server rollback and are not the development
source of truth.

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

## Server migration

`deploy-migrate.sh` is the local orchestrator and `migrate.sh` is its remote,
root-only worker. The migration is deliberately staged outside `/root/python`
and must preserve `/root/ve_tlg`, the old systemd unit, and server-side
`bot_secrets.py` for rollback. Do not broaden the accepted application or
service paths without an explicit migration design change. Keep
`deploy/runtime-versions.conf`, `.python-version`, and the CI uv pin aligned;
the deployment tests enforce this contract.
