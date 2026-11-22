"""FastAPI 入口：路灯 Kaplan–Meier 首故障观察 API。

端点
----
POST   /lamps              首次登记（revision=1）
PUT    /lamps              改报（revision=当前+1；原样重送幂等）
GET    /lamps/{lamp_id}    查单灯明细与修订历史（读 lamps）
GET    /models/{model}/curve  按型号查 KM 曲线（只读汇总表）
GET    /healthz            健康检查
GET    /                   接口说明
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import services
from .database import init_db
from .exceptions import ApiError
from .models import CurveResponse, LampRecord, WriteReceipt


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="路灯首故障 Kaplan–Meier 观察 API",
    version="1.0.0",
    lifespan=lifespan,
    description=(
        "按灯号记录型号、修订号、移交/退出观察时的整日机龄；"
        "灯在移交前须从未故障，退出时记首故障(first_failure)或末次确认正常"
        "(last_known_working)。按型号查询 Kaplan–Meier 曲线，在险区间含两端。"
    ),
)


@app.exception_handler(ApiError)
def _api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/", tags=["meta"])
def index():
    return {
        "service": "lamp-km-api",
        "endpoints": {
            "POST /lamps": "首次登记，revision 必须为 1",
            "PUT /lamps": "改报，revision 须为当前修订号+1；最新版原样重送幂等返回",
            "GET /lamps/{lamp_id}": "查询该灯明细与全部修订历史",
            "GET /models/{model}/curve": "按型号查 KM 曲线（在险数/首故障数/存活率）",
            "GET /healthz": "健康检查",
        },
    }


@app.get("/healthz", tags=["meta"])
def healthz():
    return {"status": "ok"}


@app.post(
    "/lamps",
    response_model=WriteReceipt,
    status_code=201,
    tags=["lamps"],
    summary="首次登记灯具",
    openapi_extra={
        "x-codeSamples": [
            {
                "lang": "json",
                "source": (
                    '{"lamp_id": "L-001", "model": "BR-200", "revision": 1, '
                    '"entry_age_days": 0, "exit_age_days": 120, '
                    '"outcome": "first_failure"}'
                ),
            }
        ]
    },
)
def post_lamp(record: LampRecord):
    return services.register_lamp(record.model_dump())


@app.put(
    "/lamps",
    response_model=WriteReceipt,
    status_code=200,
    tags=["lamps"],
    summary="改报灯具（含原样重送幂等）",
    openapi_extra={
        "x-codeSamples": [
            {
                "lang": "json",
                "source": (
                    '{"lamp_id": "L-001", "model": "BR-300", "revision": 2, '
                    '"entry_age_days": 0, "exit_age_days": 200, '
                    '"outcome": "last_known_working"}'
                ),
            }
        ]
    },
)
def put_lamp(record: LampRecord):
    receipt = services.amend_lamp(record.model_dump())
    # replayed=True 时以响应头标明这是原样重送、未产生新版本
    return JSONResponse(
        status_code=200,
        content=WriteReceipt(**receipt).model_dump(),
        headers={"X-Idempotent-Replay": "true"} if receipt["replayed"] else None,
    )


@app.get("/lamps/{lamp_id}", tags=["lamps"], summary="查询单灯明细与修订历史")
def get_lamp(lamp_id: str):
    return services.get_lamp(lamp_id)


@app.get(
    "/models/{model}/curve",
    response_model=CurveResponse,
    tags=["curves"],
    summary="按型号查询 Kaplan–Meier 曲线",
)
def get_curve(model: str):
    return services.get_curve(model)
