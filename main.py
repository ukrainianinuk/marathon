"""Точка входу: збирає Application, реєструє обробники та фонові задачі."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from bot.admin_commands import cmd_bonus, cmd_leaderboard, cmd_schedule, cmd_start
from bot.handlers import handle_admin_callback, handle_group_message
from config import load_settings
from db.database import Database
from scheduler.jobs import register_jobs
from services.claude_service import ClaudeService

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def error_handler(update: object, context) -> None:
    logger.error("Виняток під час обробки update: %s", context.error, exc_info=context.error)


async def post_init(application: Application) -> None:
    # Ініціалізуємо схему БД усередині циклу подій, яким керує сам python-telegram-bot,
    # а не через окремий asyncio.run() до старту polling.
    await application.bot_data["db"].init()


def main() -> None:
    settings = load_settings()

    db = Database(settings.db_path)
    claude = ClaudeService(settings.anthropic_api_key, settings.claude_model)

    application = Application.builder().token(settings.bot_token).post_init(post_init).build()
    application.bot_data["db"] = db
    application.bot_data["claude"] = claude
    application.bot_data["settings"] = settings
    application.bot_data["pending_suggestions"] = {}

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    application.add_handler(CommandHandler("bonus", cmd_bonus))
    application.add_handler(CommandHandler("schedule", cmd_schedule))
    application.add_handler(CallbackQueryHandler(handle_admin_callback))

    group_filter = filters.Chat(chat_id=settings.group_chat_ids) & ~filters.COMMAND & (filters.TEXT | filters.PHOTO)
    application.add_handler(MessageHandler(group_filter, handle_group_message))

    application.add_error_handler(error_handler)

    register_jobs(application)

    logger.info("Бот запускається...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
