import logging
import json
from datetime import timedelta

from django.db import transaction, OperationalError
from django.db.models import Sum, Case, When, F, Value, BigIntegerField
from django.db.models.functions import Coalesce
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import (
    Merchant,
    Payout,
    LedgerEntry,
    IdempotencyKey,
    BankAccount,
    InvalidTransitionError,
)
from .serializers import (
    PayoutSerializer,
    CreatePayoutSerializer,
    MerchantDashboardSerializer,
    MerchantListSerializer,
)

logger = logging.getLogger(__name__)

IDEMPOTENCY_KEY_TTL_HOURS = 24


def _get_merchant(request) -> Merchant | None:
    """Minimal auth: merchant identified by X-Merchant-Id header."""
    merchant_id = request.headers.get("X-Merchant-Id")
    if not merchant_id:
        return None
    try:
        return Merchant.objects.get(id=merchant_id)
    except (Merchant.DoesNotExist, ValueError):
        return None


class MerchantListView(APIView):
    def get(self, request):
        merchants = Merchant.objects.prefetch_related("bank_accounts").all()
        return Response(MerchantListSerializer(merchants, many=True).data)


class MerchantDashboardView(APIView):
    def get(self, request, merchant_id):
        try:
            merchant = Merchant.objects.prefetch_related("bank_accounts").get(
                id=merchant_id
            )
        except (Merchant.DoesNotExist, ValueError):
            return Response({"error": "Merchant not found"}, status=404)

        return Response(MerchantDashboardSerializer(merchant).data)


class PayoutListView(APIView):
    def get(self, request):
        merchant = _get_merchant(request)
        if not merchant:
            return Response({"error": "X-Merchant-Id header required"}, status=400)

        payouts = Payout.objects.filter(merchant=merchant).select_related("bank_account")
        return Response(PayoutSerializer(payouts, many=True).data)


class PayoutDetailView(APIView):
    def get(self, request, payout_id):
        merchant = _get_merchant(request)
        if not merchant:
            return Response({"error": "X-Merchant-Id header required"}, status=400)
        try:
            payout = Payout.objects.get(id=payout_id, merchant=merchant)
        except (Payout.DoesNotExist, ValueError):
            return Response({"error": "Payout not found"}, status=404)

        return Response(PayoutSerializer(payout).data)


class CreatePayoutView(APIView):
    """
    POST /api/v1/payouts/

    Required headers:
        Idempotency-Key: <merchant-supplied UUID>
        X-Merchant-Id: <merchant UUID>

    Body:
        { "amount_paise": <int>, "bank_account_id": "<uuid>" }

    Critical properties guaranteed:
    1. Idempotency  — same key → same response, no duplicate payout
    2. Concurrency  — SELECT FOR UPDATE NOWAIT on merchant row prevents overdraw
    3. Atomicity    — payout + ledger debit created in one transaction
    4. State machine— payout starts in PENDING only
    """

    def post(self, request):
        idempotency_key_val = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key_val:
            return Response(
                {"error": "Idempotency-Key header is required"}, status=400
            )

        merchant = _get_merchant(request)
        if not merchant:
            return Response({"error": "X-Merchant-Id header is required or invalid"}, status=400)

        now = timezone.now()
        expires_at = now + timedelta(hours=IDEMPOTENCY_KEY_TTL_HOURS)

        # ── Step 1: Claim or fetch the idempotency key ──────────────────────────
        #
        # get_or_create uses the DB unique_together (key, merchant) constraint.
        # If two requests race here, only one INSERT succeeds; the other falls
        # back to SELECT. No duplicate key rows are ever created.
        #
        try:
            idem_key, created = IdempotencyKey.objects.get_or_create(
                key=idempotency_key_val,
                merchant=merchant,
                defaults={
                    "locked": True,     # In-flight sentinel
                    "response_body": {},
                    "response_status": 0,
                    "expires_at": expires_at,
                },
            )
        except Exception as exc:
            logger.exception("get_or_create failed for idempotency key: %s", exc)
            return Response({"error": "Internal error"}, status=500)

        if not created:
            # Key already exists — check its state
            if idem_key.is_expired():
                # Expired key: delete and allow this request to proceed as new
                idem_key.delete()
                idem_key = IdempotencyKey.objects.create(
                    key=idempotency_key_val,
                    merchant=merchant,
                    locked=True,
                    response_body={},
                    response_status=0,
                    expires_at=expires_at,
                )
            elif idem_key.locked:
                # Another request with this key is still in flight
                return Response(
                    {"error": "A request with this idempotency key is already in progress. Retry in a moment."},
                    status=409,
                )
            else:
                # Key settled — replay the original response exactly
                logger.info(
                    "Idempotency hit for key=%s merchant=%s → replaying %d",
                    idempotency_key_val, merchant.id, idem_key.response_status,
                )
                return Response(idem_key.response_body, status=idem_key.response_status)

        # ── Step 2: Validate input ───────────────────────────────────────────────
        input_ser = CreatePayoutSerializer(data=request.data)
        if not input_ser.is_valid():
            self._settle_idempotency_key(idem_key, input_ser.errors, 400)
            return Response(input_ser.errors, status=400)

        amount_paise = input_ser.validated_data["amount_paise"]
        bank_account_id = input_ser.validated_data["bank_account_id"]

        try:
            bank_account = BankAccount.objects.get(
                id=bank_account_id, merchant=merchant, is_active=True
            )
        except (BankAccount.DoesNotExist, ValueError):
            err = {"error": "Bank account not found or inactive"}
            self._settle_idempotency_key(idem_key, err, 404)
            return Response(err, status=404)

        # ── Step 3: Check balance and create payout atomically ───────────────────
        #
        # SELECT FOR UPDATE NOWAIT on the Merchant row is the concurrency lock.
        #
        # Why Merchant and not LedgerEntry?
        #   - Locking individual ledger rows is insufficient: new rows don't exist
        #     yet, so a "phantom read" race is possible.
        #   - Locking Merchant serializes all payout requests for a given merchant.
        #   - NOWAIT means: if another transaction holds the lock, fail immediately
        #     (no queuing) so the client gets a fast 503 to retry.
        #
        # Why DB-level aggregation?
        #   - Fetching balance to Python first then checking creates a TOCTOU window.
        #   - The SUM runs inside the same serialized transaction so it sees the
        #     committed state at the moment the lock is held.
        #
        try:
            with transaction.atomic():
                # Acquire merchant-level row lock — raises OperationalError if busy
                try:
                    locked_merchant = Merchant.objects.select_for_update(
                        nowait=True
                    ).get(id=merchant.id)
                except OperationalError:
                    err = {
                        "error": "Another payout is being processed. Please retry in a moment."
                    }
                    self._settle_idempotency_key(idem_key, err, 503)
                    return Response(err, status=503)

                # Balance computed entirely at the database — not in Python
                balance = LedgerEntry.objects.filter(
                    merchant=locked_merchant
                ).aggregate(
                    balance=Coalesce(
                        Sum(
                            Case(
                                When(
                                    entry_type=LedgerEntry.CREDIT,
                                    then=F("amount_paise"),
                                ),
                                When(
                                    entry_type=LedgerEntry.DEBIT,
                                    then=-F("amount_paise"),
                                ),
                                output_field=BigIntegerField(),
                            )
                        ),
                        Value(0),
                        output_field=BigIntegerField(),
                    )
                )["balance"]

                if balance < amount_paise:
                    err = {
                        "error": "Insufficient balance",
                        "available_paise": balance,
                        "available_rupees": round(balance / 100, 2),
                        "requested_paise": amount_paise,
                    }
                    self._settle_idempotency_key(idem_key, err, 422)
                    return Response(err, status=422)

                # Create the payout record
                payout = Payout.objects.create(
                    merchant=locked_merchant,
                    bank_account=bank_account,
                    amount_paise=amount_paise,
                    status=Payout.PENDING,
                )

                # Immediately debit the ledger — funds are now "held"
                # The balance will show the deduction; held_paise tracks what's in-flight
                LedgerEntry.objects.create(
                    merchant=locked_merchant,
                    entry_type=LedgerEntry.DEBIT,
                    amount_paise=amount_paise,
                    description=f"Payout hold — {payout.id}",
                    payout=payout,
                )

                response_data = json.loads(json.dumps(PayoutSerializer(payout).data, default=str))

                # Settle the idempotency key atomically with the payout creation
                idem_key.payout = payout
                idem_key.response_body = response_data
                idem_key.response_status = 201
                idem_key.locked = False
                idem_key.save(
                    update_fields=["payout", "response_body", "response_status", "locked"]
                )

                logger.info(
                    "Payout created: %s merchant=%s amount_paise=%d",
                    payout.id, merchant.id, amount_paise,
                )

                # Trigger background processing
                from .tasks import process_single_payout
                process_single_payout.apply_async(
                    args=[str(payout.id)], countdown=2
                )

                return Response(response_data, status=201)

        except Exception as exc:
            # Ensure idempotency key is unlocked on unexpected errors
            try:
                err = {"error": "Internal server error"}
                self._settle_idempotency_key(idem_key, err, 500)
            except Exception:
                pass
            logger.exception("Unexpected error creating payout: %s", exc)
            raise

    def _settle_idempotency_key(self, idem_key: IdempotencyKey, body: dict, status_code: int):
        """Unlock the in-flight key and record the response."""
        try:
            idem_key.response_body = body
            idem_key.response_status = status_code
            idem_key.locked = False
            idem_key.save(update_fields=["response_body", "response_status", "locked"])
        except Exception as exc:
            logger.error("Failed to settle idempotency key %s: %s", idem_key.key, exc)
