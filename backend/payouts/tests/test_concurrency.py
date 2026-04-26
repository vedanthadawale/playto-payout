"""
Concurrency test: two simultaneous payout requests that would together overdraw
the balance. Exactly one must succeed; the other must fail cleanly with 422.

We use TransactionTestCase (not TestCase) because:
- TestCase wraps everything in a single transaction that's rolled back at the end.
- That wrapper prevents SELECT FOR UPDATE from working across threads since
  other threads can't see rows that haven't been committed yet.
- TransactionTestCase commits and rolls back between tests the hard way,
  so multiple threads see each other's writes and the locking works correctly.
"""

import threading
import uuid

from django.test import TransactionTestCase, Client

from payouts.models import Merchant, BankAccount, LedgerEntry, Payout


class ConcurrentPayoutTest(TransactionTestCase):
    def setUp(self):
        self.merchant = Merchant.objects.create(
            name="Concurrent Test Merchant",
            email="concurrent@test.com",
        )
        self.bank_account = BankAccount.objects.create(
            merchant=self.merchant,
            account_number="9876543210",
            ifsc="SBIN0001234",
            account_name="Concurrent Test Merchant",
        )
        # Seed exactly 10,000 paise (₹100)
        LedgerEntry.objects.create(
            merchant=self.merchant,
            entry_type=LedgerEntry.CREDIT,
            amount_paise=10_000,
            description="Initial seed credit",
        )

    def _make_payout_request(self, amount_paise: int, results: list, index: int):
        """Called from a thread. Appends (status_code, json) to results."""
        client = Client()
        response = client.post(
            "/api/v1/payouts/",
            data={
                "amount_paise": amount_paise,
                "bank_account_id": str(self.bank_account.id),
            },
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),  # Unique key per request
            HTTP_X_MERCHANT_ID=str(self.merchant.id),
        )
        results[index] = (response.status_code, response.json())

    def test_two_concurrent_requests_cannot_overdraw(self):
        """
        Balance: ₹100 (10,000 paise)
        Request A: ₹60 (6,000 paise)
        Request B: ₹60 (6,000 paise)

        Together they require ₹120 > ₹100 available.
        Exactly one must succeed (201) and one must fail (422).
        """
        results = [None, None]

        threads = [
            threading.Thread(
                target=self._make_payout_request,
                args=(6_000, results, 0),
            ),
            threading.Thread(
                target=self._make_payout_request,
                args=(6_000, results, 1),
            ),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        status_codes = [r[0] for r in results]

        success_count = status_codes.count(201)
        failure_count = status_codes.count(422)

        self.assertEqual(
            success_count,
            1,
            f"Expected exactly 1 success (201). Got: {status_codes}. "
            f"Responses: {results}",
        )
        self.assertEqual(
            failure_count,
            1,
            f"Expected exactly 1 failure (422). Got: {status_codes}. "
            f"Responses: {results}",
        )

        # Verify DB state: only 1 payout created
        payouts = Payout.objects.filter(merchant=self.merchant)
        self.assertEqual(payouts.count(), 1)

        # Verify ledger integrity: balance should be 10,000 - 6,000 = 4,000 paise
        balance = self.merchant.get_balance_paise()
        self.assertEqual(
            balance,
            4_000,
            f"Expected balance 4000 paise after one successful 6000 paise payout. Got: {balance}",
        )

    def test_exactly_sufficient_balance_allows_one(self):
        """
        Two requests for the exact balance amount: only one can win.
        """
        results = [None, None]

        threads = [
            threading.Thread(
                target=self._make_payout_request,
                args=(10_000, results, 0),
            ),
            threading.Thread(
                target=self._make_payout_request,
                args=(10_000, results, 1),
            ),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        status_codes = [r[0] for r in results]
        success_count = status_codes.count(201)

        self.assertEqual(
            success_count,
            1,
            f"Exactly one request for the full balance should succeed. Got: {status_codes}",
        )

        # After the successful payout, balance must be zero — no overdraft
        balance = self.merchant.get_balance_paise()
        self.assertEqual(balance, 0, f"Balance should be 0 after full withdrawal. Got: {balance}")

    def test_sequential_payouts_that_sum_to_balance_both_succeed(self):
        """
        Sequential (non-concurrent) payouts for ₹40 + ₹60 = ₹100 should both succeed.
        This verifies the happy path still works after the locking is in place.
        """
        results = [None, None]
        self._make_payout_request(4_000, results, 0)
        self._make_payout_request(6_000, results, 1)

        self.assertEqual(results[0][0], 201)
        self.assertEqual(results[1][0], 201)
        self.assertEqual(Payout.objects.filter(merchant=self.merchant).count(), 2)
