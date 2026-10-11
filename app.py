# AutoPic：把手写稿贴到真草稿纸照片上
#
# 用法：
#   python app.py            启动服务（前台，带输出）
#   python app.py --open     启动并自动打开浏览器
#   python app.py --port 2400  指定端口（被占会自动顺延）
#   python app.py --stop     停止正在运行的服务
#   python app.py --print-url 只打印当前地址
import argparse
import os
import subprocess
import sys


def _app_root():
    """程序认的根目录。打包成独立 exe 后必须取「exe 所在目录」：
       那时 __file__ 可能仍指向打包时的源码路径，换台机器就找不到数据了。"""
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.dirname(os.path.abspath(__file__))


ROOT = _app_root()
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _read(name):
    p = os.path.join(ROOT, "runtime", name)
    if not os.path.isfile(p):
        return ""
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def runs_this_script(name, cmd):
    """命令行是不是「真的在运行 app.py」。

    只判「命令行里含 app.py」太松：`python -m nuitka ... app.py`、`python -c "..." app.py`
    这类也会被算进来，停止服务时会误杀无关进程（在 AutoWrite 上实测踩过，
    把正在跑的打包进程杀了三次）。判据要落到「脚本是不是入口」。
    """
    if "python" not in name:
        return False
    toks = cmd.split()
    skip = False
    for t in toks[1:]:
        if skip:
            skip = False
            continue
        low = t.lower()                    # 调用方传进来的通常是小写命令行
        if low in ("-m", "-c"):
            return False                   # 跑模块 / 跑代码，都不是本脚本
        if low in ("-x", "-w", "--"):
            skip = True                    # 带值开关，跳过它的值
            continue
        if t.startswith("-"):
            continue
        return t.replace("\\", "/").lower().endswith("app.py")
    return False


def _find_pids():
    ps = ("Get-CimInstance Win32_Process | ForEach-Object { "
          "\"$($_.ProcessId)|$($_.Name)|$($_.CommandLine)\" }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=20)
    except Exception:                                              # noqa: BLE001
        r = None
    exe = os.path.basename(sys.executable).lower()
    me = os.getpid()
    pids = []
    if r and r.returncode == 0:
        for line in (r.stdout or "").splitlines():
            parts = line.strip().split("|", 2)
            if len(parts) < 2 or not parts[0].strip().isdigit():
                continue
            pid = int(parts[0].strip())
            name = parts[1].strip().lower()
            cmd = (parts[2] if len(parts) > 2 else "").lower()
            if pid == me:
                continue
            if runs_this_script(name, cmd) or (exe not in ("python.exe", "pythonw.exe")
                                               and name == exe):
                pids.append(pid)
    if not pids:
        pid = _read("pid.txt")
        if pid.isdigit():
            pids.append(int(pid))
    return pids


def main():
    ap = argparse.ArgumentParser(description="AutoPic 手写稿贴合工具")
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    ap.add_argument("--port", type=int, default=None, help="覆盖监听端口")
    ap.add_argument("--stop", action="store_true", help="停止正在运行的服务")
    ap.add_argument("--print-url", action="store_true", help="只打印当前地址")
    args = ap.parse_args()

    if args.print_url:
        p = _read("port.txt")
        print("http://127.0.0.1:%s/" % (p or "未启动"))
        return 0

    if args.stop:
        pids = _find_pids()
        ok = 0
        for pid in pids:
            r = subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, text=True)
            if r.returncode == 0:
                ok += 1
            else:
                print("停止 PID %s 失败：%s" % (pid, (r.stderr or r.stdout or "").strip()))
        print("已停止 %d 个服务进程。" % ok if ok else "未发现正在运行的服务。")
        for n in ("pid.txt", "port.txt"):
            try:
                os.remove(os.path.join(ROOT, "runtime", n))
            except OSError:
                pass
        return 0

    from core import config as cfgmod
    cfg = cfgmod.load(ROOT)
    if args.port:
        cfg["port"] = int(args.port)
    from core.server import serve
    return serve(ROOT, cfg, open_browser=args.open)


if __name__ == "__main__":
    sys.exit(main())
