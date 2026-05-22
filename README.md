Состав:
- Telegram-бот (команды + уведомления)
- Mini App (Telegram Web App) с личным кабинетом: баланс FA-токенов, статусы, привязка кошелька
- Привязка `telegram_id -> wallet_address` через `nonce + подпись` (EVM / eth_sign)
- Админ-рассылки по сегментам (привязанные / не привязанные / все)
- Логирование ключевых действий
- Короткая политика приватности

---

## 1) Требования

- Windows 10/11
- Python 3.10+ (рекомендуется 3.11/3.12)
- Аккаунт Telegram + BotFather

---

## 2) Установка

В PowerShell:

```powershell
cd "C:\Users\goodb\Desktop\Bot_Y\student_telegram_mvp"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## 3) Настройка окружения

Скопируйте пример:

```powershell
copy .env.example .env
```

Откройте `.env` и заполните значения:
- `TELEGRAM_BOT_TOKEN` — токен бота
- `MINI_APP_URL` — публичный HTTPS URL до `/miniapp` (пример: `https://xxxx.ngrok-free.app/miniapp`)
- `ADMIN_TELEGRAM_IDS` — Telegram user id админов через запятую (пример: `12345,67890`)

Опционально:
- `BIND_NONCE_TTL_SECONDS` (по умолчанию 600)
- `INITIAL_FA_BALANCE` (по умолчанию 1000)
- `DATABASE_URL` (по умолчанию `sqlite+aiosqlite:///./data/app.db`)

---

## 4) Публичный HTTPS (для Mini App)

Mini App должен открываться по HTTPS.

Самый быстрый вариант — ngrok:

```powershell
ngrok http 8000
```

Возьмите выданный HTTPS домен и выставьте:
- `MINI_APP_URL="https://ВАШ_ДОМЕН.ngrok-free.app/miniapp"`

---

## 5) Настройка BotFather (Web App)

В BotFather:
- откройте настройки вашего бота
- добавьте Web App URL, который **совпадает** с `MINI_APP_URL`

---

## 6) Запуск (2 терминала)

Терминал 1 — API + Mini App:

```powershell
cd "C:\Users\goodb\Desktop\Bot_Y\student_telegram_mvp"
.\.venv\Scripts\Activate.ps1
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Терминал 2 — Telegram-бот:

```powershell
cd "C:\Users\goodb\Desktop\Bot_Y\student_telegram_mvp"
.\.venv\Scripts\Activate.ps1
python -m app.telegram_bot
```

---

## 7) Проверка функционала

### Mini App
- Откройте бота в Telegram → нажмите кнопку «Личный кабинет» (Web App)
- Убедитесь, что открылась страница и показывает Telegram user info

### Привязка кошелька (nonce + подпись)
В Mini App:
1) Нажмите «Привязать кошелёк»
2) Получите nonce (кнопка/автоматически)
3) Подпишите сообщение кошельком (Metamask)
4) Отправьте подпись → сервер должен привязать адрес и начислить стартовый баланс

### Рассылки (админ)
Админ (его id в `ADMIN_TELEGRAM_IDS`) может:
- `/broadcast` → выбрать сегмент и отправить текст

---

## 8) Политика приватности

Страница: `/privacy`

---

## 9) Структура проекта

```
student_telegram_mvp/
  app/
    __init__.py
    server.py
    settings.py
    db.py
    models.py
    security.py
    telegram_bot.py
    templates/
      miniapp.html
      privacy.html
  data/               # создаётся автоматически (SQLite)
  requirements.txt
  .env.example
```

