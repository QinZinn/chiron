"""HTTP layer. The only consumer today is Mnemosyne (Rust/Actix) — a DIFFERENT
runtime, so it has to go over HTTP; in-process is not an option.

The two write routes (/transcripts, /ingest) do NOT touch the LLM. Extraction is a separate job.
"""

from __future__ import annotations

import hmac
import os
from functools import wraps
from uuid import UUID

import psycopg
from flask import Flask, jsonify, request

from typing import Callable

from ks import confirm as confirm_mod, notes as notes_mod, query as query_mod, settings
from ks.db import connect
from ks.ingest import ingest_concepts
from ks.llm import LLMError, LLMProvider, provider_from_env
from ks.ingest import MergeTargetInvalid
from ks.models import SYMMETRIC_RELATIONS, ConceptDraft, SourceModule
from ks.ocr_client import OcrClient, OcrError, Upload, client_from_env
from ks.transcripts import save_transcript


def _authorized(req) -> bool:
    """Compare tokens as BYTES.

    A BUG WE HIT: hmac.compare_digest raises TypeError (rather than returning False) when a string
    has non-ASCII characters → a malformed header crashed with 500 instead of 403. `.encode`
    never fails on a str, so comparing bytes stops it at the root.
    """
    expected = os.environ.get(settings.HTTP_TOKEN_ENV, "")
    if not expected:
        return False  # no token configured → reject everything, never wide open

    header = req.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    given = header[len(prefix):]
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def validate_token_config(env: dict[str, str] | None = None) -> str:
    """Check KS_HTTP_TOKEN at startup. Wrong → die at once, not silently 403 forever.

    MEASURED WITH CURL: a non-ASCII token can NEVER authenticate. WSGI decodes
    header values as latin-1, so the token's UTF-8 bytes reach the application as
    mojibake and never match. That is a configuration error, not a client error.
    """
    env = os.environ if env is None else env
    token = env.get(settings.HTTP_TOKEN_ENV, "")
    if not token:
        raise RuntimeError(f"Missing environment variable {settings.HTTP_TOKEN_ENV}")
    if not token.isascii():
        raise RuntimeError(
            f"{settings.HTTP_TOKEN_ENV} must be ASCII. WSGI decodes HTTP headers"
            " as latin-1, so a non-ASCII token will never match — every request"
            " would be 403 forever."
        )
    return token


def require_token(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _authorized(request):
            return jsonify({"error": "forbidden", "detail": "wrong or missing token"}), 403
        return view(*args, **kwargs)

    return wrapper


def create_app(
    *,
    ocr_factory: Callable[[], OcrClient] = client_from_env,
    provider_factory: Callable[[], LLMProvider] = provider_from_env,
) -> Flask:
    """Two factories so tests can swap OCR and the LLM for fakes; by default they read env.

    Called per request rather than once at startup: missing OCR or LLM configuration
    only breaks the route that needs it; KS still starts and serves everything else.
    """
    app = Flask(__name__)
    # Note uploads pass through here before reaching the OCR service.
    app.config["MAX_CONTENT_LENGTH"] = settings.MAX_NOTE_UPLOAD_BYTES

    # ------------------------------------------------------------ health

    @app.get("/health")
    def health():
        """Pure liveness: NO auth, NO DB.

        If the DB is down while the process is alive, /health still returns 200 — intended: DB state
        shows up as ok=false from /transcripts and 503 from /ingest.
        """
        return jsonify({"status": "ok"}), 200

    # ------------------------------------------------------------ transcripts

    @app.post("/transcripts")
    @require_token
    def post_transcripts():
        """DB write only. Does NOT trigger extraction, does NOT touch the LLM.

        Truly idempotent on session_ref — retrying the same session_ref is completely safe.
        ok=false with HTTP 200 means KS is alive but the DB is down. By design,
        not a bug: save_transcript never raises.
        """
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "invalid_body", "detail": "expected a JSON object"}), 400

        session_ref = body.get("session_ref")
        if not isinstance(session_ref, str) or not session_ref:
            return jsonify({
                "error": "invalid_session_ref",
                "detail": "session_ref must be a non-empty string",
            }), 400
        if "content" not in body:
            return jsonify({"error": "missing_content", "detail": "content is missing"}), 400

        result = save_transcript(session_ref, body["content"])
        return jsonify({
            "ok": result.ok,
            "transcript_id": str(result.transcript_id) if result.transcript_id else None,
            "error": result.error,
        }), 200

    # ------------------------------------------------------------ ingest

    @app.post("/ingest")
    @require_token
    def post_ingest():
        """All-or-nothing: one bad draft → the whole request is 400 and no draft is written.

        Matches the core's transaction and spares clients from hunting errors by index.

        WARNING: idempotency here is a fuzzy similarity match, NOT an identity
        key. Retrying is only safe with the title kept VERBATIM.
        """
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "invalid_body", "detail": "expected a JSON object"}), 400
        raw_drafts = body.get("drafts")
        if not isinstance(raw_drafts, list):
            return jsonify({"error": "invalid_drafts", "detail": "drafts must be an array"}), 400

        drafts: list[ConceptDraft] = []
        for i, raw in enumerate(raw_drafts):
            if not isinstance(raw, dict):
                return jsonify({
                    "error": "invalid_draft",
                    "detail": f"draft[{i}] is not an object",
                }), 400
            try:
                # SourceModule(value) converts and validates in one step —
                # no separate validation step.
                draft = ConceptDraft(
                    title=raw["title"],
                    subject=raw["subject"],
                    summary=raw["summary"],
                    source_module=SourceModule(raw["source_module"]),
                )
            except KeyError as exc:
                return jsonify({
                    "error": "invalid_draft",
                    "detail": f"draft[{i}] is missing field {exc.args[0]}",
                }), 400
            except ValueError as exc:
                return jsonify({"error": "invalid_draft", "detail": f"draft[{i}]: {exc}"}), 400
            drafts.append(draft)

        try:
            with connect() as conn:
                result = ingest_concepts(conn, drafts)
                conn.commit()
        except psycopg.Error as exc:
            # 503 rather than an opaque 500: a down DB is temporary, the client should retry.
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503

        return jsonify({
            "ingested": [
                {
                    "title": item.draft.title,
                    "node_id": str(item.node_id),
                    "created": item.created,
                    "candidates": [
                        {"node_id": str(c.node_id), "title": c.title, "score": c.score}
                        for c in item.candidates
                    ],
                }
                for item in result.ingested
            ]
        }), 200

    # ------------------------------------------------------------ nodes

    @app.get("/nodes")
    @require_token
    def get_nodes():
        """Pure DB read, NO LLM. Does not return edges."""
        raw_limit = request.args.get("limit")
        limit = settings.DEFAULT_NODE_LIMIT
        if raw_limit is not None:
            try:
                limit = int(raw_limit)
            except ValueError:
                return jsonify({"error": "invalid_limit", "detail": "limit must be an integer"}), 400
            if limit < 1:
                return jsonify({"error": "invalid_limit", "detail": "limit must be >= 1"}), 400
            # Over the cap → 400. NOT silently truncated: the client must know it got less.
            if limit > settings.MAX_NODE_LIMIT:
                return jsonify({
                    "error": "invalid_limit",
                    "detail": f"limit is at most {settings.MAX_NODE_LIMIT}",
                }), 400

        source_module = request.args.get("source_module")
        if source_module is not None:
            try:
                SourceModule(source_module)
            except ValueError as exc:
                return jsonify({"error": "invalid_source_module", "detail": str(exc)}), 400

        try:
            with connect() as conn:
                nodes = query_mod.list_nodes(
                    conn,
                    subject=request.args.get("subject"),
                    source_module=source_module,
                    limit=limit,
                )
        except psycopg.Error as exc:
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503

        return jsonify({
            "nodes": [
                {
                    "id": str(n.id),
                    "title": n.title,
                    "subject": n.subject,
                    "summary": n.summary,
                }
                for n in nodes
            ]
        }), 200


    @app.get("/edges")
    @require_token
    def get_edges():
        """APPROVED edges for the concept map. Pure DB read, NO LLM.

        A route of its own because GET /nodes deliberately returns no edges. `symmetric` says
        the relation has no direction (related, contrasts_with): stored one way, read
        as both ways.
        """
        raw_limit = request.args.get("limit")
        limit = settings.MAX_EDGE_LIMIT
        if raw_limit is not None:
            try:
                limit = int(raw_limit)
            except ValueError:
                return jsonify({"error": "invalid_limit", "detail": "limit must be an integer"}), 400
            if limit < 1 or limit > settings.MAX_EDGE_LIMIT:
                return jsonify({
                    "error": "invalid_limit",
                    "detail": f"limit must be between 1 and {settings.MAX_EDGE_LIMIT}",
                }), 400

        node_id = None
        raw_node = request.args.get("node_id")
        if raw_node is not None:
            try:
                node_id = UUID(raw_node)
            except ValueError:
                return jsonify({
                    "error": "invalid_node_id",
                    "detail": f"'{raw_node}' is not a valid UUID",
                }), 400

        try:
            with connect() as conn:
                edges = query_mod.list_edges(conn, node_id=node_id, limit=limit)
        except psycopg.Error as exc:
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503

        symmetric = {r.value for r in SYMMETRIC_RELATIONS}
        return jsonify({
            "edges": [
                {
                    "id": str(e.id),
                    "from": str(e.from_node_id),
                    "to": str(e.to_node_id),
                    "relation_type": e.relation_type,
                    "symmetric": e.relation_type in symmetric,
                }
                for e in edges
            ]
        }), 200

    @app.get("/nodes/<node_id>")
    @require_token
    def get_node(node_id: str):
        """One node by id. Pure DB read, NO LLM.

        A merged node → returns the TARGET node with 200 (exactly one step), just like
        GET /nodes. Two routes reading the same data must not behave differently.
        Consequence for clients: the returned `id` may DIFFER from the id requested.
        """
        try:
            parsed = UUID(node_id)
        except ValueError:
            return jsonify({
                "error": "invalid_node_id",
                "detail": f"'{node_id}' is not a valid UUID",
            }), 400

        try:
            with connect() as conn:
                node = query_mod.get_node(conn, parsed)
        except psycopg.Error as exc:
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503

        if node is None:
            return jsonify({
                "error": "node_not_found",
                "detail": f"no node {node_id}",
            }), 404

        return jsonify({
            "id": str(node.id),
            "title": node.title,
            "subject": node.subject,
            "summary": node.summary,
        }), 200

    # ------------------------------------------------------------ notes (scan)

    def _uuid_or_400(raw: str, what: str):
        try:
            return UUID(raw), None
        except ValueError:
            return None, (jsonify({"error": f"invalid_{what}_id", "detail": f"'{raw}' is not a valid UUID"}), 400)

    @app.errorhandler(413)
    def _too_large(_exc):
        mb = settings.MAX_NOTE_UPLOAD_BYTES // (1024 * 1024)
        return jsonify({"error": "too_large", "detail": f"Total size is at most {mb} MB"}), 413

    @app.post("/notes")
    @require_token
    def post_note():
        """multipart: files (images/PDF, several files) + title (optional). After OCR, returns a 'draft' note.

        No concepts are extracted at this step: the learner must review and correct the OCR text first.
        """
        uploads = [
            Upload(f.filename or "file", f.mimetype or "application/octet-stream", f.read())
            for f in request.files.getlist("files")
        ]
        if not uploads:
            return jsonify({"error": "no_files", "detail": "No files uploaded"}), 400
        try:
            ocr = ocr_factory()
            with connect() as conn:
                note = notes_mod.create_from_uploads(conn, uploads, ocr, title=request.form.get("title"))
                conn.commit()
        except OcrError as exc:
            return jsonify({"error": exc.code, "detail": str(exc)}), exc.status
        except psycopg.Error as exc:
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503
        return jsonify(note.as_dict()), 201

    @app.get("/notes")
    @require_token
    def get_notes():
        try:
            limit = min(int(request.args.get("limit", "50")), settings.MAX_NOTE_LIMIT)
        except ValueError:
            return jsonify({"error": "invalid_limit", "detail": "limit must be an integer"}), 400
        try:
            with connect() as conn:
                items = notes_mod.list_notes(conn, limit=max(1, limit))
        except psycopg.Error as exc:
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503
        return jsonify({"notes": [n.as_dict(with_pages=False) for n in items]}), 200

    @app.get("/notes/<note_id>")
    @require_token
    def get_note(note_id: str):
        parsed, err = _uuid_or_400(note_id, "note")
        if err:
            return err
        try:
            with connect() as conn:
                note = notes_mod.get(conn, parsed)
                concepts = notes_mod.concepts_for(conn, parsed)
        except notes_mod.NoteNotFound as exc:
            return jsonify({"error": "note_not_found", "detail": str(exc)}), 404
        except psycopg.Error as exc:
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503
        return jsonify({**note.as_dict(), "concepts": concepts}), 200

    @app.patch("/notes/<note_id>")
    @require_token
    def patch_note(note_id: str):
        parsed, err = _uuid_or_400(note_id, "note")
        if err:
            return err
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "invalid_body", "detail": "expected a JSON object"}), 400
        title, text = body.get("title"), body.get("text")
        if (title is not None and not isinstance(title, str)) or (text is not None and not isinstance(text, str)):
            return jsonify({"error": "invalid_body", "detail": "title and text must be strings"}), 400
        try:
            with connect() as conn:
                note = notes_mod.update(conn, parsed, title=title, text=text)
                conn.commit()
        except notes_mod.NoteNotFound as exc:
            return jsonify({"error": "note_not_found", "detail": str(exc)}), 404
        except psycopg.Error as exc:
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503
        return jsonify(note.as_dict()), 200

    @app.post("/notes/<note_id>/extract")
    @require_token
    def post_note_extract(note_id: str):
        """Extract concepts from the corrected text. Results AWAIT REVIEW; nothing enters ks.nodes yet."""
        parsed, err = _uuid_or_400(note_id, "note")
        if err:
            return err
        try:
            provider = provider_factory()
        except (LLMError, RuntimeError, ValueError, KeyError) as exc:
            return jsonify({"error": "llm_not_configured", "detail": str(exc)}), 503
        try:
            with connect() as conn:
                result = notes_mod.extract(conn, parsed, provider)
                conn.commit()
                concepts = notes_mod.concepts_for(conn, parsed)
        except notes_mod.NoteNotFound as exc:
            return jsonify({"error": "note_not_found", "detail": str(exc)}), 404
        except notes_mod.EmptyNote as exc:
            return jsonify({"error": "empty_note", "detail": str(exc)}), 400
        except psycopg.Error as exc:
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503
        if not result.ok:
            # The LLM error is already recorded on the transcript (last_error); report the cause so
            # the learner knows whether retrying will help.
            return jsonify({"error": "extraction_failed", "detail": result.error, "concepts": concepts}), 502
        return jsonify({"extracted": len(result.concepts), "concepts": concepts}), 200

    # ------------------------------------------------------------ concept review

    def _concept_dict(c) -> dict:
        return {
            "id": str(c.id),
            "transcript_id": str(c.transcript_id),
            "title": c.title,
            "subject": c.subject,
            "summary": c.summary,
            "source_module": c.source_module.value,
            "status": c.status,
            "node_id": str(c.node_id) if c.node_id else None,
        }

    @app.get("/extracted")
    @require_token
    def get_extracted():
        """The review queue — concepts from study sessions and from scanned notes alike."""
        status = request.args.get("status", "pending_review")
        if status not in ("pending_review", "accepted", "discarded"):
            return jsonify({"error": "invalid_status", "detail": status}), 400
        try:
            with connect() as conn:
                items = confirm_mod.list_extracted(conn, status=status, limit=200)
        except psycopg.Error as exc:
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503
        return jsonify({"concepts": [_concept_dict(c) for c in items]}), 200

    @app.patch("/extracted/<concept_id>")
    @require_token
    def patch_extracted(concept_id: str):
        parsed, err = _uuid_or_400(concept_id, "concept")
        if err:
            return err
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "invalid_body", "detail": "expected a JSON object"}), 400
        fields = {k: body.get(k) for k in ("title", "subject", "summary")}
        if any(v is not None and not isinstance(v, str) for v in fields.values()):
            return jsonify({"error": "invalid_body", "detail": "title, subject and summary must be strings"}), 400
        try:
            with connect() as conn:
                concept = confirm_mod.edit_pending(conn, parsed, **fields)
                conn.commit()
        except confirm_mod.AlreadyDecided as exc:
            return jsonify({"error": "already_decided", "detail": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"error": "invalid_body", "detail": str(exc)}), 400
        except confirm_mod.ExtractedConceptNotFound as exc:
            return jsonify({"error": "concept_not_found", "detail": str(exc)}), 404
        except psycopg.Error as exc:
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503
        return jsonify(_concept_dict(concept)), 200

    @app.get("/extracted/<concept_id>/candidates")
    @require_token
    def get_candidates(concept_id: str):
        """Near-duplicate nodes for a concept, and the node the 0.6 rule WOULD merge into.

        So the review screen asks the learner before writing, instead of reporting "merged" afterwards.
        """
        parsed, err = _uuid_or_400(concept_id, "concept")
        if err:
            return err
        try:
            with connect() as conn:
                _concept, candidates, suggested = confirm_mod.candidates_for(conn, parsed)
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, subject, summary FROM ks.nodes WHERE id = ANY(%s)",
                        ([c.node_id for c in candidates],),
                    )
                    extra = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        except confirm_mod.ExtractedConceptNotFound as exc:
            return jsonify({"error": "concept_not_found", "detail": str(exc)}), 404
        except psycopg.Error as exc:
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503
        return jsonify({
            "threshold": settings.DUPLICATE_THRESHOLD,
            "suggested_node_id": str(suggested.node_id) if suggested else None,
            "candidates": [
                {
                    "node_id": str(c.node_id),
                    "title": c.title,
                    "score": round(c.score, 3),
                    "subject": extra.get(c.node_id, ("", ""))[0],
                    "summary": extra.get(c.node_id, ("", ""))[1],
                }
                for c in candidates
            ],
        }), 200

    @app.post("/extracted/<concept_id>/accept")
    @require_token
    def post_accept(concept_id: str):
        """Write into the graph.

        No body: the automatic rule (threshold 0.6), as before. A body of
        `{"decision": "create"}` or `{"decision": "merge", "node_id": …}`:
        the learner has chosen, and the threshold no longer decides.
        """
        parsed, err = _uuid_or_400(concept_id, "concept")
        if err:
            return err
        body = request.get_json(silent=True) or {}
        decision = body.get("decision")
        merge_into = None
        if decision is not None:
            if decision not in ("create", "merge"):
                return jsonify({"error": "invalid_decision", "detail": "decision must be create or merge"}), 400
            if decision == "merge":
                merge_into, err = _uuid_or_400(str(body.get("node_id") or ""), "node")
                if err:
                    return err
        try:
            with connect() as conn:
                item = confirm_mod.accept(conn, parsed, decision=decision, merge_into=merge_into)
                conn.commit()
        except confirm_mod.AlreadyDecided as exc:
            return jsonify({"error": "already_decided", "detail": str(exc)}), 409
        except confirm_mod.ExtractedConceptNotFound as exc:
            return jsonify({"error": "concept_not_found", "detail": str(exc)}), 404
        except MergeTargetInvalid as exc:
            return jsonify({"error": "invalid_merge_target", "detail": str(exc)}), 400
        except psycopg.Error as exc:
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503
        return jsonify({
            "node_id": str(item.node_id),
            # created=False: matched an existing concept — the learner needs to know
            # it was merged rather than added.
            "created": item.created,
            "candidates": [{"node_id": str(c.node_id), "title": c.title, "score": c.score} for c in item.candidates],
        }), 200

    @app.post("/extracted/<concept_id>/discard")
    @require_token
    def post_discard(concept_id: str):
        parsed, err = _uuid_or_400(concept_id, "concept")
        if err:
            return err
        try:
            with connect() as conn:
                confirm_mod.discard(conn, parsed)
                conn.commit()
        except confirm_mod.AlreadyDecided as exc:
            return jsonify({"error": "already_decided", "detail": str(exc)}), 409
        except confirm_mod.ExtractedConceptNotFound as exc:
            return jsonify({"error": "concept_not_found", "detail": str(exc)}), 404
        except psycopg.Error as exc:
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503
        return jsonify({"discarded": True}), 200

    return app


def serve() -> None:
    """Run the server. Blocks forever — systemd Type=simple, NOT a cron job.

    Flask's dev server prints a "not for production" warning: acceptable at single-user
    scale, recorded as technical debt (gunicorn/waitress if ever needed).
    """
    validate_token_config()
    port = int(os.environ.get(settings.HTTP_PORT_ENV, settings.DEFAULT_HTTP_PORT))
    host = os.environ.get(settings.HTTP_HOST_ENV, "").strip() or settings.DEFAULT_HTTP_HOST
    create_app().run(host=host, port=port)
