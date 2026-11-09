"""Research assistant: retrieval-augmented generation over our database with a local open-source LLM (Ollama).

No training or fine-tuning. Facts about grants and people come only from retrieved records.
"""
import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models import ChatLog, Faculty, Grant, Match, Student
from app.services import llm
from app.services.matching import COMPONENTS, engine

log = logging.getLogger("chat")
UNAVAILABLE = "The assistant is temporarily unavailable because the local language model is not running."

SYSTEM = """You are the AURAK Research Grants Assistant for the American University of Ras Al Khaimah (AURAK).
You help students and faculty find research funding, understand grants, and find AURAK collaborators.

HARD RULES:
1. Names of people, grant titles, funders, deadlines, amounts, eligibility conditions and URLs MUST come from the
   RECORDS below. Never invent or estimate them. If a value is missing in the records, say it is not published.
2. If the records do not answer the question, say clearly that the AURAK database has no answer for it.
3. When you recommend people, say why using the evidence in the records (interests, publications, past grants),
   mention the match score as a "match score" (never a probability), and list faculty before students.
   Do not claim anyone is available or willing to collaborate; that is unknown.
4. AURAK has no official research clusters. The clusters in the RECORDS ([C..]) are proposed by this project and
   their membership is AI-suggested; say so when you mention them. Teams you suggest are AI-suggested too.
5. General knowledge questions (e.g. how to write a proposal) may be answered from general knowledge, but keep
   the AURAK-specific facts tied to the records.
6. Always mention eligibility warnings from the records (for example US-only or EU-only funding).
7. Cite records by their tags like [G1] or [F2]. Be concise: at most about 200 words."""


def ollama_available() -> bool:
    """True when some language model can answer (local Ollama by default, or a configured cloud provider)."""
    return llm.active_provider() is not None


def _grant_block(tag, g: Grant) -> str:
    amount = g.amount_text or "not published"
    return (f"[{tag}] GRANT: {g.title}\n  Funder: {g.funder or 'not published'} | Type: {g.type} | Scope: {g.scope}"
            f"{' (' + g.emirate + ')' if g.emirate else ''} | Status: {g.status}\n"
            f"  Deadline: {g.deadline.isoformat() if g.deadline else 'not published'} | Amount: {amount}\n"
            f"  Eligibility warnings: {'; '.join(g.eligibility_warnings or []) or 'none recorded'}\n"
            f"  Apply / source URL: {g.apply_url or g.source_url or 'not published'}\n"
            f"  Summary: {(g.description or '')[:600]}")


def _evidence_text(ev: dict) -> str:
    parts = []
    for k, v in (ev or {}).items():
        if k == "same_funder":
            parts.append("previously funded by the same funder: " + "; ".join(v))
        elif isinstance(v, dict) and v.get("items"):
            parts.append(f"{COMPONENTS[k][0]}: " + "; ".join(i["label"] for i in v["items"][:3]))
    return " | ".join(parts) or "no specific evidence items above threshold"


def retrieve(db: Session, question: str, grant_id: int | None = None) -> dict:
    """Collect the records for the prompt. Kept small on purpose: a CPU-only laptop reads ~10 tokens/s."""
    ctx = {"grants": [], "people": [], "blocks": []}
    if not engine.ready:
        engine.rebuild(db)
    seen = set()
    gi = 1
    focused = False
    if grant_id:
        g = db.get(Grant, grant_id)
        if g and not g.hidden:
            focused = True
            ctx["blocks"].append(_grant_block(f"G{gi}", g))
            ctx["grants"].append((f"G{gi}", g.id, g.title))
            seen.add(g.id)
            gi += 1
            ms = list(db.scalars(select(Match).where(Match.grant_id == g.id).order_by(Match.person_type, Match.rank)))
            ms = [m for m in ms if m.person_type == "faculty"][:6] + [m for m in ms if m.person_type == "student"][:3]
            lines = []
            for m in ms:
                name = _name(db, m.person_type, m.person_id)
                ctx["people"].append(("M", m.person_type, m.person_id, name))
                lines.append(f"  - {m.person_type.upper()} {name}: match score {round(m.score * 100)} — {_evidence_text(m.evidence)}")
            ctx["blocks"].append("[M] PRECOMPUTED MATCHES FOR [G1] (faculty first, then students):\n" +
                                 ("\n".join(lines) or "  No AURAK person matches this grant above the threshold."))
    max_grants = 2 if focused else 4
    for gid, sim in engine.search_grants(db, question, limit=6):
        if gid in seen or len(ctx["grants"]) >= max_grants:
            continue
        g = db.get(Grant, gid)
        if g is None or g.status == "closed" or sim < 0.25:
            continue
        seen.add(gid)
        ctx["blocks"].append(_grant_block(f"G{gi}", g))
        ctx["grants"].append((f"G{gi}", g.id, g.title))
        gi += 1
    if not focused:
        fi = si = 1
        related = engine.search_items(db, question, limit=600)
        for (ptype, pid), score, _ in engine.score_text(db, question)[:30]:
            if (ptype == "faculty" and fi > 5) or (ptype == "student" and si > 2):
                continue
            meta = engine.people_meta.get((ptype, pid), {})
            tag = f"F{fi}" if ptype == "faculty" else f"S{si}"
            if ptype == "faculty":
                fi += 1
            else:
                si += 1
            items = [lbl for (pk, comp, lbl, s) in related if pk == (ptype, pid)][:3]
            ctx["blocks"].append(f"[{tag}] {ptype.upper()}: {meta.get('name')} — {meta.get('department') or meta.get('school') or ''}\n"
                                 f"  Match score to the question: {round(score * 100)}\n"
                                 f"  Most related evidence: {'; '.join(items) or 'see profile'}")
            ctx["people"].append((tag, ptype, pid, meta.get("name")))
    from app.services import clusters as cluster_service
    for ci, (c, sim, members) in enumerate(cluster_service.for_text(db, question, top=1), 1):
        fac = [m[1]["name"] for m in members if m[0][0] == "faculty"][:8]
        stu = [m[1]["name"] for m in members if m[0][0] == "student"][:3]
        ctx["blocks"].append(f"[C{ci}] PROPOSED AURAK RESEARCH CLUSTER (defined by this project, not an official AURAK unit; "
                             f"membership is AI-suggested): {c['name']}\n"
                             f"  Faculty members: {', '.join(fac) or 'none'}\n  Students: {', '.join(stu) or 'none'}")
        ctx.setdefault("clusters", []).append((f"C{ci}", c["key"], c["name"]))
    return ctx


def _name(db, ptype, pid):
    if ptype == "faculty":
        f = db.get(Faculty, pid)
        return f.full_name if f else pid
    s = db.get(Student, int(pid))
    return s.full_name if s else pid


def stream_answer(db: Session, question: str, history: list[dict], user_id: int | None, grant_id: int | None = None):
    """Yield events: one {"sources", "available"} header, then {"delta"} text chunks, then {"done"}."""
    ctx = retrieve(db, question, grant_id)
    sources = ([{"tag": t, "label": title, "url": f"/grants/{gid}"} for t, gid, title in ctx["grants"]] +
               [{"tag": t, "label": name, "url": f"/{'faculty' if pt == 'faculty' else 'students'}/{pid}"}
                for t, pt, pid, name in ctx["people"]] +
               [{"tag": t, "label": f"Cluster: {name}", "url": f"/clusters/{key}"} for t, key, name in ctx.get("clusters", [])])
    provider = llm.active_provider()
    if provider is None:
        db.add(ChatLog(user_id=user_id, question=question, answer=UNAVAILABLE, context={"sources": sources}, model=None))
        db.commit()
        yield {"sources": sources, "available": False}
        yield {"delta": UNAVAILABLE}
        yield {"done": True}
        return
    yield {"sources": sources, "available": True}
    records = "\n\n".join(ctx["blocks"]) or "NO RECORDS FOUND."
    system = SYSTEM + "\n\nRECORDS:\n" + records
    messages = []
    for h in history[-4:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": str(h["content"])[:1200]})
    while messages and messages[0]["role"] != "user":  # Claude requires the conversation to start with the user
        messages.pop(0)
    messages.append({"role": "user", "content": question})
    text = []
    try:
        try:
            for delta in llm.stream(provider, system, messages):
                text.append(delta)
                yield {"delta": delta}
        except llm.ProviderUnavailable as e:
            # A cloud provider failed before answering: fall back to the local model when allowed
            if text or provider == "ollama" or not config.LLM_FALLBACK_TO_OLLAMA or not llm.ollama_running():
                raise
            log.warning("%s unavailable (%s); falling back to the local model", provider, e)
            provider = "ollama"
            for delta in llm.stream(provider, system, messages):
                text.append(delta)
                yield {"delta": delta}
    except Exception as e:
        log.warning("LLM call failed: %s", e)
        msg = f"\n\n{UNAVAILABLE} ({type(e).__name__}: {str(e)[:120]})"
        text.append(msg)
        yield {"delta": msg, "error": True}
    db.add(ChatLog(user_id=user_id, question=question, answer="".join(text).strip(), context={"sources": sources},
                   model=llm.model_name(provider)))
    db.commit()
    yield {"done": True}


def warm_up():
    """Load the local model into memory ahead of the first question (called at app start)."""
    if llm.active_provider() != "ollama":
        return
    try:
        httpx.post(f"{config.OLLAMA_URL}/api/generate", timeout=300,
                   json={"model": config.LLM_MODEL, "prompt": "", "keep_alive": config.LLM_KEEP_ALIVE,
                         "options": {"num_ctx": 4096}})
    except httpx.HTTPError as e:
        log.info("LLM warm-up skipped: %s", e)
