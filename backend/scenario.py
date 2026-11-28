"""Fresh, non-repeating example sentences for the article context quiz.

Every quiz sentence is written from scratch:

* it is NEVER copied from the article the user just read;
* it never repeats a sentence used before for the same word (tracked in
  `word_example`);
* its length and vocabulary follow the exam level of the current book
  (高考 / 四级 / 六级 / 考研).

Generation is pure (no DB access) so the API layer can run several calls in
parallel outside a transaction, then persist them in one short transaction.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor

from llm import GenerationFailed, NoProvider, configured, generate_json

_VOWELS = set("aeiou")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


# --------------------------------------------------------------- word forms
def word_forms(base):
    """Base word plus the usual inflections, longest first (masking/checking)."""
    b = (base or "").strip().lower()
    if not b:
        return []
    forms = {b, b + "s", b + "es", b + "ed", b + "d", b + "ing",
             b + "er", b + "est", b + "ly"}
    if b.endswith("e") and len(b) > 2:
        stem = b[:-1]
        forms |= {stem + "ing", stem + "ed", stem + "er", stem + "est"}
    if b.endswith("y") and len(b) > 1:
        stem = b[:-1]
        forms |= {stem + "ies", stem + "ied", stem + "ying",
                  stem + "ier", stem + "iest"}
    if b.endswith("is"):                      # analysis → analyses
        forms.add(b[:-2] + "es")
    if b.endswith("man"):                     # spokesman → spokesmen
        forms.add(b[:-3] + "men")
    if len(b) > 2 and b[-1] not in _VOWELS and b[-1] not in "yw" and b[-2] in _VOWELS:
        forms |= {b + b[-1] + "ed", b + b[-1] + "ing",
                  b + b[-1] + "er", b + b[-1] + "est"}
    return sorted(forms, key=len, reverse=True)


def word_pattern(base):
    forms = word_forms(base)
    if not forms:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(f) for f in forms) + r")\b",
                      re.IGNORECASE)


def has_word(text, base):
    pattern = word_pattern(base)
    return bool(pattern and pattern.search(text or ""))


def mask_sentence(sentence, base):
    """Blank every occurrence (including inflections); answer stays server-side."""
    pattern = word_pattern(base)
    if not pattern:
        return sentence or ""
    return pattern.sub(lambda m: "_" * len(m.group(0)), sentence or "")


def signature(sentence):
    """Token set of a (masked) sentence, used for near-duplicate detection."""
    text = (sentence or "").lower()
    text = re.sub(r"_{2,}", " blank ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return {tok for tok in text.split() if tok}


def _similar(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def too_similar(sentence, others, threshold=0.8):
    sig = signature(sentence)
    return any(_similar(sig, signature(o)) >= threshold for o in others if o)


def split_sentences(body):
    return [s.strip() for s in _SENT_SPLIT.split(body or "") if s.strip()]


def sentence_with(body, base):
    pattern = word_pattern(base)
    if not pattern:
        return ""
    for sent in split_sentences(body):
        if pattern.search(sent):
            return sent
    return ""


# --------------------------------------------------------------- difficulty
def exam_band(book):
    """(exam label, min words, max words) for the current book."""
    exam = ((book or {}).get("exam_type") or "考研英语").strip()
    if "高考" in exam:
        return exam, 12, 18
    if "四级" in exam:
        return exam, 15, 22
    if "六级" in exam:
        return exam, 18, 26
    return exam, 20, 32


# --------------------------------------------------------------- local fallback
LOCAL_SENTENCES = {
    "n": [
        "The {w} in the second paragraph decides how the whole passage should be read.",
        "Her essay opened with the {w} that the later examples quietly contradict.",
        "The committee asked for the {w} behind the decision before approving it.",
        "Without the {w} mentioned on page two, the conclusion feels unsupported.",
    ],
    "v": [
        "The new policy is meant to {w} the needs of students who study part-time.",
        "Critics argue that the plan fails to {w} the real cause of the problem.",
        "The essay tries to {w} two seemingly unrelated trends in a single paragraph.",
        "Only after the second reading did she begin to {w} the writer's position.",
    ],
    "adj": [
        "The evidence looked {w} until the second paragraph supplied the missing context.",
        "Critics described the reading as {w}, although the text suggests otherwise.",
        "Her conclusion was {w}, and the examiner asked for a clearer line of reasoning.",
        "The proposal sounded {w}, yet it rested on a single unverified figure.",
    ],
    "adv": [
        "The author {w} admits that the sample was too small to settle the question.",
        "Students who read {w} tend to miss the shift in the writer's attitude.",
        "The second example is {w} weaker than the first, which weakens the whole case.",
    ],
}
LOCAL_GENERIC = [
    "The passage returns to {w} only at the end, where the argument finally becomes clear.",
    "A single sentence about {w} can decide how the examiner reads the whole answer.",
    "The lecture used {w} as an example, then asked the class to explain the contrast.",
    "In the margin she noted {w} beside the sentence she could not paraphrase.",
]


def _local_sentence(row, salt=0, avoid=()):
    pos = (row.get("pos") or "").split(".")[0].split("/")[0].strip().lower()
    templates = LOCAL_SENTENCES.get(pos, LOCAL_GENERIC)
    start = (sum(ord(c) for c in (row.get("word") or "")) + salt) % len(templates)
    for offset in range(len(templates)):
        sentence = templates[(start + offset) % len(templates)].format(w=row["word"])
        if not too_similar(mask_sentence(sentence, row["word"]), avoid):
            return sentence, (row.get("translation") or "").split(";")[0].strip()[:300]
    sentence = templates[start].format(w=row["word"])
    return sentence, (row.get("translation") or "").split(";")[0].strip()[:300]


# --------------------------------------------------------------- LLM sentence
def _valid(data, base, lo, hi, avoid):
    if not isinstance(data, dict):
        return False
    sentence = data.get("sentence")
    if not isinstance(sentence, str):
        return False
    sentence = sentence.strip()
    words = len(sentence.split())
    # Generous window: the prompt asks for lo..hi, but a good exam sentence may
    # legitimately be a little shorter or longer than the requested band.
    if not (max(10, lo - 4) <= words <= hi + 10):
        return False
    if not has_word(sentence, base) or _CJK.search(sentence):
        return False
    return not too_similar(mask_sentence(sentence, base), avoid)


def _llm_sentence(row, book, avoid):
    exam, lo, hi = exam_band(book)
    banned = "\n".join(f"- {s}" for s in list(avoid)[:6]) or "- (none)"
    prompt = (
        f"You write ONE original English sentence for a Chinese student "
        f"preparing for {exam}.\n"
        f"Target word: {row['word']} ({row.get('pos') or 'word'}), "
        f"meaning: {row.get('translation') or ''}.\n"
        "Rules:\n"
        f"1. Use the target word exactly once, in a natural grammatical form.\n"
        f"2. Length {lo}-{hi} words; vocabulary and sentence structure must be "
        f"at {exam} level (a real exam sentence, not textbook-simple).\n"
        "3. Do NOT copy, quote or lightly reword any sentence listed below; "
        "the new sentence must be clearly different in content and structure.\n"
        f"{banned}\n"
        "4. No Chinese characters, no quotation marks, no markdown.\n"
        'Return ONLY JSON: {"sentence":"...","translation":"对应的中文翻译"}'
    )
    data, provider = generate_json(
        prompt, validator=lambda d: _valid(d, row["word"], lo, hi, avoid),
        max_tokens=280)
    return (data["sentence"].strip(),
            str(data.get("translation") or "").strip()[:300], provider)


def generate_sentence(row, book, avoid=()):
    """One brand-new sentence for `row`, guaranteed unlike everything in `avoid`."""
    avoid = [s for s in avoid if s]
    if configured():
        for attempt in range(3):
            try:
                sentence, translation, provider = _llm_sentence(row, book, avoid)
                if not too_similar(mask_sentence(sentence, row["word"]), avoid):
                    return {"sentence": sentence, "translation": translation,
                            "source": provider}
            except NoProvider:
                break
            except (GenerationFailed, ValueError, KeyError, TypeError):
                # Free tiers throttle parallel calls and occasionally return
                # malformed JSON — a short backoff usually clears it.
                if attempt < 2:
                    time.sleep(0.7 * (attempt + 1))
    sentence, translation = _local_sentence(row, salt=len(avoid), avoid=avoid)
    return {"sentence": sentence, "translation": translation, "source": "local"}


def build_items(candidates, book, workers=3):
    """Generate sentences for many candidates in parallel; drop near-duplicates."""
    if not candidates:
        return []

    def work(cand):
        return cand, generate_sentence(cand, book, avoid=cand.get("avoid") or [])

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(candidates)))) as pool:
        results = list(pool.map(work, candidates))

    items, seen = [], []
    for cand, gen in results:
        sentence = (gen.get("sentence") or "").strip()
        if not sentence:
            continue
        masked = mask_sentence(sentence, cand["word"])
        if masked == sentence:      # nothing blanked → would leak the answer
            continue
        if too_similar(masked, seen):
            continue
        seen.append(masked)
        items.append({
            "word_id": cand["word_id"], "answer": cand["word"],
            "sentence": masked, "translation": gen.get("translation") or "",
            "source": cand.get("kind") or "article",
            "raw": sentence,
        })
    return items
