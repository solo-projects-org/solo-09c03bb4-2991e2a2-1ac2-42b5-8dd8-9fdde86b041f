"""Pydantic 请求/响应模型。

机龄均为“整日机龄”（非负整数）。出口机龄不得小于移交机龄，
因为灯从移交机龄到退出机龄（含两端）全程在险。
"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Outcome = Literal["first_failure", "last_known_working"]


class LampRecord(BaseModel):
    lamp_id: str = Field(..., min_length=1, examples=["L-001"])
    model: str = Field(..., min_length=1, examples=["BR-200"])
    revision: int = Field(..., ge=1, examples=[1], description="修订号；首次登记为 1，改报须为当前修订号 +1")
    entry_age_days: int = Field(..., ge=0, examples=[100], description="移交观察时的整日机龄")
    exit_age_days: int = Field(..., ge=0, examples=[340], description="退出观察时的整日机龄")
    outcome: Outcome = Field(..., examples=["first_failure"])

    @field_validator("lamp_id", "model")
    @classmethod
    def _strip_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不能为空字符串")
        return v

    @field_validator("exit_age_days")
    @classmethod
    def _exit_not_before_entry(cls, v, info):
        entry = info.data.get("entry_age_days")
        if entry is not None and v < entry:
            raise ValueError("退出机龄不能早于移交机龄（观察区间含两端）")
        return v


class WriteReceipt(BaseModel):
    lamp_id: str
    accepted_revision: int
    model: str
    entry_age_days: int
    exit_age_days: int
    outcome: Outcome
    replayed: bool = Field(..., description="True 表示原样重送，未产生新版本")
    updated_at: str


class CurveRow(BaseModel):
    t_days: int = Field(..., description="首故障机龄日")
    at_risk: int = Field(..., description="该日在险数（区间含两端）")
    failures: int = Field(..., description="该日首故障数")
    survival: float = Field(..., description="截至该日的 Kaplan–Meier 存活率")


class CurveResponse(BaseModel):
    model: str
    lamp_count: int
    failure_count: int
    rows: list[CurveRow]
