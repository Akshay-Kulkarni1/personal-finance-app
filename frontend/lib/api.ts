// Same-origin API gateway; works with either localhost or 127.0.0.1 and
// avoids cross-origin cookies, CORS and mixed-network-family issues.
// The server-side Next.js rewrite is configured in next.config.ts.
export const API = "/api/ahead";

export type Indicator = { points: number; value: number; why: string };
export type Fix = {
  id: string;
  title: string;
  action: unknown;
  preview?: { score: number; safe_until: string | null; days: number[] };
};
export type Alert = {
  id: string;
  kind: string;
  level: "good" | "watch" | "act";
  title: string;
  body: string;
  fixes: Fix[];
  trace_id?: string;
  current?: { score: number; safe_until: string | null };
};
export type Summary = {
  name: string;
  today: string;
  safe_until: string | null;
  safe_why: string;
  lowest: { date: string; balance: number };
  score: number;
  indicators: Record<string, Indicator>;
  balances: Record<string, number>;
  card_limit: number;
  money_saved: { monthly: number; yearly: number; count: number; why: string };
  top_alert: Alert | null;
  alert_count: number;
};
export type Forecast = {
  safe_until: string | null;
  why: string;
  days: { date: string; p10: number; p50: number; p90: number }[];
  markers: { date: string; label: string; amount: number }[];
};
export type Overview = {
  budget: { month_passed: number; categories: { category: string; plan: number; spent: number; used: number; status: "good" | "watch" | "act" }[] };
  debt: { dues: { date: string; name: string; amount: number }[]; total_due: number; owed: { name: string; balance: number; apr: number }[]; total_owed: number };
  card: {
    owed: number; limit: number; used: number; available: number; apr: number;
    next_payment: { date: string; amount: number } | null;
    categories: { category: string; amount: number }[];
    top_merchants: { merchant: string; amount: number }[];
    why: string;
    credit: {
      score: number; band: string; min: number; max: number; level: "good" | "watch" | "act"; why: string;
      factors: { name: string; value: string; level: "good" | "watch" | "act"; tip: string }[];
    };
  };
  investments: {
    accounts: { name: string; balance: number; holding: string }[];
    total: number;
    steps: { id: string; title: string; done: boolean; why: string }[];
    next_step: string | null;
    moves: { id: string; do: string; cost: string; outcome: string; outcome_note: string }[];
    free_money_missed: number;
    projection: { age: number; assumed_return: number; today_plan: number; with_full_match: number; why: string };
    disclaimer: string;
  };
};
export type Leak = { bill_id: string; merchant: string; amount: number; yearly: number; type: string; why: string; date?: string; old?: number };
export type Cancelled = { bill_id: string; merchant: string; amount: number; yearly: number };
export type Payoff = { months: number; interest: number; debt_free: string; order: string[] };
export type BudgetData = {
  budget: { income: number; savings_first: number; lines: { category: string; monthly: number; fixed: boolean }[]; why: string; left_over?: number };
  pace: Overview["budget"];
  debt: { avalanche: Payoff; snowball: Payoff; plus_100: Payoff; same_order: boolean; minimum_trap: { months: number; interest: number; why: string } };
};
export type AskAnswer = {
  answer: string;
  spoken_text: string;
  action: { kind: string; [k: string]: unknown } | null;
  verified: boolean;
  blocked?: boolean;
  trace_id: string;
};
export type UploadResult = {
  ok: boolean;
  why?: string;
  type?: "bnpl" | "bill" | "receipt" | "price";
  found?: string;
  action?: { kind: string; [k: string]: unknown };
  before?: { score: number; safe_until: string | null };
  after?: { score: number; safe_until: string | null };
  impact?: { fits: boolean; why: string; safe_until_now: string | null; safe_until_after: string | null };
};
export type BriefCard = { title: string; line: string; level: "good" | "watch" | "act"; link: string; why?: string };
export type Brief = { date: string; cards: { subscriptions: BriefCard; update: BriefCard; limit: BriefCard } };
export type Trace = {
  id: string;
  question: string;
  planner: string;
  steps?: string[];
  tool_calls: { tool: string; args?: unknown; result: unknown; ms?: number }[];
  critic: { verified: boolean; unverified: string[] };
  ms: number;
};

let csrfToken: string | null = null;
export function setCsrfToken(value: string | null) { csrfToken = value; }
export function getCsrfToken() { return csrfToken ?? ""; }
export type User = { id: string; email: string; email_verified: boolean; mfa_enabled: boolean };
export type AuthResponse = { user: User; csrf_token: string };
export type MfaChallenge = { mfa_required: true; challenge: string };
export type UserSession = { id: string; current: boolean; created_at: number; expires_at: number };
export type BankConnection = { id: string; institution_id: string | null; institution_name: string | null; status: string; created_at: number; updated_at: number; last_synced_at: number | null; sync_status: string | null; next_sync_at: number | null; sync_error: string | null };
export type BankAccount = { id: string; connection_id: string; name: string; mask: string | null; type: string | null; subtype: string | null; currency: string | null; current_balance: number | null; available_balance: number | null; updated_at: number };
export type BankTransaction = { id: string; account_id: string; date: string; amount: number; amount_decimal: string | null; currency: string | null; pending_transaction_id: string | null; name: string; merchant: string | null; category: string | null; pending: number };

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${API}${path}`, {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(csrfToken && init?.method && init.method !== "GET" ? { "X-CSRF-Token": csrfToken } : {}), ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    throw new Error("Ahead could not reach its API. Check that the backend is running and run ./scripts/doctor-local.sh.");
  }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : null;
    if (r.status >= 500 && !detail) {
      throw new Error("Ahead's API isn't available yet. Check the backend terminal or run ./scripts/doctor-local.sh.");
    }
    throw new Error(detail ?? "Something went wrong. Try again.");
  }
  return r.json();
}

export const api = {
  config: () => call<{ demo_mode: boolean }>("/config"),
  me: () => call<AuthResponse>("/auth/me"),
  register: (email: string, password: string) => call<AuthResponse>("/auth/register", { method: "POST", body: JSON.stringify({ email, password }) }),
  login: (email: string, password: string) => call<AuthResponse | MfaChallenge>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  completeMfa: (challenge: string, code: string) => call<AuthResponse>("/auth/mfa/complete", { method: "POST", body: JSON.stringify({ challenge, code }) }),
  forgotPassword: (email: string) => call<{ ok: boolean }>("/auth/forgot-password", { method: "POST", body: JSON.stringify({ email }) }),
  resetPassword: (token: string, password: string) => call<{ ok: boolean }>("/auth/reset-password", { method: "POST", body: JSON.stringify({ token, password }) }),
  verifyEmail: (token: string) => call<{ ok: boolean }>("/auth/verify-email", { method: "POST", body: JSON.stringify({ token }) }),
  resendVerification: () => call<{ ok: boolean }>("/auth/resend-verification", { method: "POST" }),
  mfaSetup: (password: string) => call<{ secret: string; otpauth_uri: string }>("/auth/mfa/setup", { method: "POST", body: JSON.stringify({ password }) }),
  mfaEnable: (code: string) => call<{ ok: boolean; recovery_codes: string[] }>("/auth/mfa/enable", { method: "POST", body: JSON.stringify({ code }) }),
  mfaDisable: (password: string, code: string) => call<{ ok: boolean }>("/auth/mfa/disable", { method: "POST", body: JSON.stringify({ password, code }) }),
  sessions: () => call<{ sessions: UserSession[] }>("/auth/sessions"),
  revokeSession: (session_id: string) => call<{ ok: boolean }>("/auth/sessions/revoke", { method: "POST", body: JSON.stringify({ session_id }) }),
  logout: () => call<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  deleteAccount: (password: string, code?: string) => call<{ deleted: boolean }>("/auth/privacy/delete", { method: "POST", body: JSON.stringify({ password, code: code || null }) }),
  bankStatus: () => call<{ configured: boolean; environment: string; webhook_configured: boolean }>("/bank/status"),
  bankLinkToken: () => call<{ link_token: string }>("/bank/link-token", { method: "POST" }),
  bankUpdateLinkToken: (connection_id: string) => call<{ link_token: string }>("/bank/update-link-token", { method: "POST", body: JSON.stringify({ connection_id }) }),
  bankUpdateComplete: (connection_id: string) => call<{ queued: boolean }>("/bank/update-complete", { method: "POST", body: JSON.stringify({ connection_id }) }),
  bankExchange: (public_token: string, institution_id?: string, institution_name?: string) =>
    call<{ connection_id: string; accounts_connected: number }>("/bank/exchange", { method: "POST", body: JSON.stringify({ public_token, institution_id, institution_name }) }),
  bankConnections: () => call<{ connections: BankConnection[] }>("/bank/connections"),
  bankAccounts: () => call<{ accounts: BankAccount[] }>("/bank/accounts"),
  bankTransactions: () => call<{ transactions: BankTransaction[] }>("/bank/transactions"),
  bankSync: (connection_id: string) => call<{ ok: boolean; added: number; modified: number; removed: number }>("/bank/sync", { method: "POST", body: JSON.stringify({ connection_id }) }),
  bankConfigureWebhook: (connection_id: string) => call<{ configured: boolean }>("/bank/webhook/configure", { method: "POST", body: JSON.stringify({ connection_id }) }),
  bankFireTestWebhook: (connection_id: string) => call<{ requested: boolean }>("/bank/sandbox-fire-webhook", { method: "POST", body: JSON.stringify({ connection_id }) }),
  bankDisconnect: (connection_id: string) => call<{ ok: boolean }>("/bank/disconnect", { method: "POST", body: JSON.stringify({ connection_id }) }),
  summary: () => call<Summary>("/summary"),
  forecast: () => call<Forecast>("/forecast"),
  overview: () => call<Overview>("/overview"),
  budget: () => call<BudgetData>("/budget"),
  budgetBuild: (monthly_saving: number) =>
    call<BudgetData["budget"]>("/budget/build", { method: "POST", body: JSON.stringify({ monthly_saving }) }),
  leaks: () => call<{ leaks: Leak[]; monthly_total: number; yearly_total: number; cancelled: Cancelled[] }>("/leaks"),
  restoreLeak: (bill_id: string) => call("/leaks/restore", { method: "POST", body: JSON.stringify({ bill_id }) }),
  cancelLeak: (bill_id: string) =>
    call<{ undo_id: string; email: string; money_saved: { yearly: number } }>("/leaks/cancel", { method: "POST", body: JSON.stringify({ bill_id }) }),
  alerts: () => call<{ alerts: Alert[] }>("/alerts"),
  brief: () => call<Brief>("/brief"),
  trace: (id: string) => call<Trace>(`/trace/${id}`),
  simulateWeek: () => call<{ alerts: Alert[] }>("/simulate-week", { method: "POST" }),
  approve: (alert_id: string, fix_id: string) =>
    call<{ undo_id: string; summary: { score: number; safe_until: string | null } }>("/approve", {
      method: "POST",
      body: JSON.stringify({ alert_id, fix_id }),
    }),
  approveAction: (action: unknown) =>
    call<{ undo_id: string; summary: { score: number; safe_until: string | null } }>("/approve", {
      method: "POST",
      body: JSON.stringify({ action }),
    }),
  undo: (undo_id: string) => call("/undo", { method: "POST", body: JSON.stringify({ undo_id }) }),
  reset: () => call("/reset", { method: "POST" }),
  rules: () => call<{ rules: { id: string; kind: string; amount: number; start?: string }[] }>("/rules"),
  deleteRule: (id: string) => call("/rules/delete", { method: "POST", body: JSON.stringify({ id }) }),
  /** Photo or PDF (or the built-in sample) -> what Ahead read + a preview. Saves nothing. */
  upload: async (file: File | null): Promise<UploadResult> => {
    const fd = new FormData();
    if (file) fd.append("file", file);
    const r = await fetch(`${API}/upload${file ? "" : "?sample=true"}`, { method: "POST", body: fd, credentials: "include", headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {}, cache: "no-store" });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(r.status === 503 ? "I can't read files right now. Try the sample instead." : body.detail ?? "Something went wrong. Try again.");
    return body;
  },
};

export function fmtDate(iso: string | null, long = false) {
  if (!iso) return "60+ days";
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("en-US", long ? { weekday: "short", month: "short", day: "numeric" } : { month: "short", day: "numeric" });
}

export function fmtMonth(iso: string) {
  return new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

export function usd(n: number, cents = false) {
  const s = Math.abs(n).toLocaleString("en-US", {
    minimumFractionDigits: cents ? 2 : 0,
    maximumFractionDigits: cents ? 2 : 0,
  });
  return `${n < 0 ? "-" : ""}$${s}`;
}

// Sensitive data is fetched through the same-origin proxy and never sent to a
// third-party download service. The backend streams the complete JSON export.
export async function downloadPersonalData(password: string, code?: string): Promise<Blob> {
  let response: Response;
  try {
    response = await fetch(`${API}/auth/privacy/export`, {
      method: "POST", credentials: "include", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() },
      body: JSON.stringify({ password, code: code || null }),
    });
  } catch {
    throw new Error("Ahead could not reach its API. Run ./scripts/doctor-local.sh.");
  }
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(typeof problem.detail === "string" ? problem.detail : "Unable to export your data");
  }
  return response.blob();
}
