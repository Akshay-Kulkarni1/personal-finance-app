"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
export default function VerifyEmail() {
  const [message,setMessage]=useState("Verifying email…");
  const { refresh } = useAuth();
  useEffect(()=>{
    const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
    window.history.replaceState(null,"",window.location.pathname);
    if(!token){setMessage("This verification link is missing a token.");return;}
    void api.verifyEmail(token).then(async()=>{await refresh().catch(()=>{});setMessage("Your email is verified. Sign in or return to Security settings.");}).catch(()=>setMessage("This link is invalid or expired. Request a new verification email from Security settings."));
  },[refresh]);
  return <section className="mx-auto max-w-md card space-y-4 p-7"><h1 className="text-2xl font-bold">Verify email</h1><p role="status">{message}</p><Link href="/login" className="underline">Continue to Ahead</Link></section>;
}
