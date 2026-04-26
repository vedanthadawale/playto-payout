"""
Idempotency tests.

Key behaviours verified:
1. Same (merchant, key) pair → same response, no duplicate payout
2. Different keys → different payouts
3. Same key, different merchant → different payouts (key is merchant-scoped)
4. Expired key can be reused
5. Locked key (in-flight) returns 409
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, Client
from django.utils import timezone

from payouts.models import Merchant, BankAccount, LedgerEntry, Payout, IdempotencyKey


def _seed_merchant(name: str, email: str, balance_paise: int = 50_000) -> tuple:
    merchant = Merchant.objects.create(name=name, email=email)
    bank_account = BankAccount.objects.create(
        merchant=merchant,
        account_number="1234567890",
        ifsc="HDFC0001234",
        account_name=name,
    )
    LedgerEntry.objects.create(
        merchant=merchant,
        entry_type=LedgerEntry.CREDIT,
        amount_paise=balance_paise,
        description="Test seed credit",
    )
    return merchant, bank_account


class IdempotencyTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.merchant, self.bank_account = _seed_merchant(
            "Idem Merchant", "idem@test.com", balance_paise=100_000
        )

    def _post_payout(self, idempotency_key: str, amount_paise: int = 5_000, merchant=None, bank_account=None):
        m = merchant or self.merchant
        ba = bank_account or self.bank_account
        return self.client.post(
            "/api/v1/payouts/",
            data={
                "amount_paise": amount_paise,
                "bank_account_id": str(ba.id),
            },
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=idempotency_key,
            HTTP_X_MERCHANT_ID=str(m.id),
        )

    def test_same_key_returns_identical_response(self):
        """Second call with same key must return the same response — no new payout."""
        key = str(uuid.uuid4())

        r1 = self._post_payout(key)
        r2 = self._post_payout(key)

        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)

        # Exact same payout ID
        self.assertEqual(r1.json()["id"], r2.json()["id"])

        # Only one payout created in DB
        self.assertEqual(
            Payout.objects.filter(merchant=self.merchant).count(),
            1,
            "Duplicate idempotency key must not create a second payout",
        )

    def test_different_keys_create_different_payouts(self):
        """Two distinct keys must produce two distinct payouts."""
        r1 = self._post_payout(str(uuid.uuid4()))
        r2 = self._post_payout(str(uuid.uuid4()))

        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertNotEqual(r1.json()["id"], r2.json()["id"])
        self.assertEqual(Payout.objects.filter(merchant=self.merchant).count(), 2)

    def test_key_is_scoped_per_merchant(self):
        """
        The same idempotency key string used by two different merchants
        must produce two independent payouts — keys are not global.
        """
        merchant2, bank_account2 = _seed_merchant("Merchant Two", "two@test.com")

        shared_key = str(uuid.uuid4())

        r1 = self._post_payout(shared_key, merchant=self.merchant, bank_account=self.bank_account)
        r2 = self._post_payout(shared_key, merchant=merchant2, bank_account=bank_account2)

        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertNotEqual(r1.json()["id"], r2.json()["id"])

    def test_locked_key_returns_409(self):
        """
        If an idempotency key exists but is locked (first request still in-flight),
        the second request must get 409 Conflict — not a duplicate payout.
        """
        key = str(uuid.uuid4())

        # Manually insert a locked key (simulating in-flight first request)
        IdempotencyKey.objects.create(
            key=key,
            merchant=self.merchant,
            locked=True,
            response_body={},
            response_status=0,
            expires_at=timezone.now() + timedelta(hours=24),
        )

        response = self._post_payout(key)
        self.assertEqual(response.status_code, 409)
        self.assertIn("in progress", response.json()["error"])

    def test_expired_key_allows_new_payout(self):
        """
        After 24 hours the key expires and the same key string can be reused
        to create a new payout.
        """
        key = str(uuid.uuid4())

        # Create an expired idempotency key referencing a payout
        old_payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account=self.bank_account,
            amount_paise=1_000,
            status=Payout.COMPLETED,
        )
        IdempotencyKey.objects.create(
            key=key,
            merchant=self.merchant,
            payout=old_payout,
            locked=False,
            response_body={"id": str(old_payout.id)},
            response_status=201,
            expires_at=timezone.now() - timedelta(hours=1),  # Already expired
        )

        response = self._post_payout(key, amount_paise=2_000)
        self.assertEqual(response.status_code, 201)

        # Must be a NEW payout, not the old one
        self.assertNotEqual(response.json()["id"], str(old_payout.id))

    def test_idempotency_on_insufficient_balance(self):
        """
        A failed request (insufficient balance) is also idempotent —
        the same key must return the same 422 error on retry.
        """
        key = str(uuid.uuid4())

        r1 = self._post_payout(key, amount_paise=999_999_999)  # Way more than balance
        r2 = self._post_payout(key, amount_paise=999_999_999)

        self.assertEqual(r1.status_code, 422)
        self.assertEqual(r2.status_code, 422)
        self.assertEqual(r1.json(), r2.json())

    def test_missing_idempotency_key_header_returns_400(self):
        """Requests without Idempotency-Key must be rejected."""
        response = self.client.post(
            "/api/v1/payouts/",
            data={"amount_paise": 1000, "bank_account_id": str(self.bank_account.id)},
            content_type="application/json",
            HTTP_X_MERCHANT_ID=str(self.merchant.id),
            # No HTTP_IDEMPOTENCY_KEY
        )
        self.assertEqual(response.status_code, 400)
