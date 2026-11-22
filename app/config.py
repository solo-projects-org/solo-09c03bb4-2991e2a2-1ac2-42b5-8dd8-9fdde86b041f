"""应用配置：数据库文件位置与环境变量。"""
import os

# SQLite 数据文件默认放在 /data（Docker volume 挂载点），本地运行落到项目根
DEFAULT_DB_PATH = "/data/lamps.db" if os.path.isdir("/data") else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lamps.db"
)
DB_PATH = os.environ.get("LAMPS_DB_PATH", DEFAULT_DB_PATH)

# 业务约束：移交前必须从未故障，因此事件仅允许首故障或末次确认正常
VALID_OUTCOMES = ("first_failure", "last_known_working")
