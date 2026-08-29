"""Lightweight local RAG — TF–IDF + cosine (no LangChain / no embedding API).

Indexes workdir source files, MEMORY.md blocks, and transcript snippets into
`.agent/rag_index.json`. Good enough for coding-agent recall demos; swap for
real embeddings later without changing the tool surface.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.agent.memory import resolve_memory_path

_TOKEN = re.compile(r"[a-zA-Z_][\w\.]{1,40}|[\u4e00-\u9fff]{1,8}")
_CODE_GLOBS = ("**/*.py", "**/*.md", "**/*.txt", "**/*.toml", "**/*.json")
_SKIP_PARTS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    "transcripts",
}


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text or "")]


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    if not tokens:
        return {}
    tf = Counter(tokens)
    n = float(len(tokens))
    return {t: (c / n) * idf.get(t, 0.0) for t, c in tf.items() if t in idf}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    keys = set(a) & set(b)
    if not keys:
        return 0.0
    dot = sum(a[k] * b[k] for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def _iter_code_files(workdir: Path, *, max_files: int = 200) -> Iterable[Path]:
    root = workdir.resolve()
    count = 0
    for pattern in _CODE_GLOBS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            try:
                rel_parts = path.relative_to(root).parts
            except ValueError:
                continue
            if any(p in _SKIP_PARTS or p.startswith(".") for p in rel_parts[:-1]):
                # allow MEMORY.md at root / .agent
                if path.name not in {"MEMORY.md", "working_memory.json"} and any(
                    p.startswith(".") for p in rel_parts[:-1]
                ):
                    if ".agent" not in rel_parts:
                        continue
            if path.suffix.lower() == ".json" and "session_" in path.name:
                continue
            yield path
            count += 1
            if count >= max_files:
                return


def _chunk_text(text: str, *, max_chars: int = 900) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    lines = text.splitlines()
    buf: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > max_chars and buf:
            chunks.append("\n".join(buf))
            buf = []
            size = 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks[:40]


def build_rag_index(
    workdir: Path,
    *,
    transcript_dir: Path | None = None,
    max_docs: int = 400,
) -> dict[str, Any]:
    """Build / refresh a local TF–IDF index under workdir/.agent/rag_index.json."""
    docs: list[dict[str, Any]] = []

    for path in _iter_code_files(workdir):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(workdir.resolve()).as_posix()
        for i, chunk in enumerate(_chunk_text(text)):
            docs.append(
                {
                    "id": f"code:{rel}#{i}",
                    "source": rel,
                    "kind": "code",
                    "text": chunk,
                }
            )
            if len(docs) >= max_docs:
                break
        if len(docs) >= max_docs:
            break

    mem_path = resolve_memory_path(workdir)
    if mem_path.is_file() and len(docs) < max_docs:
        try:
            mem = mem_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            mem = ""
        for i, block in enumerate(re.split(r"(?m)(?=^## )", mem)):
            block = block.strip()
            if not block:
                continue
            docs.append(
                {
                    "id": f"memory:{i}",
                    "source": mem_path.name,
                    "kind": "memory",
                    "text": block[:1200],
                }
            )
            if len(docs) >= max_docs:
                break

    tdir = transcript_dir
    if tdir is None:
        cand = Path("transcripts")
        tdir = cand if cand.is_dir() else None
    if tdir is not None and tdir.is_dir() and len(docs) < max_docs:
        files = sorted(tdir.glob("session_*.json")) + sorted(tdir.glob("run_*.json"))
        for path in files[-30:]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            pieces = [
                str(data.get("task") or ""),
                str(data.get("final_text") or ""),
            ]
            mem = data.get("memory")
            if isinstance(mem, dict):
                pieces.append(str(mem.get("history_summary") or ""))
            blob = "\n".join(p for p in pieces if p)
            if not blob.strip():
                continue
            docs.append(
                {
                    "id": f"transcript:{path.name}",
                    "source": path.name,
                    "kind": "transcript",
                    "text": blob[:1500],
                }
            )
            if len(docs) >= max_docs:
                break

    # DF / IDF
    df: Counter[str] = Counter()
    tokenized: list[list[str]] = []
    for d in docs:
        toks = _tokenize(d["text"])
        tokenized.append(toks)
        for t in set(toks):
            df[t] += 1
    n_docs = max(1, len(docs))
    idf = {t: math.log((n_docs + 1) / (c + 1)) + 1.0 for t, c in df.items()}

    vectors: list[dict[str, float]] = [_tfidf_vector(toks, idf) for toks in tokenized]
    # Store sparse vectors as sorted items for JSON
    for d, vec in zip(docs, vectors):
        d["vec"] = sorted(vec.items(), key=lambda x: -x[1])[:80]

    index = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "workdir": str(workdir.resolve()),
        "n_docs": len(docs),
        "idf_sample_size": len(idf),
        "idf": {t: idf[t] for t in sorted(idf, key=lambda k: -idf[k])[:4000]},
        "docs": [
            {
                "id": d["id"],
                "source": d["source"],
                "kind": d["kind"],
                "text": d["text"][:500],
                "vec": d["vec"],
            }
            for d in docs
        ],
    }
    out = workdir.resolve() / ".agent" / "rag_index.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return index


def load_rag_index(workdir: Path) -> dict[str, Any] | None:
    path = workdir.resolve() / ".agent" / "rag_index.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def rag_search(
    workdir: Path,
    query: str,
    *,
    transcript_dir: Path | None = None,
    top_k: int = 5,
    rebuild: bool = False,
) -> str:
    """Semantic-ish search via local TF–IDF. Rebuilds index when missing/stale."""
    q = (query or "").strip()
    if not q:
        return "Error: 'query' is required"

    index = None if rebuild else load_rag_index(workdir)
    if index is None or not index.get("docs"):
        index = build_rag_index(workdir, transcript_dir=transcript_dir)

    idf = index.get("idf") or {}
    qvec = _tfidf_vector(_tokenize(q), {str(k): float(v) for k, v in idf.items()})
    if not qvec:
        return f"No lexical overlap for {q!r}. Try simpler keywords or rebuild."

    scored: list[tuple[float, dict[str, Any]]] = []
    for d in index.get("docs") or []:
        raw_vec = d.get("vec") or []
        if isinstance(raw_vec, dict):
            dvec = {str(k): float(v) for k, v in raw_vec.items()}
        else:
            dvec = {str(k): float(v) for k, v in raw_vec}
        score = _cosine(qvec, dvec)
        if score > 0.02:
            scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    top_k = max(1, min(int(top_k), 15))
    if not scored:
        return f"No RAG hits for {q!r}. Index has {index.get('n_docs', 0)} docs."

    lines = [
        f"rag_search (local TF–IDF) for {q!r} — top {min(top_k, len(scored))} "
        f"of {index.get('n_docs', 0)} docs:"
    ]
    for score, d in scored[:top_k]:
        snippet = " ".join(str(d.get("text") or "").split())
        if len(snippet) > 200:
            snippet = snippet[:197] + "…"
        lines.append(
            f"- [{score:.3f}] ({d.get('kind')}) {d.get('source')}: {snippet}"
        )
    return "\n".join(lines)
