"""Формування та надсилання приватних сповіщень адміну про повідомлення, що потребують уваги."""
from __future__ import annotations

import logging

from telegram import Bot
from telegram.error import TelegramError

from bot.keyboards import reply_suggestions_keyboard
from db.database import Database
from services.claude_service import ClaudeService

logger = logging.getLogger(__name__)

SENTIMENT_LABELS = {
    "positive": "😊 позитивне",
    "neutral": "😐 нейтральне",
    "frustrated": "😞 фрустроване",
    "question": "❓ питання",
    "urgent": "🚨 ТЕРМІНОВЕ",
}


def _build_message_link(chat_id: int, message_id: int) -> str | None:
    chat_id_str = str(chat_id)
    if chat_id_str.startswith("-100"):
        return f"https://t.me/c/{chat_id_str[4:]}/{message_id}"
    return None


def _display_name(username: str | None, full_name: str | None) -> str:
    if username:
        return f"@{username}"
    return full_name or "учасник"


async def notify_admin_about_message(
    bot: Bot,
    db: Database,
    claude: ClaudeService,
    admin_user_id: int,
    pending_suggestions: dict,
    *,
    message_db_id: int,
    user_id: int,
    username: str | None,
    full_name: str | None,
    chat_id: int,
    message_id: int,
    text: str,
    sentiment: str,
    urgent: bool,
) -> None:
    """Надсилає адміну приватне сповіщення з текстом повідомлення і 2 варіантами відповіді."""
    variants = await claude.generate_reply_suggestions(text) if text else []

    label = SENTIMENT_LABELS.get(sentiment, sentiment)
    header = "🚨 ТЕРМІНОВО: потребує негайної уваги!" if urgent else "Нове повідомлення потребує відповіді"
    link = _build_message_link(chat_id, message_id)
    parts = [
        header,
        f"Від: {_display_name(username, full_name)}",
        f"Тональність: {label}",
        "",
        f"«{text}»",
    ]
    if link:
        parts.append("")
        parts.append(link)
    if variants:
        parts.append("")
        parts.append("Запропоновані відповіді:")
        for i, v in enumerate(variants, start=1):
            parts.append(f"{i}. {v}")

    text_out = "\n".join(parts)
    keyboard = reply_suggestions_keyboard(message_db_id, len(variants)) if variants else None

    try:
        await bot.send_message(chat_id=admin_user_id, text=text_out, reply_markup=keyboard)
    except TelegramError as exc:
        logger.error("Не вдалося надіслати сповіщення адміну: %s", exc)
        return

    if variants:
        pending_suggestions[message_db_id] = {
            "chat_id": chat_id,
            "reply_to_message_id": message_id,
            "variants": variants,
        }

    if urgent:
        await db.mark_urgent_notified(message_db_id)
    else:
        await db.mark_reminder_sent(message_db_id)
