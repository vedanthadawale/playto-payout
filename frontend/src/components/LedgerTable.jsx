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

export default function LedgerTable({ entries }) {
  if (!entries || entries.length === 0) return null;

  let running = 0;
  // Calculate running balance (oldest to newest, then display newest first)
  const withRunning = [...entries].reverse().map((e) => {
    if (e.entry_type === "credit") running += e.amount_paise;
    else running -= e.amount_paise;
    return { ...e, running_balance: running };
  });
  const ordered = withRunning.reverse();

  return (
    <div className="bg-white rounded-2xl border border-gray-200 p-6">
      <h2 className="text-base font-semibold text-gray-900 mb-5">
        Ledger — Recent Entries
        <span className="ml-2 text-xs font-normal text-gray-400">
          (credits + debits in paise, integer only)
        </span>
      </h2>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-gray-400 border-b border-gray-100">
              <th className="pb-2 text-left font-medium">Type</th>
              <th className="pb-2 text-right font-medium">Amount</th>
              <th className="pb-2 text-right font-medium hidden md:table-cell">Balance After</th>
              <th className="pb-2 text-left font-medium pl-4">Description</th>
              <th className="pb-2 text-left font-medium hidden sm:table-cell">Time</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {ordered.map((entry) => (
              <tr key={entry.id} className="hover:bg-gray-50 transition-colors">
                <td className="py-3 pr-4">
                  <span
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium
                      ${entry.entry_type === "credit"
                        ? "bg-green-50 text-green-700"
                        : "bg-red-50 text-red-700"
                      }`}
                  >
                    {entry.entry_type === "credit" ? "▲ Credit" : "▼ Debit"}
                  </span>
                </td>
                <td className="py-3 pr-4 text-right">
                  <span
                    className={`font-semibold ${
                      entry.entry_type === "credit" ? "text-green-600" : "text-red-600"
                    }`}
                  >
                    {entry.entry_type === "credit" ? "+" : "−"}
                    {fmt(entry.amount_paise)}
                  </span>
                  <span className="block text-xs text-gray-400">
                    {entry.amount_paise.toLocaleString()} paise
                  </span>
                </td>
                <td className="py-3 pr-4 text-right hidden md:table-cell">
                  <span className="text-gray-600 font-mono text-xs">
                    {fmt(entry.running_balance)}
                  </span>
                </td>
                <td className="py-3 pl-4 pr-4 max-w-[200px]">
                  <span className="text-gray-600 text-xs truncate block" title={entry.description}>
                    {entry.description}
                  </span>
                </td>
                <td className="py-3 text-gray-400 text-xs hidden sm:table-cell whitespace-nowrap">
                  {timeAgo(entry.created_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
