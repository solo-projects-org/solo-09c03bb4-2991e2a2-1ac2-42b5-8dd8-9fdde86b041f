"""业务服务层。

职责切分（题目要求）：
- 改报（register/amend）只读写 lamps 明细；成功后在同一 IMMEDIATE 事务内
  重算受影响型号（旧型号 + 新型号）的汇总并整体替换。任一步失败回滚，
  已提交的旧汇总仍可被曲线接口查到。
- 查曲线只读取 model_curve_meta / model_curve_rows 汇总表。

并发语义：
- 修订号按“实际提交顺序”判定：写事务以 BEGIN IMMEDIATE 串行化，
  后到者看到的是先提交者的最新 revision。
- 最新版原样重送（同 lamp_id + 同内容哈希，revision 等于当前版）幂等，
  返回原回执；旧版号或同版号但内容不同 => 409 冲突。
"""
import hashlib
import json

from .database import get_connection, transaction
from .exceptions import ConflictError, NotFoundError
from .km import kaplan_meier


def _content_hash(payload: dict) -> str:
    """对业务内容（不含修订号/时间戳）取哈希，用于原样重送识别。"""
    body = {k: payload[k] for k in ("lamp_id", "model", "entry_age_days", "exit_age_days", "outcome")}
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _rebuild_model(conn, model: str) -> None:
    """从明细重算单个型号的 KM 汇总并整体替换。

    只统计每盏灯的最新修订：被改报取代的历史行保留可追溯，
    但其统计贡献（含旧型号归属）必须撤掉。
    """
    lamps = [
        dict(r)
        for r in conn.execute(
            """
            SELECT l.entry_age_days, l.exit_age_days, l.outcome
            FROM lamps l
            JOIN (
                SELECT lamp_id, MAX(revision) AS max_rev
                FROM lamps
                GROUP BY lamp_id
            ) cur
              ON cur.lamp_id = l.lamp_id AND cur.max_rev = l.revision
            WHERE l.model = ?
            """,
            (model,),
        ).fetchall()
    ]
    result = kaplan_meier(lamps)

    conn.execute("DELETE FROM model_curve_rows WHERE model = ?", (model,))
    conn.execute("DELETE FROM model_curve_meta WHERE model = ?", (model,))
    conn.execute(
        "INSERT INTO model_curve_meta (model, lamp_count, failure_count) VALUES (?, ?, ?)",
        (model, result["lamp_count"], result["failure_count"]),
    )
    conn.executemany(
        "INSERT INTO model_curve_rows (model, t_days, at_risk, failures, survival) "
        "VALUES (?, ?, ?, ?, ?)",
        [(model, r["t_days"], r["at_risk"], r["failures"], r["survival"]) for r in result["rows"]],
    )


def _receipt(conn, lamp_id: str, revision: int, replayed: bool) -> dict:
    row = conn.execute(
        "SELECT lamp_id, revision, model, entry_age_days, exit_age_days, outcome, updated_at "
        "FROM lamps WHERE lamp_id = ? AND revision = ?",
        (lamp_id, revision),
    ).fetchone()
    if row is None:  # 理论不可达：调用前已确认该行存在
        raise NotFoundError(f"灯 {lamp_id} 修订 {revision} 不存在")
    receipt = dict(row)
    # DB 列名 revision -> 对外回执字段 accepted_revision
    receipt["accepted_revision"] = receipt.pop("revision")
    receipt["replayed"] = replayed
    return receipt


def register_lamp(payload: dict) -> dict:
    """首次登记：revision 必须为 1，灯号不得已存在。"""
    if payload["revision"] != 1:
        raise ConflictError(
            f"首次登记 revision 必须为 1，收到 {payload['revision']}；"
            "已登记的灯请使用改报接口并提交当前修订号 +1"
        )
    digest = _content_hash(payload)
    with transaction(immediate=True) as conn:
        existing = conn.execute(
            "SELECT revision FROM lamps WHERE lamp_id = ? ORDER BY revision DESC LIMIT 1",
            (payload["lamp_id"],),
        ).fetchone()
        if existing is not None:
            raise ConflictError(
                f"灯号 {payload['lamp_id']} 已登记（当前修订号 {existing['revision']}），请走改报"
            )
        conn.execute(
            "INSERT INTO lamps (lamp_id, revision, model, entry_age_days, "
            "exit_age_days, outcome, content_hash) VALUES (?, 1, ?, ?, ?, ?, ?)",
            (
                payload["lamp_id"],
                payload["model"],
                payload["entry_age_days"],
                payload["exit_age_days"],
                payload["outcome"],
                digest,
            ),
        )
        _rebuild_model(conn, payload["model"])
        return _receipt(conn, payload["lamp_id"], 1, replayed=False)


def amend_lamp(payload: dict) -> dict:
    """改报已有灯：按提交顺序校验修订号，重算旧/新型号汇总。"""
    digest = _content_hash(payload)
    with transaction(immediate=True) as conn:
        current = conn.execute(
            "SELECT revision, model, content_hash FROM lamps "
            "WHERE lamp_id = ? ORDER BY revision DESC LIMIT 1",
            (payload["lamp_id"],),
        ).fetchone()
        if current is None:
            raise NotFoundError(f"灯号 {payload['lamp_id']} 尚未登记，无法改报")

        current_rev = current["revision"]

        # 1) 原样重送：同版号 + 同内容 => 幂等返回原回执
        if payload["revision"] == current_rev and digest == current["content_hash"]:
            return _receipt(conn, payload["lamp_id"], current_rev, replayed=True)

        # 2) 同版号但内容不同 => 冲突
        if payload["revision"] == current_rev:
            raise ConflictError(
                f"修订号 {current_rev} 已存在且内容不同；请基于最新修订号 "
                f"{current_rev} 提交 revision={current_rev + 1}"
            )

        # 3) 旧版号 => 冲突（无论内容是否相同）
        if payload["revision"] < current_rev:
            raise ConflictError(
                f"修订号过期：提交 {payload['revision']}，当前最新为 {current_rev}"
            )

        # 4) 跳号（> 当前 +1）=> 冲突，保证修订连续可追
        if payload["revision"] != current_rev + 1:
            raise ConflictError(
                f"修订号必须连续：当前最新 {current_rev}，应提交 {current_rev + 1}，"
                f"收到 {payload['revision']}"
            )

        old_model = current["model"]
        conn.execute(
            "INSERT INTO lamps (lamp_id, revision, model, entry_age_days, "
            "exit_age_days, outcome, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                payload["lamp_id"],
                payload["revision"],
                payload["model"],
                payload["entry_age_days"],
                payload["exit_age_days"],
                payload["outcome"],
                digest,
            ),
        )
        # 原记录的统计贡献随重算撤掉（明细保留可追溯，但不再是当前版）
        affected = {old_model, payload["model"]}
        for model in affected:
            _rebuild_model(conn, model)
        return _receipt(conn, payload["lamp_id"], payload["revision"], replayed=False)


def get_lamp(lamp_id: str) -> dict:
    """改报只读该灯明细：返回当前版本与历史版本。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT lamp_id, revision, model, entry_age_days, exit_age_days, outcome, updated_at "
            "FROM lamps WHERE lamp_id = ? ORDER BY revision",
            (lamp_id,),
        ).fetchall()
        if not rows:
            raise NotFoundError(f"灯号 {lamp_id} 不存在")
        history = [dict(r) for r in rows]
        return {"lamp_id": lamp_id, "current_revision": history[-1]["revision"], "history": history}
    finally:
        conn.close()


def get_curve(model: str) -> dict:
    """查曲线只读汇总表，绝不触碰明细。"""
    conn = get_connection()
    try:
        meta = conn.execute(
            "SELECT model, lamp_count, failure_count, updated_at FROM model_curve_meta WHERE model = ?",
            (model,),
        ).fetchone()
        if meta is None:
            # 从未有该型号数据：按空曲线处理，保证读路径不写库
            return {"model": model, "lamp_count": 0, "failure_count": 0, "rows": []}
        rows = conn.execute(
            "SELECT t_days, at_risk, failures, survival FROM model_curve_rows "
            "WHERE model = ? ORDER BY t_days",
            (model,),
        ).fetchall()
        return {
            "model": meta["model"],
            "lamp_count": meta["lamp_count"],
            "failure_count": meta["failure_count"],
            "rows": [dict(r) for r in rows],
        }
    finally:
        conn.close()
