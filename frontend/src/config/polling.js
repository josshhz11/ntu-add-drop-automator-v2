// Polling cadence for the swap-status endpoint. Kept in one place so it's
// easy to tune without hunting through component code.
//
// The backend tells us which `phase` a session is in on every response:
//   - "active": a round of attempts is actively in progress (per-index
//     updates land roughly every 1-3s) — poll frequently to show them live.
//   - "idle": between rounds, during the multi-minute retry gap — nothing
//     will change until the next round starts, so poll infrequently.
//   - "done": a terminal state — nothing will ever change again.
export const POLL_INTERVAL_ACTIVE_MS = 2000;
export const POLL_INTERVAL_IDLE_MS = 20000;
// Backoff used after a failed request (network hiccup, etc.), separate from
// the phase-driven intervals above.
export const POLL_INTERVAL_ERROR_MS = 20000;
