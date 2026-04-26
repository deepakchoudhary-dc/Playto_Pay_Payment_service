import React, { useEffect, useMemo, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  Clock3,
  Landmark,
  RefreshCw,
  Send,
  Wallet,
  XCircle,
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api/v1";

function formatMoney(paise = 0) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  }).format(paise / 100);
}

function statusTone(status) {
  if (status === "completed") return "bg-emerald-50 text-emerald-700 ring-emerald-200";
  if (status === "failed") return "bg-rose-50 text-rose-700 ring-rose-200";
  if (status === "processing") return "bg-amber-50 text-amber-800 ring-amber-200";
  return "bg-sky-50 text-sky-700 ring-sky-200";
}

async function api(path, { merchantId, method = "GET", body, idempotencyKey } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (merchantId) headers["X-Merchant-Id"] = merchantId;
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) {
    const message = data.detail || data.idempotency_key || "Request failed";
    throw new Error(Array.isArray(message) ? message.join(", ") : message);
  }
  return data;
}

export default function App() {
  const [merchants, setMerchants] = useState([]);
  const [merchantId, setMerchantId] = useState("");
  const [summary, setSummary] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [payouts, setPayouts] = useState([]);
  const [amountRupees, setAmountRupees] = useState("");
  const [bankAccountId, setBankAccountId] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const selectedMerchant = useMemo(
    () => merchants.find((merchant) => merchant.id === merchantId),
    [merchantId, merchants],
  );

  async function loadMerchants() {
    const data = await api("/merchants/");
    setMerchants(data);
    if (!merchantId && data.length) setMerchantId(data[0].id);
  }

  async function loadMerchantData(id = merchantId) {
    if (!id) return;
    const [summaryData, accountData, payoutData] = await Promise.all([
      api("/ledger/summary/", { merchantId: id }),
      api("/bank-accounts/", { merchantId: id }),
      api("/payouts/", { merchantId: id }),
    ]);
    setSummary(summaryData);
    setAccounts(accountData);
    setPayouts(payoutData);
    if (accountData.length && !accountData.some((account) => account.id === bankAccountId)) {
      setBankAccountId(accountData[0].id);
    }
  }

  useEffect(() => {
    loadMerchants().catch((err) => setError(err.message)).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!merchantId) return undefined;
    setLoading(true);
    loadMerchantData(merchantId).catch((err) => setError(err.message)).finally(() => setLoading(false));
    const interval = window.setInterval(() => {
      loadMerchantData(merchantId).catch((err) => setError(err.message));
    }, 3000);
    return () => window.clearInterval(interval);
  }, [merchantId]);

  async function submitPayout(event) {
    event.preventDefault();
    setNotice("");
    setError("");
    const amountPaise = Math.round(Number(amountRupees) * 100);
    if (!amountPaise || amountPaise < 1) {
      setError("Enter a payout amount greater than zero.");
      return;
    }
    setSubmitting(true);
    try {
      const payout = await api("/payouts/", {
        merchantId,
        method: "POST",
        idempotencyKey: crypto.randomUUID(),
        body: { amount_paise: amountPaise, bank_account_id: bankAccountId },
      });
      setNotice(`Payout ${payout.status}: ${formatMoney(payout.amount_paise)}`);
      setAmountRupees("");
      await loadMerchantData(merchantId);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#f6f8fb]">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-4 sm:flex-row sm:items-center sm:justify-between lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-md bg-[#0f766e] text-white">
              <Wallet size={22} />
            </div>
            <div>
              <h1 className="text-xl font-semibold text-slate-950">Playto Pay</h1>
              <p className="text-sm text-slate-500">Payout operations</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <select
              className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-800 outline-none ring-[#0f766e]/20 focus:ring-4"
              value={merchantId}
              onChange={(event) => {
                setMerchantId(event.target.value);
                setBankAccountId("");
              }}
            >
              {merchants.map((merchant) => (
                <option key={merchant.id} value={merchant.id}>
                  {merchant.name}
                </option>
              ))}
            </select>
            <button
              className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
              title="Refresh"
              type="button"
              onClick={() => loadMerchantData()}
            >
              <RefreshCw size={18} />
            </button>
          </div>
        </div>
      </header>

      <section className="mx-auto grid max-w-7xl gap-5 px-4 py-6 lg:grid-cols-[1fr_360px] lg:px-8">
        <div className="space-y-5">
          <div className="grid gap-4 md:grid-cols-3">
            <BalanceTile label="Available" value={summary?.available_balance_paise} icon={<Wallet size={20} />} />
            <BalanceTile label="Held" value={summary?.held_balance_paise} icon={<Clock3 size={20} />} />
            <BalanceTile label="Total" value={summary?.total_balance_paise} icon={<Landmark size={20} />} />
          </div>

          <section className="rounded-lg border border-slate-200 bg-white shadow-panel">
            <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <h2 className="text-base font-semibold text-slate-950">Payout history</h2>
              <span className="text-sm text-slate-500">{loading ? "Loading" : `${payouts.length} records`}</span>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-4 py-3 font-semibold">Amount</th>
                    <th className="px-4 py-3 font-semibold">Bank</th>
                    <th className="px-4 py-3 font-semibold">Status</th>
                    <th className="px-4 py-3 font-semibold">Attempts</th>
                    <th className="px-4 py-3 font-semibold">Created</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 bg-white">
                  {payouts?.length > 0 ? (
                    payouts.map((payout) => (
                      <tr key={payout.id} className="hover:bg-slate-50">
                        <td className="whitespace-nowrap px-4 py-3 font-medium text-slate-950">
                          {formatMoney(payout.amount_paise)}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-slate-600">
                          {payout.bank_account?.bank_name} {payout.bank_account?.masked_account_number}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3">
                          <StatusBadge status={payout.status} />
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-slate-600">{payout.attempt_count}</td>
                        <td className="whitespace-nowrap px-4 py-3 text-slate-600">
                          {new Date(payout.created_at).toLocaleString()}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td className="px-4 py-8 text-center text-slate-500" colSpan="5">
                        {loading ? "Loading payouts..." : "No payouts yet"}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="rounded-lg border border-slate-200 bg-white shadow-panel">
            <div className="border-b border-slate-200 px-4 py-3">
              <h2 className="text-base font-semibold text-slate-950">Recent ledger</h2>
            </div>
            <div className="divide-y divide-slate-100">
              {(summary?.recent_ledger_entries || []).map((entry) => (
                <div key={entry.id} className="grid gap-3 px-4 py-3 text-sm sm:grid-cols-[160px_110px_1fr_140px]">
                  <div className="flex items-center gap-2 font-medium text-slate-950">
                    {entry.direction === "credit" ? (
                      <ArrowUpRight size={17} className="text-emerald-600" />
                    ) : (
                      <ArrowDownRight size={17} className="text-rose-600" />
                    )}
                    {formatMoney(entry.amount_paise)}
                  </div>
                  <div className="capitalize text-slate-600">{entry.bucket}</div>
                  <div className="text-slate-600">{entry.description}</div>
                  <div className="text-slate-500">{new Date(entry.created_at).toLocaleDateString()}</div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <aside className="space-y-5">
          <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-panel">
            <h2 className="text-base font-semibold text-slate-950">Request payout</h2>
            <form className="mt-4 space-y-4" onSubmit={submitPayout}>
              <label className="block text-sm font-medium text-slate-700">
                Amount
                <input
                  className="mt-1 h-11 w-full rounded-md border border-slate-300 px-3 text-slate-950 outline-none ring-[#0f766e]/20 focus:ring-4"
                  inputMode="decimal"
                  min="0"
                  step="0.01"
                  placeholder="0.00"
                  value={amountRupees}
                  onChange={(event) => setAmountRupees(event.target.value)}
                />
              </label>
              <label className="block text-sm font-medium text-slate-700">
                Bank account
                <select
                  className="mt-1 h-11 w-full rounded-md border border-slate-300 bg-white px-3 text-slate-950 outline-none ring-[#0f766e]/20 focus:ring-4"
                  value={bankAccountId}
                  onChange={(event) => setBankAccountId(event.target.value)}
                >
                  {accounts?.length > 0 ? (
                    accounts.map((account) => (
                      <option key={account.id} value={account.id}>
                        {account.bank_name} {account.masked_account_number}
                      </option>
                    ))
                  ) : (
                    <option value="" disabled>No bank accounts available</option>
                  )}
                </select>
              </label>
              <button
                className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-md bg-[#0f766e] px-4 text-sm font-semibold text-white hover:bg-[#115e59] disabled:cursor-not-allowed disabled:bg-slate-300"
                disabled={submitting || !bankAccountId}
                type="submit"
              >
                <Send size={17} />
                {submitting ? "Submitting" : "Submit payout"}
              </button>
            </form>
            {notice && <p className="mt-4 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{notice}</p>}
            {error && <p className="mt-4 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}
          </section>

          <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-panel">
            <h2 className="text-base font-semibold text-slate-950">Merchant</h2>
            <dl className="mt-4 space-y-3 text-sm">
              <div>
                <dt className="text-slate-500">Name</dt>
                <dd className="font-medium text-slate-950">{selectedMerchant?.name || "-"}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Email</dt>
                <dd className="font-medium text-slate-950">{selectedMerchant?.email || "-"}</dd>
              </div>
              <div>
                <dt className="text-slate-500">API merchant id</dt>
                <dd className="break-all font-mono text-xs text-slate-700">{merchantId || "-"}</dd>
              </div>
            </dl>
          </section>
        </aside>
      </section>
    </main>
  );
}

function BalanceTile({ label, value, icon }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-panel">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-500">{label}</span>
        <span className="text-[#0f766e]">{icon}</span>
      </div>
      <div className="mt-3 text-2xl font-semibold text-slate-950">{formatMoney(value || 0)}</div>
    </section>
  );
}

function StatusBadge({ status }) {
  const Icon = status === "completed" ? CheckCircle2 : status === "failed" ? XCircle : Clock3;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold ring-1 ${statusTone(status)}`}>
      <Icon size={14} />
      {status}
    </span>
  );
}
