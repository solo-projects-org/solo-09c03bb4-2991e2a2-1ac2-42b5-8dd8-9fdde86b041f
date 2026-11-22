"""端到端自测：FastAPI TestClient + 临时 SQLite。

覆盖：
1. 移交当日（entry=exit）入组并在险，含两端口径
2. KM 曲线：在险数、首故障数、存活率手算比对
3. 同版异内容 / 旧版号 / 跳号改报 => 409
4. 最新版原样重送 => 200 + X-Idempotent-Replay，不新增修订
5. 跨型号改报：旧型号撤贡献、新型号加贡献，原子生效
6. 写失败回滚后原曲线仍可查
7. 并发改报按提交顺序判定修订号
"""
import threading

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import services
from app.database import init_db


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _lamp(lamp_id, model="BR-200", revision=1, entry=0, exit_=100, outcome="first_failure"):
    return {
        "lamp_id": lamp_id,
        "model": model,
        "revision": revision,
        "entry_age_days": entry,
        "exit_age_days": exit_,
        "outcome": outcome,
    }


# ---------- 1. 基础登记 + 移交当日入组 ----------

def test_register_and_same_day_entry_is_at_risk(client):
    # L-A：移交当日即退出（entry=exit=30），末次确认正常；30 当天必须在险
    r = client.post("/lamps", json=_lamp("L-A", entry=30, exit_=30, outcome="last_known_working"))
    assert r.status_code == 201, r.text
    assert r.json()["accepted_revision"] == 1
    assert r.json()["replayed"] is False

    # L-B：30 当天首故障
    client.post("/lamps", json=_lamp("L-B", entry=0, exit_=30))
    curve = client.get("/models/BR-200/curve").json()
    row30 = next(r for r in curve["rows"] if r["t_days"] == 30)
    # 含两端：L-A [30,30] 与 L-B [0,30] 都在险；仅 L-B 首故障
    assert row30["at_risk"] == 2
    assert row30["failures"] == 1
    assert row30["survival"] == pytest.approx(0.5)


def test_duplicate_register_conflicts(client):
    r = client.post("/lamps", json=_lamp("L-A"))
    assert r.status_code == 409


def test_first_register_revision_must_be_one(client):
    r = client.post("/lamps", json=_lamp("L-X9", revision=2))
    assert r.status_code == 409


def test_validation_exit_before_entry(client):
    r = client.post("/lamps", json=_lamp("L-X8", entry=100, exit_=99))
    assert r.status_code == 422


def test_invalid_outcome(client):
    bad = _lamp("L-X7")
    bad["outcome"] = "mystery"
    r = client.post("/lamps", json=bad)
    assert r.status_code == 422


# ---------- 2. KM 曲线手算 ----------

def test_km_curve_manual_calc(client):
    # 独立型号 KM-1 便于手算
    data = [
        # entry, exit, outcome
        (0, 60, "first_failure"),      # 故障 t=60
        (10, 60, "first_failure"),     # 故障 t=60
        (0, 150, "last_known_working"),  # 删失 150
        (20, 80, "last_known_working"),  # 删失 80（t=60 时仍在险）
        (0, 100, "first_failure"),     # 故障 t=100
    ]
    for i, (entry, exit_, outcome) in enumerate(data):
        client.post("/lamps", json=_lamp(f"KM-{i}", model="KM-1", entry=entry, exit_=exit_, outcome=outcome))

    curve = client.get("/models/KM-1/curve").json()
    assert curve["lamp_count"] == 5
    assert curve["failure_count"] == 3
    rows = {r["t_days"]: r for r in curve["rows"]}
    assert sorted(rows) == [60, 100]

    # t=60：5 盏全部在险（含两端），2 个首故障
    assert rows[60]["at_risk"] == 5
    assert rows[60]["failures"] == 2
    assert rows[60]["survival"] == pytest.approx(0.6)

    # t=100：删失灯 KM-3 [20,80] 已退出；KM-2 删失于150仍在险
    # 在险 = KM-0(故障于60,区间到60? 不含100) -> 否
    #   KM-0 [0,60] 不在；KM-1 [10,60] 不在；KM-2 [0,150] 在；
    #   KM-3 [20,80] 不在；KM-4 [0,100] 在（端点计入）=> 2
    assert rows[100]["at_risk"] == 2
    assert rows[100]["failures"] == 1
    assert rows[100]["survival"] == pytest.approx(0.6 * 0.5)


def test_unknown_model_returns_empty_curve(client):
    r = client.get("/models/NO-SUCH-MODEL/curve")
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == []
    assert body["lamp_count"] == 0


# ---------- 3/4. 改报冲突与幂等 ----------

def test_amend_conflict_same_revision_diff_content(client):
    client.post("/lamps", json=_lamp("L-C", model="M-A", entry=0, exit_=50))
    # revision=1 但内容不同
    r = client.put("/lamps", json=_lamp("L-C", model="M-A", revision=1, entry=0, exit_=60))
    assert r.status_code == 409


def test_amend_conflict_stale_revision(client):
    client.put("/lamps", json=_lamp("L-C", model="M-A", revision=2, entry=0, exit_=60))
    r = client.put("/lamps", json=_lamp("L-C", model="M-A", revision=1, entry=0, exit_=50))
    assert r.status_code == 409


def test_amend_conflict_skipped_revision(client):
    r = client.put("/lamps", json=_lamp("L-C", model="M-A", revision=9, entry=0, exit_=70))
    assert r.status_code == 409


def test_replay_returns_original_receipt(client):
    # 当前 L-C 最新版为 rev2（上一用例已成功改报）
    current = _lamp("L-C", model="M-A", revision=2, entry=0, exit_=60)
    r1 = client.put("/lamps", json=current)
    assert r1.status_code == 200 and r1.headers.get("X-Idempotent-Replay") == "true"
    first_receipt = r1.json()

    r2 = client.put("/lamps", json=current)
    assert r2.status_code == 200
    assert r2.headers.get("X-Idempotent-Replay") == "true"
    replay = r2.json()
    assert replay["replayed"] is True
    assert replay == first_receipt  # 原样重送返回原回执

    # 没有产生新版本
    detail = client.get("/lamps/L-C").json()
    assert detail["current_revision"] == 2
    assert len(detail["history"]) == 2


def test_amend_unknown_lamp_404(client):
    r = client.put("/lamps", json=_lamp("GHOST", revision=1))
    assert r.status_code == 404


# ---------- 5. 跨型号改报：旧贡献撤销、新型号生效 ----------

def test_cross_model_amend_rebases_both_curves(client):
    client.post("/lamps", json=_lamp("L-D", model="OLD-M", entry=0, exit_=40))
    before_old = client.get("/models/OLD-M/curve").json()
    assert before_old["lamp_count"] == 1 and before_old["failure_count"] == 1

    # 改报到 NEW-M，观察区间与结局也一起改
    r = client.put(
        "/lamps",
        json=_lamp("L-D", model="NEW-M", revision=2, entry=5, exit_=90, outcome="last_known_working"),
    )
    assert r.status_code == 200

    old = client.get("/models/OLD-M/curve").json()
    new = client.get("/models/NEW-M/curve").json()
    assert old["lamp_count"] == 0 and old["failure_count"] == 0 and old["rows"] == []
    assert new["lamp_count"] == 1 and new["failure_count"] == 0 and new["rows"] == []

    # 明细历史保留两版，当前版为 rev2
    detail = client.get("/lamps/L-D").json()
    assert detail["current_revision"] == 2
    assert [h["revision"] for h in detail["history"]] == [1, 2]
    assert detail["history"][-1]["model"] == "NEW-M"


# ---------- 6. 写失败回滚，原曲线仍可读 ----------

def test_failed_write_keeps_old_curve_intact(client):
    client.post("/lamps", json=_lamp("L-E", model="ROLLBACK-M", entry=0, exit_=40))
    before = client.get("/models/ROLLBACK-M/curve").json()

    # 同版异内容触发冲突，事务回滚
    r = client.put("/lamps", json=_lamp("L-E", model="ROLLBACK-M", revision=1, exit_=41))
    assert r.status_code == 409

    after = client.get("/models/ROLLBACK-M/curve").json()
    assert after == before


# ---------- 7. 并发改报按提交顺序判定 ----------

def test_concurrent_amends_commit_order_wins():
    init_db()
    services.register_lamp(_lamp("L-PARA", model="PARALLEL-M", entry=0, exit_=10))
    barrier = threading.Barrier(2)
    results = []

    def worker(worker_id):
        # 两个线程同抢 revision=2，但内容不同 => 先提交者赢，后到者 409
        payload = _lamp(
            "L-PARA", model="PARALLEL-M", revision=2, entry=0, exit_=20 + worker_id
        )
        barrier.wait()
        try:
            receipt = services.amend_lamp(payload)
            results.append(("ok", worker_id, receipt["accepted_revision"]))
        except Exception as exc:  # noqa: BLE001
            results.append(("conflict", worker_id, str(exc)[:60]))

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))  # 同版号竞争、内容不同
    t1.start(); t2.start()
    t1.join(); t2.join()

    statuses = sorted(r[0] for r in results)
    assert statuses == ["conflict", "ok"]  # 先提交者赢，后到者冲突
    detail = services.get_lamp("L-PARA")
    assert detail["current_revision"] == 2
    assert len(detail["history"]) == 2
    # 胜出者写入的是它自己的内容
    assert detail["history"][-1]["exit_age_days"] in (21, 22)


def test_concurrent_sequential_revisions_both_apply():
    init_db()
    services.register_lamp(_lamp("L-PARA2", model="PARALLEL-M2", entry=0, exit_=10))
    barrier = threading.Barrier(2)
    results = []

    def worker(rev):
        barrier.wait()
        try:
            services.amend_lamp(_lamp("L-PARA2", model="PARALLEL-M2", revision=rev, exit_=10 + rev))
            results.append(("ok", rev))
        except Exception as exc:  # noqa: BLE001
            results.append(("conflict", rev, str(exc)[:60]))

    t1 = threading.Thread(target=worker, args=(2,))
    t2 = threading.Thread(target=worker, args=(3,))
    t1.start(); t2.start()
    t1.join(); t2.join()

    # rev3 可能先到（跳号冲突），按实际提交顺序重试后两版都应落库
    if any(r[0] == "conflict" for r in results):
        # 模拟客户端按最新修订号重提 rev3
        detail_now = services.get_lamp("L-PARA2")
        next_rev = detail_now["current_revision"] + 1
        services.amend_lamp(
            _lamp("L-PARA2", model="PARALLEL-M2", revision=next_rev, exit_=10 + next_rev)
        )

    detail = services.get_lamp("L-PARA2")
    assert detail["current_revision"] == 3
    assert len(detail["history"]) == 3
