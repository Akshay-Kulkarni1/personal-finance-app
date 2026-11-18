"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
export default function ResetPassword() {
  const [token,setToken]=useState("");
  const [password,setPassword]=useState("");
  const [message,setMessage]=useState("");
  const [busy,setBusy]=useState(false);
  useEffect(()=>{
    setToken(new URLSearchParams(window.location.hash.slice(1)).get("token") || "");
    window.history.replaceState(null,"",window.location.pathname);
  },[]);
  return <section className="mx-auto max-w-md card space-y-4 p-7"><h1 className="text-2xl font-bold">Choose a new password</h1>
    <form className="space-y-3" onSubmit={async e=>{e.preventDefault();setBusy(true);try{await api.resetPassword(token,password);setMessage("Password changed. Sign in with your new password.");setToken("");setPassword("");}catch(err){setMessage(err instanceof Error?err.message:"Reset failed");}finally{setBusy(false);}}}>
      <label className="block text-sm">New password (12+ characters)<input minLength={12} required type="password" autoComplete="new-password" value={password} onChange={e=>setPassword(e.target.value)} className="mt-1 block w-full rounded-xl border border-line bg-bg p-3" /></label>
      <button disabled={busy || !token} className="rounded-xl bg-accent px-4 py-2 font-medium text-white">{busy?"Updating…":"Reset password"}</button>
    </form>
    {!token && !message && <p className="text-sm text-muted">No reset token found. Request a new reset link.</p>}
    {message && <p role="status" className="text-sm">{message}</p>}
    <Link href="/login" className="block text-sm underline">Back to sign in</Link>
  </section>;
}
