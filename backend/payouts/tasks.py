import logging
import random
import time

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from .models import Payout, LedgerEntry, IdempotencyKey, InvalidTransitionError

logger = logging.getLogger(__name__)

# Bank simulation weights: 70% success, 20% failure, 10% hung/processing
BANK_OUTCOME_CHOICES = ["success", "failure", "processing"]
BANK_OUTCOME_WEIGHTS = [70, 20, 10]

# Payout stuck threshold: retry if in PROCESSING longer than this
STUCK_THRESHOLD_SECONDS = 30


@shared_task(name="payouts.tasks.process_pending_payouts")
def process_pending_payouts():
    """
    Periodic beat task: picks up all PENDING payouts and dispatches
    each to its own worker task. This is the scheduler; process_single_payout
    does the actual work with proper locking.
    """
    pending_ids = list(
        Payout.objects.filter(status=Payout.PENDING).values_list("id", flat=True)
    )
    logger.info("Dispatching %d pending payouts", len(pending_ids))
    for payout_id in pending_ids:
        process_single_payout.apply_async(args=[str(payout_id)])
    return len(pending_ids)


@shared_task(name="payouts.tasks.retry_stuck_payouts")
def retry_stuck_payouts():
    """
    Periodic beat task: finds payouts that have been PROCESSING for more than
    STUCK_THRESHOLD_SECONDS and either retries or fails them permanently.

    A payout gets stuck in PROCESSING when the simulated bank API returns
    'processing' (the 10% hang case). This task is the recovery mechanism.
    """
    cutoff = timezone.now() - timedelta(seconds=STUCK_THRESHOLD_SECONDS)
    stuck_payouts = Payout.objects.filter(
        status=Payout.PROCESSING,
        processing_started_at__lt=cutoff,
    )

    retried = 0
    failed = 0
    for payout in stuck_payouts:
        if payout.attempts >= payout.max_attempts:
            # Exhausted retries — fail permanently and return funds
            _fail_payout_and_return_funds(payout.id, "Maximum retry attempts exceeded")
            failed += 1
        else:
            # More attempts remaining — reset to PENDING so it gets picked up
            _reset_to_pending_for_retry(payout.id)
            retried += 1

    logger.info("Stuck payout sweep: %d retried, %d failed", retried, failed)
    return {"retried": retried, "failed": failed}


@shared_task(name="payouts.tasks.cleanup_expired_idempotency_keys")
def cleanup_expired_idempotency_keys():
    """Hourly cleanup of expired idempotency keys to keep the table small."""
    deleted, _ = IdempotencyKey.objects.filter(
        expires_at__lt=timezone.now()
    ).delete()
    logger.info("Cleaned up %d expired idempotency keys", deleted)
    return deleted


@shared_task(
    name="payouts.tasks.process_single_payout",
    bind=True,
    # Celery-level retries are a safety net; the main retry loop is in retry_stuck_payouts
    max_retries=3,
    default_retry_delay=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def process_single_payout(self, payout_id: str):
    """
    Core worker: advances a single PENDING payout to PROCESSING,
    then simulates the bank API call, and settles the outcome.

    Concurrency note: SELECT FOR UPDATE inside the transition block ensures
    two workers cannot race to process the same payout.
    """
    logger.info("Processing payout %s (attempt %d)", payout_id, self.request.retries + 1)

    # ── Phase 1: Transition PENDING → PROCESSING ─────────────────────────────
    with transaction.atomic():
        try:
            payout = Payout.objects.select_for_update(nowait=True).get(id=payout_id)
        except Payout.DoesNotExist:
            logger.warning("Payout %s not found", payout_id)
            return
        except Exception:
            logger.warning("Could not lock payout %s, skipping", payout_id)
            return

        if payout.status != Payout.PENDING:
            logger.info("Payout %s is %s, skipping", payout_id, payout.status)
            return

        try:
            payout.transition_to(Payout.PROCESSING)
        except InvalidTransitionError as exc:
            logger.error("Invalid transition for payout %s: %s", payout_id, exc)
            return

        payout.processing_started_at = timezone.now()
        payout.attempts += 1
        payout.save(update_fields=["processing_started_at", "attempts", "updated_at"])

    # ── Phase 2: Simulate bank API call (outside transaction) ────────────────
    #
    # We deliberately do the "bank call" outside a transaction.
    # Real bank APIs are slow and we don't want to hold a DB lock
    # while waiting for an external HTTP response.
    #
    time.sleep(random.uniform(0.5, 2.0))  # Simulate network latency

    outcome = random.choices(
        BANK_OUTCOME_CHOICES, weights=BANK_OUTCOME_WEIGHTS, k=1
    )[0]

    logger.info("Payout %s bank outcome: %s", payout_id, outcome)

    # ── Phase 3: Settle based on outcome ─────────────────────────────────────
    if outcome == "success":
        _complete_payout(payout_id)

    elif outcome == "failure":
        _fail_payout_and_return_funds(payout_id, "Bank settlement declined")

    else:
        # "processing" — bank said "pending, check back later"
        # We leave it in PROCESSING. retry_stuck_payouts will handle it
        # after STUCK_THRESHOLD_SECONDS.
        logger.info(
            "Payout %s left in PROCESSING state (bank pending), "
            "retry_stuck_payouts will handle it",
            payout_id,
        )


def _complete_payout(payout_id: str) -> None:
    """Atomically transition payout to COMPLETED."""
    with transaction.atomic():
        try:
            payout = Payout.objects.select_for_update(nowait=True).get(id=payout_id)
        except Exception:
            logger.warning("Could not lock payout %s for completion", payout_id)
            return

        if payout.status != Payout.PROCESSING:
            logger.info(
                "Payout %s is %s, not PROCESSING — skipping completion",
                payout_id, payout.status,
            )
            return

        try:
            payout.transition_to(Payout.COMPLETED)
        except InvalidTransitionError as exc:
            logger.error("Cannot complete payout %s: %s", payout_id, exc)
            return

    logger.info("Payout %s COMPLETED", payout_id)


def _fail_payout_and_return_funds(payout_id: str, reason: str) -> None:
    """
    Atomically:
      1. Transition payout to FAILED
      2. Credit the ledger to return the held funds to the merchant

    Both happen in a single transaction. If either fails, neither commits.
    This ensures the invariant: failed payout ↔ funds returned.
    """
    with transaction.atomic():
        try:
            payout = Payout.objects.select_for_update(nowait=True).get(id=payout_id)
        except Exception:
            logger.warning("Could not lock payout %s for failure", payout_id)
            return

        if payout.status not in (Payout.PENDING, Payout.PROCESSING):
            logger.info(
                "Payout %s is already %s, cannot fail", payout_id, payout.status
            )
            return

        try:
            # This uses transition_to which enforces the state machine
            payout.transition_to(Payout.FAILED)
        except InvalidTransitionError as exc:
            logger.error("Cannot fail payout %s: %s", payout_id, exc)
            return

        payout.failure_reason = reason
        payout.save(update_fields=["failure_reason", "updated_at"])

        # Return the held funds — atomic with the status transition
        LedgerEntry.objects.create(
            merchant=payout.merchant,
            entry_type=LedgerEntry.CREDIT,
            amount_paise=payout.amount_paise,
            description=f"Refund for failed payout — {payout.id} ({reason})",
            payout=payout,
        )

    logger.info("Payout %s FAILED, ₹%.2f returned to merchant", payout_id, payout.amount_paise / 100)


def _reset_to_pending_for_retry(payout_id: str) -> None:
    """
    Reset a stuck PROCESSING payout back to PENDING so the scheduler
    picks it up again. Uses exponential backoff via Celery countdown.
    """
    with transaction.atomic():
        try:
            payout = Payout.objects.select_for_update(nowait=True).get(id=payout_id)
        except Exception:
            return

        if payout.status != Payout.PROCESSING:
            return

        # Manually bypass the state machine for retry reset:
        # PROCESSING → PENDING is normally invalid (it's a backwards move)
        # but we need it here to re-queue. We bypass transition_to intentionally.
        payout.status = Payout.PENDING
        payout.processing_started_at = None
        payout.save(update_fields=["status", "processing_started_at", "updated_at"])

    # Schedule with exponential backoff: 2^attempts seconds
    delay = min(2 ** payout.attempts, 60)
    process_single_payout.apply_async(args=[payout_id], countdown=delay)
    logger.info(
        "Payout %s reset to PENDING for retry (attempt %d, delay %ds)",
        payout_id, payout.attempts + 1, delay,
    )
