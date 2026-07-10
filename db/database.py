"""Шар доступу до SQLite. Усі методи асинхронні (aiosqlite)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    message_thread_id INTEGER,
    text TEXT,
    timestamp TEXT NOT NULL,
    sentiment TEXT,
    addressed_to_admin INTEGER NOT NULL DEFAULT 0,
    responded INTEGER NOT NULL DEFAULT 0,
    responded_at TEXT,
    reminder_sent INTEGER NOT NULL DEFAULT 0,
    urgent_notified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_chat_msg ON messages(chat_id, message_id);
CREATE INDEX IF NOT EXISTS idx_messages_pending ON messages(addressed_to_admin, responded);

CREATE TABLE IF NOT EXISTS points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    points INTEGER NOT NULL,
    reason TEXT NOT NULL,
    message_db_id INTEGER,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_submissions (
    user_id INTEGER NOT NULL,
    day INTEGER NOT NULL,
    message_db_id INTEGER NOT NULL,
    confirmed_at TEXT NOT NULL,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS scheduled_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    text TEXT,
    photo_file_id TEXT,
    publish_at TEXT NOT NULL,
    published INTEGER NOT NULL DEFAULT 0,
    cancelled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

POINTS_MESSAGE = 1
POINTS_THREAD_COMMENT = 1
POINTS_TASK_COMPLETION = 10


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


@dataclass
class LeaderboardEntry:
    user_id: int
    username: str | None
    full_name: str | None
    total_points: int
    messages: int = 0
    thread_comments: int = 0
    tasks_completed: int = 0
    bonus: int = 0


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def upsert_participant(self, user_id: int, username: str | None, full_name: str | None) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO participants (user_id, username, full_name, first_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name
                """,
                (user_id, username, full_name, _now_iso()),
            )
            await db.commit()

    async def log_message(
        self,
        user_id: int,
        chat_id: int,
        message_id: int,
        message_thread_id: int | None,
        text: str | None,
        sentiment: str | None = None,
        addressed_to_admin: bool = False,
    ) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO messages
                    (user_id, chat_id, message_id, message_thread_id, text, timestamp, sentiment, addressed_to_admin)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    chat_id,
                    message_id,
                    message_thread_id,
                    text,
                    _now_iso(),
                    sentiment,
                    int(addressed_to_admin),
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def set_sentiment(self, message_db_id: int, sentiment: str, addressed_to_admin: bool) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE messages SET sentiment = ?, addressed_to_admin = ? WHERE id = ?",
                (sentiment, int(addressed_to_admin), message_db_id),
            )
            await db.commit()

    async def mark_urgent_notified(self, message_db_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE messages SET urgent_notified = 1 WHERE id = ?", (message_db_id,))
            await db.commit()

    async def mark_reminder_sent(self, message_db_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE messages SET reminder_sent = 1 WHERE id = ?", (message_db_id,))
            await db.commit()

    async def mark_responded(self, message_db_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE messages SET responded = 1, responded_at = ? WHERE id = ?",
                (_now_iso(), message_db_id),
            )
            await db.commit()

    async def mark_responded_by_reply(self, chat_id: int, replied_to_message_id: int) -> int | None:
        """Позначає повідомлення учасника як таке, на яке відповіли (адмін відповів reply-ем у Telegram)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id FROM messages WHERE chat_id = ? AND message_id = ? AND responded = 0",
                (chat_id, replied_to_message_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            await db.execute(
                "UPDATE messages SET responded = 1, responded_at = ? WHERE id = ?",
                (_now_iso(), row["id"]),
            )
            await db.commit()
            return row["id"]

    async def get_unanswered_older_than(self, threshold_hours: float) -> list[aiosqlite.Row]:
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=threshold_hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT m.*, p.username, p.full_name FROM messages m
                LEFT JOIN participants p ON p.user_id = m.user_id
                WHERE m.addressed_to_admin = 1 AND m.responded = 0 AND m.reminder_sent = 0 AND m.timestamp <= ?
                ORDER BY m.timestamp ASC
                """,
                (cutoff,),
            )
            return list(await cursor.fetchall())

    async def get_unnotified_urgent(self) -> list[aiosqlite.Row]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT m.*, p.username, p.full_name FROM messages m
                LEFT JOIN participants p ON p.user_id = m.user_id
                WHERE m.sentiment = 'urgent' AND m.responded = 0 AND m.urgent_notified = 0
                ORDER BY m.timestamp ASC
                """
            )
            return list(await cursor.fetchall())

    async def award_points(
        self, user_id: int, points: int, reason: str, message_db_id: int | None = None
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO points (user_id, points, reason, message_db_id, timestamp) VALUES (?, ?, ?, ?, ?)",
                (user_id, points, reason, message_db_id, _now_iso()),
            )
            await db.commit()

    async def has_task_submission(self, user_id: int, day: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM task_submissions WHERE user_id = ? AND day = ?", (user_id, day)
            )
            return (await cursor.fetchone()) is not None

    async def award_task_completion(self, user_id: int, day: int, message_db_id: int) -> bool:
        """Атомарно захищає від подвійного зарахування балів за завдання дня.

        Повертає True, якщо бали щойно нараховані; False, якщо завдання дня вже було зараховане раніше.
        """
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO task_submissions (user_id, day, message_db_id, confirmed_at) VALUES (?, ?, ?, ?)",
                    (user_id, day, message_db_id, _now_iso()),
                )
            except aiosqlite.IntegrityError:
                await db.rollback()
                return False
            await db.execute(
                "INSERT INTO points (user_id, points, reason, message_db_id, timestamp) VALUES (?, ?, ?, ?, ?)",
                (user_id, POINTS_TASK_COMPLETION, f"task_day_{day}", message_db_id, _now_iso()),
            )
            await db.commit()
            return True

    async def get_leaderboard(self) -> list[LeaderboardEntry]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT p.user_id, p.username, p.full_name,
                       COALESCE(SUM(pt.points), 0) AS total_points
                FROM participants p
                LEFT JOIN points pt ON pt.user_id = p.user_id
                GROUP BY p.user_id
                ORDER BY total_points DESC
                """
            )
            rows = await cursor.fetchall()
            entries = []
            for row in rows:
                breakdown_cursor = await db.execute(
                    "SELECT reason, SUM(points) as total FROM points WHERE user_id = ? GROUP BY reason",
                    (row["user_id"],),
                )
                breakdown_rows = await breakdown_cursor.fetchall()
                entry = LeaderboardEntry(
                    user_id=row["user_id"],
                    username=row["username"],
                    full_name=row["full_name"],
                    total_points=row["total_points"],
                )
                for b in breakdown_rows:
                    reason = b["reason"]
                    total = b["total"]
                    if reason == "message":
                        entry.messages = total
                    elif reason == "thread_comment":
                        entry.thread_comments = total
                    elif reason == "manual_bonus":
                        entry.bonus += total
                    elif reason.startswith("task_day_"):
                        entry.tasks_completed += 1
                entries.append(entry)
            return entries

    async def add_scheduled_post(
        self, chat_id: int, text: str | None, photo_file_id: str | None, publish_at: dt.datetime
    ) -> int:
        # Нормалізуємо до UTC, інакше рядкове порівняння ISO-таймстемпів у get_due_posts буде хибним.
        publish_at_utc = publish_at.astimezone(dt.timezone.utc)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO scheduled_posts (chat_id, text, photo_file_id, publish_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (chat_id, text, photo_file_id, publish_at_utc.isoformat(), _now_iso()),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_due_posts(self) -> list[aiosqlite.Row]:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM scheduled_posts
                WHERE published = 0 AND cancelled = 0 AND publish_at <= ?
                ORDER BY publish_at ASC
                """,
                (now,),
            )
            return list(await cursor.fetchall())

    async def get_pending_posts(self) -> list[aiosqlite.Row]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM scheduled_posts WHERE published = 0 AND cancelled = 0 ORDER BY publish_at ASC"
            )
            return list(await cursor.fetchall())

    async def mark_post_published(self, post_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE scheduled_posts SET published = 1 WHERE id = ?", (post_id,))
            await db.commit()

    async def cancel_scheduled_post(self, post_id: int) -> bool:
        """Скасовує ще не опублікований пост. Повертає False, якщо пост не знайдено,
        уже опублікований або вже скасований раніше."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE scheduled_posts SET cancelled = 1 WHERE id = ? AND published = 0 AND cancelled = 0",
                (post_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_sentiment_digest(self, since: dt.datetime) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT sentiment, COUNT(*) as cnt FROM messages
                WHERE timestamp >= ? AND sentiment IS NOT NULL
                GROUP BY sentiment
                """,
                (since.isoformat(),),
            )
            counts = {row["sentiment"]: row["cnt"] for row in await cursor.fetchall()}

            cursor = await db.execute(
                """
                SELECT m.*, p.username, p.full_name FROM messages m
                LEFT JOIN participants p ON p.user_id = m.user_id
                WHERE m.timestamp >= ? AND m.sentiment IN ('frustrated', 'urgent', 'question') AND m.responded = 0
                ORDER BY m.timestamp ASC
                """,
                (since.isoformat(),),
            )
            needs_attention = list(await cursor.fetchall())
            return {"counts": counts, "needs_attention": needs_attention}
