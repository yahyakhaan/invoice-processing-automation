from __future__ import annotations

import hashlib
import json
from pathlib import Path

from invoice_agent import storage
from invoice_agent.config import Settings


def mock_payment(vendor: str, amount: float) -> dict:
    print(f"Paid {amount} to {vendor}")
    return {"status": "success"}


def payment_key(invoice_number: str, revision: str | None, vendor: str, amount: float) -> str:
    raw = f"{invoice_number}|{revision or ''}|{vendor}|{amount:.2f}"
    return hashlib.sha256(raw.encode()).hexdigest()


def execute_payment(
    *,
    vendor: str,
    amount: float,
    invoice_number: str,
    revision: str | None,
    settings: Settings,
    fail_once: bool = False,
) -> dict:
    key = payment_key(invoice_number, revision, vendor, amount)
    conn = storage.connect(Path(settings.inventory_db))
    try:
        existing = storage.get_payment_attempt(conn, key)
        if existing and existing.get("status") == "success":
            return {
                "status": "success",
                "idempotency_key": key,
                "vendor": vendor,
                "amount": amount,
                "attempts": 1,
                "response": json.loads(existing["response"]),
                "replayed": True,
            }
        attempts = 0
        last_error = None
        while attempts < 2:
            attempts += 1
            try:
                if fail_once and attempts == 1:
                    raise RuntimeError("transient mock payment failure")
                response = mock_payment(vendor, amount)
                storage.record_payment_attempt(
                    conn,
                    key=key,
                    invoice_number=invoice_number,
                    amount=amount,
                    vendor=vendor,
                    status="success",
                    response=response,
                )
                return {
                    "status": "success",
                    "idempotency_key": key,
                    "vendor": vendor,
                    "amount": amount,
                    "attempts": attempts,
                    "response": response,
                    "replayed": False,
                }
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
        storage.record_payment_attempt(
            conn,
            key=key,
            invoice_number=invoice_number,
            amount=amount,
            vendor=vendor,
            status="failed",
            response={"error": last_error},
        )
        return {
            "status": "failed",
            "idempotency_key": key,
            "vendor": vendor,
            "amount": amount,
            "attempts": attempts,
            "response": {"error": last_error},
        }
    finally:
        conn.close()
