"""Per-session tokens: TTL, $ budget, revocation. Only a SHA-256 hash of each token is kept."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Session:
    session_id: str
    token_hash: str
    expires_at: float
    budget_usd: float
    run_id: str | None = None
    spent_usd: float = 0.0
    calls: int = 0
    flagged: int = 0
    last_flag: dict | None = None  # §12a screen summary of the most recent flagged chunk (no page text)
    revoked: bool = False
    created_at: float = field(default_factory=time.time)

    def view(self) -> dict:
        return {"session_id": self.session_id, "run_id": self.run_id, "spent_usd": round(self.spent_usd, 6),
                "budget_usd": self.budget_usd, "calls": self.calls, "flagged": self.flagged,
                "last_flag": self.last_flag,
                "expires_at": self.expires_at, "revoked": self.revoked}


class SessionError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class SessionStore:
    def __init__(self, clock=time.time):
        self._by_id: dict[str, Session] = {}
        self._by_hash: dict[str, str] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def create(self, session_id: str, ttl_s: float, budget_usd: float, run_id: str | None = None) -> tuple[str, Session]:
        token = secrets.token_urlsafe(32)
        with self._lock:
            old = self._by_id.get(session_id)
            if old:
                self._by_hash.pop(old.token_hash, None)
            s = Session(session_id, _hash(token), self._clock() + float(ttl_s), float(budget_usd), run_id)
            self._by_id[session_id] = s
            self._by_hash[s.token_hash] = session_id
        return token, s

    def get(self, session_id: str) -> Session | None:
        return self._by_id.get(session_id)

    def authorize(self, token: str | None) -> Session:
        """Session for a token, or SessionError 401 (unknown/expired/revoked) / 402 (budget exhausted)."""
        if not token:
            raise SessionError(401, "missing session token")
        with self._lock:
            sid = self._by_hash.get(_hash(token))
            s = self._by_id.get(sid) if sid else None
            if s is None:
                raise SessionError(401, "unknown session token")
            if s.revoked:
                raise SessionError(401, "session revoked")
            if self._clock() >= s.expires_at:
                raise SessionError(401, "session expired")
            if s.spent_usd >= s.budget_usd:
                raise SessionError(402, "session budget exhausted")
            return s

    def charge(self, session_id: str, usd: float, flagged: bool = False, flag: dict | None = None) -> None:
        with self._lock:
            s = self._by_id.get(session_id)
            if s:
                s.spent_usd += usd
                s.calls += 1
                s.flagged += int(flagged)
                if flagged and flag:
                    s.last_flag = flag

    def revoke(self, session_id: str) -> bool:
        with self._lock:
            s = self._by_id.get(session_id)
            if not s:
                return False
            s.revoked = True
            return True
