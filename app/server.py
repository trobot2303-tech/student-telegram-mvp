from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import engine, get_session
from app.models import AuditLog, Base, Nonce, Profile, User, WalletBinding
from app.security import new_nonce, parse_init_data_user, verify_evm_signature
from app.settings import get_settings

logger = logging.getLogger("student_mvp")
templates = Jinja2Templates(directory="app/templates")

app = FastAPI(title="Student Telegram MVP")


def _now_utc() -> datetime:
    return datetime.now(UTC)


async def _audit(session: AsyncSession, telegram_id: int | None, event: str, details: dict | None = None) -> None:
    session.add(
        AuditLog(
            telegram_id=telegram_id,
            event=event,
            details=json.dumps(details, ensure_ascii=False) if details is not None else None,
        )
    )


async def get_current_user(
    session: AsyncSession = Depends(get_session),
    x_tg_init_data: str | None = Header(default=None, alias="X-Tg-Init-Data"),
) -> User:
    settings = get_settings()
    if not x_tg_init_data:
        raise HTTPException(status_code=401, detail="Missing X-Tg-Init-Data")

    tg_user = parse_init_data_user(x_tg_init_data, settings.telegram_bot_token)
    if not tg_user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    res = await session.execute(select(User).where(User.telegram_id == tg_user.id))
    user = res.scalar_one_or_none()
    if not user:
        user = User(
            telegram_id=tg_user.id,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            username=tg_user.username,
        )
        session.add(user)
        await session.flush()
        session.add(Profile(user_id=user.id, fa_balance=0, status="student"))
        await _audit(session, tg_user.id, "user_created", {"username": tg_user.username})
        await session.commit()
        await session.refresh(user)

    return user


@app.on_event("startup")
async def on_startup() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("API started")


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/miniapp", response_class=HTMLResponse)
async def miniapp(request: Request):
    return HTMLResponse("""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
        <title>Личный кабинет</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background: var(--tg-theme-bg-color, #f5f5f5);
                color: var(--tg-theme-text-color, #222);
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                padding: 16px;
            }
            .card {
                width: 100%;
                max-width: 380px;
                background: var(--tg-theme-section-bg-color, #fff);
                border-radius: 18px;
                padding: 24px 20px;
                box-shadow: 0 4px 20px rgba(0,0,0,0.08);
                text-align: center;
            }
            .avatar {
                width: 72px;
                height: 72px;
                border-radius: 50%;
                background: var(--tg-theme-button-color, #2AABEE);
                color: #fff;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 32px;
                font-weight: bold;
                margin: 0 auto 12px;
                text-transform: uppercase;
            }
            h2 { font-size: 22px; font-weight: 600; margin-bottom: 4px; }
            .username { color: var(--tg-theme-hint-color, #999); font-size: 14px; margin-bottom: 20px; }
            .balance-block {
                background: var(--tg-theme-secondary-bg-color, #f0f0f0);
                border-radius: 14px;
                padding: 16px;
                margin-bottom: 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .balance-label { font-size: 13px; color: var(--tg-theme-hint-color, #999); text-transform: uppercase; letter-spacing: 0.5px; }
            .balance-value { font-size: 28px; font-weight: 700; }
            .btn {
                display: block;
                width: 100%;
                padding: 14px 16px;
                margin-bottom: 10px;
                border-radius: 12px;
                border: none;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: opacity 0.15s;
                text-decoration: none;
                color: #fff;
                background: var(--tg-theme-button-color, #2AABEE);
            }
            .btn:active { opacity: 0.8; }
            .btn-outline {
                background: transparent;
                color: var(--tg-theme-button-color, #2AABEE);
                border: 2px solid var(--tg-theme-button-color, #2AABEE);
            }
            .footer { margin-top: 16px; font-size: 12px; color: var(--tg-theme-hint-color, #999); }

            /* Модальное окно */
            .modal {
                display: none;
                position: fixed;
                z-index: 1000;
                left: 0;
                top: 0;
                width: 100%;
                height: 100%;
                background: rgba(0,0,0,0.5);
                justify-content: center;
                align-items: center;
            }
            .modal.active { display: flex; }
            .modal-content {
                background: var(--tg-theme-section-bg-color, #fff);
                border-radius: 16px;
                padding: 20px;
                width: 90%;
                max-width: 340px;
                text-align: left;
            }
            .modal-content textarea, .modal-content input {
                width: 100%;
                padding: 10px;
                margin: 8px 0;
                border-radius: 8px;
                border: 1px solid #ccc;
                font-size: 14px;
                background: var(--tg-theme-secondary-bg-color, #fff);
                color: var(--tg-theme-text-color, #000);
            }
            .modal-content label { font-weight: 500; font-size: 14px; }
            .modal-buttons { display: flex; gap: 8px; margin-top: 16px; }
            .modal-buttons .btn { flex: 1; margin: 0; }
        </style>
    </head>
    <body>
        <div class="card">
            <div class="avatar" id="avatar">👤</div>
            <h2 id="displayName">Студент</h2>
            <div class="username" id="username">@username</div>

            <div class="balance-block">
                <div>
                    <div class="balance-label">FA Баланс</div>
                    <div class="balance-value" id="faBalance">0</div>
                </div>
                <div>⭐</div>
            </div>

            <button class="btn" id="bindWalletBtn">🔗 Привязать кошелёк</button>
            <button class="btn btn-outline" id="refreshBtn">🔄 Обновить данные</button>

            <div class="footer">
                <a href="/privacy" style="color: var(--tg-theme-link-color, #2AABEE);">Политика конфиденциальности</a>
            </div>
        </div>

        <!-- Модальное окно привязки -->
        <div class="modal" id="bindModal">
            <div class="modal-content">
                <h3>Привязка кошелька</h3>
                <p style="font-size:13px; color: var(--tg-theme-hint-color); margin: 8px 0;">Скопируйте сообщение ниже, подпишите его вашим EVM-кошельком и вставьте адрес и подпись.</p>
                <label>Сообщение для подписи:</label>
                <textarea id="messageToSign" readonly rows="4"></textarea>
                <button class="btn-outline" id="copyMessageBtn" style="font-size:14px; padding:8px; margin:4px 0;">📋 Копировать</button>
                <label>Адрес кошелька (0x...):</label>
                <input type="text" id="walletAddress" placeholder="0x...">
                <label>Подпись (0x...):</label>
                <input type="text" id="signature" placeholder="0x...">
                <div class="modal-buttons">
                    <button class="btn" id="confirmBindBtn">✅ Привязать</button>
                    <button class="btn btn-outline" id="cancelBindBtn">Отмена</button>
                </div>
            </div>
        </div>

        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <script>
            // Инициализация Telegram WebApp
            const tg = window.Telegram.WebApp;
            tg.ready();
            tg.expand();

            // Тема
            const themeParams = tg.themeParams;
            const setProp = (name, val) => document.documentElement.style.setProperty(name, val);
            setProp('--tg-theme-bg-color', themeParams.bg_color || '#f5f5f5');
            setProp('--tg-theme-text-color', themeParams.text_color || '#222');
            setProp('--tg-theme-hint-color', themeParams.hint_color || '#999');
            setProp('--tg-theme-button-color', themeParams.button_color || '#2AABEE');
            setProp('--tg-theme-button-text-color', themeParams.button_text_color || '#fff');
            setProp('--tg-theme-section-bg-color', themeParams.section_bg_color || '#fff');
            setProp('--tg-theme-secondary-bg-color', themeParams.secondary_bg_color || '#f0f0f0');
            setProp('--tg-theme-link-color', themeParams.link_color || '#2AABEE');

            const user = tg.initDataUnsafe?.user;
            if (user) {
                document.getElementById('avatar').textContent = user.first_name.charAt(0);
                document.getElementById('displayName').textContent = user.first_name + (user.last_name ? ' ' + user.last_name : '');
                document.getElementById('username').textContent = user.username ? '@' + user.username : '';
            }

            let currentNonce = null;
            let currentMessage = '';

            async function loadUserData() {
                try {
                    const res = await fetch('/api/me', { headers: { 'X-Tg-Init-Data': tg.initData } });
                    if (res.ok) {
                        const data = await res.json();
                        document.getElementById('faBalance').textContent = data.fa_balance || 0;
                        if (data.wallet_address) {
                            document.getElementById('bindWalletBtn').textContent = '✅ Кошелёк привязан';
                            document.getElementById('bindWalletBtn').disabled = true;
                        }
                    }
                } catch(e) {
                    console.log('Ошибка загрузки данных', e);
                }
            }

            document.getElementById('refreshBtn').addEventListener('click', loadUserData);
            loadUserData();

            // Привязка кошелька
            const bindBtn = document.getElementById('bindWalletBtn');
            const modal = document.getElementById('bindModal');
            const cancelBtn = document.getElementById('cancelBindBtn');
            const confirmBtn = document.getElementById('confirmBindBtn');
            const copyBtn = document.getElementById('copyMessageBtn');
            const messageArea = document.getElementById('messageToSign');
            const walletInput = document.getElementById('walletAddress');
            const sigInput = document.getElementById('signature');

            bindBtn.addEventListener('click', async () => {
                if (bindBtn.disabled) return;
                try {
                    // запрашиваем nonce
                    const res = await fetch('/api/wallet/bind/nonce', {
                        method: 'POST',
                        headers: { 'X-Tg-Init-Data': tg.initData }
                    });
                    if (!res.ok) {
                        const err = await res.json();
                        tg.showAlert('Ошибка: ' + (err.detail || 'Не удалось получить nonce'));
                        return;
                    }
                    const data = await res.json();
                    currentNonce = data.nonce;
                    currentMessage = data.message;
                    messageArea.value = currentMessage;
                    walletInput.value = '';
                    sigInput.value = '';
                    modal.classList.add('active');
                } catch(e) {
                    tg.showAlert('Ошибка сети');
                }
            });

            cancelBtn.addEventListener('click', () => {
                modal.classList.remove('active');
                currentNonce = null;
                currentMessage = '';
            });

            copyBtn.addEventListener('click', () => {
                messageArea.select();
                document.execCommand('copy');
                tg.showAlert('Сообщение скопировано!');
            });

            confirmBtn.addEventListener('click', async () => {
                const wallet = walletInput.value.trim();
                const sig = sigInput.value.trim();
                if (!wallet || !sig || !currentNonce || !currentMessage) {
                    tg.showAlert('Заполните все поля и получите сообщение');
                    return;
                }
                try {
                    const res = await fetch('/api/wallet/bind/verify', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Tg-Init-Data': tg.initData
                        },
                        body: JSON.stringify({
                            wallet_address: wallet,
                            signature: sig,
                            nonce: currentNonce,
                            message: currentMessage
                        })
                    });
                    if (!res.ok) {
                        const err = await res.json();
                        tg.showAlert('Ошибка: ' + (err.detail || 'Привязка не удалась'));
                        return;
                    }
                    tg.showAlert('✅ Кошелёк привязан!');
                    modal.classList.remove('active');
                    // обновить данные
                    loadUserData();
                } catch(e) {
                    tg.showAlert('Ошибка сети');
                }
            });
        </script>
    </body>
    </html>
    """)
@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    return HTMLResponse("""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Политика конфиденциальности</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background: var(--tg-theme-bg-color, #f5f5f5);
                color: var(--tg-theme-text-color, #222);
                padding: 20px;
                max-width: 600px;
                margin: 0 auto;
                line-height: 1.6;
            }
            h1 { font-size: 24px; margin-bottom: 16px; }
            h2 { font-size: 18px; margin-top: 24px; margin-bottom: 8px; }
            p { margin-bottom: 12px; }
            a { color: var(--tg-theme-link-color, #2AABEE); }
        </style>
    </head>
    <body>
        <h1>Политика конфиденциальности</h1>
        <p><strong>Дата последнего обновления:</strong> 23 апреля 2026 г.</p>

        <h2>1. Какие данные мы собираем</h2>
        <p>При использовании Mini App мы получаем ваш Telegram ID, имя, фамилию и username (если есть) через официальное API Telegram. Эти данные необходимы для функционирования личного кабинета и привязки кошелька.</p>
        <p>При привязке EVM-кошелька мы сохраняем ваш публичный адрес и историю подписанных сообщений (nonce) для верификации.</p>

        <h2>2. Как мы используем данные</h2>
        <p>Данные используются исключительно в рамках сервиса: отображение профиля, учёт баланса FA-токенов, отправка уведомлений через бота, выполнение действий, запрошенных пользователем (привязка кошелька, обратная связь).</p>

        <h2>3. Передача данных третьим лицам</h2>
        <p>Мы не передаём ваши данные третьим лицам, за исключением случаев, предусмотренных законодательством.</p>

        <h2>4. Хранение данных</h2>
        <p>Данные хранятся в базе данных SQLite на сервере. Вы можете запросить удаление своих данных, написав администратору через бота.</p>

        <h2>5. Контакты</h2>
        <p>По вопросам конфиденциальности обращайтесь к администратору через Telegram-бота @FA2303bot.</p>

        <p><a href="/miniapp">← Назад в личный кабинет</a></p>
    </body>
    </html>
    """)
@app.get("/api/me")
async def api_me(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    await session.refresh(user)

    wallet_addr = None
    if user.wallet:
        wallet_addr = user.wallet.wallet_address

    fa_balance = user.profile.fa_balance if user.profile else 0
    status = user.profile.status if user.profile else "student"

    return JSONResponse(
        {
            "telegram_id": user.telegram_id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "username": user.username,
            "wallet_address": wallet_addr,
            "fa_balance": fa_balance,
            "status": status,
        }
    )


@app.post("/api/wallet/bind/nonce")
async def wallet_bind_nonce(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    settings = get_settings()

    # cleanup expired
    await session.execute(delete(Nonce).where(Nonce.expires_at < _now_utc()))

    nonce_value = new_nonce()
    expires_at = _now_utc() + timedelta(seconds=settings.bind_nonce_ttl_seconds)
    session.add(Nonce(user_id=user.id, nonce=nonce_value, purpose="bind_wallet", expires_at=expires_at))
    await _audit(session, user.telegram_id, "nonce_created", {"purpose": "bind_wallet"})
    await session.commit()

    message = (
        "FA Student Portal: Bind wallet\n"
        f"Telegram ID: {user.telegram_id}\n"
        f"Nonce: {nonce_value}\n"
        f"Expires: {int(expires_at.timestamp())}\n"
    )
    return JSONResponse({"nonce": nonce_value, "expires_at": expires_at.isoformat(), "message": message})


@app.post("/api/wallet/bind/verify")
async def wallet_bind_verify(
    payload: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    settings = get_settings()
    wallet_address = (payload.get("wallet_address") or "").strip()
    signature = (payload.get("signature") or "").strip()
    nonce_value = (payload.get("nonce") or "").strip()
    message = (payload.get("message") or "").strip()

    if not wallet_address or not signature or not nonce_value or not message:
        raise HTTPException(status_code=400, detail="wallet_address, signature, nonce, message are required")

    res = await session.execute(
        select(Nonce).where(
            Nonce.user_id == user.id,
            Nonce.nonce == nonce_value,
            Nonce.purpose == "bind_wallet",
        )
    )
    nonce_row = res.scalar_one_or_none()
    if not nonce_row or nonce_row.expires_at < _now_utc():
        await _audit(session, user.telegram_id, "bind_failed", {"reason": "nonce_invalid_or_expired"})
        await session.commit()
        raise HTTPException(status_code=400, detail="Nonce invalid or expired")

    if not verify_evm_signature(message=message, signature=signature, expected_address=wallet_address):
        await _audit(session, user.telegram_id, "bind_failed", {"reason": "signature_invalid"})
        await session.commit()
        raise HTTPException(status_code=400, detail="Signature invalid")

    # ensure wallet isn't already bound to someone else
    res2 = await session.execute(select(WalletBinding).where(WalletBinding.wallet_address == wallet_address))
    existing = res2.scalar_one_or_none()
    if existing and existing.user_id != user.id:
        await _audit(session, user.telegram_id, "bind_failed", {"reason": "wallet_already_bound"})
        await session.commit()
        raise HTTPException(status_code=409, detail="Wallet already bound")

    # upsert binding
    if user.wallet:
        user.wallet.wallet_address = wallet_address
    else:
        session.add(WalletBinding(user_id=user.id, wallet_address=wallet_address))

    # consume nonce
    await session.execute(delete(Nonce).where(Nonce.id == nonce_row.id))

    # initial balance on first-time bind (only if balance is 0)
    if user.profile and user.profile.fa_balance == 0:
        user.profile.fa_balance = settings.initial_fa_balance

    await _audit(session, user.telegram_id, "wallet_bound", {"wallet_address": wallet_address})
    await session.commit()
    return JSONResponse({"ok": True, "wallet_address": wallet_address})


def _require_admin(telegram_id: int) -> None:
    if telegram_id not in get_settings().admin_ids_set():
        raise HTTPException(status_code=403, detail="Admin only")


@app.get("/api/admin/segments")
async def admin_segments(user: User = Depends(get_current_user)) -> JSONResponse:
    _require_admin(user.telegram_id)
    return JSONResponse(
        {
            "segments": [
                {"id": "all", "title": "Все пользователи"},
                {"id": "bound", "title": "Привязанные (есть кошелёк)"},
                {"id": "unbound", "title": "Не привязанные"},
            ]
        }
    )


@app.post("/api/admin/broadcast/plan")
async def admin_broadcast_plan(
    payload: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    _require_admin(user.telegram_id)
    segment = (payload.get("segment") or "").strip()
    if segment not in {"all", "bound", "unbound"}:
        raise HTTPException(status_code=400, detail="Invalid segment")

    stmt = select(User.telegram_id)
    if segment == "bound":
        stmt = stmt.join(WalletBinding, WalletBinding.user_id == User.id)
    elif segment == "unbound":
        stmt = stmt.outerjoin(WalletBinding, WalletBinding.user_id == User.id).where(WalletBinding.id.is_(None))

    res = await session.execute(stmt)
    ids = [int(x) for (x,) in res.all()]
    await _audit(session, user.telegram_id, "broadcast_planned", {"segment": segment, "count": len(ids)})
    await session.commit()
    return JSONResponse({"segment": segment, "count": len(ids), "telegram_ids": ids})


@app.post("/api/admin/broadcast/send")
async def admin_broadcast_send(
    payload: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """
    Server-side send is intentionally not implemented in MVP.
    Use bot command /broadcast to actually send messages.
    """
    _require_admin(user.telegram_id)
    segment = (payload.get("segment") or "").strip()
    text = (payload.get("text") or "").strip()
    if segment not in {"all", "bound", "unbound"} or not text:
        raise HTTPException(status_code=400, detail="segment and text are required")
    await _audit(session, user.telegram_id, "broadcast_send_requested", {"segment": segment, "len": len(text)})
    await session.commit()
    return JSONResponse({"ok": True, "note": "Use /broadcast in Telegram bot to send messages."})


@app.post("/api/feedback")
async def feedback(
    payload: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    topic = (payload.get("topic") or "").strip()
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    if topic not in {"idea", "bug", "activity", "wallet", "other", ""}:
        topic = "other"

    await _audit(session, user.telegram_id, "feedback_submitted", {"topic": topic, "text": text[:4000]})
    await session.commit()
    return JSONResponse({"ok": True})

