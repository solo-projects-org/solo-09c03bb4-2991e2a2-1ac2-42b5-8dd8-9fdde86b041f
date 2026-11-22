"""Kaplan–Meier 计算（纯函数，输入灯明细行，输出曲线行与汇总计数）。

在险口径（题目要求，含两端）：
    灯 i 在机龄日 t 计入在险，当且仅当 entry_age_days <= t <= exit_age_days。
曲线只在“首故障机龄日”上出点；每个故障日 t：
    S(t) = S(t-) * (1 - d_t / n_t)
其中 n_t 为该日在险数，d_t 为该日首故障数。末次确认正常的灯按删失处理，
其退出日不出曲线点，但若该日同时有首故障，仍计入该日在险数。
"""
from collections import defaultdict


def kaplan_meier(lamps: list[dict]) -> dict:
    """lamps: 含 entry_age_days / exit_age_days / outcome 的字典列表。

    返回 {"rows": [...], "lamp_count": n, "failure_count": f}，
    rows 按机龄日升序，仅含首故障日。
    """
    failure_days: dict[int, int] = defaultdict(int)
    for lamp in lamps:
        if lamp["outcome"] == "first_failure":
            failure_days[lamp["exit_age_days"]] += 1

    rows = []
    survival = 1.0
    for t in sorted(failure_days):
        # 含两端：移交机龄与退出机龄当天都在险
        at_risk = sum(
            1
            for lamp in lamps
            if lamp["entry_age_days"] <= t <= lamp["exit_age_days"]
        )
        failures = failure_days[t]
        survival *= 1.0 - failures / at_risk
        rows.append(
            {
                "t_days": t,
                "at_risk": at_risk,
                "failures": failures,
                "survival": round(survival, 10),
            }
        )

    return {
        "rows": rows,
        "lamp_count": len(lamps),
        "failure_count": sum(failure_days.values()),
    }
