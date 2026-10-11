# 接口端到端：起一个**隔离的测试实例**（独立端口 + 独立上传/出图目录），
# 走一遍完整流程，并断言它没有碰真实数据目录。
import base64
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image, ImageDraw, ImageFont                     # noqa: E402

PORT = 2391
fails = []
total = [0]


def check(name, ok, extra=""):
    total[0] += 1
    print(("  PASS " if ok else "  FAIL ") + name + ("  <- " + str(extra) if extra else ""))
    if not ok:
        fails.append(name)


def data_url(im):
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def call(url, body=None, raw=False, timeout=120):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read()
            return r.status, (b if raw else json.loads(b.decode("utf-8")))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, (b if raw else json.loads(b.decode("utf-8")))
        except ValueError:
            return e.code, {}


def make_paper(w=760, h=540):
    import numpy as np
    rng = np.random.default_rng(3)
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    base = np.stack([0.72 + 0.22 * x, 0.70 + 0.22 * x, 0.66 + 0.22 * x], axis=2)
    arr = np.clip(base + rng.normal(0, 0.01, (h, w, 1)).astype(np.float32), 0, 1)
    # 四周画一圈深色"桌面"，好让自动识别能靠边
    im = Image.fromarray((arr * 255).astype("uint8"))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w - 1, h - 1], outline=(60, 58, 54), width=26)
    return im


def make_ink(w=420, h=120):
    im = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("consola.ttf", 44)
    except Exception:                                            # noqa: BLE001
        f = ImageFont.load_default()
    d.text((14, 14), "AutoPic demo", fill=10, font=f)
    d.line((16, 88, 380, 94), fill=40, width=4)
    return im


TMP = tempfile.mkdtemp(prefix="autopic-test-")
REAL_OUT = os.path.join(ROOT, "output")
REAL_RT = os.path.join(ROOT, "runtime")
before_real = set(os.listdir(REAL_OUT)) if os.path.isdir(REAL_OUT) else set()

env = dict(os.environ)
env["AUTOPIC_PORT"] = str(PORT)
env["AUTOPIC_RUNTIME"] = os.path.join(TMP, "runtime")
env["AUTOPIC_OUTPUT"] = os.path.join(TMP, "output")
proc = subprocess.Popen([sys.executable, "app.py"], cwd=ROOT, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
url = "http://127.0.0.1:%d" % PORT
WANT_RT = os.path.normcase(os.path.abspath(env["AUTOPIC_RUNTIME"]))
try:
    up = None
    foreign = None
    for _ in range(40):
        time.sleep(0.5)
        try:
            st, j = call(url + "/api/ping", timeout=3)
            if j.get("app") == "autopic":
                # 必须是**本测试起的那个实例**：端口上蹲着别的 AutoPic 实例时，
                # 测试会对着它跑并写进它的目录（曾经真的写进了真实 output）
                if os.path.normcase(os.path.abspath(j.get("runtime") or "")) == WANT_RT:
                    up = j
                    break
                foreign = j.get("runtime")
        except Exception:                                        # noqa: BLE001
            continue
    check("测试实例能起来且是 AutoPic", up is not None,
          str(up) if up else ("端口被另一个实例占用：%s" % foreign if foreign else "没等到"))
    if up is None:
        raise SystemExit(1)

    st, j = call(url + "/api/status")
    check("状态接口返回参数规格", j.get("ok") and len(j.get("params") or []) > 8,
          "%d 项参数" % len(j.get("params") or []))
    check("规格里带默认值", bool(j.get("defaults")) and "bleed" in j["defaults"])
    keys = [p["k"] for p in j["params"]]
    check("每个参数都有中文标签与分组",
          all(p.get("label") and p.get("group") for p in j["params"]))
    check("滑块都有上下限", all("min" in p and "max" in p for p in j["params"]
                                if p["type"] == "range"))

    # ---- 上传 ----
    st, j = call(url + "/api/upload", {"kind": "paper", "data": data_url(make_paper())})
    check("上传纸张照片", j.get("ok") and j.get("id"), str(j)[:120])
    paper = j.get("id")
    paper_size = (j.get("width"), j.get("height"))
    st, j = call(url + "/api/upload", {"kind": "ink", "data": data_url(make_ink())})
    check("上传手写稿", j.get("ok") and j.get("id"), str(j)[:120])
    ink = j.get("id")

    st, b = call(url + "/api/image/" + paper, raw=True)
    check("能取回上传的图", st == 200 and b[:8] == b"\x89PNG\r\n\x1a\n")

    # ---- 负例 ----
    st, j = call(url + "/api/upload", {"kind": "paper", "data": "not a data url"})
    check("非图片数据被拒（负例）", st == 400 and not j.get("ok"), str(j)[:80])
    st, j = call(url + "/api/preview", {"paper": paper, "ink": ink})
    check("没给四角就报错（负例）", st == 400 and not j.get("ok"), str(j)[:80])
    st, j = call(url + "/api/preview", {"paper": paper, "ink": ink,
                                        "quad": [[0, 0], [10, 10], [20, 20], [30, 30]]})
    check("退化四角被拒（负例）", st == 400 and not j.get("ok"), str(j)[:90])
    st, j = call(url + "/api/render", {"paper": "不存在的id", "ink": ink,
                                       "quad": [[0, 0], [10, 0], [10, 10], [0, 10]]})
    check("图片 id 不存在时报错（负例）", st == 400 and not j.get("ok"), str(j)[:90])
    st, j = call(url + "/api/image/..%2F..%2Fconfig.json")
    check("路径穿越拿不到文件（负例）", st == 404, st)
    st, j = call(url + "/api/nothing")
    check("不存在的接口返回 404", st == 404)

    # ---- 自动识别 ----
    st, j = call(url + "/api/detect", {"paper": paper})
    check("自动识别纸张四角", j.get("ok") and len(j.get("quad") or []) == 4, str(j)[:140])
    quad = j.get("quad") or [[60, 60], [700, 60], [700, 480], [60, 480]]

    # ---- 预览 ----
    st, b = call(url + "/api/preview",
                 {"paper": paper, "ink": ink, "quad": quad, "params": {"bleed": 0.6}},
                 raw=True)
    check("预览返回一张 PNG", st == 200 and b[:8] == b"\x89PNG\r\n\x1a\n",
          "%d 字节" % len(b))
    st, b2 = call(url + "/api/preview",
                  {"paper": paper, "ink": ink, "quad": quad, "params": {"bleed": 0.0}},
                  raw=True)
    check("改参数后预览结果确实不同", b2 != b, "两次字节数 %d / %d" % (len(b), len(b2)))

    # ---- 出图 ----
    st, j = call(url + "/api/render", {"paper": paper, "ink": ink, "quad": quad,
                                       "params": {"scan_on": True}})
    check("提交出图任务", j.get("ok") and j.get("job"), str(j)[:120])
    jid = j.get("job")
    job = None
    for _ in range(120):
        time.sleep(0.5)
        st, jj = call(url + "/api/render/" + jid)
        job = jj.get("job")
        if job and job["status"] in ("done", "error"):
            break
    check("出图完成", job and job["status"] == "done",
          (job or {}).get("error", "") or (job or {}).get("status"))
    files = (job or {}).get("files") or []
    check("产出结果图片", len(files) == 1, str(files))
    if files:
        st, b = call(url + files[0], raw=True)
        check("结果可下载且是 PNG", st == 200 and b[:8] == b"\x89PNG\r\n\x1a\n")
        got = Image.open(io.BytesIO(b)).size
        check("出图分辨率与草稿纸完全一致", got == paper_size,
              "草稿纸 %s -> 出图 %s" % (paper_size, got))
    # 参数越界也要能出图（夹范围，不报错）
    st, j = call(url + "/api/render", {"paper": paper, "ink": ink, "quad": quad,
                                       "params": {"density": 999, "bleed": -3}})
    check("参数越界被夹住而不是报错", j.get("ok"), str(j)[:100])

    # ---- 足迹：测试实例不能碰真实目录 ----
    after_real = set(os.listdir(REAL_OUT)) if os.path.isdir(REAL_OUT) else set()
    check("没有往真实 output/ 里写东西", after_real <= before_real,
          "多出来的：%s" % (after_real - before_real))
    check("上传与出图都落在隔离目录里",
          os.path.isdir(os.path.join(TMP, "output")) and
          os.path.isdir(os.path.join(TMP, "runtime", "uploads")))
finally:
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:                                            # noqa: BLE001
        try:
            proc.kill()
        except Exception:                                        # noqa: BLE001
            pass
    time.sleep(0.6)
    s = socket.socket()
    busy = s.connect_ex(("127.0.0.1", PORT)) == 0
    s.close()
    check("结束后端口已释放", not busy)
    shutil.rmtree(TMP, ignore_errors=True)

print("\n通过 %d 项，失败 %d 项" % (total[0] - len(fails), len(fails)))
sys.exit(1 if fails else 0)
