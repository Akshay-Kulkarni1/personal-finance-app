"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, type UserSession } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function SecuritySettings() {
  const {user,refresh,signOut}=useAuth();
  const [sessions,setSessions]=useState<UserSession[]>([]);
  const [password,setPassword]=useState("");
  const [code,setCode]=useState("");
  const [setup,setSetup]=useState<{secret:string;otpauth_uri:string}|null>(null);
  const [recovery,setRecovery]=useState<string[]>([]);
  const [message,setMessage]=useState("");
  const [busy,setBusy]=useState(false);
  const reload=useCallback(()=>{void api.sessions().then(r=>setSessions(r.sessions)).catch(()=>setSessions([]));},[]);
  useEffect(()=>{reload();},[reload]);
  async function run(task:()=>Promise<unknown>, afterwards?:()=>void){
    setBusy(true);setMessage("");
    try{await task();afterwards?.();await refresh();reload();setMessage("Changes saved.");}
    catch(err){setMessage(err instanceof Error?err.message:"Unable to complete this action");}
    finally{setBusy(false);}
  }
  return <div className="mx-auto max-w-2xl space-y-6">
    <div><h1 className="text-2xl font-bold">Security settings</h1><p className="text-muted text-sm">Manage your account, authenticator and signed-in sessions.</p><Link href="/settings/privacy" className="mt-2 inline-block text-sm font-semibold underline">Data export &amp; account deletion →</Link></div>
    <section className="card space-y-3 p-6"><h2 className="text-lg font-semibold">Email verification</h2>
      <p className="text-sm">{user?.email} — {user?.email_verified?"Verified":"Not verified"}</p>
      {!user?.email_verified && <button disabled={busy} onClick={()=>void run(()=>api.resendVerification())} className="rounded-xl border border-line px-4 py-2 text-sm">Resend verification email</button>}
      <p className="text-xs text-muted">Bank access in production requires a verified email address.</p>
    </section>
    <section className="card space-y-3 p-6"><h2 className="text-lg font-semibold">Two-factor authentication</h2>
      <p className="text-sm text-muted">{user?.mfa_enabled?"Enabled. Your authenticator code is required at sign-in.":"Add an authenticator app to protect your account."}</p>
      <label className="block text-sm">Current password<input type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} className="mt-1 block w-full rounded-xl border border-line bg-bg p-3" /></label>
      {!user?.mfa_enabled && !setup && <button disabled={busy || !password} className="rounded-xl bg-accent px-4 py-2 text-white" onClick={()=>void run(async()=>{setSetup(await api.mfaSetup(password));})}>Set up authenticator</button>}
      {setup && !user?.mfa_enabled && <div className="rounded-xl border border-line p-4 space-y-2">
        <p className="text-sm">Add Ahead to your authenticator using this setup key (keep it private):</p>
        <code className="block break-all select-all rounded-lg bg-bg p-3 text-sm">{setup.secret}</code>
        <p className="text-xs text-muted">You can also import this URI into a compatible authenticator app:</p>
        <code className="block break-all select-all text-xs">{setup.otpauth_uri}</code>
      </div>}
      {(setup || user?.mfa_enabled) && <label className="block text-sm">Authenticator or recovery code<input autoComplete="one-time-code" value={code} onChange={e=>setCode(e.target.value)} className="mt-1 block w-full rounded-xl border border-line bg-bg p-3" /></label>}
      {setup && !user?.mfa_enabled && <button disabled={busy || !code} className="rounded-xl bg-accent px-4 py-2 text-white" onClick={()=>void run(async()=>{const result=await api.mfaEnable(code);setRecovery(result.recovery_codes);setSetup(null);setCode("");setPassword("");})}>Enable MFA</button>}
      {user?.mfa_enabled && <button disabled={busy || !password || !code} className="rounded-xl border border-line px-4 py-2" onClick={()=>void run(()=>api.mfaDisable(password,code),()=>{setCode("");setPassword("");setRecovery([]);})}>Disable MFA</button>}
      {recovery.length>0 && <div className="space-y-2 rounded-xl border border-line p-4"><h3 className="font-semibold">Save your recovery codes now</h3>
        <p className="text-xs text-muted">Each can be used only once. Store them securely; they will not be shown again.</p><pre className="whitespace-pre-wrap break-all text-sm">{recovery.join("\n")}</pre></div>}
    </section>
    <section className="card space-y-3 p-6"><h2 className="text-lg font-semibold">Active sessions</h2>
      {sessions.map(s=><div key={s.id} className="flex items-center justify-between gap-3 border-t border-line pt-2 text-sm">
        <div>Signed in {new Date(s.created_at*1000).toLocaleString()}{s.current?" (this session)":""}</div>
        <button className="underline" disabled={busy} onClick={()=>void run(async()=>{await api.revokeSession(s.id);if(s.current){await signOut().catch(()=>{});window.location.assign("/login");}})}>Revoke</button>
      </div>)}
    </section>
    {message && <p role="status" className="text-sm">{message}</p>}
  </div>;
}
