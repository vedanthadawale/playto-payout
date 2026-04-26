from rest_framework import serializers
from .models import Merchant, BankAccount, LedgerEntry, Payout


class BankAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankAccount
        fields = ["id", "account_number", "ifsc", "account_name", "is_active", "created_at"]


class LedgerEntrySerializer(serializers.ModelSerializer):
    amount_rupees = serializers.SerializerMethodField()

    class Meta:
        model = LedgerEntry
        fields = [
            "id",
            "entry_type",
            "amount_paise",
            "amount_rupees",
            "description",
            "payout",
            "created_at",
        ]

    def get_amount_rupees(self, obj):
        return round(obj.amount_paise / 100, 2)


class PayoutSerializer(serializers.ModelSerializer):
    amount_rupees = serializers.SerializerMethodField()
    bank_account_detail = BankAccountSerializer(source="bank_account", read_only=True)

    class Meta:
        model = Payout
        fields = [
            "id",
            "merchant",
            "bank_account",
            "bank_account_detail",
            "amount_paise",
            "amount_rupees",
            "status",
            "attempts",
            "max_attempts",
            "processing_started_at",
            "failure_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "merchant",
            "status",
            "attempts",
            "processing_started_at",
            "failure_reason",
            "created_at",
            "updated_at",
        ]

    def get_amount_rupees(self, obj):
        return round(obj.amount_paise / 100, 2)


class CreatePayoutSerializer(serializers.Serializer):
    amount_paise = serializers.IntegerField(min_value=1)
    bank_account_id = serializers.UUIDField()

    def validate_amount_paise(self, value):
        # Minimum payout: 1 rupee = 100 paise
        if value < 100:
            raise serializers.ValidationError("Minimum payout is ₹1 (100 paise).")
        return value


class MerchantDashboardSerializer(serializers.ModelSerializer):
    balance_paise = serializers.SerializerMethodField()
    balance_rupees = serializers.SerializerMethodField()
    held_paise = serializers.SerializerMethodField()
    held_rupees = serializers.SerializerMethodField()
    available_paise = serializers.SerializerMethodField()
    available_rupees = serializers.SerializerMethodField()
    bank_accounts = BankAccountSerializer(many=True, read_only=True)
    recent_entries = serializers.SerializerMethodField()
    recent_payouts = serializers.SerializerMethodField()

    class Meta:
        model = Merchant
        fields = [
            "id",
            "name",
            "email",
            "balance_paise",
            "balance_rupees",
            "held_paise",
            "held_rupees",
            "available_paise",
            "available_rupees",
            "bank_accounts",
            "recent_entries",
            "recent_payouts",
            "created_at",
        ]

    def get_balance_paise(self, obj):
        return obj.get_balance_paise()

    def get_balance_rupees(self, obj):
        return round(obj.get_balance_paise() / 100, 2)

    def get_held_paise(self, obj):
        return obj.get_held_paise()

    def get_held_rupees(self, obj):
        return round(obj.get_held_paise() / 100, 2)

    def get_available_paise(self, obj):
        return obj.get_balance_paise()

    def get_available_rupees(self, obj):
        return round(obj.get_balance_paise() / 100, 2)

    def get_recent_entries(self, obj):
        entries = obj.ledger_entries.select_related("payout").order_by("-created_at")[:20]
        return LedgerEntrySerializer(entries, many=True).data

    def get_recent_payouts(self, obj):
        payouts = obj.payouts.select_related("bank_account").order_by("-created_at")[:20]
        return PayoutSerializer(payouts, many=True).data


class MerchantListSerializer(serializers.ModelSerializer):
    balance_rupees = serializers.SerializerMethodField()

    class Meta:
        model = Merchant
        fields = ["id", "name", "email", "balance_rupees", "created_at"]

    def get_balance_rupees(self, obj):
        return round(obj.get_balance_paise() / 100, 2)
