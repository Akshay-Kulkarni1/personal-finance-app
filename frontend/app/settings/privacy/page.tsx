"use client";
import Link from "next/link";
import { useState } from "react";
import { api, downloadPersonalData } from "@/lib/api";

export default function PrivacySettings() {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function exportData() {
    setBusy(true); setMessage(""); setError("");
    try {
      const blob = await downloadPersonalData(password, code);
      const url = URL.createObjectURL(blob);
      try {
        const link = document.createElement("a");
        link.href = url;
        link.download = `ahead-user-data-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(link);
        link.click();
        link.remove();
      } finally { window.setTimeout(() => URL.revokeObjectURL(url), 3000); }
      setMessage("Your data export has been prepared. Keep the file private.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to export data");
    } finally { setBusy(false); }
  }

  async function deleteAccount() {
    if (confirmation !== "DELETE") return;
    setBusy(true); setMessage(""); setError("");
    try {
      await api.deleteAccount(password, code);
      window.location.assign("/login");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to delete account");
      setBusy(false);
    }
  }

  return <div className="mx-auto max-w-2xl space-y-6">
    <div>
      <h1 className="text-2xl font-bold">Your data &amp; privacy</h1>
      <p className="mt-1 text-sm text-muted">Download your information or close your Ahead account.</p>
      <Link href="/settings/security" className="mt-2 inline-block text-sm underline">Back to Security settings</Link>
    </div>
    <section className="card space-y-3 p-6">
      <h2 className="text-lg font-semibold">Confirm your identity</h2>
      <p className="text-sm text-muted">For your security, exporting or deleting data requires your password and an authenticator or recovery code when MFA is enabled.</p>
      <label className="block text-sm">Current password
        <input className="mt-1 block w-full rounded-xl border border-line bg-bg p-3" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} />
      </label>
      <label className="block text-sm">Authenticator or recovery code (if MFA enabled)
        <input className="mt-1 block w-full rounded-xl border border-line bg-bg p-3" autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)} />
      </label>
    </section>
    <section className="card space-y-3 p-6">
      <h2 className="text-lg font-semibold">Download your data</h2>
      <p className="text-sm text-muted">Export your Ahead account details, bank account metadata, transactions, connection status and activity history as JSON. Passwords, bank access tokens, MFA secrets and session tokens are excluded.</p>
      <button type="button" disabled={busy || !password} className="rounded-xl bg-accent px-4 py-2 text-white disabled:opacity-50" onClick={() => void exportData()}>
        {busy ? "Working…" : "Export my data"}
      </button>
    </section>
    <section className="card space-y-3 border border-red-200 p-6">
      <h2 className="text-lg font-semibold">Delete your Ahead account</h2>
      <p className="text-sm text-muted">First <Link href="/accounts" className="font-semibold underline">disconnect every linked bank</Link> so its authorization can be revoked by the provider. Deletion then removes your profile, current sessions and user-owned records from Ahead&apos;s active database. This cannot be undone.</p>
      <p className="text-xs text-muted">Development email outbox files and database backups are separate from the active database. They are subject to retention and removal procedures, not immediate in-app deletion.</p>
      <label className="block text-sm">Type DELETE to confirm
        <input className="mt-1 block w-full rounded-xl border border-line bg-bg p-3" autoComplete="off" value={confirmation} onChange={e => setConfirmation(e.target.value)} />
      </label>
      <button type="button" disabled={busy || !password || confirmation !== "DELETE"} className="rounded-xl bg-red-700 px-4 py-2 text-white disabled:opacity-50" onClick={() => void deleteAccount()}>
        Permanently delete active account data
      </button>
    </section>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {message && <p role="status" className="text-sm">{message}</p>}
  </div>;
}
