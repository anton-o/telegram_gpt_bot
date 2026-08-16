import json
import stat

import pytest

import state_store as state_store_module
from state_store import FileStateStore, StateStoreError


async def test_state_survives_new_store_instance_and_copies_values(tmp_path):
    path = tmp_path / "state" / "user-state.json"
    store = FileStateStore(path)
    user = {"use_gemini": False, "oai_model": "gpt-test"}
    conversation = {
        "provider": "openai",
        "model": "gpt-test",
        "remote_context_id": "response-1",
    }

    await store.save_user(1001, user)
    await store.save_conversation(1001, 1001, conversation)
    user["oai_model"] = "mutated"
    conversation["remote_context_id"] = "mutated"

    restarted = FileStateStore(path)
    assert await restarted.get_user(1001) == {
        "use_gemini": False,
        "oai_model": "gpt-test",
    }
    assert await restarted.get_conversation(1001, 1001) == {
        "provider": "openai",
        "model": "gpt-test",
        "remote_context_id": "response-1",
    }
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_conversations_are_isolated_by_chat_and_user(tmp_path):
    store = FileStateStore(tmp_path / "user-state.json")
    first = {"remote_context_id": "first"}
    second = {"remote_context_id": "second"}

    await store.save_conversation(1001, 1001, first)
    await store.save_conversation(2002, 1001, second)

    assert await store.get_conversation(1001, 1001) == first
    assert await store.get_conversation(2002, 1001) == second
    assert await store.get_conversation(1001, 2002) is None


async def test_user_change_and_private_reset_are_one_atomic_write(
    tmp_path, monkeypatch
):
    store = FileStateStore(tmp_path / "user-state.json")
    write = store._write
    writes = 0

    def count_write(state):
        nonlocal writes
        writes += 1
        write(state)

    monkeypatch.setattr(store, "_write", count_write)
    settings = {
        "use_gemini": False,
        "gemini_model": "gemini-test",
        "oai_model": "gpt-test",
    }

    await store.save_user_and_reset_conversation(
        1001,
        settings,
        provider="openai",
        model="gpt-test",
    )

    assert writes == 1
    assert await store.get_user(1001) == settings
    assert await store.get_conversation(1001, 1001) == {
        "provider": "openai",
        "model": "gpt-test",
        "remote_context_id": None,
        "last_successful_turn_at": None,
        "suppress_next_start_notice": True,
    }


async def test_reset_records_notice_policy(tmp_path):
    store = FileStateStore(tmp_path / "user-state.json")

    await store.reset_conversation(
        1001,
        1001,
        provider="gemini",
        model="gemini-test",
        suppress_next_start_notice=False,
    )

    conversation = await store.get_conversation(1001, 1001)
    assert conversation["remote_context_id"] is None
    assert conversation["last_successful_turn_at"] is None
    assert conversation["suppress_next_start_notice"] is False


def test_initialize_uses_configured_environment_path(tmp_path, monkeypatch):
    path = tmp_path / "configured" / "user-state.json"
    monkeypatch.setenv("TLGGPTBOT_STATE_PATH", str(path))

    store = FileStateStore()
    store.initialize()

    assert store.path == path
    assert path.parent.is_dir()
    assert not path.exists()


def test_initialize_restricts_existing_state_file_permissions(tmp_path):
    path = tmp_path / "user-state.json"
    path.write_text(json.dumps(FileStateStore._empty_state()))
    path.chmod(0o644)

    FileStateStore(path).initialize()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ("[]", "state root must be an object"),
        (
            '{"schema_version": 99, "users": {}, "conversations": {}}',
            "unsupported state schema version",
        ),
        (
            '{"schema_version": 1, "users": [], "conversations": {}}',
            "invalid state collections",
        ),
        (
            '{"schema_version": 1, "users": {"1001": []}, "conversations": {}}',
            "invalid user records",
        ),
        (
            '{"schema_version": 1, "users": {}, "conversations": {"1001:1001": []}}',
            "invalid conversation records",
        ),
        ("not json", "failed to read state file"),
    ],
)
def test_initialize_rejects_invalid_state(tmp_path, payload, error):
    path = tmp_path / "user-state.json"
    path.write_text(payload)

    with pytest.raises(StateStoreError, match=error):
        FileStateStore(path).initialize()


def test_initialize_rejects_symlinked_state_file(tmp_path):
    target = tmp_path / "target.json"
    target.write_text(json.dumps(FileStateStore._empty_state()))
    path = tmp_path / "user-state.json"
    path.symlink_to(target)

    with pytest.raises(StateStoreError, match="invalid state file"):
        FileStateStore(path).initialize()


def test_initialize_rejects_broken_symlinked_state_file(tmp_path):
    path = tmp_path / "user-state.json"
    path.symlink_to(tmp_path / "missing.json")

    with pytest.raises(StateStoreError, match="invalid state file"):
        FileStateStore(path).initialize()


def test_initialize_rejects_non_directory_parent(tmp_path):
    parent = tmp_path / "not-a-directory"
    parent.write_text("content")

    with pytest.raises(StateStoreError, match="invalid state directory"):
        FileStateStore(parent / "user-state.json").initialize()


async def test_failed_replace_is_reported_and_temporary_file_is_removed(
    tmp_path, monkeypatch
):
    path = tmp_path / "user-state.json"
    store = FileStateStore(path)

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(state_store_module.os, "replace", fail_replace)

    with pytest.raises(StateStoreError, match="failed to write state file"):
        await store.save_user(1001, {"use_gemini": True})

    assert not list(tmp_path.glob("*.tmp"))
    assert not path.exists()


def test_conversation_key_is_stable():
    assert FileStateStore.conversation_key(-1001, 2002) == "-1001:2002"
