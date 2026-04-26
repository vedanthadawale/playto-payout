import { useState, useEffect, useCallback } from "react";
import { api } from "./api";
import BalanceCards from "./components/BalanceCards";
import PayoutForm from "./components/PayoutForm";
import PayoutHistory from "./components/PayoutHistory";
import LedgerTable from "./components/LedgerTable";

export default function App() {
  const [merchants, setMerchants] = useState([]);
  const [selectedMerchantId, setSelectedMerchantId] = useState(null);
  const [dashboard, setDashboard] = useState(null);
  const [loadingMerchants, setLoadingMerchants] = useState(true);
  const [loadingDashboard, setLoadingDashboard] = useState(false);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);

  // Load merchants on mount
  useEffect(() => {
    api.getMerchants()
      .then((data) => {
        setMerchants(data);
        if (data.length > 0) setSelectedMerchantId(data[0].id);
      })
      .catch(() => setError("Failed to load merchants. Is the backend running?"))
      .finally(() => setLoadingMerchants(false));
  }, []);

  // Load dashboard when merchant changes
  const loadDashboard = useCallback(() => {
    if (!selectedMerchantId) return;
    setLoadingDashboard(true);
    api.getMerchantDashboard(selectedMerchantId)
      .then(setDashboard)
      .catch(() => setError("Failed to load dashboard"))
      .finally(() => setLoadingDashboard(false));
  }, [selectedMerchantId]);

  useEffect(() => {
    loadDashboard();
  }, [loadDashboard]);

  // Poll for live updates every 5 seconds
  useEffect(() => {
    if (!selectedMerchantId) return;
    const interval = setInterval(loadDashboard, 5000);
    return () => clearInterval(interval);
  }, [selectedMerchantId, loadDashboard]);

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  const handlePayoutSuccess = () => {
    showToast("Payout request submitted successfully!");
    loadDashboard();
  };

  const handlePayoutError = (msg) => {
    showToast(msg, "error");
  };

  if (loadingMerchants) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-brand-500 mx-auto mb-4" />
          <p className="text-gray-500">Loading merchants…</p>
        </div>
      </div>
    );
  }

  if (error && !dashboard) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="bg-white rounded-xl shadow p-8 max-w-md text-center">
          <div className="text-red-500 text-4xl mb-4">⚠️</div>
          <h2 className="text-xl font-semibold text-gray-800 mb-2">Connection Error</h2>
          <p className="text-gray-500">{error}</p>
          <button
            onClick={() => window.location.reload()}
            className="mt-4 px-4 py-2 bg-brand-500 text-white rounded-lg hover:bg-brand-600 transition"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const selectedMerchant = merchants.find((m) => m.id === selectedMerchantId);

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 bg-brand-500 rounded-lg flex items-center justify-center">
                <span className="text-white font-bold text-sm">₹</span>
              </div>
              <span className="font-bold text-gray-900 text-lg">Playto Pay</span>
              <span className="text-gray-400 text-sm hidden sm:block">Payout Engine</span>
            </div>

            {/* Merchant Selector */}
            <div className="flex items-center gap-3">
              <label className="text-sm text-gray-500 hidden sm:block">Merchant:</label>
              <select
                value={selectedMerchantId || ""}
                onChange={(e) => setSelectedMerchantId(e.target.value)}
                className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm text-gray-700 bg-white focus:outline-none focus:ring-2 focus:ring-brand-500"
              >
                {merchants.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
              {loadingDashboard && (
                <div className="w-4 h-4 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
              )}
            </div>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {dashboard && (
          <div className="space-y-8">
            {/* Merchant Name */}
            <div>
              <h1 className="text-2xl font-bold text-gray-900">{dashboard.name}</h1>
              <p className="text-gray-500 text-sm">{dashboard.email}</p>
            </div>

            {/* Balance cards */}
            <BalanceCards dashboard={dashboard} />

            {/* Two-column layout */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
              {/* Payout form */}
              <div className="lg:col-span-1">
                <PayoutForm
                  merchantId={selectedMerchantId}
                  bankAccounts={dashboard.bank_accounts}
                  availablePaise={dashboard.available_paise}
                  onSuccess={handlePayoutSuccess}
                  onError={handlePayoutError}
                />
              </div>

              {/* Payout history */}
              <div className="lg:col-span-2">
                <PayoutHistory
                  payouts={dashboard.recent_payouts}
                  onRefresh={loadDashboard}
                />
              </div>
            </div>

            {/* Ledger */}
            <LedgerTable entries={dashboard.recent_entries} />
          </div>
        )}
      </main>

      {/* Toast */}
      {toast && (
        <div
          className={`fixed bottom-4 right-4 z-50 px-5 py-3 rounded-xl shadow-lg text-white text-sm font-medium transition-all
            ${toast.type === "error" ? "bg-red-500" : "bg-green-500"}`}
        >
          {toast.type === "success" ? "✓ " : "✗ "}
          {toast.message}
        </div>
      )}
    </div>
  );
}
