"""Configurable free-LLM layer (requirement 3.3 / B3).

One function, generate_text(prompt), shared by daily articles, weekly reading
and subjective grading. base_url / model / api_key all come from backend/.env;
no model name is hard-coded in business logic. Primary + domestic fallback,
with a deterministic local generator used when no key is configured so the app
still works completely offline.

Keys live only on the server and are never sent to the browser.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

TIMEOUT = int(os.getenv("LLM_TIMEOUT", "12"))
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)
_ENV_PATH = Path(__file__).resolve().parent / ".env"
_ENV_LOCK = threading.Lock()
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Known free/cheap models on the configured provider. Notes are informational
# only; MODEL_PRESETS can be overridden with LLM_MODEL_PRESETS (comma-separated
# model ids) so new models can be offered without a code change.
_PRESET_NOTES = {
    "glm-4-flash": "永久免费 · 稳定（推荐）",
    "glm-4.5-flash": "免费 · 速度较快",
    "glm-4.7-flash": "免费 · 新版，晚高峰可能限流",
    "glm-4-flashx": "免费 · 轻量快速",
    "glm-4-air": "免费额度 · 质量更高",
    "agnes-3.0-flash": "Agnes 免费档（需兜底 Key）",
}
_DEFAULT_PRESETS = "glm-4-flash,glm-4.5-flash,glm-4.7-flash"


class NoProvider(Exception):
    """No key configured - caller should use the deterministic local generator."""


class GenerationFailed(Exception):
    """Both providers failed; surface a clear retry hint instead of hanging."""


def _extract_json(text):
    """Find the first JSON object/array in a reply, tolerating markdown fences."""
    if not text:
        raise ValueError("empty response")
    cleaned = _FENCE_RE.sub("", str(text).strip())
    starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no JSON object in response")
    start = min(starts)
    end = max(cleaned.rfind("}"), cleaned.rfind("]"))
    if end < start:
        raise ValueError("unbalanced JSON")
    return json.loads(cleaned[start:end + 1])


def _slot(prefix=""):
    base = os.getenv(f"{prefix}LLM_BASE_URL", "").strip()
    key = os.getenv(f"{prefix}LLM_API_KEY", "").strip()
    model = os.getenv(f"{prefix}LLM_MODEL", "").strip()
    return base, key, model


def configured() -> bool:
    primary = _slot()
    fallback = _slot("LLM_FALLBACK_")
    return bool(primary[0] and primary[1]) or bool(fallback[0] and fallback[1])


def _chat(base_url, api_key, model, prompt, max_tokens, timeout=None):
    url = base_url.rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"].strip()


def generate_text(prompt: str, max_tokens: int = 900, timeout: int | None = None):
    """Return (text, provider_name). Raises NoProvider / GenerationFailed."""
    slots = [("primary", _slot()), ("fallback", _slot("LLM_FALLBACK_"))]
    slots = [(name, s) for name, s in slots if s[0] and s[1] and s[2]]
    if not slots:
        raise NoProvider()
    errors = []
    for name, (base, key, model) in slots:
        try:
            return _chat(base, key, model, prompt, max_tokens, timeout), f"{name}:{model}"
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                KeyError, ValueError, TimeoutError) as exc:
            errors.append(f"{name}: {type(exc).__name__}")
    raise GenerationFailed("大模型调用失败（" + "; ".join(errors) + "），请检查 .env 配置或稍后重试")


def generate_json(prompt: str, validator=None, max_tokens: int = 900):
    """Return (parsed JSON, provider). One repair retry on bad JSON.

    Free-tier providers occasionally wrap JSON in prose or markdown fences; a
    repair call is cheaper than dropping the whole generation to the local
    fallback. Network failures propagate so the caller can fall back cleanly.
    """
    text, provider = generate_text(prompt, max_tokens=max_tokens)
    try:
        data = _extract_json(text)
        if validator is not None and not validator(data):
            raise ValueError("payload failed validation")
        return data, provider
    except (ValueError, TypeError, KeyError):
        repair = (
            "Your last reply was not a valid JSON object satisfying the schema. "
            "Return ONLY the corrected JSON, with no prose or markdown.\n\n" + prompt
        )
        text, provider = generate_text(repair, max_tokens=max_tokens)
        data = _extract_json(text)
        if validator is not None and not validator(data):
            raise GenerationFailed("大模型返回格式不符合要求（已重试一次）")
        return data, provider


@lru_cache(maxsize=256)
def _grade_cached(reference: str, answer: str, full_score: float):
    """Actual grading logic; cached so identical answers do not burn free tokens."""
    if not answer:
        return 0.0, "未作答", "rule"
    if configured():
        prompt = (
            "你是考研英语阅卷助手。请只输出 JSON，格式 "
            '{"score": 0-10 的数字, "reason": "不超过80字的中文理由"}。\n'
            f"参考答案要点：{reference}\n考生答案：{answer}\n"
            "按要点覆盖程度给分，不要因为措辞不同而扣分。"
        )
        try:
            data, provider = generate_json(prompt, max_tokens=200)
            raw = max(0.0, min(10.0, float(data["score"]))) / 10.0
            score = round(raw * full_score, 2)
            return score, str(data.get("reason", ""))[:500], provider
        except (GenerationFailed, NoProvider, ValueError, KeyError, TypeError):
            pass  # fall through to the deterministic rule grader
    normalised = (reference.replace("；", ";").replace("，", ";")
                  .replace(",", ";").replace("、", ";"))
    keywords = [k.strip() for k in normalised.split(";") if k.strip()]
    if not keywords:
        keywords = [reference.strip()]
    hit = sum(1 for k in keywords if k and k.lower() in answer.lower())
    ratio = hit / max(1, len(keywords))
    if len(answer) >= 12:
        ratio = min(1.0, ratio + 0.15)
    score = round(full_score * ratio, 2)
    return score, f"按参考答案要点覆盖率给分（命中 {hit}/{len(keywords)} 个要点）", "local-rule"


def grade_subjective(reference: str, answer: str, full_score: float = 10.0):
    """Grade one short-answer question. Returns (score, reason, provider).

    B4: objective multiple-choice questions are compared directly elsewhere;
    only this subjective path uses AI, and its reason is stored in answer_log.
    """
    return _grade_cached((reference or "").strip(), (answer or "").strip(),
                         float(full_score))


# ------------------------------------------------------------ model selection
# The browser may only pick a model NAME from a server-defined allowlist. Keys
# and base_urls never leave the server; a preset takes the primary slot's
# base_url + key and changes only `model`. Runtime switch mutates os.environ so
# the next generate_text call uses it immediately, and is persisted to .env so
# it survives a restart.

def current_model() -> str:
    """The model name actually in use right now (primary slot first)."""
    for prefix in ("", "LLM_FALLBACK_"):
        model = os.getenv(f"{prefix}LLM_MODEL", "").strip()
        if model:
            return model
    return ""


def available_models():
    """Return [{id,label,note,current}] from the server-side allowlist."""
    raw = os.getenv("LLM_MODEL_PRESETS", _DEFAULT_PRESETS)
    ids = []
    for item in raw.split(","):
        item = item.strip()
        if item and item not in ids:
            ids.append(item)
    # Always include the active model so the UI is never blank on a custom key.
    active = current_model()
    if active and active not in ids:
        ids.insert(0, active)
    current = os.getenv("LLM_MODEL", "").strip()
    return [{"id": m, "label": m,
             "note": _PRESET_NOTES.get(m, "自定义 / 其他 OpenAI 兼容模型"),
             "current": m == current} for m in ids]


def switch_model(model: str) -> str:
    """Validate + activate a preset model immediately and persist to .env."""
    model = (model or "").strip()
    if not _MODEL_NAME_RE.fullmatch(model):
        raise ValueError("模型名只能包含字母、数字、点、下划线和连字符")
    allowed = {m["id"] for m in available_models()}
    if model not in allowed:
        raise ValueError("这个模型不在允许列表里，请先在服务端 LLM_MODEL_PRESETS 中添加")
    if not configured():
        raise NoProvider("请先配置 LLM_API_KEY 再切换模型")

    # 1) runtime: _slot() reads os.getenv on every call, so this takes effect
    #    on the very next generation without a restart.
    os.environ["LLM_MODEL"] = model
    # 2) a model change invalidates cached subjective grades.
    _grade_cached.cache_clear()
    # 3) persist so a restart keeps the choice. Never rewrite the key line.
    _persist_env("LLM_MODEL", model)
    return model


def _persist_env(key: str, value: str):
    """Replace one KEY=value line in .env, preserving every other line."""
    with _ENV_LOCK:
        lines = _ENV_PATH.read_text(encoding="utf-8").splitlines() if _ENV_PATH.exists() else []
        out, replaced = [], False
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("#") or "=" not in stripped:
                out.append(line)
                continue
            existing_key = stripped.split("=", 1)[0].strip()
            if existing_key == key:
                out.append(f"{key}={value}")
                replaced = True
            else:
                out.append(line)
        if not replaced:
            out.append(f"{key}={value}")
        tmp = _ENV_PATH.with_name(_ENV_PATH.name + ".tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
        tmp.replace(_ENV_PATH)


def test_model():
    """One tiny live call to verify the active model really answers.

    Returns a plain dict (never raises) so the UI can show a clear verdict
    instead of a stale `configured()` green light when the provider is busy.
    """
    base, key, model = _slot()
    if not (base and key and model):
        return {"ok": False, "model": model, "error": "还没有配置好接口地址、Key 或模型名"}
    url = base.rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 16,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"}, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=min(TIMEOUT, 20)) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        reply = body["choices"][0]["message"]["content"].strip()
        return {"ok": True, "model": model,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "reply": reply[:40]}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            parsed = json.loads(exc.read().decode("utf-8", "replace"))
            detail = parsed.get("error", {}).get("message", "")[:120]
        except (ValueError, AttributeError):
            detail = ""
        return {"ok": False, "model": model,
                "error": f"HTTP {exc.code}" + (f"：{detail}" if detail else "")}
    except Exception as exc:  # network / timeout / bad payload
        return {"ok": False, "model": model,
                "error": f"{type(exc).__name__}：{str(exc)[:120]}"}
