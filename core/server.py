# 本地服务：只绑 127.0.0.1，纯标准库 http.server，出图走任务制
import base64
import io
import json
import os
import re
import socket
import sys
import threading
import time
import uuid
import webbrowser

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

from pic import autoquad, geom, pipeline

APP_ID = "autopic"
VERSION = "1.0.0"

_DATA_URL = re.compile(r"^data:image/[a-zA-Z0-9.+-]+;base64,(.*)$", re.S)


def _decode_image(data, max_mb):
    """接住前端传来的 dataURL。这里永不信任客户端：体积、格式、可解码性都要查。"""
    m = _DATA_URL.match((data or "").strip())
    if not m:
        raise ValueError("图片数据格式不对（需要 data:image/...;base64, 开头）")
    raw = m.group(1)
    if len(raw) > (max_mb * 1024 * 1024 * 4) // 3 + 1024:
        raise ValueError("图片太大，超过 %d MB 上限" % max_mb)
    try:
        blob = base64.b64decode(raw, validate=False)
    except Exception as e:                                        # noqa: BLE001
        raise ValueError("图片解码失败：%s" % e)
    if len(blob) > max_mb * 1024 * 1024:
        raise ValueError("图片太大，超过 %d MB 上限" % max_mb)
    try:
        im = Image.open(io.BytesIO(blob))
        im.load()
    except Exception as e:                                        # noqa: BLE001
        raise ValueError("这不是一张能识别的图片：%s" % e)
    # 大图先按上限缩一次，避免后续每步都在超大图上做卷积
    if max(im.size) > 4200:
        k = 4200 / float(max(im.size))
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
    return im


class Ctx:
    def __init__(self, root, cfg):
        from .jobs import Jobs
        self.root = root
        self.cfg = cfg
        self.jobs = Jobs(root, cfg)
        self.started = time.time()
        self.web = os.path.join(root, "web")
        self.out = cfg.get("out_dir") or os.path.join(root, "output")
        self.runtime = cfg.get("runtime_dir") or os.path.join(root, "runtime")

    def recent(self, limit=8):
        """列出 AutoWrite 最近生成的整页手写稿，让两个工具串起来用：
           不用去文件夹里翻，点一下就能拿来贴。"""
        import glob
        bases = [os.path.join(os.path.dirname(self.root), "AutoWrite")]
        env = (os.environ.get("AUTOWRITE_DIR") or "").strip()
        if env:
            bases.append(env)
        found = []
        for base in bases:
            if not os.path.isdir(base):
                continue
            for f in glob.glob(os.path.join(base, "output", "*", "page_*.png")):
                try:
                    found.append((os.path.getmtime(f), f))
                except OSError:
                    continue
        found.sort(key=lambda t: -t[0])
        items = []
        for i, (mt, f) in enumerate(found[:limit]):
            items.append({
                "name": "%s / %s" % (os.path.basename(os.path.dirname(f)),
                                     os.path.basename(f)),
                "when": time.strftime("%m-%d %H:%M", time.localtime(mt)),
                "url": "/api/recent/%d.png" % i,
                "path": f,
            })
        return items


def _json_types(v):
    return v


class Handler(BaseHTTPRequestHandler):
    server_version = "AutoPic/" + VERSION
    ctx = None

    # ---------- 基础 ----------
    def log_message(self, fmt, *args):
        pass                                   # 静默默认日志，避免刷屏

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:                                          # noqa: BLE001
            raise ValueError("请求体不是合法 JSON")

    def _err(self, msg, code=400):
        self._json({"ok": False, "error": str(msg)}, code)

    # ---------- 路由 ----------
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        ctx = self.ctx
        try:
            if path == "/api/ping":
                return self._json({"ok": True, "app": APP_ID, "version": VERSION,
                                   "base": ctx.root,
                                   # 这一项是给自动化测试的凭证：它必须是**本实例自己的**运行目录，
                                   # 否则测试可能连到了另一个已在跑的实例上（曾经因此把结果
                                   # 写进了真实目录——测试的"足迹"断言就是这样抓到的）
                                   "runtime": ctx.runtime})
            if path == "/api/status":
                return self._json({
                    "ok": True, "app": APP_ID, "version": VERSION,
                    "params": pipeline.PARAMS,
                    "defaults": pipeline.defaults(),
                    "preview_max": ctx.cfg.get("preview_max", 900),
                    "max_upload_mb": ctx.cfg.get("max_upload_mb", 24),
                })
            m = re.match(r"^/api/render/([0-9a-zA-Z]+)$", path)
            if m:
                j = ctx.jobs.get(m.group(1))
                if j is None:
                    return self._json({"ok": False, "error": "任务不存在"}, 404)
                return self._json({"ok": True, "job": j})
            m = re.match(r"^/api/image/([0-9a-zA-Z_-]+)$", path)
            if m:
                p = ctx.jobs.uploads.path(m.group(1))
                if not p:
                    return self._json({"ok": False, "error": "图片不存在"}, 404)
                with open(p, "rb") as f:
                    return self._send(200, f.read(), "image/png")
            if path == "/api/recent":
                return self._json({"ok": True, "items": ctx.recent()})
            m = re.match(r"^/api/recent/(\d+)\.png$", path)
            if m:
                items = ctx.recent()
                i = int(m.group(1))
                if 0 <= i < len(items):
                    with open(items[i]["path"], "rb") as f:
                        return self._send(200, f.read(), "image/png")
                return self._json({"ok": False, "error": "没有这一项"}, 404)
            m = re.match(r"^/output/([0-9a-zA-Z]+)/([A-Za-z0-9_.-]+)$", path)
            if m:
                p = os.path.join(ctx.out, m.group(1), m.group(2))
                if not os.path.isfile(p):
                    return self._json({"ok": False, "error": "文件不存在"}, 404)
                with open(p, "rb") as f:
                    return self._send(200, f.read(), "image/png")
            # 静态页面
            return self._static(path)
        except Exception as e:                                     # noqa: BLE001
            return self._err(e, 500)

    def _static(self, path):
        ctx = self.ctx
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/"):]
        full = os.path.normpath(os.path.join(ctx.web, rel))
        if not full.startswith(os.path.normpath(ctx.web)) or not os.path.isfile(full):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        ctype = {".html": "text/html; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8"}.get(os.path.splitext(full)[1],
                                                        "application/octet-stream")
        with open(full, "rb") as f:
            return self._send(200, f.read(), ctype)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        ctx = self.ctx
        try:
            body = self._body()
        except ValueError as e:
            return self._err(e)
        try:
            if path == "/api/upload":
                im = _decode_image(body.get("data"), int(ctx.cfg.get("max_upload_mb", 24)))
                uid = uuid.uuid4().hex[:12]
                ctx.jobs.uploads.put(uid, im.convert("RGB"))
                return self._json({"ok": True, "id": uid,
                                   "width": im.width, "height": im.height})
            if path == "/api/detect":
                p = ctx.jobs.uploads.path(body.get("paper"))
                if not p:
                    return self._err("请先上传纸张照片")
                im = Image.open(p)
                quad = autoquad.detect(im)
                if quad is None:
                    return self._json({"ok": False,
                                       "error": "没能自动认出纸张边缘，请手动拖四角"})
                return self._json({"ok": True, "quad": [[round(x, 1), round(y, 1)]
                                                        for x, y in quad]})
            if path in ("/api/preview", "/api/render"):
                pp = ctx.jobs.uploads.path(body.get("paper"))
                ip = ctx.jobs.uploads.path(body.get("ink"))
                if not pp or not ip:
                    return self._err("请先上传「纸张照片」和「手写稿图片」两张图")
                quad = body.get("quad")
                if not isinstance(quad, list) or len(quad) != 4:
                    return self._err("请先在纸张上拖出四个角")
                params = body.get("params")
                if path == "/api/render":
                    jid = ctx.jobs.submit(body.get("paper"), body.get("ink"), quad, params)
                    return self._json({"ok": True, "job": jid})
                # 预览：同步算完直接回图，前端好做拖动时的实时反馈
                paper = Image.open(pp)
                ink = Image.open(ip)
                try:
                    out = pipeline.preview(paper, ink, quad, params,
                                           max_side=int(ctx.cfg.get("preview_max", 900)))
                except geom.Degenerate as e:
                    return self._err(e)
                buf = io.BytesIO()
                out.convert("RGB").save(buf, "PNG")
                return self._send(200, buf.getvalue(), "image/png")
            return self._json({"ok": False, "error": "没有这个接口"}, 404)
        except geom.Degenerate as e:
            return self._err(e)
        except ValueError as e:
            return self._err(e)
        except Exception as e:                                     # noqa: BLE001
            return self._err(e, 500)


def _pick_port(start, tries=20):
    """端口被占就顺延，并把实际端口告诉调用方"""
    for i in range(tries):
        p = start + i
        if p > 65535:
            break
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("从 %d 起连续 %d 个端口都被占用了" % (start, tries))


def serve(root, cfg, open_browser=False):
    port = _pick_port(int(cfg.get("port", 2300)))
    ctx = Ctx(root, cfg)
    Handler.ctx = ctx
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    rt = cfg.get("runtime_dir") or os.path.join(root, "runtime")
    os.makedirs(rt, exist_ok=True)
    url = "http://127.0.0.1:%d/" % port
    with open(os.path.join(rt, "port.txt"), "w", encoding="utf-8") as f:
        f.write(str(port))
    with open(os.path.join(rt, "pid.txt"), "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    print("AutoPic 已启动：%s" % url)
    print("（只监听本机 127.0.0.1；关闭窗口或按 Ctrl+C 结束）")
    sys.stdout.flush()
    if open_browser:
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(url)),
                         daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
