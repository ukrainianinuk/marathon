"""Юніт-тести логіки нарахування балів і детекції завдань. Реальний Telegram/Claude не використовується."""
import asyncio

import pytest

from db.database import (
    POINTS_MESSAGE,
    POINTS_TASK_COMPLETION,
    POINTS_THREAD_COMMENT,
    Database,
)
from services.claude_service import ClaudeService


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.init()
    return database


@pytest.mark.asyncio
async def test_message_point_awarded(db):
    await db.award_points(user_id=1, points=POINTS_MESSAGE, reason="message")
    await db.upsert_participant(1, "ivan", "Іван")
    entries = await db.get_leaderboard()
    assert entries[0].total_points == 1
    assert entries[0].messages == 1


@pytest.mark.asyncio
async def test_thread_comment_point_awarded(db):
    await db.upsert_participant(2, "olha", "Ольга")
    await db.award_points(user_id=2, points=POINTS_THREAD_COMMENT, reason="thread_comment")
    entries = await db.get_leaderboard()
    assert entries[0].total_points == 1
    assert entries[0].thread_comments == 1


@pytest.mark.asyncio
async def test_task_completion_awards_ten_points(db):
    await db.upsert_participant(3, "maria", "Марія")
    msg_id = await db.log_message(3, chat_id=-100, message_id=10, message_thread_id=None, text="#день5 зробила!")
    awarded = await db.award_task_completion(user_id=3, day=5, message_db_id=msg_id)
    assert awarded is True

    entries = await db.get_leaderboard()
    assert entries[0].total_points == POINTS_TASK_COMPLETION
    assert entries[0].tasks_completed == 1


@pytest.mark.asyncio
async def test_task_completion_double_submission_protected(db):
    await db.upsert_participant(4, "petro", "Петро")
    msg1 = await db.log_message(4, chat_id=-100, message_id=20, message_thread_id=None, text="#день3 готово")
    msg2 = await db.log_message(4, chat_id=-100, message_id=21, message_thread_id=None, text="#день3 ще раз")

    first = await db.award_task_completion(user_id=4, day=3, message_db_id=msg1)
    second = await db.award_task_completion(user_id=4, day=3, message_db_id=msg2)

    assert first is True
    assert second is False  # подвійне зарахування заблоковано

    entries = await db.get_leaderboard()
    assert entries[0].total_points == POINTS_TASK_COMPLETION  # лише один раз +10, а не +20


@pytest.mark.asyncio
async def test_task_completion_different_days_both_award(db):
    await db.upsert_participant(5, "nadia", "Надія")
    msg1 = await db.log_message(5, chat_id=-100, message_id=30, message_thread_id=None, text="#день1")
    msg2 = await db.log_message(5, chat_id=-100, message_id=31, message_thread_id=None, text="#день2")

    await db.award_task_completion(user_id=5, day=1, message_db_id=msg1)
    await db.award_task_completion(user_id=5, day=2, message_db_id=msg2)

    entries = await db.get_leaderboard()
    assert entries[0].total_points == POINTS_TASK_COMPLETION * 2
    assert entries[0].tasks_completed == 2


@pytest.mark.asyncio
async def test_manual_bonus_added_to_total(db):
    await db.upsert_participant(6, "sofia", "Софія")
    await db.award_points(user_id=6, points=1, reason="message")
    await db.award_points(user_id=6, points=5, reason="manual_bonus")
    entries = await db.get_leaderboard()
    assert entries[0].total_points == 6
    assert entries[0].bonus == 5


@pytest.mark.asyncio
async def test_leaderboard_sorted_descending(db):
    await db.upsert_participant(7, "a", "A")
    await db.upsert_participant(8, "b", "B")
    await db.award_points(user_id=7, points=3, reason="message")
    await db.award_points(user_id=8, points=10, reason="manual_bonus")

    entries = await db.get_leaderboard()
    assert entries[0].user_id == 8
    assert entries[0].total_points == 10
    assert entries[1].user_id == 7


@pytest.mark.asyncio
async def test_reminder_query_respects_threshold(db):
    msg_id = await db.log_message(
        9, chat_id=-100, message_id=40, message_thread_id=None, text="Питання?", addressed_to_admin=True
    )
    # Щойно написане повідомлення НЕ має потрапляти у прострочені за поріг 6 годин
    overdue_now = await db.get_unanswered_older_than(threshold_hours=6)
    assert len(overdue_now) == 0

    # Але при порозі 0 годин (тобто "будь-що старше за зараз") воно вже прострочене
    overdue_zero = await db.get_unanswered_older_than(threshold_hours=0)
    assert any(r["id"] == msg_id for r in overdue_zero)


@pytest.mark.asyncio
async def test_mark_responded_by_reply_stops_reminder(db):
    msg_id = await db.log_message(
        10, chat_id=-100, message_id=50, message_thread_id=None, text="Допоможіть", addressed_to_admin=True
    )
    matched_id = await db.mark_responded_by_reply(chat_id=-100, replied_to_message_id=50)
    assert matched_id == msg_id

    overdue = await db.get_unanswered_older_than(threshold_hours=0)
    assert not any(r["id"] == msg_id for r in overdue)


def test_task_hashtag_detection_valid_day():
    assert ClaudeService.detect_task_day("Ось моє домашнє #день5 готово!", max_day=20) == 5
    assert ClaudeService.detect_task_day("#День 12 виконано", max_day=20) == 12


def test_task_hashtag_detection_out_of_range():
    assert ClaudeService.detect_task_day("#день25 щось", max_day=20) is None


def test_task_hashtag_detection_no_hashtag():
    assert ClaudeService.detect_task_day("Просто повідомлення без хештегу", max_day=20) is None


def test_task_hashtag_detection_none_text():
    assert ClaudeService.detect_task_day(None, max_day=20) is None
