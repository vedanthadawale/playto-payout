from django.contrib import admin
from .models import Merchant, BankAccount, LedgerEntry, Payout, IdempotencyKey


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "email", "created_at"]
    search_fields = ["name", "email"]


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ["id", "merchant", "account_number", "ifsc", "is_active"]
    list_filter = ["is_active"]


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ["id", "merchant", "entry_type", "amount_paise", "description", "created_at"]
    list_filter = ["entry_type"]
    search_fields = ["merchant__name", "description"]


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ["id", "merchant", "amount_paise", "status", "attempts", "created_at"]
    list_filter = ["status"]
    search_fields = ["merchant__name"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ["key", "merchant", "response_status", "locked", "expires_at"]
    list_filter = ["locked"]
