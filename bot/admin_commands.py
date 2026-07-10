"""Команди керування марафоном: /start, /leaderboard, /bonus, /schedule.

Усі команди доступні лише організаторці марафону (перевірка по ADMIN_USER_ID).
"""
from __future__ import annotations

import datetime as dt
import functools
import logging
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

START_TEXT = """\
Привіт! Я бот-асистент твого марафону. Ось що я вмію:

/leaderboard — рейтинг активності учасників
/bonus — нарахувати бонусні бали вручну
/schedule — поставити пост у чергу публікації (або переглянути чергу)
/cancel — скасувати запланований пост із черги

Я також сам стежу за чатом, шлю нагадування про невідповідені повідомлення,
попереджаю про термінові ситуації і щодня надсилаю дайджест настроїв.
"""


def admin_only(func):
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        settings = context.application.bot_data["settings"]
        if update.effective_user is None or update.effective_user.id != settings.admin_user_id:
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(START_TEXT)


TELEGRAM_MESSAGE_LIMIT = 4000  # трохи нижче реального ліміту Telegram (4096) про всяк випадок


@admin_only
async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = context.application.bot_data["db"]
    entries = await db.get_leaderboard()
    if not entries:
        await update.message.reply_text("Поки що немає жодної активності учасників.")
        return

    lines = ["🏆 Рейтинг марафону:\n"]
    for i, e in enumerate(entries, start=1):
        name = f"@{e.username}" if e.username else (e.full_name or f"id{e.user_id}")
        lines.append(
            f"{i}. {name} — {e.total_points} балів "
            f"(повідомлення: {e.messages}, коментарі: {e.thread_comments}, "
            f"завдання: {e.tasks_completed}, бонус: {e.bonus})"
        )

    for chunk in _chunk_lines(lines, TELEGRAM_MESSAGE_LIMIT):
        await update.message.reply_text(chunk)


def _chunk_lines(lines: list[str], limit: int) -> list[str]:
    """Розбиває список рядків на повідомлення, кожне з яких не перевищує ліміт Telegram."""
    chunks = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1  # +1 за символ нового рядка
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


@admin_only
async def cmd_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Використання: /bonus <user_id> <бали> [причина] або reply на повідомлення учасника: /bonus <бали> [причина]."""
    db = context.application.bot_data["db"]
    args = context.args or []
    reply = update.message.reply_to_message

    try:
        if reply and reply.from_user:
            target_user_id = reply.from_user.id
            amount = int(args[0])
            reason_words = args[1:]
        else:
            target_user_id = int(args[0])
            amount = int(args[1])
            reason_words = args[2:]
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Формат: /bonus <бали> [причина] у відповідь на повідомлення учасника,\n"
            "або /bonus <user_id> <бали> [причина]."
        )
        return

    reason = "manual_bonus" + (f": {' '.join(reason_words)}" if reason_words else "")
    await db.award_points(target_user_id, amount, "manual_bonus", None)
    await update.message.reply_text(f"Нараховано {amount} бонусних балів учаснику {target_user_id}. ({reason})")


@admin_only
async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Використання: /schedule YYYY-MM-DD HH:MM [текст] (у reply на текст/фото — текст можна не вказувати).

    Без аргументів — показує чергу запланованих постів.
    """
    db = context.application.bot_data["db"]
    settings = context.application.bot_data["settings"]
    args = context.args or []

    if not args:
        pending = await db.get_pending_posts()
        if not pending:
            await update.message.reply_text("Черга публікацій порожня.")
            return
        lines = ["📅 Заплановані публікації:\n"]
        for p in pending:
            preview = (p["text"] or "(фото без підпису)")[:60]
            lines.append(f"#{p['id']} — {p['publish_at']} — {preview}")
        await update.message.reply_text("\n".join(lines))
        return

    if len(args) < 2:
        await update.message.reply_text("Формат: /schedule YYYY-MM-DD HH:MM [текст]")
        return

    date_str, time_str = args[0], args[1]
    try:
        naive = dt.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        publish_at = naive.replace(tzinfo=ZoneInfo(settings.timezone))
    except ValueError:
        await update.message.reply_text("Не вдалося розпізнати дату/час. Формат: YYYY-MM-DD HH:MM")
        return

    if publish_at <= dt.datetime.now(ZoneInfo(settings.timezone)):
        await update.message.reply_text("Цей час уже минув. Вкажіть час у майбутньому.")
        return

    reply = update.message.reply_to_message
    text = " ".join(args[2:]) if len(args) > 2 else None
    photo_file_id = None

    if reply:
        text = text or reply.text or reply.caption
        if reply.photo:
            photo_file_id = reply.photo[-1].file_id

    if not text and not photo_file_id:
        await update.message.reply_text(
            "Немає що публікувати: вкажіть текст після дати/часу або зробіть /schedule у відповідь на пост."
        )
        return

    post_id = await db.add_scheduled_post(settings.channel_chat_id, text, photo_file_id, publish_at)
    await update.message.reply_text(f"Пост #{post_id} заплановано на {publish_at.strftime('%Y-%m-%d %H:%M %Z')}.")


@admin_only
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Використання: /cancel <номер поста> (номер видно у списку /schedule без аргументів)."""
    db = context.application.bot_data["db"]
    args = context.args or []

    if not args or not args[0].isdigit():
        await update.message.reply_text("Формат: /cancel <номер поста> — номер бачиш у списку команди /schedule.")
        return

    post_id = int(args[0])
    cancelled = await db.cancel_scheduled_post(post_id)
    if cancelled:
        await update.message.reply_text(f"Пост #{post_id} скасовано і не буде опублікований.")
    else:
        await update.message.reply_text(
            f"Пост #{post_id} не знайдено в черзі — можливо, він уже опублікований, скасований раніше, "
            "або такого номера не існує. Перевір актуальний список командою /schedule."
        )
