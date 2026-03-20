"""Dashboard read-only summary endpoint (Phase 17-1)."""
from fastapi import APIRouter
from database import get_connection

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
async def get_dashboard_summary():
    """Aggregate workspace-level counts in a single call.

    Pure read-only — no writes, no side effects.
    """
    conn = get_connection()
    try:
        # Project count
        project_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]

        # Task counts by status
        task_rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
        ).fetchall()
        task_by_status = {row["status"]: row["cnt"] for row in task_rows}
        total_tasks = sum(task_by_status.values())

        # Pending approvals
        pending_approvals = conn.execute(
            "SELECT COUNT(*) FROM approval_requests WHERE status = 'pending'"
        ).fetchone()[0]

        # Active orchestrations (distinct tasks that have agent_runs with status='running')
        active_orchestrations = conn.execute(
            "SELECT COUNT(DISTINCT task_id) FROM agent_runs WHERE status = 'running'"
        ).fetchone()[0]

        # Failed tasks
        failed_tasks = task_by_status.get("failed", 0)

        # Per-project health
        project_health = []
        projects = conn.execute(
            "SELECT id, name FROM projects ORDER BY created_at DESC"
        ).fetchall()
        for proj in projects:
            pid = proj["id"]
            task_counts = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM tasks WHERE project_id = ? GROUP BY status",
                (pid,),
            ).fetchall()
            status_map = {r["status"]: r["cnt"] for r in task_counts}
            proj_total = sum(status_map.values())

            pending_app = conn.execute(
                "SELECT COUNT(*) FROM approval_requests ar JOIN tasks t ON ar.task_id = t.id WHERE t.project_id = ? AND ar.status = 'pending'",
                (pid,),
            ).fetchone()[0]

            last_activity = conn.execute(
                "SELECT MAX(updated_at) FROM tasks WHERE project_id = ?",
                (pid,),
            ).fetchone()[0]

            project_health.append({
                "project_id": pid,
                "project_name": proj["name"],
                "total_tasks": proj_total,
                "active_tasks": (
                    status_map.get("planning", 0)
                    + status_map.get("in_progress", 0)
                    + status_map.get("reviewing", 0)
                ),
                "failed_tasks": status_map.get("failed", 0),
                "done_tasks": status_map.get("done", 0),
                "pending_approvals": pending_app,
                "last_activity": last_activity,
            })

        # Token summary (aggregate, best-effort)
        token_row = conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens), 0) as prompt,"
            " COALESCE(SUM(completion_tokens), 0) as completion,"
            " COALESCE(SUM(total_tokens), 0) as total,"
            " COUNT(*) as entries"
            " FROM token_usage_log"
        ).fetchone()
        token_summary = {
            "total_prompt_tokens": token_row["prompt"],
            "total_completion_tokens": token_row["completion"],
            "total_tokens": token_row["total"],
            "log_entries": token_row["entries"],
        }

        return {
            "project_count": project_count,
            "total_tasks": total_tasks,
            "task_by_status": task_by_status,
            "pending_approvals": pending_approvals,
            "active_orchestrations": active_orchestrations,
            "failed_tasks": failed_tasks,
            "project_health": project_health,
            "token_summary": token_summary,
        }
    finally:
        conn.close()


@router.get("/search")
async def search_workspace(q: str = "", limit: int = 10):
    """Search workspace objects by keyword.

    Pure read-only. Uses SQL LIKE for simplicity.
    Returns results grouped by object type.
    """
    conn = get_connection()
    try:
        if not q.strip():
            return {"query": q, "projects": [], "tasks": [], "approvals": []}

        pattern = f"%{q.strip()}%"

        # Search projects (name, description, repo path)
        projects = conn.execute(
            "SELECT id, name, description, local_repo_path, created_at "
            "FROM projects "
            "WHERE name LIKE ? OR description LIKE ? OR local_repo_path LIKE ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (pattern, pattern, pattern, limit),
        ).fetchall()

        # Search tasks (title, description, status)
        tasks = conn.execute(
            "SELECT t.id, t.title, t.description, t.status, t.priority, "
            "t.project_id, p.name as project_name, t.created_at, t.updated_at "
            "FROM tasks t JOIN projects p ON t.project_id = p.id "
            "WHERE t.title LIKE ? OR t.description LIKE ? "
            "ORDER BY t.updated_at DESC LIMIT ?",
            (pattern, pattern, limit),
        ).fetchall()

        # Search approvals (action_type, linked task title)
        approvals = conn.execute(
            "SELECT ar.id, ar.task_id, ar.action_type, ar.status, ar.created_at, "
            "t.title as task_title, t.project_id, p.name as project_name "
            "FROM approval_requests ar "
            "JOIN tasks t ON ar.task_id = t.id "
            "JOIN projects p ON t.project_id = p.id "
            "WHERE t.title LIKE ? OR ar.action_type LIKE ? "
            "ORDER BY ar.created_at DESC LIMIT ?",
            (pattern, pattern, limit),
        ).fetchall()

        return {
            "query": q,
            "projects": [dict(r) for r in projects],
            "tasks": [dict(r) for r in tasks],
            "approvals": [dict(r) for r in approvals],
        }
    finally:
        conn.close()


@router.get("/attention")
async def get_dashboard_attention():
    """Return items needing attention and recent activity feed.

    Pure read-only — no writes, no side effects.
    """
    conn = get_connection()
    try:
        # Failed tasks (most recent first, limit 10)
        failed_tasks = conn.execute(
            "SELECT t.id, t.title, t.status, t.project_id, p.name as project_name, t.updated_at "
            "FROM tasks t JOIN projects p ON t.project_id = p.id "
            "WHERE t.status = 'failed' ORDER BY t.updated_at DESC LIMIT 10"
        ).fetchall()

        # Pending approvals (oldest first — longest waiting, limit 10)
        pending_approvals = conn.execute(
            "SELECT ar.id, ar.task_id, ar.action_type, ar.status, ar.created_at, "
            "t.title as task_title, t.project_id, p.name as project_name "
            "FROM approval_requests ar "
            "JOIN tasks t ON ar.task_id = t.id "
            "JOIN projects p ON t.project_id = p.id "
            "WHERE ar.status = 'pending' "
            "ORDER BY ar.created_at ASC LIMIT 10"
        ).fetchall()

        # Unconfirmed execution requests (requested but not confirmed/rejected, limit 10)
        stalled_requests = conn.execute(
            "SELECT er.id, er.task_id, er.status, er.risk_level, er.created_at, "
            "t.title as task_title, t.project_id, p.name as project_name "
            "FROM execution_requests er "
            "JOIN tasks t ON er.task_id = t.id "
            "JOIN projects p ON t.project_id = p.id "
            "WHERE er.status = 'requested' "
            "ORDER BY er.created_at ASC LIMIT 10"
        ).fetchall()

        # Recent activity: latest tasks updated, limit 15
        recent_tasks = conn.execute(
            "SELECT t.id, t.title, t.status, t.project_id, p.name as project_name, "
            "t.created_at, t.updated_at "
            "FROM tasks t JOIN projects p ON t.project_id = p.id "
            "ORDER BY t.updated_at DESC LIMIT 15"
        ).fetchall()

        # Recent agent runs, limit 10
        recent_runs = conn.execute(
            "SELECT ar.id, ar.task_id, ar.role, ar.status, ar.started_at, ar.ended_at, ar.created_at, "
            "t.title as task_title "
            "FROM agent_runs ar JOIN tasks t ON ar.task_id = t.id "
            "ORDER BY ar.created_at DESC LIMIT 10"
        ).fetchall()

        return {
            "attention": {
                "failed_tasks": [dict(r) for r in failed_tasks],
                "pending_approvals": [dict(r) for r in pending_approvals],
                "stalled_requests": [dict(r) for r in stalled_requests],
            },
            "recent_activity": {
                "recent_tasks": [dict(r) for r in recent_tasks],
                "recent_runs": [dict(r) for r in recent_runs],
            },
        }
    finally:
        conn.close()
