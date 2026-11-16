# Ahead

**Banks show your past. Ahead shows what's next.**

Ahead is an AI money assistant. It sees all your money in one place, predicts trouble before it happens, and fixes it with one tap. Nothing moves without your OK.

## The problem

- Only 63% of US adults can cover a $400 surprise without borrowing (Fed SHED, May 2026).
- Over $12B in overdraft and NSF fees were paid in 2025 (NCLC).
- 47% of pay-later users paid late in the past year (LendingTree 2026).

Money is spread across checking, savings, cards, loans and pay-later plans, and banking apps only show what already happened.

## What it does

| See | Predict | Act |
|---|---|---|
| Checking, savings, card, loans, pay-later, 401(k) and IRA in one place | 60-day forecast with an uncertainty band and a "safe until" date | Alerts that each come with 3 fixes and a before/after preview |
| Health score from 4 indicators | What-if: two lines, now vs with the change | Approve → real Nessie transfer → score and date update, with Undo |
| Upload a bill, receipt, PDF or pay-later screenshot | "Should I buy this?" from a price photo | Cancel unused subscriptions, autopilot savings rules |

Plus: chat and push-to-talk voice, a live "How I figured this out" trace for every answer, and a Verified badge when every number checks out against the tools.

## Demo (Maya, 24, first job)

1. Home: balances, budget, dues, card, Future you.
2. **Simulate next week** → the watcher raises "Rent on Dec 1 may take you below $0" with 3 fixes.
3. **Approve** the best fix → score 58 → 64, safe date moves later.
4. Plan → Money leaks → cancel unused charges → money-saved counter.
5. Ask → **upload a pay-later screenshot** → the forecast and alerts update.
6. Hold the mic: "Can I afford a trip in March?" → "Yes, if you save $100 a week…" → "Yes" → autopilot rule.

## Architecture

```
Browser (Next.js) ──► FastAPI ──► Guard ──► Planner (Nemotron) ──► Python tools ──► Critic ──► answer
     ▲  SSE steps        │                                          │
     │                   ├── Watcher (proactive alerts on simulate / upload / approve)
     │                   ├── Gemini: reads photos & PDFs into JSON (never decides)
     │                   ├── Nemotron Speech: ASR + TTS (browser fallback)
     └── approve / undo ─┴── Ledger (Tiger Data / Postgres) ◄──► Capital One Nessie (read + write)
```

- **Python does all the math.** The LLM only plans and explains; a critic checks every number against tool outputs.
- Every external call has a timeout, 3 retries with backoff, and a fallback. With no keys at all, the app runs on built-in demo data and a rule-based planner, so the demo never breaks.

## Run it

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # keys are optional
.venv/bin/uvicorn main:app --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:3000. Tests: `cd backend && .venv/bin/pytest -q tests`.

Optional: `python seed.py` (needs `NESSIE_API_KEY`) creates Maya in Nessie; `python reset_demo.py` resets everything. Server voice needs `ffmpeg`.

## Deploy (backup)

- Backend: Render, using `render.yaml`. Set `FRONTEND_ORIGINS` to the frontend URL.
- Frontend: Vercel with root `frontend` and `NEXT_PUBLIC_API_URL` set to the Render URL.

## Hackathon tracks

- **PNC 360° Financial Health Navigator:** full picture, 4 health indicators, forecast, what-if, coach, one clear next move.
- **NVIDIA Nemotron beyond a chatbot:** proactive watcher, multi-step tool use, guard, voice.
- **Capital One best use of Nessie:** reads accounts and history, writes transfers and bills.
- **Gemini:** document and screenshot understanding. **Tiger Data:** TimescaleDB ledger.

*For education only. Not financial advice.*
