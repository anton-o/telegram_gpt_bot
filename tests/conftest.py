import sys
from types import ModuleType

fake_secrets = ModuleType("bot_secrets")
fake_secrets.BOT_TOKEN = "test-telegram-token"
fake_secrets.BOT_NAME = "test_bot"
fake_secrets.OPENAI_KEY = "test-openai-key"
fake_secrets.ORGANIZATION = None
fake_secrets.PROJECT = None
fake_secrets.GEMINI_API_KEY = "test-gemini-key"
fake_secrets._admins = [1001]
fake_secrets._groups = [-2001]

sys.modules["bot_secrets"] = fake_secrets
