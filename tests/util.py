import os
import shutil
import tempfile

from autoresearch import config


def temp_cfg(case, **env):
    """为一个测试建独立的 AR_ROOT，测试结束自动删除。"""
    root = tempfile.mkdtemp(prefix="ar-test-")
    case.addCleanup(shutil.rmtree, root, True)
    old = {k: os.environ.get(k) for k in ["AR_ROOT", *env]}
    os.environ["AR_ROOT"] = root
    os.environ.update({k: str(v) for k, v in env.items()})

    def restore():
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    case.addCleanup(restore)
    return config.load()
