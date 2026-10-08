import os
import sqlite3
import secrets
from datetime import datetime, timedelta, timezone

import telebot
from telebot import types


# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

DB_FILE = "nova_vpn.db"

SUBSCRIPTION_PRICE = 100
SUBSCRIPTION_DAYS = 30


# =========================
# DATABASE
# =========================

def get_db():
    connection = sqlite3.connect(DB_FILE)
    connection.row_factory = sqlite3.Row
    return connection


def init_database():
    connection = get_db()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance INTEGER DEFAULT 0,
            vpn_token TEXT UNIQUE,
            subscription_expires_at TEXT,
            created_at TEXT NOT NULL
        )
    """)

    connection.commit()
    connection.close()


def get_user(telegram_id):
    connection = get_db()

    user = connection.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    connection.close()
    return user


def create_user(message):
    telegram_id = message.from_user.id

    if get_user(telegram_id):
        return

    vpn_token = secrets.token_urlsafe(32)

    connection = get_db()

    connection.execute("""
        INSERT INTO users (
            telegram_id,
            username,
            first_name,
            balance,
            vpn_token,
            subscription_expires_at,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        telegram_id,
        message.from_user.username or "",
        message.from_user.first_name or "",
        0,
        vpn_token,
        None,
        datetime.now(timezone.utc).isoformat()
    ))

    connection.commit()
    connection.close()


# =========================
# SUBSCRIPTION
# =========================

def get_expiration(user):
    value = user["subscription_expires_at"]

    if not value:
        return None

    return datetime.fromisoformat(value)


def is_subscription_active(user):
    expiration = get_expiration(user)

    if not expiration:
        return False

    return expiration > datetime.now(timezone.utc)


def days_left(user):
    expiration = get_expiration(user)

    if not expiration:
        return 0

    remaining = expiration - datetime.now(timezone.utc)

    if remaining.total_seconds() <= 0:
        return 0

    return remaining.days + 1


def activate_subscription(telegram_id):
    connection = get_db()

    user = connection.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    if not user:
        connection.close()
        return False

    current_expiration = get_expiration(user)
    now = datetime.now(timezone.utc)

    if current_expiration and current_expiration > now:
        new_expiration = current_expiration + timedelta(
            days=SUBSCRIPTION_DAYS
        )
    else:
        new_expiration = now + timedelta(
            days=SUBSCRIPTION_DAYS
        )

    new_balance = max(0, user["balance"] - SUBSCRIPTION_PRICE)

    connection.execute("""
        UPDATE users
        SET balance = ?,
            subscription_expires_at = ?
        WHERE telegram_id = ?
    """, (
        new_balance,
        new_expiration.isoformat(),
        telegram_id
    ))

    connection.commit()
    connection.close()

    return True


# =========================
# KEYBOARDS
# =========================

def main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True
    )

    keyboard.row(
        "👤 Профиль",
        "🔐 Мой VPN"
    )

    keyboard.row(
        "💰 Баланс",
        "📅 Подписка"
    )

    keyboard.row(
        "💳 Пополнить",
        "🆘 Поддержка"
    )

    return keyboard


# =========================
# START
# =========================

@bot.message_handler(commands=["start"])
def start_handler(message):

    create_user(message)

    text = (
        "🛡 <b>Добро пожаловать в Nova VPN!</b>\n\n"
        "Надёжный VPN-сервис для безопасного доступа в интернет.\n\n"
        "💳 Подписка: <b>100 ₽ / 30 дней</b>\n"
        "🔗 После активации вы получите постоянный VPN-link.\n\n"
        "Выберите нужный раздел ниже."
    )

    bot.send_message(
        message.chat.id,
        text,
        reply_markup=main_keyboard()
    )


# =========================
# PROFILE
# =========================

@bot.message_handler(func=lambda message: message.text == "👤 Профиль")
def profile_handler(message):

    create_user(message)
    user = get_user(message.from_user.id)

    active = is_subscription_active(user)

    if active:
        status = "🟢 Активна"
        remaining = f"{days_left(user)} дней"
    else:
        status = "🔴 Не активна"
        remaining = "0 дней"

    text = (
        "👤 <b>Ваш профиль</b>\n\n"
        f"🆔 ID: <code>{user['telegram_id']}</code>\n"
        f"💰 Баланс: <b>{user['balance']} ₽</b>\n"
        f"📅 Подписка: <b>{status}</b>\n"
        f"⏳ Осталось: <b>{remaining}</b>\n"
    )

    if active:
        expiration = get_expiration(user)
        expiration_text = expiration.astimezone().strftime(
            "%d.%m.%Y %H:%M"
        )

        text += (
            f"📆 До: <b>{expiration_text}</b>\n"
        )

    text += (
        "\n💳 Стоимость продления: <b>100 ₽ / 30 дней</b>"
    )

    bot.send_message(
        message.chat.id,
        text,
        reply_markup=main_keyboard()
    )


# =========================
# BALANCE
# =========================

@bot.message_handler(func=lambda message: message.text == "💰 Баланс")
def balance_handler(message):

    create_user(message)
    user = get_user(message.from_user.id)

    text = (
        "💰 <b>Ваш баланс</b>\n\n"
        f"Баланс: <b>{user['balance']} ₽</b>\n\n"
        "Для пополнения нажмите «💳 Пополнить»."
    )

    bot.send_message(
        message.chat.id,
        text,
        reply_markup=main_keyboard()
    )


# =========================
# SUBSCRIPTION
# =========================

@bot.message_handler(func=lambda message: message.text == "📅 Подписка")
def subscription_handler(message):

    create_user(message)
    user = get_user(message.from_user.id)

    if is_subscription_active(user):

        expiration = get_expiration(user)
        expiration_text = expiration.astimezone().strftime(
            "%d.%m.%Y %H:%M"
        )

        text = (
            "📅 <b>Ваша подписка</b>\n\n"
            "🟢 Статус: <b>АКТИВНА</b>\n"
            f"⏳ Осталось: <b>{days_left(user)} дней</b>\n"
            f"📆 До: <b>{expiration_text}</b>\n\n"
            "🔐 Ваш VPN-link остаётся постоянным."
        )

    else:

        text = (
            "📅 <b>Ваша подписка</b>\n\n"
            "🔴 Статус: <b>ЗАКОНЧИЛАСЬ</b>\n\n"
            "Срок действия VPN закончился.\n"
            "Чтобы продолжить пользоваться Nova VPN, "
            "пополните баланс и продлите подписку.\n\n"
            "💳 Стоимость: <b>100 ₽ / 30 дней</b>"
        )

    bot.send_message(
        message.chat.id,
        text,
        reply_markup=main_keyboard()
    )


# =========================
# VPN
# =========================

@bot.message_handler(func=lambda message: message.text == "🔐 Мой VPN")
def vpn_handler(message):

    create_user(message)
    user = get_user(message.from_user.id)

    if not is_subscription_active(user):

        text = (
            "🔴 <b>VPN не активен</b>\n\n"
            "Срок вашей подписки закончился.\n\n"
            "💳 Продление: <b>100 ₽ / 30 дней</b>\n\n"
            "После оплаты ваш прежний VPN-link "
            "будет снова активирован."
        )

        bot.send_message(
            message.chat.id,
            text,
            reply_markup=main_keyboard()
        )

        return

    # IMPORTANT:
    # This is the permanent user identifier.
    # Real VPN configuration will be connected later.

    vpn_link = (
        "https://vpn.novavpn.example/sub/"
        + user["vpn_token"]
    )

    text = (
        "🔐 <b>Ваш Nova VPN</b>\n\n"
        "🟢 Статус: <b>АКТИВЕН</b>\n"
        f"⏳ Осталось: <b>{days_left(user)} дней</b>\n\n"
        "🔗 <b>Ваш постоянный VPN-link:</b>\n"
        f"<code>{vpn_link}</code>\n\n"
        "⚠️ Этот link является вашим постоянным link. "
        "После окончания подписки он перестанет работать. "
        "После продления снова будет активирован."
    )

    bot.send_message(
        message.chat.id,
        text,
        reply_markup=main_keyboard()
    )


# =========================
# PAYMENT
# =========================

@bot.message_handler(func=lambda message: message.text == "💳 Пополнить")
def payment_handler(message):

    create_user(message)

    text = (
        "💳 <b>Пополнение баланса</b>\n\n"
        "Стоимость подписки:\n"
        "⭐ <b>100 ₽ / 30 дней</b>\n\n"
        "Доступные способы оплаты будут подключены "
        "после настройки платёжного провайдера.\n\n"
        "Планируемые способы:\n"
        "• СБП\n"
        "• Мир\n"
        "• T-Pay\n"
        "• SberPay\n"
        "• Alfa Pay\n"
        "• Visa / Mastercard / UnionPay — "
        "если поддерживаются выбранным провайдером\n\n"
        "⚠️ Реальная оплата пока не подключена."
    )

    bot.send_message(
        message.chat.id,
        text,
        reply_markup=main_keyboard()
    )


# =========================
# SUPPORT
# =========================

@bot.message_handler(func=lambda message: message.text == "🆘 Поддержка")
def support_handler(message):

    text = (
        "🆘 <b>Поддержка Nova VPN</b>\n\n"
        "Если у вас проблема с оплатой, подпиской "
        "или VPN-link, напишите в поддержку.\n\n"
        "Администратор поддержки будет добавлен позже."
    )

    bot.send_message(
        message.chat.id,
        text,
        reply_markup=main_keyboard()
    )


# =========================
# UNKNOWN COMMAND
# =========================

@bot.message_handler(func=lambda message: True)
def unknown_handler(message):

    bot.send_message(
        message.chat.id,
        "Выберите раздел из меню ниже 👇",
        reply_markup=main_keyboard()
    )


# =========================
# START BOT
# =========================

init_database()

print("Nova VPN bot is starting...")

bot.infinity_polling(
    skip_pending=True
)
