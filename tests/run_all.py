# 一键自检：几何 -> 自动识别 -> 图像管线 -> 接口 -> 界面
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
T = os.path.join(ROOT, "tests")

STEPS = [
    ("几何变换", [sys.executable, os.path.join(T, "test_geom.py")], True),
    ("自动识别纸张四角", [sys.executable, os.path.join(T, "test_autoquad.py")], True),
    ("图像管线", [sys.executable, os.path.join(T, "test_pipeline.py")], True),
    ("接口端到端", [sys.executable, os.path.join(T, "test_api.py")], True),
    ("浏览器界面", ["node", os.path.join(T, "ui_e2e.js")], False),
]


def main():
    failed = []
    for name, cmd, required in STEPS:
        print("=" * 58)
        print("  %s" % name)
        print("=" * 58)
        try:
            r = subprocess.run(cmd, cwd=ROOT)
            code = r.returncode
        except FileNotFoundError as e:
            code = 0 if not required else 1
            print("  跳过（缺少运行环境：%s）" % e)
        if code != 0:
            failed.append(name)
        print()
    print("=" * 58)
    print("  自检未通过：%s" % "、".join(failed) if failed else "  自检全部通过")
    print("=" * 58)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
