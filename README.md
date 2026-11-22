# 路灯首故障 Kaplan–Meier 观察 API

按灯号记录路灯的型号、修订号、移交观察与退出观察时的**整日机龄**；灯在移交前须从未故障，
退出观察时记录**首故障**（`first_failure`）或**末次确认正常**（`last_known_working`，按删失处理）。
按型号查询 Kaplan–Meier 曲线，给出每个首故障机龄日的在险数、首故障数和存活率。

## 口径约定

- **在险区间含两端**：灯 `i` 在机龄日 `t` 计入在险 ⇔ `entry_age_days <= t <= exit_age_days`。
  移交当日（`entry == exit`）即退出的灯，当天仍在险。
- 曲线只在**首故障机龄日**出点；删失灯的退出日不出点，但若当日有首故障，它仍计入当日在险数。
- 每个故障日 `t`：`S(t) = S(t-) × (1 − d_t / n_t)`，`n_t` 在险数、`d_t` 首故障数。
- 机龄为非负整数，且 `exit_age_days >= entry_age_days`（请求级 422 校验）。

## 改报（修订）语义

- `POST /lamps`：首次登记，`revision` 必须为 1；重复灯号 → 409。
- `PUT /lamps`：改报，`revision` 必须为当前最新修订号 + 1（不允许跳号）。
- **最新版原样重送**：同灯号、同修订号、同业务内容 → 幂等返回原回执，
  响应头 `X-Idempotent-Replay: true`，不产生新版本。
- **旧版号**或**同版号异内容** → `409 Conflict`。
- 并发改报以 `BEGIN IMMEDIATE` 写事务串行化，按**实际拿到写锁（提交）的顺序**判定修订号。
- 每次成功改报在同一事务内：写入新明细行，并对**旧型号 + 新型号**各重算一次汇总后整体替换。
  历史修订全部保留可追溯，但只有每灯的最新修订参与统计——原记录（含跨型号）的统计贡献被撤销。
- 写事务任一步失败即回滚，已提交的旧曲线仍可查询。
- 读写分离：改报只读写 `lamps` 明细表；`GET /models/{model}/curve` 只读
  `model_curve_meta` / `model_curve_rows` 汇总表。

## 端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/lamps` | 首次登记（revision=1） |
| PUT | `/lamps` | 改报 / 原样重送 |
| GET | `/lamps/{lamp_id}` | 单灯明细与全部修订历史 |
| GET | `/models/{model}/curve` | 型号 KM 曲线 |
| GET | `/docs` | Swagger UI（可直接试跑） |
| GET | `/healthz` | 健康检查 |

## Docker Compose 启动

```bash
docker compose up --build
# 服务在 http://localhost:8000，Swagger: http://localhost:8000/docs
```

SQLite 数据文件位于容器内 `/data/lamps.db`，由命名卷 `lamp_data` 持久化。
可用 `LAMPS_DB_PATH` 环境变量覆盖数据库路径。

灌入随附样例（容器外执行，仅用标准库）：

```bash
python3 samples/seed.py --base-url http://localhost:8000
# 或在容器内执行（样例已打进镜像）：
docker compose exec lamp-km-api python samples/seed.py --base-url http://127.0.0.1:8000
```

## Swagger 试跑样例

`samples/` 下提供可直接粘贴到 Swagger 的请求体：

- `samples/lamps.json`：7 盏灯（BR-200 ×5、BR-300 ×2），含首故障与删失、非零移交机龄。
- `samples/amend_L-001_to_BR-300.json`：跨型号改报示例（revision=2）。
- `samples/replay_L-001_rev1.json`：原样重送示例（命中幂等）。
- `samples/seed.py`：批量登记脚本，可重复执行（已登记灯号自动跳过）。

## 本地开发（无 Docker）

```bash
python3 -m pip install -r requirements.txt
python3 -m uvicorn app.main:app --reload
python3 -m pytest tests/ -v
```

## 项目结构

```
app/
  config.py      配置（DB 路径、合法结局）
  database.py    SQLite 连接、建表、IMMEDIATE 事务
  models.py      Pydantic 请求/响应模型与校验
  km.py          Kaplan–Meier 纯函数
  services.py    登记/改报/重算/查询业务逻辑
  exceptions.py  404/409 业务异常
  main.py        FastAPI 路由
samples/         Swagger 样例与种子脚本
tests/           16 项端到端自测（含并发竞争）
```
