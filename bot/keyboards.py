"""Inline-клавіатури для приватних сповіщень адміну."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def reply_suggestions_keyboard(message_db_id: int, variant_count: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(f"Надіслати варіант {i + 1}", callback_data=f"send_reply:{message_db_id}:{i}")
        for i in range(variant_count)
    ]
    rows = [[b] for b in buttons]
    rows.append([InlineKeyboardButton("Відповім сама", callback_data=f"dismiss_reply:{message_db_id}")])
    return InlineKeyboardMarkup(rows)
