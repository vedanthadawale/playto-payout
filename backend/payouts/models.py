import uuid
import logging
from django.db import models
from django.db.models import Sum, Case, When, F, Value, BigIntegerField
from django.db.models.functions import Coalesce
from django.utils import timezone

logger = logging.getLogger(__name__)


class InvalidTransitionError(Exception):
    pass


class Merchant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "merchants"

    def __str__(self):
        return f"{self.name} ({self.email})"

    def get_balance_paise(self) -> int:
        """
        Derives balance entirely at the database level using a single aggregation.
        Credits add to balance, debits subtract. Never fetches rows into Python.
        """
        result = LedgerEntry.objects.filter(merchant=self).aggregate(
            balance=Coalesce(
                Sum(
                    Case(
                        When(entry_type=LedgerEntry.CREDIT, then=F("amount_paise")),
                        When(entry_type=LedgerEntry.DEBIT, then=-F("amount_paise")),
                        output_field=BigIntegerField(),
                    )
                ),
                Value(0),
                output_field=BigIntegerField(),
            )
        )
        return result["balance"]

    def get_held_paise(self) -> int:
        """
        Returns total paise currently locked by pending/processing payouts.
        These have already been debited from the ledger, so available = total balance.
        This is purely for display purposes to show the breakdown.
        """
        result = Payout.objects.filter(
            merchant=self,
            status__in=[Payout.PENDING, Payout.PROCESSING],
        ).aggregate(
            held=Coalesce(Sum("amount_paise"), Value(0), output_field=BigIntegerField())
        )
        return result["held"]


class BankAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="bank_accounts"
    )
    account_number = models.CharField(max_length=20)
    ifsc = models.CharField(max_length=11)
    account_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bank_accounts"

    def __str__(self):
        return f"{self.account_name} - {self.account_number[-4:].rjust(len(self.account_number), '*')}"


class LedgerEntry(models.Model):
    CREDIT = "credit"
    DEBIT = "debit"
    ENTRY_TYPE_CHOICES = [(CREDIT, "Credit"), (DEBIT, "Debit")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.PROTECT, related_name="ledger_entries"
    )
    entry_type = models.CharField(max_length=10, choices=ENTRY_TYPE_CHOICES)
    # BigIntegerField: amounts in paise. Never FloatField. Never DecimalField.
    amount_paise = models.BigIntegerField()
    description = models.CharField(max_length=500)
    # Nullable: credits from customers have no associated payout
    payout = models.ForeignKey(
        "Payout",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ledger_entries"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["merchant", "created_at"]),
            models.Index(fields=["payout"]),
        ]

    def __str__(self):
        sign = "+" if self.entry_type == self.CREDIT else "-"
        return f"{sign}₹{self.amount_paise / 100:.2f} ({self.description})"


class Payout(models.Model):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (PROCESSING, "Processing"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
    ]

    # Exhaustive state machine. Keys not present = terminal states with no valid exits.
    VALID_TRANSITIONS: dict[str, list[str]] = {
        PENDING: [PROCESSING],
        PROCESSING: [COMPLETED, FAILED],
        COMPLETED: [],  # Terminal — no exits
        FAILED: [],     # Terminal — no exits
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.PROTECT, related_name="payouts"
    )
    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT)
    # BigIntegerField in paise — never store money as float
    amount_paise = models.BigIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    attempts = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=3)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payouts"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["merchant", "status"]),
            models.Index(fields=["status", "processing_started_at"]),
        ]

    def __str__(self):
        return f"Payout {self.id} — ₹{self.amount_paise / 100:.2f} [{self.status}]"

    def transition_to(self, new_status: str) -> None:
        """
        Enforces the state machine. Any illegal transition raises InvalidTransitionError.
        This is the single choke point — nowhere else should status be set directly.
        """
        valid = self.VALID_TRANSITIONS.get(self.status, [])
        if new_status not in valid:
            raise InvalidTransitionError(
                f"Illegal transition: '{self.status}' → '{new_status}'. "
                f"Valid targets from '{self.status}': {valid}"
            )
        old_status = self.status
        self.status = new_status
        self.save(update_fields=["status", "updated_at"])
        logger.info("Payout %s: %s → %s", self.id, old_status, new_status)


class IdempotencyKey(models.Model):
    """
    Stores the first response for a (merchant, key) pair so repeat requests
    return the exact same response without re-executing business logic.
    Keys are scoped per merchant and expire after 24 hours.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # The merchant-supplied UUID
    key = models.CharField(max_length=255)
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE)
    payout = models.ForeignKey(
        Payout, on_delete=models.SET_NULL, null=True, blank=True
    )
    response_body = models.JSONField(default=dict)
    response_status = models.IntegerField(default=0)
    # True while the first request is still in flight; second callers get 409
    locked = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "idempotency_keys"
        # The database-level uniqueness constraint that makes get_or_create atomic
        unique_together = [("key", "merchant")]
        indexes = [
            models.Index(fields=["expires_at"]),
        ]

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at
