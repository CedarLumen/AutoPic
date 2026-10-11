# 配置：出厂默认 config.json，用户改动写 config.local.json（后者不随包分发）
import json
import os

DEFAULTS = {
    "port": 2300,
    "preview_max": 900,        # 实时预览的最长边（像素）
    "render_dpi_max": 6000,    # 出图最长边上限，防止超大照片把内存吃爆
    "max_upload_mb": 24,       # 单张上传图的体积上限
    "keep_uploads": 24,        # 最多保留几张上传图（清理旧的）
}


def load(root):
    cfg = dict(DEFAULTS)
    for name in ("config.json", "config.local.json"):
        p = os.path.join(root, name)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if v is not None})
        except (OSError, ValueError):
            continue                      # 配置坏了就用默认值，不让程序起不来
    # 目录可被环境变量覆盖：自动化测试要起独立实例，绝不能碰真实的上传与出图
    cfg["runtime_dir"] = os.environ.get("AUTOPIC_RUNTIME") or os.path.join(root, "runtime")
    cfg["out_dir"] = os.environ.get("AUTOPIC_OUTPUT") or os.path.join(root, "output")
    env_port = (os.environ.get("AUTOPIC_PORT") or "").strip()
    if env_port.isdigit():
        cfg["port"] = int(env_port)
    return cfg


def save_local(root, patch):
    p = os.path.join(root, "config.local.json")
    cur = {}
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                cur = json.load(f) or {}
        except (OSError, ValueError):
            cur = {}
    cur.update(patch or {})
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)                    # 原子写
    return cur
