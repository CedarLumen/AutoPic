/* AutoPic 界面端到端：真浏览器（CDP）驱动，零依赖
   验证的是"用户点得动、拖得动、出得来图"，不是"文件写完了"。

   要点（都踩过或按 skill 纪律加上的）：
   - 先连上并 enable，再 navigate，否则启动期的 JS 异常会被丢掉
   - 每个 send() 都有超时，否则发错会话会静默挂死
   - 页面侧表达式包 async + try/catch，异常提升为断言失败而不是 undefined
   - 用 DOM.setFileInputFiles 走真实的文件选择路径
   - 测试起自己的隔离实例（独立端口 + 独立目录），不碰真实数据
   - finally 里杀浏览器与服务，并清理临时 profile */
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const PY = process.env.PYTHON || "python";
const PORT = 2392;
const CDP_PORT = 9333;
const PROFILE = path.join(os.tmpdir(), "autopic-cdp-profile");
const SHOT = path.join(ROOT, "output", "ui-preview.png");
const REPORT = path.join(ROOT, "output", "ui-report.txt");

const results = [];
const errors = [];
function check(name, pass, detail) {
  results.push({ name, pass: !!pass, detail: detail === undefined ? "" : String(detail) });
}

function findBrowser() {
  const cands = [
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  ];
  for (const c of cands) if (fs.existsSync(c)) return c;
  return null;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "autopic-ui-"));
  // 造两张真图给上传用
  const paperPath = path.join(tmp, "paper.png");
  const inkPath = path.join(tmp, "ink.png");
  const mk = spawn(PY, [path.join(ROOT, "tests", "make_fixtures.py"), paperPath, inkPath],
                   { cwd: ROOT, stdio: "inherit" });
  await new Promise((res) => mk.on("exit", res));

  const env = Object.assign({}, process.env, {
    AUTOPIC_PORT: String(PORT),
    AUTOPIC_RUNTIME: path.join(tmp, "runtime"),
    AUTOPIC_OUTPUT: path.join(tmp, "output"),
  });
  const svc = spawn(PY, ["app.py"], { cwd: ROOT, env, stdio: "ignore" });

  let browser = null, ws = null;
  let msgId = 0;
  const pending = new Map();

  function send(method, params, timeoutMs) {
    return new Promise((resolve) => {
      const id = ++msgId;
      const timer = setTimeout(() => {
        if (pending.has(id)) {
          pending.delete(id);
          errors.push("CDP 命令超时未应答: " + method);
          resolve({ error: { message: "timeout " + method } });
        }
      }, timeoutMs || 25000);
      pending.set(id, (m) => { clearTimeout(timer); resolve(m); });
      ws.send(JSON.stringify({ id, method, params: params || {} }));
    });
  }

  async function ev(expr) {
    const wrapped = "(async function(){ try { return JSON.stringify(await (" + expr +
      ")); } catch (e) { return JSON.stringify({__err: String(e && e.message || e)}); } })()";
    const m = await send("Runtime.evaluate", {
      expression: wrapped, returnByValue: true, awaitPromise: true,
    });
    if (m.result && m.result.exceptionDetails) {
      errors.push("求值错误: " + (m.result.exceptionDetails.text || "") + " :: " + expr.slice(0, 60));
      return undefined;
    }
    const raw = m.result && m.result.result && m.result.result.value;
    if (raw === undefined || raw === null) {
      errors.push("求值没有返回值 :: " + expr.slice(0, 60));
      return undefined;
    }
    let v;
    try { v = JSON.parse(raw); } catch (e) { return raw; }
    if (v && v.__err) { errors.push("页面异常: " + v.__err + " :: " + expr.slice(0, 60)); return undefined; }
    return v;
  }

  try {
    // ---- 等后端 ----
    const url = "http://127.0.0.1:" + PORT + "/";
    const wantRt = path.resolve(path.join(tmp, "runtime")).toLowerCase();
    let up = false, foreign = "";
    for (let i = 0; i < 40; i++) {
      try {
        const r = await fetch(url + "api/ping", { signal: AbortSignal.timeout(2000) });
        const j = await r.json();
        if (j.app === "autopic") {
          // 必须是本测试起的实例：端口被别的 AutoPic 实例占着就会测错对象
          if (path.resolve(String(j.runtime || "")).toLowerCase() === wantRt) { up = true; break; }
          foreign = String(j.runtime || "");
        }
      } catch (e) { /* 还没起来 */ }
      await sleep(400);
    }
    check("隔离的测试实例能起来", up, up ? url : (foreign ? "端口被另一个实例占用：" + foreign : url));
    if (!up) throw new Error("服务未起来");

    // ---- 起浏览器 ----
    const bin = findBrowser();
    check("找到本机浏览器", !!bin, bin || "没找到");
    if (!bin) throw new Error("没有浏览器可驱动");
    if (fs.existsSync(PROFILE)) fs.rmSync(PROFILE, { recursive: true, force: true });
    browser = spawn(bin, ["--headless=new", "--disable-gpu", "--no-first-run",
                          "--no-default-browser-check", "--hide-scrollbars",
                          "--remote-debugging-port=" + CDP_PORT,
                          "--user-data-dir=" + PROFILE, "about:blank"], { stdio: "ignore" });

    let target = null;
    for (let i = 0; i < 60; i++) {
      await sleep(300);
      try {
        const r = await fetch("http://127.0.0.1:" + CDP_PORT + "/json/list",
                              { signal: AbortSignal.timeout(2000) });
        const list = await r.json();
        target = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
        if (target) break;
      } catch (e) { /* 端口还没开 */ }
    }
    check("浏览器调试端口已就绪", !!target);
    if (!target) throw new Error("CDP 没就绪");

    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      ws.addEventListener("open", res);
      ws.addEventListener("error", rej);
    });
    ws.addEventListener("message", (e) => {
      let m;
      try { m = JSON.parse(e.data); } catch (err) { return; }
      if (m.id && pending.has(m.id)) { const cb = pending.get(m.id); pending.delete(m.id); cb(m); return; }
      if (m.method === "Runtime.exceptionThrown") {
        const d = m.params.exceptionDetails || {};
        errors.push("未捕获异常: " + (d.text || "") + " " +
          ((d.exception && d.exception.description) || "").split("\n")[0]);
      }
      if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
        errors.push("console.error: " + (m.params.args || []).map((a) => a.value).join(" "));
      }
    });
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Log.enable");
    await send("DOM.enable");
    await send("Emulation.setDeviceMetricsOverride",
               { width: 1560, height: 940, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url });
    await sleep(2500);

    // ---- 界面骨架 ----
    const title = await ev("document.title");
    check("页面标题正确", /AutoPic/.test(title || ""), title);
    const stat = await ev("document.getElementById('stat').textContent");
    check("顶部显示服务已连接", /已连接/.test(stat || ""), stat);
    const ntab = await ev("document.querySelectorAll('#tabs .tab').length");
    check("参数分组页签已生成", ntab >= 3, ntab + " 个页签");
    const nrange = await ev("document.querySelectorAll('#params input[type=range]').length");
    check("参数滑块已生成（数量随分组变化）", nrange >= 2, nrange + " 个滑块");
    const overflow = await ev("document.documentElement.scrollWidth - window.innerWidth");
    check("没有横向溢出", (overflow || 0) <= 2, "差 " + overflow + "px");

    // ---- 上传两张图（走真实文件选择） ----
    async function setFile(sel, file) {
      const doc = await send("DOM.getDocument", {});
      const node = await send("DOM.querySelector",
                              { nodeId: doc.result.root.nodeId, selector: sel });
      return send("DOM.setFileInputFiles",
                  { files: [file], nodeId: node.result.nodeId });
    }
    await setFile("#file-paper", paperPath);
    await sleep(2200);
    const paperOk = await ev("!document.getElementById('cv-paper').hidden");
    check("上传纸张照片后画布出现", paperOk === true, paperOk);
    const quad0 = await ev("JSON.stringify(window.__S ? window.__S.quad : null)");

    await setFile("#file-ink", inkPath);
    await sleep(2500);
    const inkMeta = await ev("document.getElementById('meta-ink').textContent");
    check("上传手写稿后显示尺寸信息", /\d+\s*×\s*\d+/.test(inkMeta || ""), inkMeta);

    // ---- 四角拖拽：真的拖一下，四角要跟着变 ----
    const geom = await ev(`(function(){
      var cv = document.getElementById('cv-paper');
      var r = cv.getBoundingClientRect();
      return {x:r.left, y:r.top, w:r.width, h:r.height};
    })()`);
    const before = await ev("JSON.stringify(document.getElementById('cv-paper').width)");
    // 拖第一个角（左上）往右下挪
    const sx = geom.x + geom.w * 0.12, sy = geom.y + geom.h * 0.12;
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: sx, y: sy,
                                             button: "left", clickCount: 1 });
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: sx + 90, y: sy + 60,
                                             button: "left" });
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: sx + 90, y: sy + 60,
                                             button: "left", clickCount: 1 });
    await sleep(1500);
    const prevOk = await ev(`(function(){
      var cv = document.getElementById('cv-prev');
      if (cv.hidden || !cv.width) return 'blank';
      var d = cv.getContext('2d').getImageData(0,0,cv.width,cv.height).data;
      var dark = 0;
      for (var i=0;i<d.length;i+=4*17) if (d[i] < 120) dark++;
      return dark > 20 ? 'ink' : 'empty';
    })()`);
    check("拖动四角后预览画布出现墨迹", prevOk === "ink", prevOk);
    check("画布尺寸合理（未出现 0 宽）", parseInt(before || "0", 10) > 100, before);

    // ---- 参数联动：开关与滑块 ----
    const countVisible = `(function(){
      var rows = document.querySelectorAll('#params .prow');
      var vis = 0;
      for (var i=0;i<rows.length;i++) if (!rows[i].hidden) vis++;
      return vis;
    })()`;
    // 切到"扫描件"页签，勾上开关，那组滑块才应出现
    const clickedTab = await ev(`(function(){
      var tabs = document.querySelectorAll('#tabs .tab');
      for (var i=0;i<tabs.length;i++) {
        if (tabs[i].textContent.indexOf('扫描') >= 0) { tabs[i].click(); return tabs[i].textContent; }
      }
      return '';
    })()`);
    await sleep(500);
    const activeTab = await ev("(document.querySelector('#tabs .tab.on')||{}).textContent");
    check("能切到「扫描件」页签", activeTab && activeTab.indexOf("扫描") >= 0,
          "点中 " + clickedTab + " / 当前 " + activeTab);
    const visNoScan = await ev(`(function(){
      var rows = document.querySelectorAll('#params .prow');
      var vis = 0;
      for (var i=0;i<rows.length;i++) if (!rows[i].hidden) vis++;
      return vis;
    })()`);
    await ev(`(function(){
      var cb = document.querySelector('#params input[type=checkbox]');
      cb.checked = true; cb.dispatchEvent(new Event('change', {bubbles:true}));
      return true;
    })()`);
    await sleep(400);
    const visScan = await ev(`(function(){
      var rows = document.querySelectorAll('#params .prow');
      var vis = 0;
      for (var i=0;i<rows.length;i++) if (!rows[i].hidden) vis++;
      return vis;
    })()`);
    check("没开扫描时扫描参数收起（控件不冗余）", visScan > visNoScan,
          "关 " + visNoScan + " 项 -> 开 " + visScan + " 项");

    // ---- 手动光源：开关 -> 参数出现 -> 在画布上拖动光点 ----
    await ev(`(function(){
      var tabs = document.querySelectorAll('#tabs .tab');
      for (var i=0;i<tabs.length;i++) if (tabs[i].textContent.indexOf('光影') >= 0) tabs[i].click();
      return true;
    })()`);
    await sleep(400);
    const lightBefore = await ev("window.__S.params.light_on");
    const toggled = await ev(`(function(){
      var rows = document.querySelectorAll('#params .prow');
      for (var i=0;i<rows.length;i++) {
        if (rows[i].dataset.k === 'light_on') {
          var cb = rows[i].querySelector('input[type=checkbox]');
          cb.checked = true;
          cb.dispatchEvent(new Event('change', {bubbles:true}));
          return true;
        }
      }
      return false;
    })()`);
    await sleep(600);
    const lightAfter = await ev("window.__S.params.light_on");
    check("能打开手动光源", toggled === true && lightBefore === false && lightAfter === true,
          "前 " + lightBefore + " -> 后 " + lightAfter);
    const visLight = await ev(countVisible);
    check("打开光源后才出现光源参数（控件不冗余）", visLight > visScan,
          "关 " + visScan + " 项 -> 开 " + visLight + " 项");

    // 光点在画布上的位置：拖它，参数里的位置必须跟着变
    const lg = await ev(`(function(){
      var S = window.__S;
      return { lx: Number(S.params.light_x), ly: Number(S.params.light_y),
               px: Number(S.params.light_x) * S.paper.width * S.zoom,
               py: Number(S.params.light_y) * S.paper.height * S.zoom };
    })()`);
    const hx = geom.x + lg.px, hy = geom.y + lg.py;
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: hx, y: hy,
                                             button: "left", clickCount: 1 });
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: hx + 70, y: hy + 45,
                                             button: "left" });
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: hx + 70, y: hy + 45,
                                             button: "left", clickCount: 1 });
    await sleep(800);
    const lm = await ev(`(function(){
      var S = window.__S;
      var out = { lx: Number(S.params.light_x), ly: Number(S.params.light_y), slider: null };
      var rows = document.querySelectorAll('#params .prow');
      for (var i=0;i<rows.length;i++) if (rows[i].dataset.k === 'light_x')
        out.slider = parseFloat(rows[i].querySelector('input[type=range]').value);
      return out;
    })()`);
    check("在画布上拖动光点能改变光源位置",
          lm.lx > lg.lx + 0.02 && lm.ly > lg.ly + 0.01,
          // 注意：这里是 JavaScript，"%" 是取模不是格式化（写 Python 顺过手，详情会变 NaN）
          "(" + lg.lx.toFixed(3) + ", " + lg.ly.toFixed(3) + ") -> (" +
          lm.lx.toFixed(3) + ", " + lm.ly.toFixed(3) + ")");
    check("拖动后位置滑块同步（界面数值与实际一致）",
          lm.slider !== null && Math.abs(lm.slider - lm.lx) < 0.02,
          "滑块 " + lm.slider + " / 实际 " + lm.lx);

    // ---- 出图 ----
    const btnOk = await ev("!document.getElementById('btn-render').disabled");
    check("两张图齐了之后「出图」可用", btnOk === true, btnOk);
    // 注意：不能写成 "a(); true" 这种语句列表 —— 包进 JSON.stringify 会变语法错误，
    // 求值失败会被吞掉，后面几条断言就变成"对着旧状态失败"（本测试踩过）
    await ev("(function(){ document.getElementById('btn-render').click(); return true; })()");
    let done = false, prog = "";
    for (let i = 0; i < 60; i++) {
      await sleep(700);
      prog = await ev("document.getElementById('prog').textContent");
      if (/完成/.test(prog || "")) { done = true; break; }
      if (/失败|超过|错误/.test(prog || "")) break;
    }
    check("点「出图」后跑完并提示完成", done, prog);
    const dlOk = await ev("!document.getElementById('btn-download').disabled");
    check("出图后「保存图片」可用", dlOk === true, dlOk);
    const capText = await ev("document.getElementById('cap-prev').textContent");
    check("结果画布标注了实际尺寸", /\d+\s*×\s*\d+/.test(capText || ""), capText);

    // ---- 截图 ----
    const shot = await send("Page.captureScreenshot", { format: "png" });
    if (shot.result && shot.result.data) {
      fs.mkdirSync(path.dirname(SHOT), { recursive: true });
      const buf = Buffer.from(shot.result.data, "base64");
      fs.writeFileSync(SHOT, buf);
      const head = Buffer.alloc(8);
      const fd = fs.openSync(SHOT, "r");
      fs.readSync(fd, head, 0, 8, 0);
      fs.closeSync(fd);
      check("截图为合法 PNG 且体积正常",
            head.toString("hex") === "89504e470d0a1a0a" && buf.length > 40000,
            buf.length + " 字节");
    } else {
      check("截图成功", false, "没有拿到数据");
    }
  } catch (e) {
    check("测试过程未抛异常", false, String(e && e.message || e));
  } finally {
    try { if (ws) ws.close(); } catch (e) { /* ignore */ }
    try { if (browser) browser.kill(); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { fs.rmSync(PROFILE, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  // ---- 报告 ----
  const lines = [];
  let failed = 0;
  for (const r of results) {
    if (!r.pass) failed++;
    lines.push((r.pass ? "  PASS " : "  FAIL ") + r.name + (r.detail ? "  <- " + r.detail : ""));
  }
  lines.push("");
  if (errors.length) {
    lines.push("JS 异常 / 求值问题（" + errors.length + "）：");
    for (const e of errors.slice(0, 12)) lines.push("  ! " + e);
  } else {
    lines.push("没有任何 JS 异常");
  }
  lines.push("\n通过 " + (results.length - failed) + " 项，失败 " + failed + " 项");
  const text = lines.join("\n");
  console.log(text);
  fs.mkdirSync(path.dirname(REPORT), { recursive: true });
  fs.writeFileSync(REPORT, text, "utf8");
  process.exit(failed || errors.length ? 1 : 0);
}

main();
