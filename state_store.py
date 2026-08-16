import asyncio
import json
import os
import stat
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_STATE_PATH = Path("/var/lib/tlggptbot/user-state.json")


class StateStoreError(RuntimeError):
    """Raised when the persistent state cannot be read or safely written."""


class FileStateStore:
    def __init__(self, path: Path | str | None = None) -> None:
        configured_path = path or os.environ.get(
            "TLGGPTBOT_STATE_PATH", str(DEFAULT_STATE_PATH)
        )
        self.path = Path(configured_path)
        self._lock = asyncio.Lock()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "users": {},
            "conversations": {},
        }

    @staticmethod
    def conversation_key(chat_id: int, user_id: int) -> str:
        return f"{chat_id}:{user_id}"

    def initialize(self) -> None:
        self._ensure_parent()
        self._read()

    async def get_user(self, user_id: int) -> dict[str, Any]:
        async with self._lock:
            state = self._read()
            return deepcopy(state["users"].get(str(user_id), {}))

    async def get_conversation(
        self, chat_id: int, user_id: int
    ) -> dict[str, Any] | None:
        async with self._lock:
            state = self._read()
            conversation = state["conversations"].get(
                self.conversation_key(chat_id, user_id)
            )
            return deepcopy(conversation) if conversation is not None else None

    async def save_user(self, user_id: int, user: dict[str, Any]) -> None:
        async with self._lock:
            state = self._read()
            state["users"][str(user_id)] = deepcopy(user)
            self._write(state)

    async def save_user_and_reset_conversation(
        self,
        user_id: int,
        user: dict[str, Any],
        *,
        provider: str,
        model: str,
    ) -> None:
        async with self._lock:
            state = self._read()
            state["users"][str(user_id)] = deepcopy(user)
            state["conversations"][self.conversation_key(user_id, user_id)] = {
                "provider": provider,
                "model": model,
                "remote_context_id": None,
                "last_successful_turn_at": None,
                "suppress_next_start_notice": True,
            }
            self._write(state)

    async def reset_conversation(
        self,
        chat_id: int,
        user_id: int,
        *,
        provider: str,
        model: str,
        suppress_next_start_notice: bool,
    ) -> None:
        async with self._lock:
            state = self._read()
            state["conversations"][self.conversation_key(chat_id, user_id)] = {
                "provider": provider,
                "model": model,
                "remote_context_id": None,
                "last_successful_turn_at": None,
                "suppress_next_start_notice": suppress_next_start_notice,
            }
            self._write(state)

    async def save_conversation(
        self,
        chat_id: int,
        user_id: int,
        conversation: dict[str, Any],
    ) -> None:
        async with self._lock:
            state = self._read()
            state["conversations"][self.conversation_key(chat_id, user_id)] = deepcopy(
                conversation
            )
            self._write(state)

    def _ensure_parent(self) -> None:
        parent = self.path.parent
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise StateStoreError(f"invalid state directory: {parent}")
        try:
            if not parent.exists():
                parent.mkdir(parents=True, mode=0o700)
                os.chmod(parent, 0o700)
        except OSError as exc:
            raise StateStoreError(
                f"failed to prepare state directory: {parent}"
            ) from exc

    def _read(self) -> dict[str, Any]:
        self._ensure_parent()
        if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()):
            raise StateStoreError(f"invalid state file: {self.path}")
        if not self.path.exists():
            return self._empty_state()

        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
            with self.path.open(encoding="utf-8") as state_file:
                state = json.load(state_file)
        except (OSError, json.JSONDecodeError) as exc:
            raise StateStoreError(f"failed to read state file: {self.path}") from exc

        if not isinstance(state, dict):
            raise StateStoreError("state root must be an object")
        if state.get("schema_version") != SCHEMA_VERSION:
            raise StateStoreError("unsupported state schema version")
        if not isinstance(state.get("users"), dict) or not isinstance(
            state.get("conversations"), dict
        ):
            raise StateStoreError("invalid state collections")
        if not all(
            isinstance(key, str) and isinstance(value, dict)
            for key, value in state["users"].items()
        ):
            raise StateStoreError("invalid user records")
        if not all(
            isinstance(key, str) and isinstance(value, dict)
            for key, value in state["conversations"].items()
        ):
            raise StateStoreError("invalid conversation records")
        return state

    def _write(self, state: dict[str, Any]) -> None:
        self._ensure_parent()
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as state_file:
                json.dump(state, state_file, indent=2, sort_keys=True)
                state_file.write("\n")
                state_file.flush()
                os.fsync(state_file.fileno())
            os.chmod(temporary_path, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temporary_path, self.path)
            directory_descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as exc:
            raise StateStoreError(f"failed to write state file: {self.path}") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


state_store = FileStateStore()
