"use client";
import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
export default function ForgotPassword() {
  const [email,setEmail]=useState("");
  const [message,setMessage]=useState("");
  const [busy,setBusy]=useState(false);
  return <section className="mx-auto max-w-md card space-y-4 p-7">
    <h1 className="text-2xl font-bold">Reset your password</h1>
    <p className="text-sm text-muted">We&apos;ll email you a one-time reset link if there&apos;s an account with this address.</p>
    <form onSubmit={async e=>{e.preventDefault();setBusy(true);try{await api.forgotPassword(email);setMessage("If an account exists, check your email for a reset link.");}catch{setMessage("Unable to send a reset email right now.");}finally{setBusy(false);}}} className="space-y-4">
      <label className="block text-sm">Email<input required type="email" value={email} onChange={e=>setEmail(e.target.value)} className="mt-1 block w-full rounded-xl border border-line bg-bg p-3" /></label>
      <button disabled={busy} className="rounded-xl bg-accent px-4 py-2 font-medium text-white">{busy?"Sending…":"Send reset link"}</button>
    </form>
    {message && <p role="status" className="text-sm">{message}</p>}
    <Link href="/login" className="block text-sm underline">Back to sign in</Link>
  </section>;
}
