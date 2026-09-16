"""HTTP layer. Consumer duy nhất hiện tại là Mnemosyne (Rust/Actix) — runtime
KHÁC, nên bắt buộc qua HTTP chứ không in-process được.

Hai route ghi (/transcripts, /ingest) KHÔNG chạm LLM. Extraction là job riêng.
"""

from __future__ import annotations

import hmac
import os
from functools import wraps
from uuid import UUID

import psycopg
from flask import Flask, jsonify, request

from ks import query as query_mod, settings
from ks.db import connect
from ks.ingest import ingest_concepts
from ks.models import ConceptDraft, SourceModule
from ks.transcripts import save_transcript


def _authorized(req) -> bool:
    """So token trên BYTES.

    BUG ĐÃ GẶP: hmac.compare_digest ném TypeError (không trả False) khi chuỗi
    có ký tự non-ASCII → header dị dạng làm crash 500 thay vì 403. `.encode`
    không bao giờ hỏng với str, nên so trên bytes là chặn tận gốc.
    """
    expected = os.environ.get(settings.HTTP_TOKEN_ENV, "")
    if not expected:
        return False  # chưa cấu hình token → từ chối hết, không mở toang

    header = req.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    given = header[len(prefix):]
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def validate_token_config(env: dict[str, str] | None = None) -> str:
    """Kiểm tra KS_HTTP_TOKEN lúc khởi động. Sai → chết ngay, không âm thầm 403 mãi.

    ĐO ĐƯỢC BẰNG CURL: token non-ASCII KHÔNG BAO GIỜ xác thực được. WSGI giải mã
    giá trị header bằng latin-1, nên byte UTF-8 của token tới tay ứng dụng dưới
    dạng mojibake và không bao giờ khớp. Đây là lỗi cấu hình, không phải lỗi client.
    """
    env = os.environ if env is None else env
    token = env.get(settings.HTTP_TOKEN_ENV, "")
    if not token:
        raise RuntimeError(f"Thiếu biến môi trường {settings.HTTP_TOKEN_ENV}")
    if not token.isascii():
        raise RuntimeError(
            f"{settings.HTTP_TOKEN_ENV} phải là ASCII. Header HTTP được WSGI giải mã"
            " bằng latin-1 nên token non-ASCII sẽ không bao giờ khớp — mọi request"
            " sẽ 403 vĩnh viễn."
        )
    return token


def require_token(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _authorized(request):
            return jsonify({"error": "forbidden", "detail": "token sai hoặc thiếu"}), 403
        return view(*args, **kwargs)

    return wrapper


def create_app() -> Flask:
    app = Flask(__name__)

    # ------------------------------------------------------------ health

    @app.get("/health")
    def health():
        """Liveness thuần: KHÔNG cần auth, KHÔNG chạm DB.

        DB chết mà tiến trình sống thì /health vẫn 200 — đúng ý: trạng thái DB
        được phản ánh ở ok=false của /transcripts và 503 của /ingest.
        """
        return jsonify({"status": "ok"}), 200

    # ------------------------------------------------------------ transcripts

    @app.post("/transcripts")
    @require_token
    def post_transcripts():
        """Chỉ ghi DB. KHÔNG tự trigger extraction, KHÔNG chạm LLM.

        Idempotent thật theo session_ref — retry cùng session_ref an toàn tuyệt đối.
        ok=false kèm HTTP 200 nghĩa là KS sống nhưng DB chết. Đúng thiết kế,
        không phải bug: save_transcript không bao giờ raise.
        """
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "invalid_body", "detail": "cần một JSON object"}), 400

        session_ref = body.get("session_ref")
        if not isinstance(session_ref, str) or not session_ref:
            return jsonify({
                "error": "invalid_session_ref",
                "detail": "session_ref phải là chuỗi không rỗng",
            }), 400
        if "content" not in body:
            return jsonify({"error": "missing_content", "detail": "thiếu content"}), 400

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
        """All-or-nothing: một draft lỗi → cả request 400, không ghi draft nào.

        Khớp transaction của core và tránh buộc client dò lỗi theo index.

        CẢNH BÁO: idempotency ở đây là fuzzy match theo similarity, KHÔNG phải
        khoá định danh. Retry an toàn chỉ khi giữ NGUYÊN VĂN title.
        """
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "invalid_body", "detail": "cần một JSON object"}), 400
        raw_drafts = body.get("drafts")
        if not isinstance(raw_drafts, list):
            return jsonify({"error": "invalid_drafts", "detail": "drafts phải là mảng"}), 400

        drafts: list[ConceptDraft] = []
        for i, raw in enumerate(raw_drafts):
            if not isinstance(raw, dict):
                return jsonify({
                    "error": "invalid_draft",
                    "detail": f"draft[{i}] không phải object",
                }), 400
            try:
                # SourceModule(value) convert và validate cùng một thao tác —
                # không tách bước validate riêng.
                draft = ConceptDraft(
                    title=raw["title"],
                    subject=raw["subject"],
                    summary=raw["summary"],
                    source_module=SourceModule(raw["source_module"]),
                )
            except KeyError as exc:
                return jsonify({
                    "error": "invalid_draft",
                    "detail": f"draft[{i}] thiếu field {exc.args[0]}",
                }), 400
            except ValueError as exc:
                return jsonify({"error": "invalid_draft", "detail": f"draft[{i}]: {exc}"}), 400
            drafts.append(draft)

        try:
            with connect() as conn:
                result = ingest_concepts(conn, drafts)
                conn.commit()
        except psycopg.Error as exc:
            # 503 chứ không phải 500 mù mờ: DB chết là tạm thời, client nên retry.
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
        """Thuần đọc DB, KHÔNG chạm LLM. Không trả edges."""
        raw_limit = request.args.get("limit")
        limit = settings.DEFAULT_NODE_LIMIT
        if raw_limit is not None:
            try:
                limit = int(raw_limit)
            except ValueError:
                return jsonify({"error": "invalid_limit", "detail": "limit phải là số nguyên"}), 400
            if limit < 1:
                return jsonify({"error": "invalid_limit", "detail": "limit phải >= 1"}), 400
            # Vượt trần → 400. KHÔNG âm thầm cắt: client phải biết mình nhận thiếu.
            if limit > settings.MAX_NODE_LIMIT:
                return jsonify({
                    "error": "invalid_limit",
                    "detail": f"limit tối đa là {settings.MAX_NODE_LIMIT}",
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


    @app.get("/nodes/<node_id>")
    @require_token
    def get_node(node_id: str):
        """Một node theo id. Thuần đọc DB, KHÔNG chạm LLM.

        Node đã merge → trả node ĐÍCH với 200 (đúng một bước), giống hệt
        GET /nodes. Hai route đọc cùng dữ liệu không được hành xử khác nhau.
        Hệ quả cho client: `id` trả về có thể KHÁC id đã hỏi.
        """
        try:
            parsed = UUID(node_id)
        except ValueError:
            return jsonify({
                "error": "invalid_node_id",
                "detail": f"'{node_id}' không phải UUID hợp lệ",
            }), 400

        try:
            with connect() as conn:
                node = query_mod.get_node(conn, parsed)
        except psycopg.Error as exc:
            return jsonify({"error": "database_unavailable", "detail": str(exc)}), 503

        if node is None:
            return jsonify({
                "error": "node_not_found",
                "detail": f"không có node {node_id}",
            }), 404

        return jsonify({
            "id": str(node.id),
            "title": node.title,
            "subject": node.subject,
            "summary": node.summary,
        }), 200

    return app


def serve() -> None:
    """Chạy server. Block vô hạn — systemd Type=simple, KHÔNG phải cron job.

    Flask dev server in cảnh báo "not for production": chấp nhận được ở quy mô
    một người dùng, đã ghi làm nợ kỹ thuật (gunicorn/waitress nếu sau này cần).
    """
    validate_token_config()
    port = int(os.environ.get(settings.HTTP_PORT_ENV, settings.DEFAULT_HTTP_PORT))
    create_app().run(host="127.0.0.1", port=port)
