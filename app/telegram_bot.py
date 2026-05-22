from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo
from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import AuditLog, Base, Profile, User, WalletBinding
from app.settings import get_settings

logger = logging.getLogger("student_mvp.bot")


def main_kb() -> ReplyKeyboardMarkup:
    settings = get_settings()
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="Личный кабинет",
                    web_app=WebAppInfo(url=settings.mini_app_url),
                )
            ],
        ],
        resize_keyboard=True,
    )


async def audit(telegram_id: int | None, event: str, details: str | None = None) -> None:
    async with SessionLocal() as session:
        session.add(AuditLog(telegram_id=telegram_id, event=event, details=details))
        await session.commit()


async def ensure_user(message: Message) -> None:
    if not message.from_user:
        return
    tg = message.from_user
    async with SessionLocal() as session:
        res = await session.execute(select(User).where(User.telegram_id == tg.id))
        user = res.scalar_one_or_none()
        if not user:
            user = User(
                telegram_id=tg.id,
                first_name=tg.first_name,
                last_name=tg.last_name,
                username=tg.username,
            )
            session.add(user)
            await session.commit()
    await audit(tg.id, "bot_seen_user")


async def send_telegram_message(chat_id: int, text: str) -> bool:
    """Отправляет сообщение через Telegram Bot API. Возвращает True в случае успеха."""
    settings = get_settings()
    token = settings.telegram_bot_token
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                return resp.status == 200
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


# Существующие обработчики
async def cmd_start(message: Message) -> None:
    await ensure_user(message)
    await message.answer(
        "Привет! Я бот студенческой экосистемы.\n\n"
        "Открой «Личный кабинет» (Mini App), чтобы увидеть баланс/статусы и привязать кошелёк.",
        reply_markup=main_kb(),
    )


async def cmd_help(message: Message) -> None:
    await message.answer(
        "Команды:\n"
        "/start — старт\n"
        "/help — помощь\n"
        "/cabinet — открыть Mini App\n"
        "/status — статус привязки\n"
        "/broadcast — админ-рассылка\n"
        "/addbalance — пополнить баланс пользователя (админ)\n"
    )


async def cmd_cabinet(message: Message) -> None:
    await ensure_user(message)
    await message.answer("Открываю личный кабинет.", reply_markup=main_kb())


async def cmd_status(message: Message) -> None:
    await ensure_user(message)
    if not message.from_user:
        return
    tg_id = message.from_user.id
    async with SessionLocal() as session:
        stmt = (
            select(WalletBinding.wallet_address)
            .select_from(User)
            .join(WalletBinding, WalletBinding.user_id == User.id, isouter=True)
            .where(User.telegram_id == tg_id)
        )
        res = await session.execute(stmt)
        wallet = res.scalar_one_or_none()
    if wallet:
        await message.answer(f"Кошелёк привязан: `{wallet}`", parse_mode="Markdown")
    else:
        await message.answer("Кошелёк ещё не привязан. Открой Mini App → «Привязать кошелёк».")


def _is_admin(tg_id: int) -> bool:
    return tg_id in get_settings().admin_ids_set()


async def cmd_broadcast(message: Message) -> None:
    await ensure_user(message)
    if not message.from_user:
        return
    if not _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return

    await message.answer(
        "Админ-рассылка.\n\n"
        "Формат:\n"
        "`/broadcast all Текст`\n"
        "`/broadcast bound Текст`\n"
        "`/broadcast unbound Текст`\n",
        parse_mode="Markdown",
    )


async def broadcast_send(bot: Bot, segment: str, text: str) -> tuple[int, int]:
    delivered = 0
    failed = 0
    async with SessionLocal() as session:
        stmt = select(User.telegram_id)
        if segment == "bound":
            stmt = stmt.join(WalletBinding, WalletBinding.user_id == User.id)
        elif segment == "unbound":
            stmt = stmt.outerjoin(WalletBinding, WalletBinding.user_id == User.id).where(WalletBinding.id.is_(None))
        res = await session.execute(stmt)
        ids = [int(x) for (x,) in res.all()]

    for tg_id in ids:
        try:
            await bot.send_message(chat_id=tg_id, text=text)
            delivered += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    return delivered, failed


async def on_message(message: Message, bot: Bot) -> None:
    # Lightweight parser for /broadcast segment text (single message).
    if not message.text or not message.from_user:
        return
    if not message.text.startswith("/broadcast"):
        return
    if not _is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        return
    segment = parts[1].strip()
    text = parts[2].strip()
    if segment not in {"all", "bound", "unbound"}:
        await message.answer("Сегмент должен быть: all | bound | unbound")
        return

    await message.answer("Отправляю рассылку...")
    delivered, failed = await broadcast_send(bot, segment=segment, text=text)
    await audit(message.from_user.id, "broadcast_sent", f"segment={segment} delivered={delivered} failed={failed}")
    await message.answer(f"Готово. Доставлено: {delivered}. Ошибок: {failed}.")


# --- Новая команда /addbalance ---
async def cmd_addbalance(message: Message) -> None:
    """Пополнение баланса пользователя (только для админов)."""
    await ensure_user(message)
    if not message.from_user or not _is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав для выполнения этой команды.")
        return

    # Формат: /addbalance <telegram_id> <сумма>
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("❗ Использование: /addbalance <telegram_id> <сумма>")
        return

    try:
        target_id = int(parts[1])
        amount = float(parts[2])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ ID и сумма должны быть положительными числами.")
        return

    async with SessionLocal() as session:
        async with session.begin():
            result = await session.execute(select(User).where(User.telegram_id == target_id))
            user = result.scalar_one_or_none()
            if not user:
                await message.answer("🔍 Пользователь с таким Telegram ID не найден.")
                return

            profile = user.profile
            if not profile:
                profile = Profile(user_id=user.id, fa_balance=0, status="student")
                session.add(profile)
                await session.flush()

            profile.fa_balance += amount

            # Уведомление получателю
            await send_telegram_message(
                target_id,
                f"💰 На ваш баланс зачислено {amount} FA.\nТекущий баланс: {profile.fa_balance} FA."
            )
            await message.answer(f"✅ Пользователю {target_id} начислено {amount} FA.\nЕго баланс: {profile.fa_balance} FA.")


# --- Точка входа для запуска бота ---
async def start_bot() -> None:
    """Запускает бота (используется как из server.py, так и для ручного запуска)."""
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    # Таблицы создадутся и здесь (на случай, если бот запущен отдельно)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    # Регистрация обработчиков
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_cabinet, Command("cabinet"))
    dp.message.register(cmd_status, Command("status"))
    dp.message.register(cmd_broadcast, Command("broadcast"))
    dp.message.register(cmd_addbalance, Command("addbalance"))
    dp.message.register(on_message, F.text)

    logger.info("Bot started")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


# Блок для ручного запуска
if __name__ == "__main__":
    asyncio.run(start_bot())
