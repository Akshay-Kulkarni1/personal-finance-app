"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeftRight, Check, ChevronLeft, ChevronRight, Pencil, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { api, type ActivityType, type TrackedAccount, type TrackedCategory, type TrackedTransaction, type TrackingFilters, type TrackingPage } from "@/lib/api";

const KINDS: { value: ActivityType; label: string }[] = [
  { value: "income", label: "Income" }, { value: "spending", label: "Expense" },
  { value: "transfer", label: "Transfer" }, { value: "card_payment", label: "Credit card payment" },
  { value: "loan_payment", label: "Loan payment" }, { value: "refund", label: "Refund" },
  { value: "unclassified", label: "Unclassified" },
];
const kindLabel = (kind: ActivityType) => KINDS.find(x => x.value === kind)?.label ?? kind;
const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
};
function money(amount: string, currency: string) {
  // Never round transaction amounts through JS Number.
  const negative = amount.startsWith("-");
  const [whole, fraction = "00"] = (negative ? amount.slice(1) : amount).split(".");
  return `${negative ? "−" : ""}${currency} ${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${fraction.padEnd(2, "0")}`;
}
const inputClass = "w-full min-w-0 rounded-xl border border-line bg-white px-3 py-2.5 text-sm text-ink focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-soft";
const buttonClass = "inline-flex items-center justify-center gap-2 rounded-full bg-dark px-4 py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50";
const subtleButton = "inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-2 text-sm font-medium hover:bg-bg disabled:opacity-50";

function TransactionEditor({ row, categories, onDone, onError }: {
  row: TrackedTransaction; categories: TrackedCategory[]; onDone: () => void; onError: (message: string) => void;
}) {
  const [activity, setActivity] = useState<ActivityType>(row.activity_type);
  const [category, setCategory] = useState(row.category_id ?? (row.source === "bank" && row.category_key.startsWith("provider:") ? "provider" : ""));
  const [name, setName] = useState(row.name);
  const [amount, setAmount] = useState(row.amount);
  const [date, setDate] = useState(row.date);
  const [busy, setBusy] = useState(false);
  const custom = categories.filter(c => c.source === "custom");
  async function update() {
    setBusy(true);
    try {
      if (row.source === "bank") {
        // "provider" leaves original category untouched. Reset restores both provider fields.
        await api.editBankTransaction(row.id, category === "provider" ? { activity_type: activity } : {
          activity_type: activity, category_id: category || null,
        });
      } else {
        await api.editManualTransaction(row.id, { activity_type: activity, category_id: category || null, name, amount, date });
      }
      onDone();
    } catch (e) { onError(e instanceof Error ? e.message : "Unable to save transaction"); }
    finally { setBusy(false); }
  }
  async function reset() {
    setBusy(true);
    try { await api.resetBankTransaction(row.id); onDone(); }
    catch (e) { onError(e instanceof Error ? e.message : "Unable to reset transaction"); }
    finally { setBusy(false); }
  }
  async function remove() {
    if (!window.confirm("Delete this manual entry? This cannot be undone.")) return;
    setBusy(true);
    try {
      if (row.transfer_group_id) await api.deleteManualTransfer(row.transfer_group_id);
      else await api.deleteManualTransaction(row.id);
      onDone();
    } catch (e) { onError(e instanceof Error ? e.message : "Unable to remove entry"); }
    finally { setBusy(false); }
  }
  return <div className="rounded-xl bg-bg p-4" aria-label="Edit transaction">
    <div className="grid gap-3 sm:grid-cols-2">
      {row.source === "manual" && <>
        <label className="text-xs font-semibold">Description<input className={inputClass + " mt-1"} value={name} onChange={e => setName(e.target.value)} maxLength={160} /></label>
        <label className="text-xs font-semibold">Signed amount<input className={inputClass + " mt-1"} value={amount} onChange={e => setAmount(e.target.value)} inputMode="decimal" /></label>
        <label className="text-xs font-semibold">Date<input className={inputClass + " mt-1"} type="date" value={date} onChange={e => setDate(e.target.value)} /></label>
      </>}
      <label className="text-xs font-semibold">Activity
        <select className={inputClass + " mt-1"} value={activity} onChange={e => setActivity(e.target.value as ActivityType)}>
          {KINDS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
        </select>
      </label>
      <label className="text-xs font-semibold">Category
        <select className={inputClass + " mt-1"} value={category} onChange={e => setCategory(e.target.value)}>
          {row.source === "bank" && <option value="provider">Keep provider category</option>}
          <option value="">Uncategorized</option>
          {custom.map(c => <option key={c.key} value={c.id ?? ""}>{c.name}</option>)}
        </select>
      </label>
    </div>
    {row.transfer_group_id && <p className="mt-2 text-xs text-muted">Both sides of a transfer stay paired. To change it, delete the pair and create a new transfer.</p>}
    <div className="mt-3 flex flex-wrap gap-2">
      <button type="button" className={subtleButton} onClick={() => void update()} disabled={busy || Boolean(row.transfer_group_id)}><Check size={15} /> Save edits</button>
      {row.source === "bank" && <button type="button" className={subtleButton} disabled={busy} onClick={() => void reset()}><RotateCcw size={15} /> Reset bank edits</button>}
      {row.source === "manual" && <button type="button" className={subtleButton + " text-act"} disabled={busy} onClick={() => void remove()}><Trash2 size={15} /> {row.transfer_group_id ? "Delete transfer pair" : "Delete entry"}</button>}
    </div>
  </div>;
}

export default function ActivityPage() {
  const [accounts, setAccounts] = useState<TrackedAccount[]>([]);
  const [categories, setCategories] = useState<TrackedCategory[]>([]);
  const [page, setPage] = useState<TrackingPage | null>(null);
  const [filters, setFilters] = useState<TrackingFilters>({ limit: 30, offset: 0 });
  const [refreshKey, setRefreshKey] = useState(0);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [newAccount, setNewAccount] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [newCategory, setNewCategory] = useState("");
  const manualRequestId = useRef<string | null>(null);
  const transferRequestId = useRef<string | null>(null);
  const [manualAccount, setManualAccount] = useState("");
  const [manualDate, setManualDate] = useState(today);
  const [manualAmount, setManualAmount] = useState("");
  const [manualName, setManualName] = useState("");
  const [manualType, setManualType] = useState<ActivityType>("spending");
  const [manualCategory, setManualCategory] = useState("");
  const [transferFrom, setTransferFrom] = useState("");
  const [transferTo, setTransferTo] = useState("");
  const [transferAmount, setTransferAmount] = useState("");
  const [transferDate, setTransferDate] = useState(today);
  const [transferName, setTransferName] = useState("Internal transfer");
  const [showTransfer, setShowTransfer] = useState(false);
  const manualAccounts = useMemo(() => accounts.filter(a => a.source === "manual"), [accounts]);
  const customCategories = categories.filter(c => c.source === "custom");

  const reload = useCallback(() => { setRefreshKey(k => k+1); setExpanded(null); }, []);
  useEffect(() => {
    let alive = true;
    Promise.all([api.trackedAccounts(), api.trackedCategories()])
      .then(([a, c]) => { if (alive) { setAccounts(a.accounts); setCategories(c.categories); } })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : "Unable to load accounts"); });
    return () => { alive = false; };
  }, [refreshKey]);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.trackedTransactions(filters).then(result => { if (alive) { setPage(result); setError(""); } })
      .catch(e => { if (alive) { setPage(null); setError(e instanceof Error ? e.message : "Unable to load activity"); } })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [filters, refreshKey]);
  function changeFilter(changes: Partial<TrackingFilters>) { setFilters(old => ({ ...old, ...changes, offset: 0 })); }
  async function act(action: () => Promise<unknown>, success: string, onSuccess?: () => void) {
    setWorking(true); setError(""); setMessage("");
    try { await action(); onSuccess?.(); setMessage(success); reload(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not save your changes"); }
    finally { setWorking(false); }
  }
  return <div className="space-y-6">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div><p className="text-xs font-semibold uppercase tracking-wide text-accent">Phase 1 · Milestone 1</p><h1 className="text-[28px] font-bold">Income & expenses</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted">One view of your connected banks and manually entered money. Income, refunds, transfers and payments are tracked separately to avoid overstating spending.</p></div>
    </header>
    {error && <div role="alert" className="rounded-xl border border-act bg-act-soft p-3 text-sm text-act">{error}</div>}
    {message && <div role="status" className="rounded-xl bg-good-soft p-3 text-sm text-good">{message}</div>}

    <section className="card p-5 sm:p-6" aria-label="Transaction filters">
      <h2 className="mb-4 text-lg font-bold">Activity filters</h2>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <label className="text-xs font-semibold">From<input aria-label="From date" type="date" className={inputClass + " mt-1"} value={filters.start_date ?? ""} onChange={e => changeFilter({ start_date: e.target.value })} /></label>
        <label className="text-xs font-semibold">To<input aria-label="To date" type="date" className={inputClass + " mt-1"} value={filters.end_date ?? ""} onChange={e => changeFilter({ end_date: e.target.value })} /></label>
        <label className="text-xs font-semibold">Account<select className={inputClass + " mt-1"} value={filters.account ?? ""} onChange={e => changeFilter({ account: e.target.value })}>
          <option value="">All accounts</option>{accounts.map(a => <option key={a.key} value={a.key}>{a.name} ({a.source})</option>)}
        </select></label>
        <label className="text-xs font-semibold">Activity<select className={inputClass + " mt-1"} value={filters.activity ?? ""} onChange={e => changeFilter({ activity: e.target.value as ActivityType | "" })}>
          <option value="">All types</option>{KINDS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
        </select></label>
        <label className="text-xs font-semibold">Category<select className={inputClass + " mt-1"} value={filters.category ?? ""} onChange={e => changeFilter({ category: e.target.value })}>
          <option value="">All categories</option><option value="uncategorized">Uncategorized</option>{categories.map(c => <option key={c.key} value={c.key}>{c.name}</option>)}
        </select></label>
        <label className="text-xs font-semibold">Currency<select className={inputClass + " mt-1"} value={filters.currency ?? ""} onChange={e => changeFilter({ currency: e.target.value })}>
          <option value="">All currencies</option>{[...new Set(accounts.map(a => a.currency).filter((x): x is string => Boolean(x)))].sort().map(c => <option key={c} value={c}>{c}</option>)}
        </select></label>
        <div className="flex items-end"><button type="button" className={subtleButton} onClick={() => setFilters({ limit: 30, offset: 0 })}>Clear filters</button></div>
      </div>
    </section>

    <section aria-label="Financial summaries">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h2 className="text-lg font-bold">Posted activity summary</h2><span className="text-xs text-muted">Pending transactions excluded · All filtered matches, not just this page</span></div>
      {loading && <p role="status" className="text-sm text-muted">Loading summaries…</p>}
      {!loading && page?.summaries.length === 0 && <div className="card p-6 text-sm text-muted">No transactions match your filters. Add a manual entry or connect a bank to get started.</div>}
      {!loading && page?.summaries.map(s => <div key={s.currency} className="mb-3 grid gap-3 rounded-2xl bg-card p-5 shadow-sm sm:grid-cols-3">
        <div><p className="text-xs font-semibold text-muted">Income · {s.currency}</p><p className="mt-1 text-2xl font-bold tabular-nums">{money(s.income,s.currency)}</p></div>
        <div><p className="text-xs font-semibold text-muted">Expenses · {s.currency}</p><p className="mt-1 text-2xl font-bold tabular-nums">{money(s.spending,s.currency)}</p></div>
        <div><p className="text-xs font-semibold text-muted">Net expenses after refunds · {s.currency}</p><p className="mt-1 text-2xl font-bold tabular-nums">{money(s.net_spending,s.currency)}</p></div>
        {(s.unclassified_inflows !== "0.00" || s.pending_count > 0) && <p className="text-xs text-muted sm:col-span-3">
          {s.unclassified_inflows !== "0.00" ? `${money(s.unclassified_inflows,s.currency)} incoming is not classified as income. ` : ""}
          {s.pending_count ? `${s.pending_count} pending transaction(s) are not included in totals.` : ""}
        </p>}
      </div>)}
    </section>

    <section className="card p-5 sm:p-6" aria-label="Unified transaction feed">
      <div className="mb-3 flex items-center justify-between gap-3"><h2 className="text-lg font-bold">Transactions</h2><span className="text-xs text-muted">Newest first · Up to 30 per page</span></div>
      {loading && <p role="status" className="py-6 text-sm text-muted">Loading transactions…</p>}
      {!loading && !error && page?.transactions.length === 0 && <p className="py-6 text-sm text-muted">Nothing here yet for these filters. Manual accounts work without linking a bank.</p>}
      {!loading && page && <div className="divide-y divide-line">{page.transactions.map(t => <div key={`${t.source}:${t.id}`} className="py-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1"><p className="font-semibold">{t.merchant || t.name}</p><p className="text-xs text-muted">{t.account_name} · {t.date} · {t.source === "bank" ? "Synced bank" : "Manual"} {t.pending ? "· Pending" : ""}</p>
            <p className="mt-1 text-xs text-muted">{kindLabel(t.activity_type)} · {t.category_name?.replace(/_/g," ") ?? "Uncategorized"}</p></div>
          <div className="flex flex-col items-end gap-1"><span className="font-semibold tabular-nums">{money(t.amount,t.currency)}</span>
            <button type="button" className="inline-flex items-center gap-1 text-xs font-medium text-accent" aria-expanded={expanded === `${t.source}:${t.id}`} onClick={() => setExpanded(expanded === `${t.source}:${t.id}` ? null : `${t.source}:${t.id}`)}><Pencil size={12} /> Edit</button></div>
        </div>
        {expanded === `${t.source}:${t.id}` && <div className="mt-3"><TransactionEditor key={`${t.source}:${t.id}`} row={t} categories={categories} onDone={() => { setMessage("Transaction updated."); reload(); }} onError={setError} /></div>}
      </div>)}</div>}
      {!loading && page && <div className="mt-4 flex items-center justify-between gap-3"><button type="button" className={subtleButton} disabled={page.offset === 0} onClick={() => setFilters(f => ({ ...f, offset: Math.max(0,(f.offset ?? 0)-30) }))}><ChevronLeft size={16} /> Previous</button>
        <span className="text-xs text-muted">Showing {page.transactions.length ? page.offset+1 : 0}–{page.offset+page.transactions.length}</span>
        <button type="button" className={subtleButton} disabled={page.next_offset === null} onClick={() => setFilters(f => ({ ...f, offset: page.next_offset ?? 0 }))}>Next <ChevronRight size={16} /></button></div>}
    </section>

    <section className="grid gap-5 lg:grid-cols-2" aria-label="Manual financial tracking">
      <div className="card space-y-4 p-5 sm:p-6">
        <div><h2 className="text-lg font-bold">Manual accounts</h2><p className="text-xs text-muted">Keep cash or unlinked accounts in your feed without connecting a bank. They do not change provider-reported balances.</p></div>
        {manualAccounts.length > 0 && <ul className="space-y-2 text-sm">{manualAccounts.map(a => <li key={a.id} className="flex items-center justify-between gap-2"><span>{a.name} · {a.currency}</span><button type="button" className="text-xs text-muted hover:text-act" title={`Delete ${a.name}`} aria-label={`Delete manual account ${a.name}`} disabled={working} onClick={() => { if (window.confirm(`Delete empty account ${a.name}? Entries must be removed first.`)) void act(() => api.deleteManualAccount(a.id),"Manual account removed."); }}><Trash2 size={15} /></button></li>)}</ul>}
        <form className="space-y-3" onSubmit={e => { e.preventDefault(); void act(() => api.createManualAccount(newAccount,currency),"Manual account created.", () => setNewAccount("")); }}>
          <label className="block text-xs font-semibold">Account name<input required maxLength={80} className={inputClass + " mt-1"} placeholder="Cash wallet" value={newAccount} onChange={e => setNewAccount(e.target.value)} /></label>
          <label className="block text-xs font-semibold">Currency<input required maxLength={3} pattern="[A-Z]{3}" className={inputClass + " mt-1"} value={currency} onChange={e => setCurrency(e.target.value.toUpperCase())} /></label>
          <button type="submit" className={buttonClass} disabled={working}><Plus size={15} /> Add account</button>
        </form>
      </div>
      <div className="card space-y-4 p-5 sm:p-6">
        <div><h2 className="text-lg font-bold">Your categories</h2><p className="text-xs text-muted">Create categories and use them on bank or manual entries. Provider categories remain available as filters.</p></div>
        <div className="flex flex-wrap gap-2">{customCategories.map(c => <span key={c.key} className="inline-flex items-center gap-1 rounded-full bg-bg px-3 py-1.5 text-xs">{c.name}
          <button type="button" aria-label={`Rename ${c.name}`} title={`Rename ${c.name}`} onClick={() => { const next = window.prompt("Rename category",c.name); if (next?.trim()) void act(() => api.renameCategory(c.id!,next),"Category renamed."); }}><Pencil size={12} /></button>
          <button type="button" aria-label={`Delete ${c.name}`} title={`Delete ${c.name}`} onClick={() => { if (window.confirm(`Delete ${c.name}? Transactions will become uncategorized.`)) void act(() => api.deleteCategory(c.id!),"Category removed."); }}><X size={13} /></button>
        </span>)}</div>
        <form onSubmit={e => { e.preventDefault(); void act(() => api.createCategory(newCategory),"Category created.", () => setNewCategory("")); }} className="space-y-3">
          <label className="block text-xs font-semibold">Category name<input required maxLength={60} className={inputClass + " mt-1"} placeholder="Groceries" value={newCategory} onChange={e => setNewCategory(e.target.value)} /></label>
          <button type="submit" className={buttonClass} disabled={working}><Plus size={15} /> Add category</button>
        </form>
      </div>
      <div className="card space-y-4 p-5 sm:p-6">
        <div><h2 className="text-lg font-bold">Record a transaction</h2><p className="text-xs text-muted">Use a negative amount for money leaving an account and a positive amount for incoming money. Choose the activity type.</p></div>
        <form className="space-y-3" onSubmit={e => { e.preventDefault(); void act(() => api.createManualTransaction({ request_id: (manualRequestId.current ??= crypto.randomUUID()), account_id: manualAccount, date: manualDate, amount: manualAmount, name: manualName, category_id: manualCategory || null, activity_type: manualType }),"Transaction recorded.", () => { manualRequestId.current = null; setManualAmount(""); setManualName(""); }); }}>
          <label className="block text-xs font-semibold">Manual account<select required className={inputClass + " mt-1"} value={manualAccount} onChange={e => { manualRequestId.current = null; setManualAccount(e.target.value); }}><option value="">Choose account</option>{manualAccounts.map(a => <option key={a.id} value={a.id}>{a.name} ({a.currency})</option>)}</select></label>
          <div className="grid grid-cols-2 gap-3"><label className="text-xs font-semibold">Date<input className={inputClass + " mt-1"} required type="date" value={manualDate} onChange={e => { manualRequestId.current = null; setManualDate(e.target.value); }} /></label>
            <label className="text-xs font-semibold">Signed amount<input className={inputClass + " mt-1"} required placeholder="-24.50" inputMode="decimal" value={manualAmount} onChange={e => { manualRequestId.current = null; setManualAmount(e.target.value); }} /></label></div>
          <label className="block text-xs font-semibold">Description<input className={inputClass + " mt-1"} required maxLength={160} placeholder="Monthly salary, groceries…" value={manualName} onChange={e => { manualRequestId.current = null; setManualName(e.target.value); }} /></label>
          <div className="grid grid-cols-2 gap-3"><label className="text-xs font-semibold">Activity<select className={inputClass + " mt-1"} value={manualType} onChange={e => { manualRequestId.current = null; setManualType(e.target.value as ActivityType); }}>{KINDS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}</select></label>
            <label className="text-xs font-semibold">Category<select className={inputClass + " mt-1"} value={manualCategory} onChange={e => { manualRequestId.current = null; setManualCategory(e.target.value); }}><option value="">Uncategorized</option>{customCategories.map(c => <option key={c.key} value={c.id ?? ""}>{c.name}</option>)}</select></label></div>
          <button type="submit" className={buttonClass} disabled={working || !manualAccounts.length}><Plus size={15} /> Record transaction</button>
        </form>
      </div>
      <div className="card space-y-4 p-5 sm:p-6">
        <button type="button" className="flex w-full items-center justify-between gap-2" aria-expanded={showTransfer} onClick={() => setShowTransfer(!showTransfer)}><span className="text-left"><strong className="text-lg">Internal transfer</strong><span className="mt-1 block text-xs font-normal text-muted">Move money between your manual accounts. Both sides are recorded, excluded from spending and income.</span></span><ArrowLeftRight size={19} /></button>
        {showTransfer && <form className="space-y-3" onSubmit={e => { e.preventDefault(); void act(() => api.createManualTransfer({ request_id: (transferRequestId.current ??= crypto.randomUUID()), from_account_id: transferFrom, to_account_id: transferTo, date: transferDate, amount: transferAmount, name: transferName }),"Transfer recorded on both accounts.", () => { transferRequestId.current = null; setTransferAmount(""); }); }}>
          <label className="block text-xs font-semibold">From account<select required className={inputClass + " mt-1"} value={transferFrom} onChange={e => { transferRequestId.current = null; setTransferFrom(e.target.value); }}><option value="">Choose account</option>{manualAccounts.map(a => <option key={a.id} value={a.id}>{a.name} · {a.currency}</option>)}</select></label>
          <label className="block text-xs font-semibold">To account<select required className={inputClass + " mt-1"} value={transferTo} onChange={e => { transferRequestId.current = null; setTransferTo(e.target.value); }}><option value="">Choose account</option>{manualAccounts.map(a => <option key={a.id} value={a.id}>{a.name} · {a.currency}</option>)}</select></label>
          <div className="grid grid-cols-2 gap-3"><label className="text-xs font-semibold">Date<input required type="date" className={inputClass + " mt-1"} value={transferDate} onChange={e => { transferRequestId.current = null; setTransferDate(e.target.value); }} /></label>
            <label className="text-xs font-semibold">Positive amount<input required inputMode="decimal" className={inputClass + " mt-1"} value={transferAmount} onChange={e => { transferRequestId.current = null; setTransferAmount(e.target.value); }} placeholder="250.00" /></label></div>
          <label className="block text-xs font-semibold">Description<input required maxLength={160} className={inputClass + " mt-1"} value={transferName} onChange={e => { transferRequestId.current = null; setTransferName(e.target.value); }} /></label>
          <button type="submit" className={buttonClass} disabled={working || manualAccounts.length < 2}><ArrowLeftRight size={16} /> Record both sides</button>
        </form>}
      </div>
    </section>
    <p className="text-xs text-muted">Tracking is informational and does not move money. Synced bank activity is read-only at the provider; only Ahead&apos;s classifications can be edited. Manual entries do not alter bank balances. Connected Sandbox data is fictional.</p>
  </div>;
}
