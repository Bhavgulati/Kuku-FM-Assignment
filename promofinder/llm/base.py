"""Provider-agnostic LLM client.

Every pipeline stage calls `LLMClient.complete_json(system, user, tag=...)` and gets a
dict back. Swapping the model = changing `provider` / `model` / `base_url` in
config.yaml or on the command line. A new provider is one class with a `call()`
method returning (text, usage).

The client adds, for every provider:
  * a disk cache keyed by (provider, model, prompts, temperature, replica) so reruns
    are free and an interrupted run resumes where it stopped;
  * a shared requests-per-minute throttle (thread safe);
  * retries with backoff on 429 / 5xx / timeouts, honouring the server's retry hint;
  * JSON extraction + one corrective retry if the model returns invalid JSON;
  * a raw log of every response in the run's output folder;
  * token accounting.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


class LLMError(RuntimeError):
    pass


class TransientError(LLMError):
    """Worth retrying (rate limit, server error, timeout)."""

    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


class QuotaExhausted(LLMError):
    """Daily / hard quota hit - retrying now will not help. Rerun later; the cache keeps progress."""


@dataclass
class Usage:
    calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0
    json_repairs: int = 0
    seconds: float = 0.0
    by_tag: dict = field(default_factory=dict)

    def add(self, tag: str, usage: dict, cached: bool, seconds: float = 0.0):
        stage = tag.split(":", 1)[0]
        t = self.by_tag.setdefault(stage, {"calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0})
        if cached:
            self.cache_hits += 1
            t["cache_hits"] += 1
        else:
            self.calls += 1
            t["calls"] += 1
            self.seconds += seconds
        inp, out = int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
        self.input_tokens += inp
        self.output_tokens += out
        t["input_tokens"] += inp
        t["output_tokens"] += out

    def to_dict(self) -> dict:
        return {
            "api_calls": self.calls,
            "cache_hits": self.cache_hits,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "retries": self.retries,
            "json_repairs": self.json_repairs,
            "api_seconds": round(self.seconds, 1),
            "by_stage": self.by_tag,
        }


class Provider:
    """Implement `call` for a new backend. Return (text, {'input_tokens': n, 'output_tokens': m})."""

    name = "base"

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None, extra: dict | None = None,
                 timeout: float = 180.0):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.extra = extra or {}
        self.timeout = timeout

    def call(self, system: str, user: str, temperature: float, tag: str) -> tuple[str, dict]:
        raise NotImplementedError

    def list_models(self) -> list[str]:
        raise NotImplementedError


class Throttle:
    def __init__(self, max_rpm: float | None):
        self.interval = 60.0 / max_rpm if max_rpm else 0.0
        self.lock = threading.Lock()
        self.next_time = 0.0

    def wait(self):
        if not self.interval:
            return
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next_time)
            self.next_time = slot + self.interval
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str):
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # first balanced {...} or [...]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("no valid JSON found in model output")


def items_of(res, key: str) -> list[dict]:
    """Models sometimes return a bare list instead of {key: [...]}; accept both."""
    if isinstance(res, list):
        seq = res
    elif isinstance(res, dict):
        seq = res.get(key)
        if seq is None and len(res) == 1 and isinstance(next(iter(res.values())), list):
            seq = next(iter(res.values()))  # same list under a different key
    else:
        seq = None
    return [x for x in (seq or []) if isinstance(x, dict)]


def one_dict(res) -> dict:
    if isinstance(res, dict):
        return res
    if isinstance(res, list) and res and isinstance(res[0], dict):
        return res[0]
    return {}


class LLMClient:
    def __init__(self, provider: Provider, cache_dir: str | Path, raw_dir: str | Path | None = None,
                 max_rpm: float | None = None, max_retries: int = 6, temperature: float = 0.3):
        self.provider = provider
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir = Path(raw_dir) if raw_dir else None
        if self.raw_dir:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.throttle = Throttle(max_rpm)
        self.max_retries = max_retries
        self.default_temperature = temperature
        self.usage = Usage()
        self._usage_lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return f"{self.provider.name}:{self.provider.model}"

    def _key(self, system: str, user: str, temperature: float, replica: int) -> str:
        blob = json.dumps(
            {"p": self.provider.name, "m": self.provider.model, "x": self.provider.extra, "s": system, "u": user,
             "t": temperature, "r": replica},
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _call_with_retries(self, system: str, user: str, temperature: float, tag: str) -> tuple[str, dict, float]:
        attempt = 0
        while True:
            self.throttle.wait()
            t0 = time.monotonic()
            try:
                text, usage = self.provider.call(system, user, temperature, tag)
                return text, usage, time.monotonic() - t0
            except TransientError as e:
                attempt += 1
                with self._usage_lock:
                    self.usage.retries += 1
                if attempt > self.max_retries:
                    raise LLMError(f"[{tag}] giving up after {self.max_retries} retries: {e}") from e
                delay = e.retry_after if e.retry_after else min(120.0, 5.0 * 2 ** (attempt - 1))
                print(f"  ! {tag}: {e} -> retry {attempt}/{self.max_retries} in {delay:.0f}s", flush=True)
                time.sleep(delay)

    def complete_json(self, system: str, user: str, *, tag: str, temperature: float | None = None,
                      replica: int = 0) -> dict:
        temperature = self.default_temperature if temperature is None else temperature
        key = self._key(system, user, temperature, replica)
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            rec = json.loads(cache_file.read_text(encoding="utf-8"))
            with self._usage_lock:
                self.usage.add(tag, rec.get("usage", {}), cached=True)
            self._log_raw(tag, key, rec, cached=True)
            return rec["parsed"]

        text, usage, secs = self._call_with_retries(system, user, temperature, tag)
        try:
            parsed = extract_json(text)
        except ValueError:
            with self._usage_lock:
                self.usage.json_repairs += 1
            fix_user = (
                user
                + "\n\nYOUR PREVIOUS REPLY WAS NOT VALID JSON. Reply again with ONLY the JSON object, no prose:\n"
                + text[:2000]
            )
            text2, usage2, secs2 = self._call_with_retries(system, fix_user, temperature, tag + ":json-fix")
            usage = {k: int(usage.get(k, 0) or 0) + int(usage2.get(k, 0) or 0) for k in ("input_tokens", "output_tokens")}
            secs += secs2
            text = text2
            try:
                parsed = extract_json(text2)
            except ValueError as e:
                raise LLMError(f"[{tag}] model returned invalid JSON twice") from e
        rec = {
            "tag": tag,
            "model": self.model_id,
            "temperature": temperature,
            "replica": replica,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "system": system,
            "user": user,
            "response_text": text,
            "parsed": parsed,
            "usage": usage,
            "seconds": round(secs, 2),
        }
        cache_file.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        with self._usage_lock:
            self.usage.add(tag, usage, cached=False, seconds=secs)
        self._log_raw(tag, key, rec, cached=False)
        return parsed

    def _log_raw(self, tag: str, key: str, rec: dict, cached: bool):
        if not self.raw_dir:
            return
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", tag)[:80]
        out = {k: rec[k] for k in ("tag", "model", "temperature", "replica", "created", "response_text", "usage") if k in rec}
        out["from_cache"] = cached
        out["prompt_sha256"] = key
        (self.raw_dir / f"{safe}__{key[:10]}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                                               encoding="utf-8")
