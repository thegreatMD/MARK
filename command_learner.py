"""Ultra-lightweight command learner for the JARVIS / Mark assistant.

Design goals (in priority order):
  1. Zero extra runtime dependencies — stdlib + difflib (already used by Mark) only.
  2. Minimal CPU + memory: every inference is O(ngram_candidates * avg_query_length)
     and the worker thread only writes/rewrites storage when work is queued.
  3. Bounded resource usage: `MAX_SAMPLES` cap on in-memory rows, `MAX_STORAGE_BYTES`
     cap on on-disk JSONL, automatic compaction when exceeded.
  4. Never block the hot path. `record()` is O(1) put on a queue; a single dedicated
     worker thread does persistence + index rebuild and swaps the live snapshot atomically.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


MAX_SAMPLES: int = 1200            # hard cap on in-memory rows
MIN_CONFIDENCE_TO_STORE: float = 0.55
MAX_STORAGE_BYTES: int = 2 * 1024 * 1024  # 2 MB before on-disk compaction
COMPACT_KEEP_RATIO: float = 0.8   # keep top 80% when compacting file
INFERENCE_MIN_TOKENS: int = 2
INFERENCE_NGRAM_RANGE: Tuple[int, int] = (1, 3)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_']+")
_WS_RE = re.compile(r"\s+")


def _tokenize(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _ngrams(tokens: Iterable[str], lo: int, hi: int) -> List[str]:
    toks = list(tokens)
    out: List[str] = []
    for n in range(lo, hi + 1):
        if n > len(toks):
            continue
        for i in range(len(toks) - n + 1):
            out.append(" ".join(toks[i:i + n]))
    return out


def _normalise(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


@dataclass
class _Sample:
    sid: int
    raw: str
    tokens: List[str]
    intent: str
    confidence: float
    timestamp: float
    hit_count: int = 0
    acted: bool = True


@dataclass
class _Snapshot:
    """Immutable, swap-able view of the learner state used for inference."""

    samples: List[_Sample] = field(default_factory=list)
    ngram_index: Dict[str, List[int]] = field(default_factory=lambda: defaultdict(list))
    intent_stats: Dict[str, Dict[str, float]] = field(default_factory=lambda: defaultdict(lambda: {"count": 0, "sum_conf": 0.0, "hits": 0}))
    total_seen: int = 0


class CommandLearner:
    """Capture every executed command, build a tiny pattern model,
    use it to boost intent recognition over time — with strictly bounded resources.
    """

    def __init__(
        self,
        storage_path: Path,
        event_fn: Optional[Callable[[str], None]] = None,
        max_samples: int = MAX_SAMPLES,
        max_storage_bytes: int = MAX_STORAGE_BYTES,
    ) -> None:
        self.storage_path = Path(storage_path)
        self.event_fn = event_fn
        self.max_samples = max(10, int(max_samples))
        self.max_storage_bytes = max(64 * 1024, int(max_storage_bytes))

        self._snap_lock = threading.Lock()
        self._snap: _Snapshot = _Snapshot()
        self._next_sid: int = 1
        self._total_seen: int = 0

        self._q: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._worker_loop, name="CommandLearnerWorker", daemon=True)

        # bootstrap from disk, then start worker
        self._bootstrap_from_disk()
        self._worker.start()

    # ------------------------------------------------------------------
    # Bootstrap (runs once on constructor, before worker is up)
    # ------------------------------------------------------------------
    def _bootstrap_from_disk(self) -> None:
        if not self.storage_path.is_file():
            return
        samples: List[_Sample] = []
        max_sid = 0
        seen = 0
        try:
            with self.storage_path.open("r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    raw = str(rec.get("q") or rec.get("raw") or "")
                    intent = str(rec.get("intent") or "general_agent")
                    conf = float(rec.get("c") or rec.get("confidence") or 0.0)
                    ts = float(rec.get("t") or rec.get("timestamp") or time.time())
                    sid = int(rec.get("sid") or 0)
                    hits = int(rec.get("hits") or rec.get("hit_count") or 0)
                    acted = bool(rec.get("acted", True))
                    if not raw:
                        continue
                    tokens = _tokenize(raw)
                    if not tokens:
                        continue
                    samples.append(_Sample(
                        sid=sid, raw=raw, tokens=tokens, intent=intent,
                        confidence=conf, timestamp=ts, hit_count=hits, acted=acted,
                    ))
                    if sid > max_sid:
                        max_sid = sid
                    seen += 1
                    if len(samples) >= self.max_samples:
                        break
        except OSError:
            return
        self._next_sid = max_sid + 1
        self._total_seen = seen
        snap = self._build_snapshot(samples, self._total_seen)
        with self._snap_lock:
            self._snap = snap

    # ------------------------------------------------------------------
    # Public API — record (non-blocking) + infer (read-only, no IO)
    # ------------------------------------------------------------------
    def record(
        self,
        query: str,
        intent: str,
        confidence: float,
        acted: bool = True,
        timestamp: Optional[float] = None,
    ) -> None:
        """Schedule a single command execution to be persisted + indexed.

        Non-blocking. O(1) enqueue — all heavy work happens in the worker thread.
        Low-confidence or empty inputs are dropped immediately to keep storage clean.
        """
        q = _normalise(query)
        if not q:
            return
        tokens = _tokenize(q)
        if len(tokens) < INFERENCE_MIN_TOKENS:
            return
        if float(confidence) < MIN_CONFIDENCE_TO_STORE and not acted:
            return
        try:
            self._q.put_nowait({
                "kind": "rec",
                "raw": q,
                "tokens": tokens,
                "intent": str(intent) or "general_agent",
                "confidence": max(0.0, min(1.0, float(confidence))),
                "acted": bool(acted),
                "timestamp": float(timestamp) if timestamp is not None else time.time(),
            })
        except queue.Full:
            pass

    def infer(self, query: str) -> Tuple[Optional[str], float, str]:
        """Lightweight inference. Returns (intent, confidence 0..1, short rationale).

        Computation:
          - Extract ngrams 1..3 from the query.
          - Retrieve up to ~40 candidate samples that share >= 1 ngram (ngram_index).
          - Score each candidate: weighted sum of ngram-overlap + SequenceMatcher ratio.
          - Vote per-intent (sum of scores weighted by each sample's own hit_count + conf).
          - Normalise to [0, 1]. Returns (None, 0.0, ...) if there is little evidence.
        """
        text = _normalise(query)
        tokens = _tokenize(text)
        if len(tokens) < INFERENCE_MIN_TOKENS:
            return None, 0.0, "query too short; learner has no basis"
        with self._snap_lock:
            snap = self._snap
        if not snap.samples:
            return None, 0.0, "learner has no stored commands yet"

        ngrams = set(_ngrams(tokens, *INFERENCE_NGRAM_RANGE))
        if not ngrams:
            return None, 0.0, "no tokenisable ngrams"

        candidate_ids: Dict[int, int] = {}
        for ng in ngrams:
            for sid in snap.ngram_index.get(ng, ()):
                candidate_ids[sid] = candidate_ids.get(sid, 0) + 1
        if not candidate_ids:
            return None, 0.0, "learner found no matching historical ngrams"

        # Keep only top ~40 candidates by shared-ngram count to bound CPU work.
        top_ids = sorted(candidate_ids.items(), key=lambda kv: kv[1], reverse=True)[:40]
        id_to_sample = {s.sid: s for s in snap.samples}

        sample_scores: List[Tuple[_Sample, float, float]] = []
        for sid, overlap in top_ids:
            s = id_to_sample.get(sid)
            if s is None:
                continue
            sample_ngrams = set(_ngrams(s.tokens, *INFERENCE_NGRAM_RANGE)) or {""}
            jac = (len(ngrams & sample_ngrams) + 1e-9) / (len(ngrams | sample_ngrams) + 1e-9)
            seq = SequenceMatcher(None, text, _normalise(s.raw)).ratio()
            weight = 0.55 * jac + 0.45 * seq
            # Weight by how often this sample has helped before + its stored confidence.
            quality = 0.7 * (min(1.0, (s.hit_count + 1) / 8.0)) + 0.3 * (s.confidence or 0.5)
            final = weight * (0.5 + 0.5 * quality)
            sample_scores.append((s, final, jac + seq))

        intent_totals: Dict[str, float] = defaultdict(float)
        intent_examples: Dict[str, List[str]] = defaultdict(list)
        for s, score, _ in sample_scores:
            intent_totals[s.intent] += score
            if len(intent_examples[s.intent]) < 2:
                intent_examples[s.intent].append(s.raw[:60])

        if not intent_totals:
            return None, 0.0, "candidate samples did not yield a score"

        best_intent, best_raw = max(intent_totals.items(), key=lambda kv: kv[1])
        # Softly normalise: raw_score is typically within [0, ~1.2]; clamp and shape.
        conf = min(1.0, max(0.0, best_raw * 0.95))
        examples = "; ".join(f"'{e}'" for e in intent_examples.get(best_intent, []))
        rationale = (
            f"matched {len(sample_scores)} historical sample(s); "
            f"top intent '{best_intent}' from: {examples or 'n/a'}"
        )
        if conf < 0.45:
            return None, conf, f"weak match ({conf:.2f}): {rationale}"
        return best_intent, conf, rationale

    def stats(self) -> Dict[str, Any]:
        with self._snap_lock:
            snap = self._snap
        samples = snap.samples
        disk_bytes = 0
        try:
            if self.storage_path.is_file():
                disk_bytes = self.storage_path.stat().st_size
        except OSError:
            disk_bytes = 0
        intents_top = sorted(
            (
                {
                    "intent": k,
                    "count": int(v["count"]),
                    "avg_confidence": round(v["sum_conf"] / max(1, v["count"]), 3),
                    "hits": int(v["hits"]),
                }
                for k, v in snap.intent_stats.items()
            ),
            key=lambda r: r["count"],
            reverse=True,
        )[:20]
        # Approximate in-memory size: 4 pointers + 2 strings per token, per sample
        approx_mem_bytes = sum(
            80 + 2 * len(s.raw) + 40 * len(s.tokens) for s in samples
        )
        return {
            "samples_held": len(samples),
            "samples_cap": self.max_samples,
            "total_seen": snap.total_seen,
            "distinct_intents": len(snap.intent_stats),
            "storage_file": str(self.storage_path),
            "storage_bytes": disk_bytes,
            "storage_cap_bytes": self.max_storage_bytes,
            "approx_mem_bytes": approx_mem_bytes,
            "top_intents": intents_top,
            "worker_pending": self._q.qsize(),
        }

    def top_patterns(self, n: int = 15) -> Dict[str, List[Dict[str, Any]]]:
        with self._snap_lock:
            snap = self._snap
        out: Dict[str, List[Dict[str, Any]]] = {}
        buckets: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for s in snap.samples:
            for ng in _ngrams(s.tokens, 2, 3):
                buckets[s.intent][ng] += 1
        for intent, counts in buckets.items():
            ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:n]
            out[intent] = [{"pattern": p, "count": c} for p, c in ranked]
        return dict(sorted(out.items(), key=lambda kv: sum(r["count"] for r in kv[1]), reverse=True))

    # ------------------------------------------------------------------
    # Controls — enqueued jobs so the caller never waits on IO
    # ------------------------------------------------------------------
    def forget_intent(self, intent: str) -> None:
        self._q.put({"kind": "forget_intent", "intent": str(intent)})

    def forget_all(self) -> None:
        self._q.put({"kind": "forget_all"})

    def shutdown(self) -> None:
        self._stop.set()
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass

    # ------------------------------------------------------------------
    # Worker + snapshot building
    # ------------------------------------------------------------------
    def _worker_loop(self) -> None:
        pending_append: List[Dict[str, Any]] = []
        dirty = False
        while not self._stop.is_set():
            item: Optional[Dict[str, Any]]
            try:
                item = self._q.get(timeout=1.0)
            except queue.Empty:
                if dirty:
                    self._flush_append(pending_append)
                    pending_append = []
                    self._maybe_compact_and_rebuild()
                    dirty = False
                continue
            if item is None:
                break
            kind = item.get("kind")
            if kind == "rec":
                pending_append.append(item)
                dirty = True
                # Flush eagerly when the queue is caught up (so a small batch of
                # records is available for inference quickly, not 1 second later).
                if self._q.empty() or len(pending_append) >= 50:
                    self._flush_append(pending_append)
                    pending_append = []
                    self._maybe_compact_and_rebuild()
                    dirty = False
            elif kind == "forget_intent":
                self._flush_append(pending_append)
                pending_append = []
                self._apply_forget_intent(str(item.get("intent") or ""))
                dirty = False
            elif kind == "forget_all":
                self._flush_append(pending_append)
                pending_append = []
                self._apply_forget_all()
                dirty = False
        if pending_append:
            self._flush_append(pending_append)
            self._maybe_compact_and_rebuild()

    def _flush_append(self, batch: List[Dict[str, Any]]) -> None:
        if not batch:
            return
        with self._snap_lock:
            old_snap = self._snap
            samples = list(old_snap.samples)
        new_rows: List[_Sample] = []
        lines: List[str] = []
        for rec in batch:
            sid = self._next_sid
            self._next_sid += 1
            self._total_seen += 1
            s = _Sample(
                sid=sid,
                raw=str(rec["raw"]),
                tokens=list(rec["tokens"]),
                intent=str(rec.get("intent") or "general_agent"),
                confidence=float(rec.get("confidence") or 0.0),
                timestamp=float(rec.get("timestamp") or time.time()),
                acted=bool(rec.get("acted", True)),
            )
            samples.append(s)
            new_rows.append(s)
            lines.append(json.dumps({
                "sid": s.sid,
                "q": s.raw,
                "intent": s.intent,
                "c": round(s.confidence, 4),
                "t": round(s.timestamp, 3),
                "hits": s.hit_count,
                "acted": s.acted,
            }, ensure_ascii=False, separators=(",", ":")))
        try:
            with self.storage_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines))
                f.write("\n")
        except OSError as exc:
            self._emit(f"Learner storage write failed: {exc}")
        # Prune to max_samples by dropping oldest rows that also have lowest hit_count.
        if len(samples) > self.max_samples:
            samples.sort(key=lambda s: (s.hit_count, s.timestamp))
            samples = samples[-self.max_samples:]
        snap = self._build_snapshot(samples, self._total_seen)
        with self._snap_lock:
            self._snap = snap
        if new_rows:
            self._emit(f"Learner indexed {len(new_rows)} new command(s); total {len(samples)} held")

    def _maybe_compact_and_rebuild(self) -> None:
        try:
            size = self.storage_path.stat().st_size if self.storage_path.is_file() else 0
        except OSError:
            size = 0
        if size < self.max_storage_bytes:
            return
        with self._snap_lock:
            snap = self._snap
            samples = list(snap.samples)
        if not samples:
            return
        # Keep top samples by (hit_count, confidence, recency), write a clean file.
        keep_count = max(50, int(self.max_samples * COMPACT_KEEP_RATIO))
        samples.sort(key=lambda s: (s.hit_count, s.confidence, s.timestamp), reverse=True)
        kept = samples[:keep_count]
        kept.sort(key=lambda s: s.sid)
        tmp = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                for s in kept:
                    f.write(json.dumps({
                        "sid": s.sid,
                        "q": s.raw,
                        "intent": s.intent,
                        "c": round(s.confidence, 4),
                        "t": round(s.timestamp, 3),
                        "hits": s.hit_count,
                        "acted": s.acted,
                    }, ensure_ascii=False, separators=(",", ":")))
                    f.write("\n")
            tmp.replace(self.storage_path)
        except OSError as exc:
            self._emit(f"Learner compaction failed: {exc}")
            try:
                tmp.unlink()
            except OSError:
                pass
            return
        snap = self._build_snapshot(kept, self._total_seen)
        with self._snap_lock:
            self._snap = snap
        self._emit(f"Learner compacted storage to {len(kept)} rows")

    def _apply_forget_intent(self, intent: str) -> None:
        if not intent:
            return
        with self._snap_lock:
            old_snap = self._snap
            kept = [s for s in old_snap.samples if s.intent != intent]
        removed = len(old_snap.samples) - len(kept)
        self._rewrite_file(kept)
        snap = self._build_snapshot(kept, self._total_seen)
        with self._snap_lock:
            self._snap = snap
        self._emit(f"Learner forgot intent '{intent}' ({removed} row(s))")

    def _apply_forget_all(self) -> None:
        self._rewrite_file([])
        with self._snap_lock:
            self._snap = _Snapshot()
        self._next_sid = 1
        self._total_seen = 0
        self._emit("Learner forgot all stored commands")

    def _rewrite_file(self, samples: List[_Sample]) -> None:
        tmp = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                for s in samples:
                    f.write(json.dumps({
                        "sid": s.sid, "q": s.raw, "intent": s.intent,
                        "c": round(s.confidence, 4), "t": round(s.timestamp, 3),
                        "hits": s.hit_count, "acted": s.acted,
                    }, ensure_ascii=False, separators=(",", ":")))
                    f.write("\n")
            tmp.replace(self.storage_path)
        except OSError as exc:
            self._emit(f"Learner storage rewrite failed: {exc}")
            try:
                tmp.unlink()
            except OSError:
                pass

    def _build_snapshot(self, samples: List[_Sample], total_seen: int) -> _Snapshot:
        ngram_index: Dict[str, List[int]] = defaultdict(list)
        intent_stats: Dict[str, Dict[str, float]] = defaultdict(lambda: {"count": 0, "sum_conf": 0.0, "hits": 0})
        for s in samples:
            for ng in _ngrams(s.tokens, *INFERENCE_NGRAM_RANGE):
                ngram_index[ng].append(s.sid)
            st = intent_stats[s.intent]
            st["count"] += 1
            st["sum_conf"] += s.confidence
            st["hits"] += s.hit_count
        # Cap the posting lists so inference stays cheap.
        for k in list(ngram_index.keys()):
            if len(ngram_index[k]) > 200:
                ngram_index[k] = ngram_index[k][-200:]
        return _Snapshot(
            samples=samples,
            ngram_index=dict(ngram_index),
            intent_stats={k: dict(v) for k, v in intent_stats.items()},
            total_seen=total_seen,
        )

    def _emit(self, message: str) -> None:
        try:
            if self.event_fn:
                self.event_fn(message)
        except Exception:
            pass
