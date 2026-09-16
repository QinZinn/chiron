"""CLI của KS. Mỗi lệnh tự mở/đóng connection và tự commit."""

from __future__ import annotations

import argparse
import json
import sys
from uuid import UUID

from ks import (
    card_sync as card_sync_mod,
    confirm as confirm_mod,
    db,
    edges as edges_mod,
    transcripts as tr_mod,
)
from ks.ingest import ingest_concepts
from ks.llm import provider_from_env
from ks.models import ConceptDraft, RelationType, SourceModule


# ---------------------------------------------------------------- commands


def cmd_migrate(args) -> int:
    with db.connect() as conn:
        ran = db.migrate(conn)
    if ran:
        for name in ran:
            print(f"đã chạy: {name}")
    else:
        print("không có migration mới")
    return 0


def cmd_create_node(args) -> int:
    draft = ConceptDraft(
        title=args.title,
        subject=args.subject,
        summary=args.summary,
        source_module=SourceModule(args.source_module),
    )
    with db.connect() as conn:
        result = ingest_concepts(conn, [draft])
        conn.commit()
    item = result.ingested[0]
    verb = "tạo mới" if item.created else "gộp vào node có sẵn"
    print(f"{verb}: {item.node_id}")
    if item.candidates:
        print("candidate gần giống:")
        for cand in item.candidates:
            print(f"  {cand.score:.3f}  {cand.title}  ({cand.node_id})")
    return 0


def cmd_suggest_edges(args) -> int:
    provider = provider_from_env()
    with db.connect() as conn:
        run = edges_mod.suggest_edges(conn, UUID(args.node_id), provider)
        conn.commit()
    print(f"outcome: {run.outcome}  candidate: {len(run.candidates)}  gợi ý mới: {len(run.suggestions)}")
    if run.error:
        print(f"lỗi: {run.error}")
    for s in run.suggestions:
        print(f"  {s.edge_id}  {s.relation_type.value:>14}  → {s.to_title}")
        if s.reason:
            print(f"      lý do: {s.reason}")
    return 0


def cmd_list_pending(args) -> int:
    with db.connect() as conn:
        pending = edges_mod.list_pending(conn, limit=args.limit)
    if not pending:
        print("không có cạnh nào chờ duyệt")
    for p in pending:
        print(f"{p.edge_id}  [{p.suggested_by.value}]  {p.from_title} --{p.relation_type.value}--> {p.to_title}")
    return 0


def cmd_approve(args) -> int:
    with db.connect() as conn:
        edges_mod.approve_edge(conn, UUID(args.edge_id))
        conn.commit()
    print(f"đã duyệt: {args.edge_id}")
    return 0


def cmd_reject(args) -> int:
    with db.connect() as conn:
        edges_mod.reject_edge(conn, UUID(args.edge_id))
        conn.commit()
    print(f"đã từ chối (giữ row vĩnh viễn): {args.edge_id}")
    return 0


def cmd_edit(args) -> int:
    with db.connect() as conn:
        edges_mod.edit_edge(conn, UUID(args.edge_id), RelationType(args.relation_type))
        conn.commit()
    print(f"đã sửa thành {args.relation_type} và duyệt: {args.edge_id}")
    return 0


def cmd_add_edge(args) -> int:
    with db.connect() as conn:
        edge_id = edges_mod.add_edge(
            conn, UUID(args.from_node), UUID(args.to_node), RelationType(args.relation_type)
        )
        conn.commit()
    print(f"đã thêm (approved): {edge_id}")
    return 0


def cmd_neighbors(args) -> int:
    with db.connect() as conn:
        result = edges_mod.neighbors(conn, UUID(args.node_id))
    if not result:
        print("không có node kề nào (chỉ tính cạnh đã approved)")
    for n in result:
        print(f"  {n.relation_type.value:>14}  [{n.direction:>4}]  {n.title}  ({n.node_id})")
    return 0


def cmd_stats(args) -> int:
    with db.connect() as conn:
        print(json.dumps(edges_mod.stats(conn), ensure_ascii=False, indent=2))
    return 0


def cmd_save_transcript(args) -> int:
    with open(args.file, encoding="utf-8") as fh:
        content = json.load(fh)
    result = tr_mod.save_transcript(args.session_ref, content)
    if result.ok:
        print(f"đã lưu: {result.transcript_id}")
        return 0
    # save_transcript không raise; CLI mới là nơi quyết định exit code.
    print(f"lỗi: {result.error}", file=sys.stderr)
    return 1


def cmd_extract(args) -> int:
    provider = provider_from_env()
    with db.connect() as conn:
        if args.transcript_id:
            ids = [UUID(args.transcript_id)]
        else:
            ids = list(tr_mod.pending_transcripts(conn, limit=args.limit))
        if not ids:
            print("không có transcript nào chờ extract")
            return 0
        for tid in ids:
            result = tr_mod.extract_concepts(conn, tid, provider)
            conn.commit()  # commit từng transcript: một cái lỗi không kéo cả lô
            if result.ok:
                print(f"{tid}: rút được {len(result.concepts)} khái niệm")
                for c in result.concepts:
                    print(f"  {c.id}  {c.title}  [{c.subject}]")
            else:
                print(f"{tid}: lỗi (lần thử {result.attempts}) — {result.error}", file=sys.stderr)
    return 0


def cmd_list_extracted(args) -> int:
    with db.connect() as conn:
        items = confirm_mod.list_extracted(conn, status=args.status, limit=args.limit)
    if not items:
        print(f"không có khái niệm nào ở trạng thái {args.status}")
    for c in items:
        print(f"{c.id}  {c.title}  [{c.subject}]")
        print(f"    {c.summary}")
    return 0


def cmd_accept(args) -> int:
    with db.connect() as conn:
        item = confirm_mod.accept(conn, UUID(args.concept_id))
        conn.commit()
    verb = "tạo mới" if item.created else "gộp vào node có sẵn"
    print(f"{verb}: {item.node_id}")
    return 0


def cmd_discard(args) -> int:
    with db.connect() as conn:
        confirm_mod.discard(conn, UUID(args.concept_id))
        conn.commit()
    print(f"đã bỏ (giữ row vĩnh viễn): {args.concept_id}")
    return 0


def cmd_card_sync(args) -> int:
    """Đẩy node đã duyệt sang Mnemosyne thành card."""
    client = card_sync_mod.client_from_env()
    with db.connect() as conn:
        kwargs = {} if args.limit is None else {"limit": args.limit}
        result = card_sync_mod.sync_cards(conn, client, **kwargs)
        conn.commit()
    if not result.outcomes:
        print("không có node nào cần đồng bộ")
        return 0
    for o in result.outcomes:
        line = f"{o.node_id}  {o.status}"
        if o.http_status is not None:
            line += f"  http={o.http_status}"
        if o.reason:
            line += f"  reason={o.reason}"
        print(line)
        if o.error:
            # In nguyên văn: đây là bằng chứng để verify nhánh truncated.
            print(f"    {o.error}")
    print(f"tổng kết: {result.by_status()}")
    return 0


def cmd_serve(args) -> int:
    """Block vô hạn — systemd Type=simple, KHÔNG phải cron job."""
    from ks.http_app import serve

    serve()
    return 0


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ks", description="Knowledge Store CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("migrate", help="Chạy migration chưa áp dụng")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("create-node", help="Tạo một khái niệm (qua dò trùng)")
    p.add_argument("--title", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument(
        "--source-module",
        default=SourceModule.MNEMOSYNE.value,
        choices=[m.value for m in SourceModule],
    )
    p.set_defaults(func=cmd_create_node)

    p = sub.add_parser("suggest-edges", help="Top-K ứng viên → LLM → cạnh 'pending'")
    p.add_argument("--node-id", required=True)
    p.set_defaults(func=cmd_suggest_edges)

    p = sub.add_parser("list-pending", help="Cạnh chờ duyệt")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_list_pending)

    p = sub.add_parser("approve", help="Duyệt một cạnh")
    p.add_argument("edge_id")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="Từ chối một cạnh (giữ row vĩnh viễn)")
    p.add_argument("edge_id")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("edit", help="Sửa loại quan hệ rồi duyệt")
    p.add_argument("edge_id")
    p.add_argument("--relation-type", required=True, choices=[r.value for r in RelationType])
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("add-edge", help="Tự thêm cạnh (vào thẳng approved)")
    p.add_argument("--from-node", required=True, dest="from_node")
    p.add_argument("--to-node", required=True, dest="to_node")
    p.add_argument("--relation-type", required=True, choices=[r.value for r in RelationType])
    p.set_defaults(func=cmd_add_edge)

    p = sub.add_parser("neighbors", help="Node kề qua cạnh đã approved")
    p.add_argument("--node-id", required=True)
    p.set_defaults(func=cmd_neighbors)

    p = sub.add_parser("stats", help="Số liệu instrumentation")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("save-transcript", help="Lưu transcript raw (không chạm LLM)")
    p.add_argument("--session-ref", required=True, dest="session_ref")
    p.add_argument("--file", required=True, help="File JSON chứa content")
    p.set_defaults(func=cmd_save_transcript)

    p = sub.add_parser("extract", help="Rút khái niệm từ transcript chờ xử lý")
    p.add_argument("--transcript-id", default=None, dest="transcript_id")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("list-extracted", help="Khái niệm chờ xác nhận")
    p.add_argument("--status", default="pending_review",
                   choices=["pending_review", "accepted", "discarded"])
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_list_extracted)

    p = sub.add_parser("accept", help="Chấp nhận khái niệm → ghi vào đồ thị")
    p.add_argument("concept_id")
    p.set_defaults(func=cmd_accept)

    p = sub.add_parser("discard", help="Bỏ khái niệm (giữ row vĩnh viễn)")
    p.add_argument("concept_id")
    p.set_defaults(func=cmd_discard)

    p = sub.add_parser("card-sync", help="Đẩy node đã duyệt sang Mnemosyne thành card")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_card_sync)

    p = sub.add_parser("serve", help="Chạy HTTP server (block vô hạn)")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
