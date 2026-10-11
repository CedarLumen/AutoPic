# 出图任务：后台线程 + 进度 + 结果落盘
#
# 单张照片的完整处理可能要几秒（大图 + 高斯模糊），所以做成任务制：
# 前端拿到任务号轮询进度，处理期间界面不假死，也不会因为一次请求超时白等。
import os
import threading
import time
import traceback

from PIL import Image

from pic import pipeline

MAX_UPLOADS_KEPT = 24


class Uploads:
    """上传图仓库：按 id 存盘，顺手清理旧文件"""

    def __init__(self, base_dir, keep=MAX_UPLOADS_KEPT):
        self.dir = os.path.join(base_dir, "uploads")
        os.makedirs(self.dir, exist_ok=True)
        self.keep = max(4, int(keep))
        self._lock = threading.Lock()

    def put(self, uid, pil_img, fmt="PNG"):
        with self._lock:
            path = os.path.join(self.dir, "%s.png" % uid)
            pil_img.save(path, fmt)
            self._trim()
        return path

    def path(self, uid):
        if not uid or "/" in uid or "\\" in uid or ".." in uid:
            return None                    # 防目录穿越
        p = os.path.join(self.dir, "%s.png" % uid)
        return p if os.path.isfile(p) else None

    def _trim(self):
        try:
            files = [os.path.join(self.dir, f) for f in os.listdir(self.dir)]
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for p in files[self.keep:]:
                os.remove(p)
        except OSError:
            pass


class Job:
    def __init__(self, jid):
        self.id = jid
        self.status = "running"            # running | done | error
        self.progress = 0
        self.message = "排队中"
        self.files = []
        self.error = ""
        self.started = time.time()
        self.elapsed = 0.0


class Jobs:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.out_dir = cfg.get("out_dir") or os.path.join(root, "output")
        os.makedirs(self.out_dir, exist_ok=True)
        self.uploads = Uploads(cfg.get("runtime_dir") or os.path.join(root, "runtime"),
                               cfg.get("keep_uploads", MAX_UPLOADS_KEPT))
        self._jobs = {}
        self._lock = threading.Lock()

    # ---------- 对外 ----------
    def get(self, jid):
        with self._lock:
            j = self._jobs.get(jid)
            if j is None:
                return None
            return {"id": j.id, "status": j.status, "progress": j.progress,
                    "message": j.message, "files": list(j.files), "error": j.error,
                    "elapsed": round(j.elapsed, 2)}

    def submit(self, paper_id, ink_id, quad, params):
        pp = self.uploads.path(paper_id)
        ip = self.uploads.path(ink_id)
        if not pp:
            raise ValueError("纸张图不见了，请重新上传（可能已被自动清理）")
        if not ip:
            raise ValueError("手写稿图不见了，请重新上传")
        jid = "%08x" % (int(time.time() * 1000) & 0xFFFFFFFF)
        job = Job(jid)
        with self._lock:
            self._jobs[jid] = job
            if len(self._jobs) > 40:
                for k in sorted(self._jobs, key=lambda k: self._jobs[k].started)[:10]:
                    if self._jobs[k].status != "running":
                        self._jobs.pop(k, None)
        t = threading.Thread(target=self._run, args=(job, pp, ip, quad, params), daemon=True)
        t.start()
        return jid

    # ---------- 执行 ----------
    def _run(self, job, pp, ip, quad, params):
        t0 = time.time()
        try:
            job.message = "读取图片"
            job.progress = 5
            paper = Image.open(pp)
            ink = Image.open(ip)
            # 出图分辨率与草稿纸**一致**：只有超过上限的超大照片才等比缩一次
            # （上限可在设置里调，默认 6000，普通手机照片远达不到）
            maxside = int(self.cfg.get("render_dpi_max", 6000))
            if max(paper.size) > maxside:
                k = maxside / float(max(paper.size))
                paper = paper.resize((max(1, int(paper.width * k)),
                                      max(1, int(paper.height * k))), Image.LANCZOS)
                quad = [(x * k, y * k) for x, y in quad]
            out_size = paper.size

            job.message = "对齐与合成"
            job.progress = 35
            result = pipeline.process(paper, ink, quad, params, seed=job.started)
            if result.size != out_size:
                # 合成过程不该改变画布尺寸；真变了就拉回来，保证对外承诺的分辨率
                result = result.resize(out_size, Image.LANCZOS)

            job.message = "写出图片"
            job.progress = 85
            d = os.path.join(self.out_dir, job.id)
            os.makedirs(d, exist_ok=True)
            name = "result.png"
            result.convert("RGB").save(os.path.join(d, name))
            job.files = ["/output/%s/%s" % (job.id, name)]
            job.progress = 100
            job.message = "完成"
            job.status = "done"
        except Exception as e:                                     # noqa: BLE001
            job.status = "error"
            job.error = str(e) or e.__class__.__name__
            job.message = "失败：" + job.error
            print("[job %s] %s\n%s" % (job.id, job.error, traceback.format_exc()))
        finally:
            job.elapsed = time.time() - t0
