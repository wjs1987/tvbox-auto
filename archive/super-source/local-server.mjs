// ============================================================
// 本地模拟服务器 —— 在本机以 Node 直接运行 functions/api.js，
// 模拟 OK影视/TVBox 对源的全部请求（首页/搜索/详情/播放/网盘代理）。
// 注意：/catalog.json、/index.html 等静态文件直接从磁盘返回
//       （线上它们是静态资源；若走函数逻辑会造成自递归 OOM）。
// 用法：
//   node local-server.mjs            # 默认 8787 端口
//   PORT=9000 node local-server.mjs
// 测试（模拟客户端）：
//   curl "http://127.0.0.1:8787/api.js?mode=panck"              # 网盘Cookie状态
//   curl "http://127.0.0.1:8787/api.js?mode=panck&set=quark=..." 
//   python test-chain.py <分享链接> [夸克网盘|UC网盘]            # 全链路
// ============================================================
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { onRequest } from "./functions/api.js";

const PORT = Number(process.env.PORT || 8787);
const ROOT = path.dirname(fileURLToPath(import.meta.url));

// 本地内存 Cookie 覆盖：LOCAL_CK 环境变量 json 传入 {"quark":"...","uc":"..."}
// 不需要时留空（api.js 自身存储即可，mode=panck&set= 也能配置）

const server = http.createServer(async (req, res) => {
  const t0 = Date.now();
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);

  // 静态文件直返（关键：防止 api.js 自取 catalog.json 造成递归）
  if (req.method === "GET" && !url.pathname.startsWith("/api.js")) {
    const safe = path.normalize(url.pathname).replace(/^([/\\])+/, "");
    const file = path.join(ROOT, safe);
    if (file.startsWith(ROOT) && fs.existsSync(file) && fs.statSync(file).isFile()) {
      res.writeHead(200, { "Content-Type": safe.endsWith(".json") ? "application/json; charset=utf-8" : "text/html; charset=utf-8" });
      res.end(fs.readFileSync(file));
      console.log(`[static] ${url.pathname} -> 200 (${Date.now() - t0}ms)`);
      return;
    }
    res.writeHead(404); res.end("not found"); return;
  }

  try {
    const hasBody = req.method === "POST" || req.method === "PUT";
    const body = hasBody
      ? await new Promise((resolve) => {
          const chunks = [];
          req.on("data", (c) => chunks.push(c));
          req.on("end", () => resolve(Buffer.concat(chunks)));
        })
      : undefined;
    const request = new Request(url.href, { method: req.method, headers: req.headers, body });
    const resp = await onRequest({ request, env: {} });
    const text = await resp.text();
    res.writeHead(resp.status, {
      "Content-Type": resp.headers.get("Content-Type") || "application/json; charset=utf-8",
      "Access-Control-Allow-Origin": "*",
    });
    res.end(text);
    console.log(`[${new Date().toISOString().slice(11, 19)}] ${req.method} ${url.pathname}${url.search.slice(0, 80)} -> ${resp.status} (${Date.now() - t0}ms, ${text.length}B)`);
  } catch (e) {
    console.error("ERROR:", e && e.stack || e);
    res.writeHead(500, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: String(e && e.message || e) }));
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`本地模拟源已启动: http://127.0.0.1:${PORT}/api.js`);
  console.log("按 Ctrl+C 停止");
});
