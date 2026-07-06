"""Фонові задачі: нагадування, публікація відкладених постів, щоденний дайджест."""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.notifier import SENTIMENT_LABELS, notify_admin_about_message

logger = logging.getLogger(__name__)

REMINDER_CHECK_INTERVAL_SECONDS = 5 * 60
POST_PUBLISH_CHECK_INTERVAL_SECONDS = 60


async def check_reminders_and_urgent(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Періодично: (1) шле нагадування про повідомлення, на які не відповіли N годин;
    (2) підстраховує термінові повідомлення, якщо миттєве сповіщення з якоїсь причини не пішло."""
    bot_data = context.application.bot_data
    db = bot_data["db"]
    claude = bot_data["claude"]
    settings = bot_data["settings"]
    pending_suggestions = bot_data["pending_suggestions"]

    try:
        overdue = await db.get_unanswered_older_than(settings.reminder_threshold_hours)
        for row in overdue:
            await notify_admin_about_message(
                context.bot,
                db,
                claude,
                settings.admin_user_id,
                pending_suggestions,
                message_db_id=row["id"],
                user_id=row["user_id"],
                username=row["username"],
                full_name=row["full_name"],
                chat_id=row["chat_id"],
                message_id=row["message_id"],
                text=row["text"] or "",
                sentiment=row["sentiment"] or "neutral",
                urgent=False,
            )

        urgent_missed = await db.get_unnotified_urgent()
        for row in urgent_missed:
            await notify_admin_about_message(
                context.bot,
                db,
                claude,
                settings.admin_user_id,
                pending_suggestions,
                message_db_id=row["id"],
                user_id=row["user_id"],
                username=row["username"],
                full_name=row["full_name"],
                chat_id=row["chat_id"],
                message_id=row["message_id"],
                text=row["text"] or "",
                sentiment="urgent",
                urgent=True,
            )
    except Exception:
        logger.exception("Помилка під час перевірки нагадувань/термінових повідомлень")


async def publish_due_posts(context: ContextTypes.DEFAULT_TYPE) -> None:
    db = context.application.bot_data["db"]
    try:
        due = await db.get_due_posts()
    except Exception:
        logger.exception("Помилка при отриманні черги публікацій")
        return

    for post in due:
        try:
            if post["photo_file_id"]:
                await context.bot.send_photo(
                    chat_id=post["chat_id"], photo=post["photo_file_id"], caption=post["text"] or None
                )
            else:
                await context.bot.send_message(chat_id=post["chat_id"], text=post["text"] or "")
            await db.mark_post_published(post["id"])
        except TelegramError:
            logger.exception("Не вдалося опублікувати запланований пост #%s", post["id"])


async def send_daily_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    bot_data = context.application.bot_data
    db = bot_data["db"]
    settings = bot_data["settings"]

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
    try:
        digest = await db.get_sentiment_digest(since)
    except Exception:
        logger.exception("Помилка при формуванні щоденного дайджесту")
        return

    counts = digest["counts"]
    needs_attention = digest["needs_attention"]

    lines = ["📊 Щоденний дайджест настроїв за останні 24 години:\n"]
    if counts:
        for sentiment, cnt in counts.items():
            label = SENTIMENT_LABELS.get(sentiment, sentiment)
            lines.append(f"{label}: {cnt}")
    else:
        lines.append("Повідомлень за цей період не було.")

    lines.append("")
    if needs_attention:
        lines.append(f"⚠️ Потребують уваги ({len(needs_attention)}):")
        for row in needs_attention:
            name = f"@{row['username']}" if row["username"] else (row["full_name"] or f"id{row['user_id']}")
            preview = (row["text"] or "")[:80]
            lines.append(f"— {name}: «{preview}»")
    else:
        lines.append("✅ Усі повідомлення, що потребували уваги, вже опрацьовані.")

    try:
        await context.bot.send_message(chat_id=settings.admin_user_id, text="\n".join(lines))
    except TelegramError:
        logger.exception("Не вдалося надіслати щоденний дайджест")


def register_jobs(application) -> None:
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders_and_urgent, interval=REMINDER_CHECK_INTERVAL_SECONDS, first=30)
    job_queue.run_repeating(publish_due_posts, interval=POST_PUBLISH_CHECK_INTERVAL_SECONDS, first=15)

    settings = application.bot_data["settings"]
    hour, minute = (int(x) for x in settings.daily_digest_time.split(":"))
    digest_time = dt.time(hour=hour, minute=minute, tzinfo=ZoneInfo(settings.timezone))
    job_queue.run_daily(send_daily_digest, time=digest_time)
