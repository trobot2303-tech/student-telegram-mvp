from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo
from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import AuditLog, Base, User, WalletBinding
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


async def amain() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    # Ensure tables exist (same DB as API)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_cabinet, Command("cabinet"))
    dp.message.register(cmd_status, Command("status"))
    dp.message.register(cmd_broadcast, Command("broadcast"))
    dp.message.register(on_message, F.text)

    logger.info("Bot started")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(amain())

