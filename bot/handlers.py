"""Обробники повідомлень у групі марафону та callback-кнопок адміна."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.notifier import notify_admin_about_message
from config import Settings
from db.database import POINTS_MESSAGE, POINTS_THREAD_COMMENT, Database
from services.claude_service import ClaudeService

logger = logging.getLogger(__name__)


def _ctx(context: ContextTypes.DEFAULT_TYPE):
    bot_data = context.application.bot_data
    return bot_data["db"], bot_data["claude"], bot_data["settings"], bot_data["pending_suggestions"]


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.from_user is None or message.from_user.is_bot:
        return

    db, claude, settings, _ = _ctx(context)
    user = message.from_user
    chat_id = message.chat_id

    await db.upsert_participant(user.id, user.username, user.full_name)

    # Повідомлення самої організаторки в моніторингованому чаті — не оцінюємо й не аналізуємо,
    # лише використовуємо як сигнал "відповіла учаснику", якщо це reply.
    if user.id == settings.admin_user_id:
        if message.reply_to_message:
            await db.mark_responded_by_reply(chat_id, message.reply_to_message.message_id)
        return

    text = message.text or message.caption or ""

    is_thread_comment = message.message_thread_id is not None
    await db.award_points(
        user.id,
        POINTS_THREAD_COMMENT if is_thread_comment else POINTS_MESSAGE,
        "thread_comment" if is_thread_comment else "message",
    )

    analysis = await claude.analyze_message(text) if text.strip() else None
    sentiment = analysis.sentiment if analysis else "neutral"
    addressed = analysis.addressed_to_admin if analysis else False

    message_db_id = await db.log_message(
        user_id=user.id,
        chat_id=chat_id,
        message_id=message.message_id,
        message_thread_id=message.message_thread_id,
        text=text,
        sentiment=sentiment,
        addressed_to_admin=addressed,
    )

    day = ClaudeService.detect_task_day(text, settings.task_hashtag_days)
    if day is not None:
        await db.award_task_completion(user.id, day, message_db_id)

    if sentiment == "urgent":
        pending_suggestions = context.application.bot_data["pending_suggestions"]
        await notify_admin_about_message(
            context.bot,
            db,
            claude,
            settings.admin_user_id,
            pending_suggestions,
            message_db_id=message_db_id,
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            chat_id=chat_id,
            message_id=message.message_id,
            text=text,
            sentiment=sentiment,
            urgent=True,
        )


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    db, _, settings, pending_suggestions = _ctx(context)

    if query.from_user.id != settings.admin_user_id:
        await query.answer("Ця дія доступна лише організаторці марафону.", show_alert=True)
        return

    action, message_db_id_str, *rest = query.data.split(":")
    message_db_id = int(message_db_id_str)
    pending = pending_suggestions.get(message_db_id)

    if action == "dismiss_reply":
        pending_suggestions.pop(message_db_id, None)
        await query.answer("Гаразд, відповісте самі.")
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if action == "send_reply":
        if pending is None:
            await query.answer("Варіанти відповіді більше недоступні.", show_alert=True)
            return
        variant_index = int(rest[0])
        variant_text = pending["variants"][variant_index]
        try:
            await context.bot.send_message(
                chat_id=pending["chat_id"],
                text=variant_text,
                reply_to_message_id=pending["reply_to_message_id"],
            )
        except TelegramError as exc:
            logger.error("Не вдалося надіслати відповідь у групу: %s", exc)
            await query.answer("Помилка надсилання. Спробуйте ще раз.", show_alert=True)
            return
        await db.mark_responded(message_db_id)
        pending_suggestions.pop(message_db_id, None)
        await query.answer("Надіслано!")
        await query.edit_message_reply_markup(reply_markup=None)
