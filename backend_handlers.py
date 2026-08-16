import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from textwrap import wrap
from typing import Any

from google import genai
from openai import AsyncOpenAI
from telegram import Update
from telegram.ext import ContextTypes

from bot_secrets import GEMINI_API_KEY, OPENAI_KEY, ORGANIZATION, PROJECT
from state_store import state_store

# Initialize clients
openai_client = AsyncOpenAI(
    organization=ORGANIZATION, project=PROJECT, api_key=OPENAI_KEY
)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

DEFAULT_OAI_MODEL = "gpt-5.6-terra"
DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview"
CONVERSATION_TIMEOUT = timedelta(minutes=30)
NEW_CONVERSATION_NOTICE = "A new conversation has started."

logger = logging.getLogger(__name__)
_user_locks: dict[int, asyncio.Lock] = {}


@dataclass(frozen=True)
class LLMResult:
    text: str
    context_id: str | None
    succeeded: bool = True


class ContextUnavailableError(RuntimeError):
    """The provider no longer has the requested conversation context."""


def _get_user_lock(user_id: int) -> asyncio.Lock:
    return _user_locks.setdefault(user_id, asyncio.Lock())


def _default_user_settings() -> dict[str, Any]:
    return {
        "use_gemini": False,
        "gemini_model": DEFAULT_GEMINI_MODEL,
        "oai_model": DEFAULT_OAI_MODEL,
    }


async def _get_user_settings(user_id: int) -> dict[str, Any]:
    settings = _default_user_settings()
    settings.update(await state_store.get_user(user_id))
    return settings


def _active_provider_and_model(settings: dict[str, Any]) -> tuple[str, str]:
    if settings["use_gemini"]:
        return "gemini", settings["gemini_model"]
    return "openai", settings["oai_model"]


def _ids_from_update(update: Update) -> tuple[int, int] | None:
    if update.effective_chat is None or update.effective_user is None:
        return None
    return update.effective_chat.id, update.effective_user.id


async def gpt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_req = update.message.text
    if not user_req:
        return

    identifiers = _ids_from_update(update)
    if identifiers is None:
        return
    chat_id, user_id = identifiers
    result, announce_new = await _ask_private_llm(
        user_req,
        chat_id=chat_id,
        user_id=user_id,
    )

    if announce_new:
        await update.message.reply_text(NEW_CONVERSATION_NOTICE)
    for chunk in wrap(result.text, width=4096, replace_whitespace=False):
        # Added Markdown parsing so the extracted URLs render as clickable links
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def bot_mentioned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.entities or not update.message.text:
        return

    text_offset = update.message.entities[0].offset + update.message.entities[0].length
    user_req = update.message.text[text_offset:].strip()
    if not user_req:
        return

    identifiers = _ids_from_update(update)
    if identifiers is None:
        return
    _, user_id = identifiers
    settings = await _get_user_settings(user_id)
    _, model = _active_provider_and_model(settings)
    result = await _async_ask_llm(
        user_req,
        settings["use_gemini"],
        model,
        store_context=False,
    )

    for chunk in wrap(result.text, width=4096, replace_whitespace=False):
        await update.message.reply_text(chunk, parse_mode="Markdown")


def _conversation_is_expired(conversation: dict[str, Any], now: datetime) -> bool:
    timestamp = conversation.get("last_successful_turn_at")
    if not timestamp:
        return conversation.get("remote_context_id") is not None
    try:
        last_turn = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return True
    if last_turn.tzinfo is None:
        return True
    return now - last_turn >= CONVERSATION_TIMEOUT


async def _ask_private_llm(
    user_request: str,
    *,
    chat_id: int,
    user_id: int,
) -> tuple[LLMResult, bool]:
    async with _get_user_lock(user_id):
        settings = await _get_user_settings(user_id)
        provider, model = _active_provider_and_model(settings)
        conversation = await state_store.get_conversation(chat_id, user_id)
        now = datetime.now(timezone.utc)

        context_matches = bool(
            conversation
            and conversation.get("provider") == provider
            and conversation.get("model") == model
        )
        expired = bool(
            context_matches
            and conversation
            and _conversation_is_expired(conversation, now)
        )

        if conversation is not None and (not context_matches or expired):
            await state_store.reset_conversation(
                chat_id,
                user_id,
                provider=provider,
                model=model,
                suppress_next_start_notice=False,
            )
            conversation = await state_store.get_conversation(chat_id, user_id)

        previous_context_id = (
            conversation.get("remote_context_id") if conversation else None
        )
        suppress_notice = bool(
            conversation and conversation.get("suppress_next_start_notice", False)
        )
        announce_new = previous_context_id is None and not suppress_notice

        try:
            result = await _async_ask_llm(
                user_request,
                settings["use_gemini"],
                model,
                previous_context_id=previous_context_id,
                store_context=True,
            )
        except ContextUnavailableError:
            await state_store.reset_conversation(
                chat_id,
                user_id,
                provider=provider,
                model=model,
                suppress_next_start_notice=False,
            )
            result = await _async_ask_llm(
                user_request,
                settings["use_gemini"],
                model,
                store_context=True,
            )
            announce_new = True

        if not result.succeeded or result.context_id is None:
            return result, False

        await state_store.save_conversation(
            chat_id,
            user_id,
            {
                "provider": provider,
                "model": model,
                "remote_context_id": result.context_id,
                "last_successful_turn_at": datetime.now(timezone.utc).isoformat(),
                "suppress_next_start_notice": False,
            },
        )
        return result, announce_new


async def set_current_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    identifiers = _ids_from_update(update)
    if identifiers is None:
        return
    _, user_id = identifiers
    settings = await _get_user_settings(user_id)
    use_gemini = settings["use_gemini"]
    _, current_model = _active_provider_and_model(settings)

    if not context.args:
        await update.message.reply_text("Fetching available models... please wait ⏳")
        try:
            if use_gemini:
                available_models = []
                pager = await gemini_client.aio.models.list()
                async for model_obj in pager:
                    m_name = (
                        model_obj.name.removeprefix("models/")
                        if model_obj.name
                        else None
                    )
                    if m_name and "gemini-" in m_name:
                        available_models.append(m_name)

                model_list_str = "\n".join(available_models)
                await update.message.reply_text(
                    f"Current Gemini model: **{current_model}**\n\n"
                    f"Available Models:\n{model_list_str}\n\n"
                    f"To change, use: `/smodel <model_name>`",
                    parse_mode="Markdown",
                )
            else:
                models_response = await openai_client.models.list()
                available_models = [
                    m.id
                    for m in models_response.data
                    if "gpt" in m.id or "o1" in m.id or "o3" in m.id
                ]

                model_list_str = "\n".join(available_models)
                await update.message.reply_text(
                    f"Current OpenAI model: **{current_model}**\n\n"
                    f"Available Models:\n{model_list_str}\n\n"
                    f"To change, use: `/smodel <model_name>`",
                    parse_mode="Markdown",
                )
        except Exception as exc:
            provider = "Gemini" if use_gemini else "OpenAI"
            logger.warning(
                "%s model listing failed with %s",
                provider,
                type(exc).__name__,
            )
            await update.message.reply_text(
                "⚠️ Failed to fetch the model list. Please try again."
            )
        return

    new_model = context.args[0].strip()
    if new_model == current_model:
        await update.message.reply_text(
            f"Current model is already {current_model}. Conversation preserved."
        )
        return

    async with _get_user_lock(user_id):
        settings = await _get_user_settings(user_id)
        use_gemini = settings["use_gemini"]
        _, current_model = _active_provider_and_model(settings)
        if new_model == current_model:
            await update.message.reply_text(
                f"Current model is already {current_model}. Conversation preserved."
            )
            return
        if use_gemini:
            settings["gemini_model"] = new_model
            provider_name = "Gemini"
        else:
            settings["oai_model"] = new_model
            provider_name = "OpenAI"
        provider, _ = _active_provider_and_model(settings)
        await state_store.save_user_and_reset_conversation(
            user_id,
            settings,
            provider=provider,
            model=new_model,
        )
    await update.message.reply_text(
        f"✅ {provider_name} model changed from {current_model} to {new_model}. "
        "Conversation reset."
    )


async def set_backend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    identifiers = _ids_from_update(update)
    if identifiers is None:
        return
    _, user_id = identifiers
    if not context.args:
        settings = await _get_user_settings(user_id)
        use_gemini = settings["use_gemini"]
        current = "Gemini" if use_gemini else "OpenAI"
        await update.message.reply_text(
            f"Current backend is {current}. Use '/sbackend gemini' or "
            "'/sbackend openai' to change."
        )
        return

    backend_requested = context.args[0].lower()
    if backend_requested not in {"gemini", "openai"}:
        await update.message.reply_text(
            "Invalid backend. Please use 'gemini' or 'openai'."
        )
        return

    requested_use_gemini = backend_requested == "gemini"
    async with _get_user_lock(user_id):
        settings = await _get_user_settings(user_id)
        if settings["use_gemini"] == requested_use_gemini:
            await update.message.reply_text(
                f"{backend_requested.title()} is already your active backend. "
                "Conversation preserved."
            )
            return
        settings["use_gemini"] = requested_use_gemini
        provider, model = _active_provider_and_model(settings)
        await state_store.save_user_and_reset_conversation(
            user_id,
            settings,
            provider=provider,
            model=model,
        )
    await update.message.reply_text(
        f"{backend_requested.title()} is now your active backend. Conversation reset."
    )


async def reset_conversation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    identifiers = _ids_from_update(update)
    if identifiers is None:
        return
    chat_id, user_id = identifiers
    async with _get_user_lock(user_id):
        settings = await _get_user_settings(user_id)
        provider, model = _active_provider_and_model(settings)
        await state_store.reset_conversation(
            chat_id,
            user_id,
            provider=provider,
            model=model,
            suppress_next_start_notice=True,
        )
    await update.message.reply_text(
        "✅ Conversation reset. Your next message will start a new conversation."
    )


def _gemini_sources(interaction: Any) -> dict[str, str]:
    source_links: dict[str, str] = {}
    for step in getattr(interaction, "steps", []) or []:
        if getattr(step, "type", None) != "model_output":
            continue
        for content in getattr(step, "content", []) or []:
            for annotation in getattr(content, "annotations", []) or []:
                if getattr(annotation, "type", None) != "url_citation":
                    continue
                url = getattr(annotation, "url", None)
                if url:
                    source_links[url] = (
                        getattr(annotation, "title", None) or "Source Link"
                    )
    return source_links


async def _async_ask_llm(
    user_request: str,
    use_gemini: bool,
    model: str,
    *,
    previous_context_id: str | None = None,
    store_context: bool,
) -> LLMResult:
    try:
        if use_gemini:
            request: dict[str, Any] = {
                "model": model,
                "input": user_request,
                "generation_config": {"temperature": 0.0},
                "tools": [{"type": "google_search"}],
                "store": store_context,
            }
            if previous_context_id is not None:
                request["previous_interaction_id"] = previous_context_id

            interaction = await gemini_client.aio.interactions.create(**request)
            resp_text = interaction.output_text or "[No text returned]"
            source_links = _gemini_sources(interaction)
            if source_links:
                resp_text += "\n\n**Sources:**\n"
                for uri, title in source_links.items():
                    resp_text += f"- [{title}]({uri})\n"

            return LLMResult(
                text="gmn: " + resp_text,
                context_id=interaction.id if store_context else None,
            )

        request = {
            "model": model,
            "input": user_request,
            "store": store_context,
        }
        if previous_context_id is not None:
            request["previous_response_id"] = previous_context_id
        response = await openai_client.responses.create(**request)
        return LLMResult(
            text="oai: " + (response.output_text or "[No text returned]"),
            context_id=response.id if store_context else None,
        )

    except Exception as exc:
        if previous_context_id is not None and getattr(exc, "status_code", None) == 404:
            raise ContextUnavailableError from exc
        provider = "Gemini" if use_gemini else "OpenAI"
        logger.warning(
            "%s request failed with %s",
            provider,
            type(exc).__name__,
        )
        return LLMResult(
            text="⚠️ API request failed. Please try again.",
            context_id=None,
            succeeded=False,
        )
