"""Конфігурація бота. Усі секрети та налаштування читаються з .env, нічого не хардкодиться."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int | None = None) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        if default is not None:
            return default
        raise RuntimeError(f"Змінна оточення {name} обов'язкова, але не задана в .env")
    return int(value)


def _get_int_list(name: str) -> list[int]:
    value = os.getenv(name, "")
    return [int(x.strip()) for x in value.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    bot_token: str
    anthropic_api_key: str
    admin_user_id: int
    group_chat_ids: list[int]
    channel_chat_id: int
    reminder_threshold_hours: float
    timezone: str
    daily_digest_time: str  # "HH:MM" за налаштованою таймзоною
    claude_model: str
    db_path: str
    task_hashtag_days: int = 20


def load_settings() -> Settings:
    group_chat_ids = _get_int_list("GROUP_CHAT_IDS")
    channel_chat_id_str = os.getenv("CHANNEL_CHAT_ID", "").strip()
    # CHANNEL_CHAT_ID — окремий чат (зазвичай канал), куди публікуються пости через /schedule.
    # Якщо не задано, публікуємо в перший чат із GROUP_CHAT_IDS (моніторинг і публікація — той самий чат).
    channel_chat_id = int(channel_chat_id_str) if channel_chat_id_str else group_chat_ids[0]

    return Settings(
        bot_token=os.environ["BOT_TOKEN"],
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        admin_user_id=_get_int("ADMIN_USER_ID"),
        group_chat_ids=group_chat_ids,
        channel_chat_id=channel_chat_id,
        reminder_threshold_hours=float(os.getenv("REMINDER_THRESHOLD_HOURS", "6")),
        timezone=os.getenv("TIMEZONE", "Europe/London"),
        daily_digest_time=os.getenv("DAILY_DIGEST_TIME", "20:00"),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5"),
        db_path=os.getenv("DB_PATH", "marathon.db"),
        task_hashtag_days=_get_int("TASK_HASHTAG_DAYS", 20),
    )
