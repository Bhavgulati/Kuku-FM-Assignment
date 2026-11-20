"""Concrete providers. Each one only knows how to send (system, user) and return text."""
from __future__ import annotations

import json
import re

import requests

from .base import LLMError, Provider, QuotaExhausted, TransientError

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


def _retry_after_header(resp) -> float | None:
    v = resp.headers.get("Retry-After")
    try:
        return float(v) if v else None
    except ValueError:
        return None


class GeminiProvider(Provider):
    """Google Gemini via the public REST API (generateContent). Needs GEMINI_API_KEY."""

    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def _headers(self):
        if not self.api_key:
            raise LLMError("Gemini API key missing: set GEMINI_API_KEY in promo-finder/.env or the environment")
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def call(self, system, user, temperature, tag):
        base = self.base_url or self.BASE
        gen_cfg = {"temperature": temperature, "responseMimeType": "application/json"}
        gen_cfg.update(self.extra.get("generation_config", {}) or {})
        threshold = self.extra.get("safety_threshold", "BLOCK_ONLY_HIGH")
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen_cfg,
            "safetySettings": [
                {"category": c, "threshold": threshold}
                for c in (
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                )
            ],
        }
        url = f"{base}/models/{self.model}:generateContent"
        try:
            resp = requests.post(url, headers=self._headers(), json=body, timeout=self.timeout)
        except (requests.Timeout, requests.ConnectionError) as e:
            raise TransientError(f"network error: {e.__class__.__name__}") from e
        if resp.status_code != 200:
            self._raise_for(resp)
        data = resp.json()
        cands = data.get("candidates") or []
        if not cands:
            fb = data.get("promptFeedback", {})
            raise LLMError(f"[{tag}] no candidates returned (blockReason={fb.get('blockReason')})")
        cand = cands[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise TransientError(f"empty response (finishReason={cand.get('finishReason')})")
        um = data.get("usageMetadata", {})
        usage = {
            "input_tokens": um.get("promptTokenCount", 0),
            # thinking tokens are billed as output tokens
            "output_tokens": (um.get("candidatesTokenCount", 0) or 0) + (um.get("thoughtsTokenCount", 0) or 0),
        }
        return text, usage

    def _raise_for(self, resp):
        try:
            err = resp.json().get("error", {})
        except ValueError:
            err = {"message": resp.text[:300]}
        msg = f"HTTP {resp.status_code}: {err.get('message', '')[:160]}"
        if resp.status_code == 429:
            details = err.get("details", []) or []
            quota_ids = [v.get("quotaId", "") for d in details for v in d.get("violations", []) or []]
            retry = None
            m = re.search(r'"retryDelay":\s*"([\d.]+)s"', json.dumps(details))
            if m:
                retry = float(m.group(1)) + 1
            if quota_ids:
                msg += f" [quota: {', '.join(quota_ids)}]"
            # Only a per-day limit is fatal; per-minute limits (requests or tokens) just need a wait.
            if quota_ids and all("PerDay" in q for q in quota_ids):
                raise QuotaExhausted(msg + " (daily free-tier quota; rerun later - cached work is kept)")
            raise TransientError(msg, retry_after=retry or _retry_after_header(resp) or 30.0)
        if resp.status_code in TRANSIENT_STATUS:
            raise TransientError(msg, retry_after=_retry_after_header(resp))
        raise LLMError(msg)

    def list_models(self):
        base = self.base_url or self.BASE
        resp = requests.get(f"{base}/models", headers=self._headers(), params={"pageSize": 1000}, timeout=60)
        if resp.status_code != 200:
            self._raise_for(resp)
        return [
            m["name"].removeprefix("models/")
            for m in resp.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]


class OpenAICompatProvider(Provider):
    """Any OpenAI-compatible /chat/completions endpoint: Groq, OpenRouter, Ollama, vLLM,
    Together, OpenAI itself, or an API Kuku provides. Set base_url + model (+ key env)."""

    name = "openai_compat"

    def call(self, system, user, temperature, tag):
        if not self.base_url:
            raise LLMError("openai_compat provider needs base_url (e.g. https://api.groq.com/openai/v1)")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.extra.get("json_mode", True):
            body["response_format"] = {"type": "json_object"}
        body.update(self.extra.get("body", {}) or {})
        url = self.base_url.rstrip("/") + "/chat/completions"
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=self.timeout)
        except (requests.Timeout, requests.ConnectionError) as e:
            raise TransientError(f"network error: {e.__class__.__name__}") from e
        if resp.status_code == 400 and "response_format" in resp.text and "response_format" in body:
            # endpoint does not support JSON mode: retry once without it (prompts already demand JSON)
            body.pop("response_format")
            resp = requests.post(url, headers=headers, json=body, timeout=self.timeout)
        if resp.status_code != 200:
            msg = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if resp.status_code in TRANSIENT_STATUS:
                raise TransientError(msg, retry_after=_retry_after_header(resp))
            raise LLMError(msg)
        data = resp.json()
        text = data["choices"][0]["message"].get("content") or ""
        u = data.get("usage", {}) or {}
        return text, {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}

    def list_models(self):
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        resp = requests.get(self.base_url.rstrip("/") + "/models", headers=headers, timeout=60)
        resp.raise_for_status()
        return [m["id"] for m in resp.json().get("data", [])]


class OfflineProvider(Provider):
    """Deterministic stand-in with no network, for tests and dry runs. It is NOT a
    quality method - it returns structurally valid answers so the whole pipeline can be
    exercised. (The rules-only baseline lives in heuristic.py.)"""

    name = "offline"

    def call(self, system, user, temperature, tag):
        stage = tag.split(":", 1)[0]
        h = sum(map(ord, user)) % 5  # stable pseudo-score source
        if stage in ("propose", "repair"):
            ids = re.findall(r"^(B\d{3}) (\d\d):(\d\d\.\d\d)–(\d\d):(\d\d\.\d\d)", user, re.M)
            promos = []
            if ids:
                times = [(i, int(a) * 60 + float(b), int(c) * 60 + float(d)) for i, a, b, c, d in ids]
                start = times[min(len(times) - 1, len(times) // 3)]
                end = next((t for t in times if t[2] - start[1] >= 35), times[-1])
                promos.append({"start_block": start[0], "end_block": end[0], "hook": "offline hook",
                               "cliffhanger": "offline cliffhanger", "why": "offline stub", "trope": "other"})
            return json.dumps({"promos": promos}), {}
        if stage == "montage":
            return json.dumps({"montages": []}), {}
        if stage == "judge_hook":
            ids = re.findall(r"^### (H\d+)", user, re.M)
            return json.dumps({"ratings": [{"id": i, "score": 1 + (h + n) % 5, "reason": "offline"}
                                           for n, i in enumerate(ids)]}), {}
        if stage == "judge_blind":
            return json.dumps({"understood": "offline", "confusions": [], "open_question": "offline",
                               "cliffhanger": 1 + h, "standalone": 1 + (h + 2) % 5, "would_watch_more": h > 2}), {}
        if stage == "pair":
            ids = re.findall(r"^### (P\d+)", user, re.M)
            return json.dumps({"results": [{"id": i, "hook": "1" if (h + n) % 2 else "2", "overall": "1",
                                            "reason": "offline"} for n, i in enumerate(ids)]}), {}
        if stage == "beats":
            beats = []
            for part in re.split(r"^=== EPISODE ", user, flags=re.M)[1:]:
                epn = int(part.split(" ", 1)[0])
                rows = re.findall(r"^(B\d{3}) (\d\d):(\d\d\.\d\d)–(\d\d):(\d\d\.\d\d)", part, re.M)
                t = [(i, int(a) * 60 + float(b), int(c) * 60 + float(d)) for i, a, b, c, d in rows]
                if t:
                    end = next((x for x in t if x[2] - t[0][1] >= 12), t[-1])
                    beats.append({"episode": epn, "start_block": t[0][0], "end_block": end[0], "kind": "conflict",
                                  "summary": "offline beat", "needs": "nothing"})
            return json.dumps({"beats": beats}), {}
        if stage == "links":
            ids = re.findall(r"^(E\d\d\.\d+) ", user, re.M)
            return json.dumps({"links": [{"setup": ids[0], "payoff": ids[-1], "why": "offline"}] if len(ids) > 1 else [],
                               "threads": [{"name": "offline", "beats": ids}]}), {}
        if stage == "smontage":
            beats = re.findall(r"^(E\d\d\.\d+) \[\w+\] \(([\d.]+)s\)", user, re.M)
            pick, total = [], 0.0
            for bid, dur in beats:
                if total < 25 and len(pick) < 3:
                    pick.append(bid)
                    total += float(dur)
            ms = [{"beats": pick, "hook": "offline", "cliffhanger": "offline", "why": "offline", "trope": "other"}]
            return json.dumps({"montages": ms if len(pick) > 1 else []}), {}
        if stage == "grade":
            ids = re.findall(r"^### (G\d+)", user, re.M)
            return json.dumps({"grades": [{"id": i, "grade": "consistent", "note": "offline"} for i in ids]}), {}
        return "{}", {}

    def list_models(self):
        return ["offline"]


PROVIDERS = {p.name: p for p in (GeminiProvider, OpenAICompatProvider, OfflineProvider)}


def make_provider(name: str, model: str, api_key: str | None, base_url: str | None, extra: dict | None,
                  timeout: float = 180.0) -> Provider:
    if name not in PROVIDERS:
        raise LLMError(f"unknown provider {name!r}; choose one of {sorted(PROVIDERS)}")
    return PROVIDERS[name](model=model, api_key=api_key, base_url=base_url, extra=extra, timeout=timeout)
