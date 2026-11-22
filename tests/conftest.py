"""测试环境：在导入 app 之前把数据库指到临时文件。"""
import os
import tempfile

_tmpdir = tempfile.mkdtemp(prefix="lampkm-test-")
os.environ["LAMPS_DB_PATH"] = os.path.join(_tmpdir, "test.db")
