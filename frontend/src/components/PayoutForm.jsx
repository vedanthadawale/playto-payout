import { useState } from "react";
import { api } from "../api";

function generateIdempotencyKey() {
  return crypto.randomUUID();
}

function fmt(paise) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
  }).format(paise / 100);
}

export default function PayoutForm({ merchantId, bankAccounts, availablePaise, onSuccess, onError }) {
  const [amountRupees, setAmountRupees] = useState("");
  const [bankAccountId, setBankAccountId] = useState(
    bankAccounts.length > 0 ? bankAccounts[0].id : ""
  );
  const [loading, setLoading] = useState(false);
  const [fieldError, setFieldError] = useState("");

  const activeBanks = bankAccounts.filter((b) => b.is_active);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setFieldError("");

    const rupees = parseFloat(amountRupees);
    if (!amountRupees || isNaN(rupees) || rupees <= 0) {
      setFieldError("Enter a valid amount");
      return;
    }

    const amountPaise = Math.round(rupees * 100);

    if (amountPaise < 100) {
      setFieldError("Minimum payout is ₹1.00");
      return;
    }

    if (amountPaise > availablePaise) {
      setFieldError(`Exceeds available balance (${fmt(availablePaise)})`);
      return;
    }

    if (!bankAccountId) {
      setFieldError("Select a bank account");
      return;
    }

    setLoading(true);
    try {
      await api.createPayout(merchantId, {
        amount_paise: amountPaise,
        bank_account_id: bankAccountId,
        idempotencyKey: generateIdempotencyKey(),
      });
      setAmountRupees("");
      onSuccess();
    } catch (err) {
      const msg =
        err?.data?.error ||
        err?.data?.amount_paise?.[0] ||
        "Something went wrong";
      onError(msg);
    } finally {
      setLoading(false);
    }
  };

  const amountPaisePreview = Math.round(parseFloat(amountRupees || "0") * 100);

  return (
    <div className="bg-white rounded-2xl border border-gray-200 p-6">
      <h2 className="text-base font-semibold text-gray-900 mb-5">Request Payout</h2>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Amount */}
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1.5">
            Amount (₹)
          </label>
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 font-medium">
              ₹
            </span>
            <input
              type="number"
              step="0.01"
              min="1"
              value={amountRupees}
              onChange={(e) => {
                setAmountRupees(e.target.value);
                setFieldError("");
              }}
              placeholder="0.00"
              className="w-full pl-8 pr-4 py-2.5 border border-gray-200 rounded-lg text-gray-900 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          {amountPaisePreview > 0 && (
            <p className="text-xs text-gray-400 mt-1">{amountPaisePreview.toLocaleString()} paise</p>
          )}
        </div>

        {/* Bank Account */}
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1.5">
            Bank Account
          </label>
          {activeBanks.length === 0 ? (
            <p className="text-sm text-red-500">No active bank accounts</p>
          ) : (
            <select
              value={bankAccountId}
              onChange={(e) => setBankAccountId(e.target.value)}
              className="w-full border border-gray-200 rounded-lg px-3 py-2.5 text-sm text-gray-700 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {activeBanks.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.account_name} • ****{b.account_number.slice(-4)} • {b.ifsc}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Available balance hint */}
        <div className="bg-gray-50 rounded-lg px-3 py-2 flex justify-between items-center">
          <span className="text-xs text-gray-500">Available</span>
          <span className="text-xs font-semibold text-gray-700">{fmt(availablePaise)}</span>
        </div>

        {/* Error */}
        {fieldError && (
          <p className="text-xs text-red-500 bg-red-50 rounded-lg px-3 py-2">{fieldError}</p>
        )}

        {/* Submit */}
        <button
          type="submit"
          disabled={loading || activeBanks.length === 0}
          className="w-full py-2.5 px-4 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2"
        >
          {loading ? (
            <>
              <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              Submitting…
            </>
          ) : (
            "Request Payout"
          )}
        </button>
      </form>

      <p className="text-xs text-gray-400 mt-4 text-center">
        A unique idempotency key is generated per request automatically.
      </p>
    </div>
  );
}
