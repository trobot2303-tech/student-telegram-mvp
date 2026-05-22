from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import aiohttp
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import engine, get_session
from app.models import AuditLog, Base, Nonce, Profile, User, WalletBinding
from app.security import new_nonce, parse_init_data_user, verify_evm_signature
from app.settings import get_settings
from sqlalchemy.orm import selectinload
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
    res = await session.execute(
    select(User)
    .where(User.telegram_id == tg_user.id)
    .options(selectinload(User.wallet), selectinload(User.profile))
)
    
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

    # Если нужно автоматически запускать бота – раскомментируйте строки ниже
    from app.telegram_bot import start_bot
    asyncio.create_task(start_bot())
    logger.info("Bot polling started in background")


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
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 20px;
                margin: 0;
            }
            .card {
                width: 100%;
                max-width: 400px;
                background: rgba(255, 255, 255, 0.95);
                backdrop-filter: blur(10px);
                border-radius: 24px;
                padding: 32px 24px;
                box-shadow: 0 20px 40px rgba(0, 0, 0, 0.2), 0 0 0 1px rgba(255, 255, 255, 0.1);
                text-align: center;
                animation: fadeInUp 0.5s ease-out;
            }
            @keyframes fadeInUp {
                from { opacity: 0; transform: translateY(20px); }
                to { opacity: 1; transform: translateY(0); }
            }
            .avatar {
                width: 80px;
                height: 80px;
                border-radius: 50%;
                background: linear-gradient(135deg, #6a11cb 0%, #2575fc 100%);
                color: #fff;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 36px;
                font-weight: 700;
                margin: 0 auto 16px;
                box-shadow: 0 8px 20px rgba(102, 126, 234, 0.4);
                text-transform: uppercase;
            }
            h2 {
                font-size: 24px;
                font-weight: 700;
                margin-bottom: 4px;
                color: #1a202c;
            }
            .username {
                font-size: 14px;
                color: #718096;
                margin-bottom: 24px;
                font-weight: 500;
            }
            .balance-block {
                background: white;
                border-radius: 16px;
                padding: 20px;
                margin-bottom: 24px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .balance-label {
                font-size: 12px;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 1px;
                color: #a0aec0;
            }
            .balance-value {
                font-size: 32px;
                font-weight: 700;
                background: linear-gradient(135deg, #6a11cb 0%, #2575fc 100%);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }
            .btn {
                width: 100%;
                padding: 16px 20px;
                margin-bottom: 12px;
                border-radius: 16px;
                border: none;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s ease;
                font-family: inherit;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
            }
            .btn-primary {
                background: linear-gradient(135deg, #6a11cb 0%, #2575fc 100%);
                color: white;
                box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
            }
            .btn-primary:active {
                transform: scale(0.98);
            }
            .btn-secondary {
                background: white;
                color: #4a5568;
                border: 2px solid #e2e8f0;
            }
            .footer {
                margin-top: 16px;
                font-size: 13px;
                color: #a0aec0;
            }
            .footer a {
                color: #667eea;
                text-decoration: none;
                font-weight: 500;
            }
            /* Модальное окно */
            .modal {
                display: none;
                position: fixed;
                z-index: 1000;
                left: 0;
                top: 0;
                width: 100%;
                height: 100%;
                background: rgba(0, 0, 0, 0.6);
                backdrop-filter: blur(4px);
                justify-content: center;
                align-items: center;
                padding: 20px;
            }
            .modal.active {
                display: flex;
            }
            .modal-content {
                background: white;
                border-radius: 24px;
                padding: 24px;
                width: 100%;
                max-width: 380px;
                box-shadow: 0 25px 50px rgba(0, 0, 0, 0.3);
                text-align: left;
                color: #1a202c;
            }
            .modal-content h3 {
                font-size: 20px;
                margin-bottom: 12px;
            }
            .modal-content label {
                display: block;
                font-size: 13px;
                font-weight: 600;
                color: #2d3748;
                margin-bottom: 4px;
                margin-top: 12px;
            }
            .modal-content textarea,
            .modal-content input {
                width: 100%;
                padding: 12px;
                border-radius: 12px;
                border: 1px solid #e2e8f0;
                font-size: 14px;
                background: #f7fafc;
                color: #1a202c;
                resize: vertical;
                font-family: inherit;
            }
            .modal-buttons {
                display: flex;
                gap: 10px;
                margin-top: 24px;
            }
            .modal-buttons .btn {
                flex: 1;
                margin-bottom: 0;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <div class="avatar" id="avatar">👤</div>
            <h2 id="displayName">Студент</h2>
            <div class="username" id="username">@username</div>

            <div class="balance-block">
                <div>
                    <div class="balance-label">Баланс FA</div>
                    <div class="balance-value" id="faBalance">0</div>
                </div>
                <div style="font-size: 40px;">⭐</div>
            </div>

            <button class="btn btn-primary" id="bindWalletBtn">🔗 Привязать кошелёк</button>
            <button class="btn btn-secondary" id="refreshBtn">🔄 Обновить</button>

            <div class="footer">
                <a href="/privacy">Политика конфиденциальности</a>
            </div>
        </div>

        <!-- Модальное окно привязки -->
        <div class="modal" id="bindModal">
            <div class="modal-content">
                <h3>🔐 Привязка кошелька</h3>
                <p style="font-size:14px; color: #4a5568; margin-bottom: 16px;">
                    Скопируйте сообщение, подпишите его в вашем EVM-кошельке (MetaMask, Trust Wallet и др.) и вставьте адрес и подпись ниже.
                </p>
                <label>Сообщение для подписи:</label>
                <textarea id="messageToSign" readonly rows="4"></textarea>
                <button class="btn btn-secondary" id="copyMessageBtn" style="font-size:14px; padding:10px; margin-top:8px;">📋 Копировать</button>
                <label>Адрес кошелька (0x...):</label>
                <input type="text" id="walletAddress" placeholder="0x...">
                <label>Подпись (0x...):</label>
                <input type="text" id="signature" placeholder="0x...">
                <div class="modal-buttons">
                    <button class="btn btn-primary" id="confirmBindBtn">✅ Привязать</button>
                    <button class="btn btn-secondary" id="cancelBindBtn">Отмена</button>
                </div>
            </div>
        </div>

        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <script>
            var tg = window.Telegram.WebApp;
            tg.ready();
            tg.expand();

            var user = tg.initDataUnsafe ? tg.initDataUnsafe.user : null;
            var isTelegram = user ? true : false;
            
            // Отображаем данные пользователя
            if (user) {
                document.getElementById('avatar').textContent = user.first_name.charAt(0);
                document.getElementById('displayName').textContent = user.first_name + (user.last_name ? ' ' + user.last_name : '');
                document.getElementById('username').textContent = user.username ? '@' + user.username : 'ID: ' + user.id;
            }

            // Загрузка данных
            function loadUserData() {
                if (!isTelegram) return;
                
                var xhr = new XMLHttpRequest();
                xhr.open('GET', '/api/me', true);
                xhr.setRequestHeader('X-Tg-Init-Data', tg.initData);
                xhr.onload = function() {
                    if (xhr.status === 200) {
                        var data = JSON.parse(xhr.responseText);
                        document.getElementById('faBalance').textContent = data.fa_balance || 0;
                        if (data.wallet_address) {
                            var btn = document.getElementById('bindWalletBtn');
                            btn.textContent = '✅ Кошелёк привязан';
                            btn.disabled = true;
                            btn.style.opacity = '0.6';
                        }
                    }
                };
                xhr.send();
            }

            // Кнопка Обновить
            document.getElementById('refreshBtn').onclick = function() {
                if (isTelegram) {
                    loadUserData();
                    tg.showAlert('Данные обновлены');
                }
            };

            loadUserData();

            // Привязка кошелька
            var modal = document.getElementById('bindModal');
            var currentNonce = null;
            var currentMessage = '';

            // Кнопка Привязать
            document.getElementById('bindWalletBtn').onclick = function() {
                if (!isTelegram || this.disabled) return;

                this.textContent = 'Загрузка...';
                this.disabled = true;

                var xhr = new XMLHttpRequest();
                xhr.open('POST', '/api/wallet/bind/nonce', true);
                xhr.setRequestHeader('Content-Type', 'application/json');
                xhr.setRequestHeader('X-Tg-Init-Data', tg.initData);
                xhr.onload = function() {
                    var btn = document.getElementById('bindWalletBtn');
                    btn.textContent = '🔗 Привязать кошелёк';
                    btn.disabled = false;

                    if (xhr.status === 200) {
                        var data = JSON.parse(xhr.responseText);
                        currentNonce = data.nonce;
                        currentMessage = data.message;
                        document.getElementById('messageToSign').value = currentMessage;
                        document.getElementById('walletAddress').value = '';
                        document.getElementById('signature').value = '';
                        modal.classList.add('active');
                    } else {
                        tg.showAlert('Ошибка сервера');
                    }
                };
                xhr.onerror = function() {
                    var btn = document.getElementById('bindWalletBtn');
                    btn.textContent = '🔗 Привязать кошелёк';
                    btn.disabled = false;
                    tg.showAlert('Нет связи с сервером');
                };
                xhr.send();
            };

            // Кнопка Отмена
            document.getElementById('cancelBindBtn').onclick = function() {
                modal.classList.remove('active');
            };

            // Кнопка Копировать
            document.getElementById('copyMessageBtn').onclick = function() {
                document.getElementById('messageToSign').select();
                document.execCommand('copy');
                tg.showAlert('Сообщение скопировано!');
            };

            // Кнопка Привязать (подтверждение)
            document.getElementById('confirmBindBtn').onclick = function() {
                var wallet = document.getElementById('walletAddress').value.trim();
                var sig = document.getElementById('signature').value.trim();

                if (!wallet || !sig) {
                    tg.showAlert('Заполните все поля');
                    return;
                }

                var btn = this;
                btn.textContent = 'Отправка...';
                btn.disabled = true;

                var xhr = new XMLHttpRequest();
                xhr.open('POST', '/api/wallet/bind/verify', true);
                xhr.setRequestHeader('Content-Type', 'application/json');
                xhr.setRequestHeader('X-Tg-Init-Data', tg.initData);
                xhr.onload = function() {
                    btn.textContent = '✅ Привязать';
                    btn.disabled = false;

                    if (xhr.status === 200) {
                        tg.showAlert('✅ Кошелёк привязан! Баланс обновлён.');
                        modal.classList.remove('active');
                        loadUserData();
                    } else {
                        var err = JSON.parse(xhr.responseText);
                        tg.showAlert('Ошибка: ' + (err.detail || 'Не удалось'));
                    }
                };
                xhr.onerror = function() {
                    btn.textContent = '✅ Привязать';
                    btn.disabled = false;
                    tg.showAlert('Нет связи с сервером');
                };
                xhr.send(JSON.stringify({
                    wallet_address: wallet,
                    signature: sig,
                    nonce: currentNonce,
                    message: currentMessage
                }));
            };
        </script>
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
    ).options(selectinload(Nonce.user))
)
    nonce_row = res.scalar_one_or_none()
    if not nonce_row or nonce_row.expires_at.replace(tzinfo=UTC) < _now_utc():
        await _audit(session, user.telegram_id, "bind_failed", {"reason": "nonce_invalid_or_expired"})
        await session.commit()
        raise HTTPException(status_code=400, detail="Nonce invalid or expired")

    if not verify_evm_signature(message=message, signature=signature, expected_address=wallet_address):
        await _audit(session, user.telegram_id, "bind_failed", {"reason": "signature_invalid"})
        await session.commit()
        raise HTTPException(status_code=400, detail="Signature invalid")

    # ensure wallet isn't already bound to someone else
    res2 = await session.execute(
    select(WalletBinding)
    .where(WalletBinding.wallet_address == wallet_address)
    .options(selectinload(WalletBinding.user))
)
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

    # Отправляем уведомление пользователю в Telegram
    initial_balance = user.profile.fa_balance if user.profile else settings.initial_fa_balance
    await send_telegram_message(
        user.telegram_id,
        f"🎉 Кошелёк привязан! Ваш адрес: {wallet_address}\nНа ваш баланс зачислено {initial_balance} FA."
    )

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