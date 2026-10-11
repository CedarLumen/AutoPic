/* AutoPic 前端：上传两张图 -> 拖动四角 -> 实时预览 -> 出图
   参数面板完全由后端的参数规格生成，前端不写死任何默认值或范围。 */
(function () {
  "use strict";

  var S = {
    spec: [], params: {}, previewMax: 900, maxMb: 24,
    paperId: null, inkId: null, paper: null, ink: null,
    quad: null, zoom: 1, showQuad: true, tab: null, groups: [],
    previewUrl: null, previewTimer: null, previewBusy: false,
    job: null, pollTimer: null, busy: false, drag: null,
    fitZoom: 1, zoomTouched: false
  };

  function $(id) { return document.getElementById(id); }

  // 给自动化测试用的只读挂点（界面上看不见，也不影响使用）
  window.__S = S;

  function api(path, body, opts) {
    opts = opts || {};
    var init = { method: body === undefined ? "GET" : "POST" };
    if (body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(body);
    }
    return fetch(path, init).then(function (r) {
      if (opts.raw) return r;
      return r.json().then(function (j) { return j; });
    });
  }

  function setProg(text, pct, isErr) {
    var p = $("prog");
    p.textContent = text;
    p.className = "prog" + (isErr ? " err" : "");
    $("bar").firstElementChild.style.width = Math.max(0, Math.min(100, pct || 0)) + "%";
  }

  function loadImg(url) {
    return new Promise(function (res, rej) {
      var im = new Image();
      im.onload = function () { res(im); };
      im.onerror = function () { rej(new Error("图片加载失败")); };
      im.src = url;
    });
  }

  function readFile(file) {
    return new Promise(function (res, rej) {
      var fr = new FileReader();
      fr.onload = function () { res(fr.result); };
      fr.onerror = function () { rej(new Error("读取文件失败")); };
      fr.readAsDataURL(file);
    });
  }

  /* ---------- 上传 ---------- */
  function upload(kind, file) {
    if (!file) return;
    if (file.size > S.maxMb * 1024 * 1024) {
      setProg("图片太大，超过 " + S.maxMb + " MB 上限", 0, true);
      return;
    }
    setProg("正在上传" + (kind === "paper" ? "纸张照片" : "手写稿") + "…", 15);
    readFile(file).then(function (dataUrl) {
      return api("/api/upload", { kind: kind, data: dataUrl });
    }).then(function (j) {
      if (!j.ok) throw new Error(j.error || "上传失败");
      return loadImg("/api/image/" + j.id).then(function (im) {
        if (kind === "paper") {
          S.paperId = j.id; S.paper = im;
          $("th-paper").innerHTML = '<img src="/api/image/' + j.id + '" alt="">';
          $("meta-paper").innerHTML = "<b>" + im.width + " × " + im.height + "</b> 像素" +
            (file.name ? "　" + file.name : "");
          fitQuad();
          fitToBox();
          $("btn-detect").disabled = false;
          $("btn-reset").disabled = false;
        } else {
          S.inkId = j.id; S.ink = im;
          $("th-ink").innerHTML = '<img src="/api/image/' + j.id + '" alt="">';
          $("meta-ink").innerHTML = "<b>" + im.width + " × " + im.height + "</b> 像素" +
            (file.name ? "　" + file.name : "");
        }
        refreshReady();
        drawPaper();
        schedulePreview();
        setProg("已就绪，可以拖动四角微调后出图", 0);
      });
    }).catch(function (e) {
      setProg(String(e.message || e), 0, true);
    });
  }

  function refreshReady() {
    $("btn-render").disabled = !(S.paperId && S.inkId && S.quad);
  }

  function fitQuad() {
    var w = S.paper.width, h = S.paper.height;
    var ix = w * 0.12, iy = h * 0.12;
    S.quad = [[ix, iy], [w - ix, iy], [w - ix, h - iy], [ix, h - iy]];
    refreshReady();
  }

  /* 把整张纸缩放到刚好放得进左边那个框里。
     不做这一步的话，大照片会以 1:1 显示，四个角直接被裁在视野外，
     用户以为"没有控制点"（界面测试的截图里就是这样）。 */
  function fitToBox() {
    if (!S.paper) return;
    var box = $("box-paper");
    var w = Math.max(120, box.clientWidth - 28);
    var h = Math.max(120, box.clientHeight - 28);
    S.fitZoom = Math.max(0.05, Math.min(1, w / S.paper.width, h / S.paper.height));
    S.zoom = S.fitZoom;
    var sl = $("zoom");
    sl.min = Math.max(0.05, S.fitZoom * 0.4).toFixed(3);
    sl.max = Math.max(3, S.fitZoom * 3).toFixed(2);
    sl.value = S.zoom;
  }

  /* ---------- 画纸张与四角 ---------- */
  function toScreen(p) { return [p[0] * S.zoom, p[1] * S.zoom]; }

  function drawPaper() {
    if (!S.paper) return;
    var cv = $("cv-paper"), ctx = cv.getContext("2d");
    var w = Math.max(1, Math.round(S.paper.width * S.zoom));
    var h = Math.max(1, Math.round(S.paper.height * S.zoom));
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    cv.style.width = w + "px"; cv.style.height = h + "px";
    cv.hidden = false;
    $("hint-paper").hidden = true;
    $("cap-paper").hidden = false;
    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(S.paper, 0, 0, w, h);

    if (!S.showQuad || !S.quad) return;
    var q = S.quad.map(toScreen);
    ctx.save();
    // 四边形之外压暗，一眼看清写字区域
    ctx.fillStyle = "rgba(8,10,16,0.55)";
    ctx.beginPath();
    ctx.rect(0, 0, w, h);
    ctx.moveTo(q[0][0], q[0][1]);
    for (var i = 3; i >= 0; i--) ctx.lineTo(q[i][0], q[i][1]);
    ctx.closePath();
    ctx.fill("evenodd");

    ctx.strokeStyle = "rgba(110,168,254,0.95)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(q[0][0], q[0][1]);
    for (var k = 1; k < 4; k++) ctx.lineTo(q[k][0], q[k][1]);
    ctx.closePath();
    ctx.stroke();

    // 手柄要足够显眼：白底光圈 + 蓝环 + 实心点，任何底色上都看得见
    for (var j = 0; j < 4; j++) {
      var px = q[j][0], py = q[j][1];
      ctx.beginPath();
      ctx.arc(px, py, 13, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255,255,255,0.82)";
      ctx.fill();
      ctx.beginPath();
      ctx.arc(px, py, 11, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(110,168,254,0.98)";
      ctx.lineWidth = 3.5;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(px, py, 3.5, 0, Math.PI * 2);
      ctx.fillStyle = "#2f6bd0";
      ctx.fill();
    }
    ctx.restore();
    drawLight(ctx, w, h);
  }

  /* 手动光源的可视化：一圈光晕 + 半径环 + 中心点。
     拖它就能挪位置。用暖白表示提亮字迹，用深蓝表示压深字迹，一眼看得出方向。 */
  function lightGeom() {
    if (!S.paper) return null;
    var lx = Number(S.params.light_x), ly = Number(S.params.light_y);
    var lr = Number(S.params.light_r);
    return {
      x: lx * S.paper.width * S.zoom,
      y: ly * S.paper.height * S.zoom,
      r: Math.max(6, lr * Math.max(S.paper.width, S.paper.height) * S.zoom),
      gain: Number(S.params.light_gain)
    };
  }

  function drawLight(ctx, w, h) {
    if (!S.params.light_on) return;
    var g = lightGeom();
    if (!g) return;
    var warm = g.gain >= 0;
    var core = warm ? "255,244,214" : "40,52,96";
    var ring = warm ? "rgba(255,226,150,0.95)" : "rgba(140,170,255,0.95)";
    var grad = ctx.createRadialGradient(g.x, g.y, 0, g.x, g.y, g.r);
    grad.addColorStop(0.0, "rgba(" + core + "," + (warm ? 0.42 : 0.35) + ")");
    grad.addColorStop(0.55, "rgba(" + core + ",0.14)");
    grad.addColorStop(1.0, "rgba(" + core + ",0)");
    ctx.save();
    ctx.beginPath();
    ctx.arc(g.x, g.y, g.r, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.setLineDash([7, 6]);
    ctx.strokeStyle = ring;
    ctx.lineWidth = 1.6;
    ctx.stroke();
    ctx.setLineDash([]);
    // 中心：白底光圈 + 实心点，方便按住
    ctx.beginPath();
    ctx.arc(g.x, g.y, 12, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.fill();
    ctx.beginPath();
    ctx.arc(g.x, g.y, 10, 0, Math.PI * 2);
    ctx.strokeStyle = ring;
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(g.x, g.y, 3.6, 0, Math.PI * 2);
    ctx.fillStyle = warm ? "#e0a83a" : "#3a56b0";
    ctx.fill();
    ctx.restore();
  }

  function hitCorner(pos) {
    if (!S.quad) return -1;
    var best = -1, bd = 18 * 18;
    for (var i = 0; i < 4; i++) {
      var p = toScreen(S.quad[i]);
      var d = (p[0] - pos[0]) * (p[0] - pos[0]) + (p[1] - pos[1]) * (p[1] - pos[1]);
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }

  function insideQuad(pos) {
    if (!S.quad) return false;
    var q = S.quad.map(toScreen), c = false;
    for (var i = 0, j = 3; i < 4; j = i++) {
      if (((q[i][1] > pos[1]) !== (q[j][1] > pos[1])) &&
          (pos[0] < (q[j][0] - q[i][0]) * (pos[1] - q[i][1]) /
                   (q[j][1] - q[i][1]) + q[i][0])) c = !c;
    }
    return c;
  }

  function canvasPos(ev) {
    var cv = $("cv-paper"), r = cv.getBoundingClientRect();
    return [ev.clientX - r.left, ev.clientY - r.top];
  }

  /* 光点在画布上的判定：光点优先级高于四角（它更小、更容易被四角抢走） */
  function hitLight(pos) {
    if (!S.params.light_on) return false;
    var g = lightGeom();
    if (!g) return false;
    var dx = g.x - pos[0], dy = g.y - pos[1];
    return dx * dx + dy * dy <= 20 * 20;
  }

  function specOf(k) {
    for (var i = 0; i < S.spec.length; i++) if (S.spec[i].k === k) return S.spec[i];
    return null;
  }

  /* 把内部参数回填到滑块上：拖动光点之后，位置滑块要跟着动，
     否则界面显示的数值和实际生效的对不上 */
  function syncParamInputs() {
    Array.prototype.forEach.call($("params").querySelectorAll("input[type=range]"),
      function (r) {
        var row = r.parentNode;
        while (row && row.className.indexOf("prow") < 0) row = row.parentNode;
        if (!row || !row.dataset) return;
        var k = row.dataset.k;
        if (S.params[k] === undefined) return;
        if (Math.abs(parseFloat(r.value) - S.params[k]) > 1e-6) {
          r.value = S.params[k];
          var b = row.querySelector("b"), p = specOf(k);
          if (b && p) b.textContent = fmt(p, S.params[k]);
        }
      });
  }

  function bindDrag() {
    var cv = $("cv-paper");
    cv.addEventListener("pointerdown", function (ev) {
      if (!S.quad) return;
      var pos = canvasPos(ev);
      var c = hitCorner(pos);
      if (hitLight(pos)) {
        S.drag = { mode: "light" };      // 光点优先：它更小，否则总被四角抢走
      } else if (c >= 0) {
        S.drag = { mode: "corner", idx: c };
      } else if (insideQuad(pos)) {
        S.drag = { mode: "move", last: pos };
      } else {
        // 点在外面：把最近的那个角挪过来，省得非要精准点中手柄
        var best = 0, bd = Infinity;
        for (var i = 0; i < 4; i++) {
          var p = toScreen(S.quad[i]);
          var d = (p[0] - pos[0]) * (p[0] - pos[0]) + (p[1] - pos[1]) * (p[1] - pos[1]);
          if (d < bd) { bd = d; best = i; }
        }
        S.drag = { mode: "corner", idx: best };
      }
      cv.setPointerCapture(ev.pointerId);
      cv.style.cursor = "grabbing";
      ev.preventDefault();
    });
    cv.addEventListener("pointermove", function (ev) {
      if (!S.quad) return;
      var pos = canvasPos(ev);
      if (!S.drag) {
        cv.style.cursor = hitCorner(pos) >= 0 ? "grab"
          : (insideQuad(pos) ? "move" : "crosshair");
        return;
      }
      var W = S.paper.width, H = S.paper.height;
      if (S.drag.mode === "light") {
        S.params.light_x = Math.max(0, Math.min(1, pos[0] / (W * S.zoom)));
        S.params.light_y = Math.max(0, Math.min(1, pos[1] / (H * S.zoom)));
        syncParamInputs();
        drawPaper();
        schedulePreview();
        return;
      }
      if (S.drag.mode === "corner") {
        S.quad[S.drag.idx] = [Math.max(0, Math.min(W, pos[0] / S.zoom)),
                              Math.max(0, Math.min(H, pos[1] / S.zoom))];
      } else {
        var dx = (pos[0] - S.drag.last[0]) / S.zoom;
        var dy = (pos[1] - S.drag.last[1]) / S.zoom;
        S.drag.last = pos;
        S.quad = S.quad.map(function (p) {
          return [Math.max(0, Math.min(W, p[0] + dx)), Math.max(0, Math.min(H, p[1] + dy))];
        });
      }
      drawPaper();
      schedulePreview();
    });
    function up() {
      if (S.drag) { S.drag = null; cv.style.cursor = "crosshair"; schedulePreview(); }
    }
    cv.addEventListener("pointerup", up);
    cv.addEventListener("pointercancel", up);
  }

  /* ---------- 实时预览 ---------- */
  function schedulePreview() {
    if (!(S.paperId && S.inkId && S.quad)) return;
    if (S.previewTimer) clearTimeout(S.previewTimer);
    S.previewTimer = setTimeout(doPreview, 200);
  }

  function doPreview() {
    if (!(S.paperId && S.inkId && S.quad) || S.previewBusy) return;
    S.previewBusy = true;
    setProg("正在预览…", 0);
    api("/api/preview", { paper: S.paperId, ink: S.inkId, quad: S.quad, params: S.params },
        { raw: true }).then(function (r) {
      if (!r.ok) {
        return r.json().then(function (j) { throw new Error(j.error || "预览失败"); });
      }
      return r.blob();
    }).then(function (blob) {
      if (S.previewUrl) URL.revokeObjectURL(S.previewUrl);
      S.previewUrl = URL.createObjectURL(blob);
      return loadImg(S.previewUrl);
    }).then(function (im) {
      var cv = $("cv-prev"), ctx = cv.getContext("2d");
      cv.width = im.width; cv.height = im.height;
      ctx.drawImage(im, 0, 0);
      cv.hidden = false;
      $("hint-prev").hidden = true;
      $("cap-prev").hidden = false;
      setProg("预览已更新", 0);
    }).catch(function (e) {
      setProg(String(e.message || e), 0, true);
    }).then(function () {
      S.previewBusy = false;
    });
  }

  /* ---------- 参数面板（由后端规格生成） ---------- */
  function buildParams() {
    var tabs = $("tabs"), box = $("params");
    tabs.innerHTML = "";
    box.innerHTML = "";
    S.groups = [];
    S.spec.forEach(function (p) {
      if (S.groups.indexOf(p.group) < 0) S.groups.push(p.group);
    });
    // 保留当前页签：早先这里无条件重置成第一个分组，
    // 点页签时先设了新值又立刻被重置回去 —— 页签完全切不动（界面测试抓到的真 bug）
    if (S.groups.indexOf(S.tab) < 0) S.tab = S.groups[0];
    S.groups.forEach(function (g) {
      var b = document.createElement("div");
      b.className = "tab" + (g === S.tab ? " on" : "");
      b.textContent = g;
      b.onclick = function () { S.tab = g; buildParams(); };
      tabs.appendChild(b);
    });
    S.spec.filter(function (p) { return p.group === S.tab; }).forEach(function (p) {
      box.appendChild(rowFor(p));
    });
    syncNeeds();
  }

  function rowFor(p) {
    var row = document.createElement("div");
    row.className = "prow";
    row.dataset.k = p.k;
    if (p.type === "bool") {
      var l = document.createElement("label");
      l.className = "chk";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!S.params[p.k];
      cb.onchange = function () {
        S.params[p.k] = cb.checked;
        syncNeeds();
        drawPaper();          // 开关光源时，画布上的光点要立刻出现/消失
        schedulePreview();
      };
      l.appendChild(cb);
      l.appendChild(document.createTextNode(p.label));
      row.appendChild(l);
      return row;
    }
    var lab = document.createElement("label");
    var nm = document.createElement("span");
    nm.textContent = p.label;
    var val = document.createElement("b");
    lab.appendChild(nm);
    lab.appendChild(val);
    row.appendChild(lab);

    if (p.type === "range") {
      var r = document.createElement("input");
      r.type = "range";
      r.min = p.min; r.max = p.max; r.step = p.step;
      r.value = S.params[p.k];
      val.textContent = fmt(p, S.params[p.k]);
      r.oninput = function () {
        S.params[p.k] = parseFloat(r.value);
        val.textContent = fmt(p, S.params[p.k]);
      };
      r.onchange = function () { schedulePreview(); };
      row.appendChild(r);
    } else if (p.type === "enum") {
      var wrap = document.createElement("div");
      wrap.className = "colors";
      p.options.forEach(function (o) {
        var sw = document.createElement("div");
        sw.className = "sw" + (S.params[p.k] === o[0] ? " on" : "");
        sw.title = o[1];
        var i = document.createElement("i");
        i.style.background = inkPreview(o[0]);
        sw.appendChild(i);
        sw.onclick = function () {
          S.params[p.k] = o[0];
          Array.prototype.forEach.call(wrap.children, function (c) { c.className = "sw"; });
          sw.className = "sw on";
          schedulePreview();
        };
        wrap.appendChild(sw);
      });
      row.appendChild(wrap);
    }
    return row;
  }

  function fmt(p, v) {
    if (p.type === "range" && (p.max - p.min) <= 4) return Number(v).toFixed(2);
    return String(Math.round(v * 100) / 100);
  }

  function inkPreview(key) {
    var m = { black: "#141416", blueblack: "#1b2145", blue: "#2a3f8f",
              green: "#2f6b45", red: "#8f2226", gray: "#494b52" };
    return m[key] || "#141416";
  }

  /* 依赖项：没开「处理成扫描件」时，扫描件那组滑块收起来（不摆没用的控件） */
  function syncNeeds() {
    Array.prototype.forEach.call($("params").children, function (row) {
      var p = null;
      for (var i = 0; i < S.spec.length; i++) if (S.spec[i].k === row.dataset.k) p = S.spec[i];
      if (!p || !p.needs) return;
      row.hidden = !S.params[p.needs];
    });
  }

  /* ---------- 出图 ---------- */
  function render() {
    if (S.busy) return;
    S.busy = true;
    $("btn-render").disabled = true;
    setProg("正在出图…", 5);
    api("/api/render", { paper: S.paperId, ink: S.inkId, quad: S.quad, params: S.params })
      .then(function (j) {
        if (!j.ok) throw new Error(j.error || "提交失败");
        S.job = j.job;
        poll();
      }).catch(function (e) {
        S.busy = false;
        refreshReady();
        setProg(String(e.message || e), 0, true);
      });
  }

  function poll() {
    api("/api/render/" + S.job).then(function (j) {
      if (!j.ok) throw new Error(j.error || "查询失败");
      var job = j.job;
      setProg(job.message + (job.progress >= 100 ? "" : "（" + job.progress + "%）"),
              job.progress, job.status === "error");
      if (job.status === "running") {
        S.pollTimer = setTimeout(poll, 600);
        return;
      }
      S.busy = false;
      refreshReady();
      if (job.status === "done" && job.files.length) {
        S.resultUrl = job.files[0];
        $("btn-download").disabled = false;
        setProg("出图完成，用时 " + job.elapsed + " 秒", 100);
        return loadImg(job.files[0]).then(function (im) {
          var cv = $("cv-prev"), ctx = cv.getContext("2d");
          cv.width = im.width; cv.height = im.height;
          ctx.drawImage(im, 0, 0);
          cv.hidden = false;
          $("hint-prev").hidden = true;
          $("cap-prev").hidden = false;
          $("cap-prev").textContent = "出图结果（" + im.width + " × " + im.height + "）";
        });
      }
    }).catch(function (e) {
      S.busy = false;
      refreshReady();
      setProg(String(e.message || e), 0, true);
    });
  }

  function download() {
    if (!S.resultUrl) return;
    fetch(S.resultUrl).then(function (r) { return r.blob(); }).then(function (b) {
      var a = document.createElement("a");
      a.href = URL.createObjectURL(b);
      a.download = "AutoPic_贴合.png";
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
    });
  }

  /* ---------- 最近的手写稿（跟 AutoWrite 打通） ---------- */
  function loadRecent() {
    api("/api/recent").then(function (j) {
      if (!j.ok || !j.items || !j.items.length) return;
      var box = $("recent");
      box.innerHTML = "";
      j.items.forEach(function (it) {
        var im = document.createElement("img");
        im.src = it.url;
        im.title = it.name + "　" + it.when;
        im.onclick = function () {
          loadImg(it.url).then(function (img) {
            S.ink = img;
            // 走一次上传，拿到服务端可用的 id（后端只认自己仓库里的图）
            var c = document.createElement("canvas");
            c.width = img.width; c.height = img.height;
            c.getContext("2d").drawImage(img, 0, 0);
            return api("/api/upload", { kind: "ink", data: c.toDataURL("image/png") });
          }).then(function (r) {
            if (!r.ok) throw new Error(r.error || "取用失败");
            S.inkId = r.id;
            $("th-ink").innerHTML = '<img src="/api/image/' + r.id + '" alt="">';
            $("meta-ink").innerHTML = "<b>" + r.width + " × " + r.height + "</b> 像素　来自 AutoWrite";
            refreshReady();
            schedulePreview();
            setProg("已取用最近的手写稿", 0);
          }).catch(function (e) { setProg(String(e.message || e), 0, true); });
        };
        box.appendChild(im);
      });
      $("recent-wrap").hidden = false;
    }).catch(function () { /* 没有就没有，不影响主流程 */ });
  }

  /* ---------- 启动 ---------- */
  function boot() {
    api("/api/status").then(function (j) {
      if (!j.ok) throw new Error("服务未就绪");
      S.spec = j.params;
      S.params = j.defaults;
      S.previewMax = j.preview_max;
      S.maxMb = j.max_upload_mb;
      $("stat").innerHTML = "服务已连接 <span class='tag on'>就绪</span>";
      buildParams();
    }).catch(function (e) {
      $("stat").innerHTML = "无法连接本地服务：" + String(e.message || e);
      setProg("服务未连接。请双击「启动.cmd」，然后刷新本页。", 0, true);
    });

    $("btn-paper").onclick = function () { $("file-paper").click(); };
    $("btn-ink").onclick = function () { $("file-ink").click(); };
    $("file-paper").onchange = function () { upload("paper", this.files[0]); this.value = ""; };
    $("file-ink").onchange = function () { upload("ink", this.files[0]); this.value = ""; };
    $("zoom").oninput = function () {
      S.zoom = parseFloat(this.value);
      S.zoomTouched = true;
      drawPaper();
    };
    $("show-quad").onchange = function () {
      S.showQuad = this.checked;
      drawPaper();
    };
    $("btn-reset").onclick = function () { fitQuad(); drawPaper(); schedulePreview(); };
    $("btn-detect").onclick = function () {
      setProg("正在识别纸张边缘…", 0);
      api("/api/detect", { paper: S.paperId }).then(function (j) {
        if (!j.ok) throw new Error(j.error || "识别失败");
        S.quad = j.quad;
        drawPaper();
        schedulePreview();
        setProg("已自动识别，可继续拖四角微调", 0);
      }).catch(function (e) { setProg(String(e.message || e), 0, true); });
    };
    $("btn-render").onclick = render;
    $("btn-download").onclick = download;
    bindDrag();
    loadRecent();
    // 窗口尺寸变了就重新适配（用户自己调过缩放就不动它）
    window.addEventListener("resize", function () {
      if (!S.paper) return;
      if (!S.zoomTouched) fitToBox();
      drawPaper();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
