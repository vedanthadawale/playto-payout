import { useEffect, useRef } from "react";

const STATUS_CONFIG = {
  pending: {
    label: "Pending",
    dot: "bg-yellow-400",
    badge: "bg-yellow-50 text-yellow-700 border-yellow-200",
    pulse: true,
  },
  processing: {
    label: "Processing",
    dot: "bg-blue-400",
    badge: "bg-blue-50 text-blue-700 border-blue-200",
    pulse: true,
  },
  completed: {
    label: "Completed",
    dot: "bg-green-400",
    badge: "bg-green-50 text-green-700 border-green-200",
    pulse: false,
  },
  failed: {
    label: "Failed",
    dot: "bg-red-400",
    badge: "bg-red-50 text-red-700 border-red-200",
    pulse: false,
  },
};

function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium border ${cfg.badge}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot} ${cfg.pulse ? "animate-pulse" : ""}`} />
      {cfg.label}
    </span>
  );
}

function fmt(paise) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
  }).format(paise / 100);
}

function timeAgo(dateStr) {
  const diff = Math.floor((Date.now() - new Date(dateStr)) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return new Date(dateStr).toLocaleDateString("en-IN");
}

export default function PayoutHistory({ payouts, onRefresh }) {
  // Auto-refresh indicator
  const hasLivePayouts = payouts?.some(
    (p) => p.status === "pending" || p.status === "processing"
  );

  return (
    <div className="bg-white rounded-2xl border border-gray-200 p-6">
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-base font-semibold text-gray-900">Payout History</h2>
        <div className="flex items-center gap-3">
          {hasLivePayouts && (
            <span className="text-xs text-blue-500 flex items-center gap-1">
              <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
              Live updates
            </span>
          )}
          <button
            onClick={onRefresh}
            className="text-xs text-gray-400 hover:text-gray-600 transition-colors"
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {!payouts || payouts.length === 0 ? (
        <div className="text-center py-12 text-gray-400">
          <p className="text-3xl mb-2">📭</p>
          <p className="text-sm">No payouts yet</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-gray-400 border-b border-gray-100">
                <th className="pb-2 text-left font-medium">Amount</th>
                <th className="pb-2 text-left font-medium">Status</th>
                <th className="pb-2 text-left font-medium hidden sm:table-cell">Bank</th>
                <th className="pb-2 text-left font-medium hidden md:table-cell">Attempts</th>
                <th className="pb-2 text-left font-medium">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {payouts.map((p) => (
                <tr key={p.id} className="hover:bg-gray-50 transition-colors">
                  <td className="py-3 pr-4">
                    <span className="font-semibold text-gray-900">{fmt(p.amount_paise)}</span>
                    <span className="block text-xs text-gray-400">{p.amount_paise.toLocaleString()} paise</span>
                  </td>
                  <td className="py-3 pr-4">
                    <StatusBadge status={p.status} />
                    {p.status === "failed" && p.failure_reason && (
                      <span className="block text-xs text-red-400 mt-0.5 max-w-[120px] truncate" title={p.failure_reason}>
                        {p.failure_reason}
                      </span>
                    )}
                  </td>
                  <td className="py-3 pr-4 hidden sm:table-cell">
                    <span className="text-gray-600 font-mono text-xs">
                      ****{p.bank_account_detail?.account_number?.slice(-4)}
                    </span>
                    <span className="block text-xs text-gray-400">{p.bank_account_detail?.ifsc}</span>
                  </td>
                  <td className="py-3 pr-4 hidden md:table-cell">
                    <span className="text-gray-500">
                      {p.attempts}/{p.max_attempts}
                    </span>
                  </td>
                  <td className="py-3 text-gray-400 text-xs whitespace-nowrap">
                    {timeAgo(p.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
