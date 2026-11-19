"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Building2, Wallet } from "lucide-react";
import { api, type BankAccount, type TrackedTransaction } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function money(n: number | null, currency = "USD") {
  if (n === null) return "Not provided";
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(n);
}

export function LiveHome() {
  const { user } = useAuth();
  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [transactions, setTransactions] = useState<TrackedTransaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    Promise.all([api.bankAccounts(), api.trackedTransactions({ limit: 8 })])
      .then(([a, t]) => { setAccounts(a.accounts); setTransactions(t.transactions); })
      .catch((e) => setError(e instanceof Error ? e.message : "Unable to load accounts"))
      .finally(() => setLoading(false));
  }, []);

  return <div className="space-y-6">
    <div className="flex items-center justify-between gap-3">
      <div><p className="text-sm text-muted">Signed in as {user?.email}</p><h1 className="text-[28px] font-bold">Your financial home</h1></div>
      <div className="flex flex-wrap gap-2"><Link href="/activity" className="rounded-full bg-dark px-4 py-2.5 text-sm font-semibold text-white">Income & expenses</Link><Link href="/accounts" className="rounded-full border border-line px-4 py-2.5 text-sm font-semibold">Manage banks</Link></div>
    </div>
    {loading && <p className="text-muted">Loading your accounts…</p>}
    {error && <p role="alert" className="card p-5 text-act">{error}</p>}
    {!loading && !error && accounts.length === 0 && <div className="card flex flex-col items-start gap-4 p-8">
      <span className="rounded-2xl bg-accent-soft p-3 text-accent"><Building2 size={30} /></span>
      <h2 className="text-[23px] font-bold">Connect your first bank account</h2>
      <p className="max-w-lg text-muted">Your account is ready. Connect a bank securely to start seeing your balances and transactions. No demo numbers will appear in your personal account.</p>
      <div className="flex flex-wrap gap-2"><Link href="/accounts" className="flex items-center gap-2 rounded-full bg-dark px-5 py-3 text-sm font-semibold text-white">Connect bank <ArrowRight size={17} /></Link><Link href="/activity" className="flex items-center gap-2 rounded-full border border-line px-5 py-3 text-sm font-semibold">Track manually</Link></div>
      <p className="text-xs text-muted">Plaid Sandbox lets you test with fictional bank accounts. No personal bank account is needed while developing.</p>
    </div>}
    {!loading && !error && <>
      {accounts.length > 0 && <div className="card p-6"><div className="mb-4 flex items-center gap-2"><Wallet size={21} /><h2 className="text-lg font-bold">Connected accounts</h2></div>
        <div className="grid gap-3 sm:grid-cols-2">{accounts.map(a => <div key={a.id} className="rounded-xl border border-line p-4">
          <p className="font-semibold">{a.name} {a.mask && <span className="text-muted">••{a.mask}</span>}</p>
          <p className="text-xs capitalize text-muted">{a.subtype ?? a.type ?? "Bank account"}</p>
          <p className="mt-2 text-xl font-bold">{money(a.current_balance, a.currency ?? "USD")}</p>
          <p className="text-xs text-muted">Provider-reported balance · May be delayed</p>
        </div>)}</div>
      </div>}
      <div className="card p-6"><div className="flex items-center justify-between gap-2"><h2 className="mb-4 text-lg font-bold">Recent transactions</h2><Link href="/activity" className="text-sm font-semibold text-accent">See all</Link></div>
        {transactions.length === 0 ? <p className="text-muted">No transactions available yet. Sync your connection from Banks.</p> :
          <div className="divide-y divide-line">{transactions.slice(0, 8).map(t => <div key={t.id} className="flex items-center justify-between gap-4 py-3">
            <div><p className="font-medium">{t.merchant ?? t.name}</p><p className="text-xs text-muted">{t.account_name} · {t.date} · {t.activity_type.replace(/_/g," ")} {t.pending ? "· Pending" : ""}</p></div>
            <p className="font-semibold tabular-nums">{t.currency} {t.amount}</p></div>)}</div>}
      </div>
    </>}
    <p className="text-xs text-muted">Ahead is currently read-only for connected bank accounts. Transfers, payments, financial forecasts and automated financial decisions are not active for real accounts.</p>
  </div>;
}
