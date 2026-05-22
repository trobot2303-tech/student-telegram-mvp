from __future__ import annotations

import hmac
import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from eth_account import Account
from eth_account.messages import encode_defunct


def new_nonce() -> str:
    # Short, URL-safe. Keep it displayable in wallets.
    return secrets.token_urlsafe(24)


def verify_evm_signature(message: str, signature: str, expected_address: str) -> bool:
    """
    Verifies an EVM personal_sign (eth_sign) signature.
    """
    try:
        msg = encode_defunct(text=message)
        recovered = Account.recover_message(msg, signature=signature)
        return recovered.lower() == expected_address.lower()
    except Exception:
        return False


@dataclass(frozen=True)
class TelegramWebAppUser:
    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None


def parse_init_data_user(init_data: str, bot_token: str) -> TelegramWebAppUser | None:
    """
    Validates Telegram WebApp initData (HMAC-SHA256) and extracts user.

    Ref: Telegram Web Apps authorization.
    """
    if not init_data:
        return None

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs.keys()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    # optional freshness check (if present)
    auth_date = pairs.get("auth_date")
    if auth_date and auth_date.isdigit():
        # allow 24h drift
        if int(time.time()) - int(auth_date) > 60 * 60 * 24:
            return None

    user_json = pairs.get("user")
    if not user_json:
        return None

    try:
        import json

        user = json.loads(user_json)
        return TelegramWebAppUser(
            id=int(user["id"]),
            first_name=user.get("first_name"),
            last_name=user.get("last_name"),
            username=user.get("username"),
        )
    except Exception:
        return None

