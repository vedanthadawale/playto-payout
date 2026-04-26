function fmt(paise) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
  }).format(paise / 100);
}

function Card({ label, value, sub, color = "blue", icon }) {
  const colorMap = {
    blue: "bg-blue-50 text-blue-600 border-blue-100",
    green: "bg-green-50 text-green-600 border-green-100",
    amber: "bg-amber-50 text-amber-600 border-amber-100",
    purple: "bg-purple-50 text-purple-600 border-purple-100",
  };

  return (
    <div className={`rounded-2xl border p-6 ${colorMap[color]} bg-opacity-60`}>
      <div className="flex items-start justify-between mb-3">
        <p className="text-sm font-medium opacity-70">{label}</p>
        <span className="text-xl">{icon}</span>
      </div>
      <p className="text-2xl font-bold tracking-tight">{value}</p>
      {sub && <p className="text-xs mt-1 opacity-60">{sub}</p>}
    </div>
  );
}

export default function BalanceCards({ dashboard }) {
  const totalCredits = dashboard.recent_entries
    .filter((e) => e.entry_type === "credit")
    .reduce((sum, e) => sum + e.amount_paise, 0);

  const totalDebits = dashboard.recent_entries
    .filter((e) => e.entry_type === "debit")
    .reduce((sum, e) => sum + e.amount_paise, 0);

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <Card
        label="Available Balance"
        value={fmt(dashboard.available_paise)}
        sub="Ready to withdraw"
        color="green"
        icon="💰"
      />
      <Card
        label="Held Balance"
        value={fmt(dashboard.held_paise)}
        sub="Pending / Processing payouts"
        color="amber"
        icon="⏳"
      />
      <Card
        label="Total Received"
        value={fmt(totalCredits)}
        sub="Recent credits"
        color="blue"
        icon="📥"
      />
      <Card
        label="Total Paid Out"
        value={fmt(totalDebits)}
        sub="Recent debits"
        color="purple"
        icon="📤"
      />
    </div>
  );
}
