"use client";
import { Lock } from "lucide-react";
import { LogoMark } from "@/components/Logo";
import { useRouter } from "next/navigation";
import { useState } from "react";
import Link from "next/link";
import { PrimaryButton } from "@/components/ui";
import { useAuth } from "@/lib/auth";

export default function Login() {
  const router = useRouter();
  const { signIn, completeMfa } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [register, setRegister] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [challenge, setChallenge] = useState("");
  const [code, setCode] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      if (challenge) {
        await completeMfa(challenge, code);
        router.replace("/");
      } else {
        const result = await signIn(email, password, register);
        if (result && result.mfa_required) { setChallenge(result.challenge); setPassword(""); }
        else router.replace("/");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to sign in");
    } finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-bg px-4 py-10">
      <div className="w-full max-w-[400px]">
        <div className="mb-8 flex flex-col items-center text-center">
          <LogoMark size={56} className="drop-shadow-[0_10px_24px_rgba(21,24,27,0.25)]" />
          <h1 className="mt-4 text-[28px] font-bold tracking-tight">Welcome to Ahead</h1>
          <p className="mt-1 text-[15px] text-muted">Your money, a little clearer.</p>
        </div>
        <form onSubmit={submit} className="card space-y-4 p-6 sm:p-8">
          <h2 className="text-[18px] font-semibold">{challenge ? "Authenticator verification" : register ? "Create your account" : "Sign in"}</h2>
          {challenge ? <p className="text-sm text-muted">Enter your 6-digit authenticator code or a one-time recovery code.</p> : null}
          {!challenge && <div>
            <label htmlFor="email" className="text-[13px] font-medium text-muted">Email</label>
            <input id="email" required type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email"
              className="mt-1 w-full rounded-xl border border-line bg-bg px-3.5 py-2.5 text-[15px] outline-none focus:border-accent" />
          </div>}
          {!challenge && <div>
            <label htmlFor="pw" className="text-[13px] font-medium text-muted">Password (12+ characters)</label>
            <input id="pw" required minLength={12} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={register ? "new-password" : "current-password"}
              className="mt-1 w-full rounded-xl border border-line bg-bg px-3.5 py-2.5 text-[15px] outline-none focus:border-accent" />
          </div>}
          {challenge && <div>
            <label htmlFor="otp" className="text-[13px] font-medium text-muted">Authentication code</label>
            <input id="otp" required autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value.trim())} className="mt-1 w-full rounded-xl border border-line bg-bg px-3.5 py-2.5 text-[15px] outline-none focus:border-accent" />
          </div>}
          {error && <p role="alert" className="text-sm text-act">{error}</p>}
          <PrimaryButton type="submit" disabled={busy} className="w-full justify-center">
            {busy ? "One moment…" : challenge ? "Verify and sign in" : register ? "Create account" : "Sign in"}
          </PrimaryButton>
          <button type="button" onClick={() => {
            if (challenge) { setChallenge(""); setCode(""); }
            else setRegister(!register);
            setError("");
          }} className="w-full text-center text-[13px] text-muted hover:text-ink">
            {challenge ? "Back to sign in" : register ? "Already have an account? Sign in" : "New to Ahead? Create an account"}
          </button>
          {!register && !challenge && <Link href="/forgot-password" className="block text-center text-[13px] text-muted underline">Forgot password?</Link>}
        </form>
        <p className="mt-6 flex items-center justify-center gap-1.5 text-[13px] text-muted"><Lock size={13} aria-hidden /> Secure sign-in · No demo passwords</p>
      </div>
    </div>
  );
}
