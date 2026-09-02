"""Entry point for one isolated, controllable DeskRAG background job."""

from __future__ import annotations

import argparse
import os
import time
import traceback

from rag.jobs import finish_job, get_job, start_job, update_job


def _control_checkpoint(job_id: str, active_status: str = "indexing") -> None:
    """All worker job types honour pause/cancel between safe batches."""
    from rag.jobs import control_state
    paused, cancelled = control_state(job_id)
    if cancelled:
        raise InterruptedError("RAG 工作已取消")
    while paused:
        update_job(job_id, status="paused", message="已暫停；目前批次完成後才會停止")
        time.sleep(0.25)
        paused, cancelled = control_state(job_id)
        if cancelled:
            raise InterruptedError("RAG 工作已取消")
    update_job(job_id, status=active_status)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one OmniContext DeskRAG worker job")
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    job = get_job(args.job_id)
    if not job:
        return 2

    start_job(args.job_id)
    update_job(args.job_id, worker_pid=os.getpid())
    try:
        if job["job_type"] == "index":
            from rag.scanner import scanner
            from rag.storage import verify_index_consistency
            status = scanner.run_indexing_job(args.job_id, job.get("folder_id"))
            result = {"storage": verify_index_consistency()}
            finish_job(args.job_id, status, get_job(args.job_id).get("message", "索引工作完成"), result=result)
        elif job["job_type"] == "remove_folder":
            from rag.lifecycle import remove_folder_index
            result = remove_folder_index(job["folder_id"], lambda **values: update_job(args.job_id, **values))
            finish_job(args.job_id, "completed", f"已移除 {result['removed_files']} 個檔案的索引", result=result)
        elif job["job_type"] == "clear_all":
            from rag.lifecycle import clear_all_rag_indexes
            result = clear_all_rag_indexes(lambda **values: update_job(args.job_id, **values))
            finish_job(args.job_id, "completed", f"已清空 {result['removed_files']} 個檔案的 RAG 索引", result=result)
        elif job["job_type"] == "audit":
            from rag.storage import verify_index_consistency
            result = {"storage": verify_index_consistency()}
            finish_job(args.job_id, "completed", "已完成 RAG 索引一致性檢查", result=result)
        elif job["job_type"] == "rebuild_bm25":
            from rag.lifecycle import rebuild_bm25_from_chroma
            result = rebuild_bm25_from_chroma(
                lambda **values: update_job(args.job_id, **values),
                control_checkpoint=lambda: _control_checkpoint(args.job_id),
            )
            finish_job(args.job_id, "completed", f"已從 Chroma 重建 {result['rebuilt_chunks']} 個 BM25 切片", result=result)
        elif job["job_type"] == "activity_sync":
            # ADR-012：把秘書記憶區（筆記／每日摘要／Handoff／同步報告／STATUS 草稿）
            # 併入 RAG activity 領域；在 worker 程序做，主服務不載入索引套件。
            from rag.activity_indexer import activity_indexer
            update_job(args.job_id, message="正在把秘書記憶區與工作紀錄併入知識庫…")
            result = activity_indexer.sync_all()
            finish_job(
                args.job_id,
                "completed",
                f"已將 {result.get('total_activity_indexed', 0)} 筆工作紀錄／記憶切片併入知識庫",
                result=result,
            )
        else:
            raise ValueError(f"Unsupported RAG job type: {job['job_type']}")
        return 0
    except InterruptedError as exc:
        finish_job(args.job_id, "cancelled", str(exc))
        return 0
    except Exception as exc:
        finish_job(args.job_id, "failed", "RAG worker 執行失敗", str(exc))
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
