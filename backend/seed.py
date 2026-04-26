#!/usr/bin/env python
"""
Seed script. Run with: python seed.py
(from the backend/ directory with DJANGO_SETTINGS_MODULE set)

Creates 3 merchants with realistic credit history and bank accounts.
"""

import os
import sys
import django
from decimal import Decimal

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from django.utils import timezone
from payouts.models import Merchant, BankAccount, LedgerEntry, Payout

MERCHANTS = [
    {
        "name": "Anika Design Studio",
        "email": "anika@designstudio.in",
        "bank": {
            "account_number": "50100234567890",
            "ifsc": "HDFC0001234",
            "account_name": "Anika Sharma",
        },
        "credits": [
            (150_000, "Payment from Acme Corp USA — Invoice #1042"),
            (85_000, "Payment from Globex EU — Invoice #1043"),
            (220_000, "Payment from Initech AU — Invoice #1044"),
            (60_000, "Payment from Umbrella UK — Invoice #1045"),
        ],
    },
    {
        "name": "Rajan Freelance Dev",
        "email": "rajan@devfolio.in",
        "bank": {
            "account_number": "20123456789012",
            "ifsc": "SBIN0001234",
            "account_name": "Rajan Patel",
        },
        "credits": [
            (500_000, "Upwork contract — NovaTech Inc — Oct 2025"),
            (250_000, "Direct contract — BuildCo CA — Nov 2025"),
            (175_000, "Upwork contract — NovaTech Inc — Dec 2025"),
            (320_000, "Toptal milestone — FinStart AI — Jan 2026"),
        ],
    },
    {
        "name": "Priya SaaS Exports",
        "email": "priya@saasexports.in",
        "bank": {
            "account_number": "00000011223344",
            "ifsc": "ICIC0001234",
            "account_name": "Priya Krishnan",
        },
        "credits": [
            (1_000_000, "Stripe Mirror — Feb 2026 batch"),
            (450_000, "Stripe Mirror — Mar 2026 batch"),
            (780_000, "Stripe Mirror — Apr 2026 batch"),
        ],
    },
]


def run():
    print("=" * 60)
    print("Playto Payout Engine — Seed Script")
    print("=" * 60)

    for mdata in MERCHANTS:
        merchant, created = Merchant.objects.get_or_create(
            email=mdata["email"],
            defaults={"name": mdata["name"]},
        )
        action = "Created" if created else "Already exists"
        print(f"\n[{action}] Merchant: {merchant.name} ({merchant.id})")

        # Bank account
        bank, _ = BankAccount.objects.get_or_create(
            merchant=merchant,
            account_number=mdata["bank"]["account_number"],
            defaults={
                "ifsc": mdata["bank"]["ifsc"],
                "account_name": mdata["bank"]["account_name"],
            },
        )
        print(f"  Bank Account: {bank.account_number} ({bank.ifsc})")

        # Credits
        existing_credits = LedgerEntry.objects.filter(
            merchant=merchant, entry_type=LedgerEntry.CREDIT
        ).count()

        if existing_credits == 0:
            for amount_paise, description in mdata["credits"]:
                LedgerEntry.objects.create(
                    merchant=merchant,
                    entry_type=LedgerEntry.CREDIT,
                    amount_paise=amount_paise,
                    description=description,
                )
                print(f"  + Credit: ₹{amount_paise / 100:,.2f} — {description}")
        else:
            print(f"  Skipping credits (already seeded: {existing_credits} entries)")

        balance = merchant.get_balance_paise()
        print(f"  → Current balance: ₹{balance / 100:,.2f}")

    print("\n" + "=" * 60)
    print("Seed complete. Summary:")
    for m in Merchant.objects.all():
        balance = m.get_balance_paise()
        bank = m.bank_accounts.first()
        print(
            f"  {m.name} | Balance: ₹{balance / 100:,.2f} "
            f"| Bank: {bank.account_number if bank else 'N/A'}"
        )
        print(f"    Merchant ID: {m.id}")
        print(f"    Bank Account ID: {bank.id if bank else 'N/A'}")
    print("=" * 60)


if __name__ == "__main__":
    run()
