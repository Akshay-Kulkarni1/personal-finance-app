"use client";
import Script from "next/script";
import { useCallback, useEffect, useState } from "react";
import { Building2, Link2, RefreshCw, Trash2 } from "lucide-react";
import { api, type BankConnection, type BankAccount } from "@/lib/api";

type LinkMetadata = { institution?: { institution_id?: string; name?: string } };
type LinkInstance = { open: () => void; destroy?: () => void };
declare global {
  interface Window {
    Plaid?: { create: (options: { token: string; onSuccess: (publicToken: string, metadata: LinkMetadata) => void; onExit: (error: unknown) => void }) => LinkInstance };
  }
}

export default function Accounts() {
  const [sdkReady, setSdkReady] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [environment, setEnvironment] = useState("sandbox");
  const [webhookReady, setWebhookReady] = useState(false);
  const [connections, setConnections] = useState<BankConnection[]>([]);
  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const [c, a, status] = await Promise.all([api.bankConnections(), api.bankAccounts(), api.bankStatus()]);
    setConnections(c.connections); setAccounts(a.accounts);
    setConfigured(status.configured); setEnvironment(status.environment); setWebhookReady(status.webhook_configured);
  }, []);
  useEffect(() => {
    let active = true;
    Promise.all([api.bankConnections(), api.bankAccounts(), api.bankStatus()])
      .then(([c, a, status]) => {
        if (!active) return;
        setConnections(c.connections);
        setAccounts(a.accounts);
        setConfigured(status.configured);
        setEnvironment(status.environment);
        setWebhookReady(status.webhook_configured);
      })
      .catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, []);

  async function connect() {
    if (!window.Plaid) { setError("Plaid Link hasn't loaded yet. Please try again."); return; }
    setWorking(true); setError(""); setMessage("");
    try {
      const { link_token } = await api.bankLinkToken();
      const link = window.Plaid.create({
        token: link_token,
        onSuccess: async (publicToken, metadata) => {
          try {
            const connected = await api.bankExchange(publicToken, metadata.institution?.institution_id, metadata.institution?.name);
            setMessage(`${connected.accounts_connected} account(s) connected. Synchronizing transactions…`);
            let syncDone = true;
            try { await api.bankSync(connected.connection_id); }
            catch { syncDone = false; }
            await refresh();
            setMessage(syncDone
              ? "Your bank account is connected. You can review balances and transactions on Home."
              : "Bank connected. Transactions are not available yet; use Sync to try again.");
          } catch (e) { setError(e instanceof Error ? e.message : "Could not finish linking the bank"); }
          finally { setWorking(false); link.destroy?.(); }
        },
        onExit: (e) => {
          if (e) setError("Bank connection was not completed. No account was added.");
          setWorking(false); link.destroy?.();
        },
      });
      link.open();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to start Plaid Link"); setWorking(false);
    }
  }
  async function reconnect(connectionId: string) {
    if (!window.Plaid) { setError("Plaid Link hasn't loaded yet. Please try again."); return; }
    setWorking(true); setError(""); setMessage("");
    try {
      const { link_token } = await api.bankUpdateLinkToken(connectionId);
      const link = window.Plaid.create({
        token: link_token,
        onSuccess: async () => {
          try {
            await api.bankUpdateComplete(connectionId);
            await refresh();
            setMessage("Bank access updated. Transaction synchronization has been queued.");
          } catch (e) { setError(e instanceof Error ? e.message : "Could not finish bank reconnection"); }
          finally { setWorking(false); link.destroy?.(); }
        },
        onExit: (e) => {
          if (e) setError("Bank reconnection was not completed.");
          setWorking(false); link.destroy?.();
        },
      });
      link.open();
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to reconnect bank"); setWorking(false); }
  }
  async function sync(connectionId: string) {
    setWorking(true); setError(""); setMessage("");
    try {
      const result = await api.bankSync(connectionId);
      await refresh();
      setMessage(`Sync complete: ${result.added} new, ${result.modified} updated transactions.`);
    } catch (e) { setError(e instanceof Error ? e.message : "Sync failed"); }
    finally { setWorking(false); }
  }
  async function configureWebhook(connectionId: string) {
    setWorking(true); setError(""); setMessage("");
    try {
      await api.bankConfigureWebhook(connectionId);
      setMessage("Plaid accepted the configured HTTPS callback for this bank. Send a test event next, then confirm it arrived in the worker/operations logs.");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not register the callback"); }
    finally { setWorking(false); }
  }
  async function testWebhook(connectionId: string) {
    setWorking(true); setError(""); setMessage("");
    try {
      await api.bankFireTestWebhook(connectionId);
      setMessage("Plaid Sandbox accepted the test webhook request. Delivery is NOT yet verified: check your public HTTPS endpoint and bank worker logs.");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not request a test event"); }
    finally { setWorking(false); }
  }
  async function disconnect(connectionId: string) {
    if (!window.confirm("Disconnect this bank and delete its imported accounts and transactions from Ahead?")) return;
    setWorking(true); setError(""); setMessage("");
    try { await api.bankDisconnect(connectionId); await refresh(); setMessage("Bank disconnected; imported records removed."); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not disconnect the bank"); }
    finally { setWorking(false); }
  }

  return <div className="space-y-5">
    <Script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js" strategy="afterInteractive" onReady={() => setSdkReady(true)} onError={() => setError("Plaid Link could not be loaded.")} />
    <div><h1 className="text-[28px] font-bold">Connected banks</h1><p className="text-muted">Your banks in one place, with access controlled by you.</p></div>
    <div className="card space-y-4 p-6">
      <div className="flex items-start gap-3"><Building2 className="shrink-0 text-accent" size={28} />
        <div><h2 className="text-lg font-bold">Connect a bank securely</h2>
          <p className="mt-1 text-sm text-muted">We use Plaid Link to connect accounts. Ahead never asks you to enter your bank username or password on our site.</p>
          {environment === "sandbox" && <p className="mt-2 text-sm text-muted">Sandbox testing: select a test institution and use <code>user_good</code> / <code>pass_good</code> inside Plaid Link.</p>}
        </div></div>
      {!configured && <p className="rounded-lg bg-bg p-3 text-sm text-muted">This environment needs Plaid credentials and an encryption key on the backend. The Connect button will work when those settings are provided; you do not need to connect your own bank.</p>}
      <button type="button" onClick={() => void connect()} disabled={working || !configured || !sdkReady} className="flex items-center gap-2 rounded-full bg-dark px-5 py-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">
        <Link2 size={17} /> {working ? "Working…" : !sdkReady ? "Loading secure connector…" : "Connect bank"}
      </button>
    </div>
    {error && <p role="alert" className="rounded-lg border border-act p-3 text-sm text-act">{error}</p>}
    {message && <p role="status" className="rounded-lg border border-line p-3 text-sm">{message}</p>}
    <div className="card p-6"><h2 className="mb-4 text-lg font-bold">Your connections</h2>
      {connections.length === 0 ? <p className="text-sm text-muted">No banks connected yet.</p> :
        <div className="space-y-4">{connections.map(c => <div key={c.id} className="rounded-xl border border-line p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h3 className="font-semibold">{c.institution_name ?? "Bank connection"}</h3>
              <p className="text-xs text-muted">{c.status === "needs_attention" ? "Connection needs attention" : c.status} · {accounts.filter(a => a.connection_id === c.id).length} accounts</p>
              <p className="text-xs text-muted">Sync: {c.sync_status === "dead" ? "needs manual retry" : c.sync_status ?? "not started"} · {c.last_synced_at ? `Last successful ${new Date(c.last_synced_at * 1000).toLocaleString()}` : "Awaiting first sync"}</p>
              {c.sync_error && <p className="text-xs text-act">{c.sync_error}</p>}</div>
            <div className="flex gap-2">
              {c.status === "needs_attention" && <button type="button" onClick={() => void reconnect(c.id)} disabled={working || !sdkReady} className="flex items-center gap-1 rounded-full border border-line px-3 py-2 text-xs font-medium disabled:opacity-50"><Link2 size={14} /> Reconnect</button>}
              <button type="button" onClick={() => void sync(c.id)} disabled={working || c.sync_status === "running" || c.status === "needs_attention"} className="flex items-center gap-1 rounded-full border border-line px-3 py-2 text-xs font-medium disabled:opacity-50"><RefreshCw size={14} /> Sync</button>
              <button type="button" onClick={() => void disconnect(c.id)} disabled={working} className="flex items-center gap-1 rounded-full border border-line px-3 py-2 text-xs font-medium disabled:opacity-50"><Trash2 size={14} /> Disconnect</button>
            </div>
          </div>
          {configured && environment === "sandbox" && webhookReady && <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
            <span className="mr-1 text-xs text-muted">Sandbox callback verification:</span>
            <button type="button" onClick={() => void configureWebhook(c.id)} disabled={working} className="rounded-full border border-line px-3 py-2 text-xs font-medium disabled:opacity-50">Register HTTPS webhook</button>
            <button type="button" onClick={() => void testWebhook(c.id)} disabled={working} className="rounded-full border border-line px-3 py-2 text-xs font-medium disabled:opacity-50">Send test webhook</button>
          </div>}
          <ul className="mt-3 space-y-1 text-sm text-muted">{accounts.filter(a => a.connection_id === c.id).map(a => <li key={a.id}>{a.name} {a.mask ? `••${a.mask}` : ""}</li>)}</ul>
        </div>)}</div>}
    </div>
    <p className="text-xs text-muted">Only balance and transaction access is requested. This does not authorize transfers or bill payments. Bank access can be revoked at any time.</p>
  </div>;
}
