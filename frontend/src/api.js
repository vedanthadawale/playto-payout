const BASE_URL = import.meta.env.VITE_API_URL
  ? `${import.meta.env.VITE_API_URL}/api/v1`
  : "/api/v1";

async function request(path, options = {}) {
  const { headers: extraHeaders = {}, ...restOptions } = options;
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...extraHeaders },
    ...restOptions,
  });
  const data = await res.json();
  if (!res.ok) throw { status: res.status, data };
  return data;
}

export const api = {
  getMerchants: () => request("/merchants/"),

  getMerchantDashboard: (merchantId) =>
    request(`/merchants/${merchantId}/`),

  createPayout: (merchantId, { amount_paise, bank_account_id, idempotencyKey }) =>
    request("/payouts/", {
      method: "POST",
      headers: {
        "X-Merchant-Id": merchantId,
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({ amount_paise, bank_account_id }),
    }),

  getPayouts: (merchantId) =>
    request("/payouts/list/", {
      headers: { "X-Merchant-Id": merchantId },
    }),

  getPayoutDetail: (merchantId, payoutId) =>
    request(`/payouts/${payoutId}/`, {
      headers: { "X-Merchant-Id": merchantId },
    }),
};
