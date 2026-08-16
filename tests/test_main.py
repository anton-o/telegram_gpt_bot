from unittest.mock import MagicMock

from telegram.ext import CommandHandler, MessageHandler, PrefixHandler

import main


def test_main_registers_handlers_and_starts_polling(monkeypatch):
    application = MagicMock()
    builder = MagicMock()
    builder.token.return_value.build.return_value = application
    monkeypatch.setattr(main.Application, "builder", MagicMock(return_value=builder))
    initialize = MagicMock()
    monkeypatch.setattr(main.state_store, "initialize", initialize)

    main.main()

    initialize.assert_called_once_with()
    builder.token.assert_called_once_with("test-telegram-token")
    handlers = [call.args[0] for call in application.add_handler.call_args_list]
    assert len(handlers) == 9
    assert sum(isinstance(handler, CommandHandler) for handler in handlers) == 7
    assert sum(isinstance(handler, PrefixHandler) for handler in handlers) == 1
    assert sum(isinstance(handler, MessageHandler) for handler in handlers) == 1
    application.run_polling.assert_called_once()
