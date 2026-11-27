// ============================================================
// 超级源（Super Source）— 腾讯 EdgeOne Pages 边缘函数版
// 对外表现为一个标准 maccms JSON 采集站（type 1），
// 片单内容 = 自动抓取 latest.json 里排名前 N 的采集站，
// 按片名去重合并，跨源全局搜索，播放时实时代理回原始源。
// 部署：EdgeOne Pages（免费版），GitHub 仓库联动自动部署。
// ============================================================

const DEFAULT_LATEST = "https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/latest.json";
// latest.json 多线路回退（GitHub raw 在国内/部分边缘节点不可达时的备选）
const LATEST_FALLBACKS = [
  "https://cdn.jsdelivr.net/gh/WJS1987/tvbox-auto@main/latest.json",
  "https://gh-proxy.com/https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/latest.json",
];
// 兜底上游源列表（latest.json 全部拉不到时使用，每日流水线会持续更新源的质量）
const DEFAULT_UPSTREAMS = [
  { name: "淘片 采集", type: 1, api: "https://taopianapi.com/cjapi/mc/vod/json.html" },
  { name: "虎牙采集", type: 1, api: "https://www.huyaapi.com/api.php/provide/vod/from/hym3u8/" },
  { name: "影视 建安", type: 1, api: "http://154.219.117.232:9981/jacloudapi.php/provide/vod/" },
  { name: "豪华 资源", type: 1, api: "https://hhzyapi.com/api.php/provide/vod/?ac=list" },
  { name: "影视 非凡[直连]", type: 1, api: "http://ffzy.tv/api.php/provide/vod/" },
  { name: "索尼 资源", type: 1, api: "https://suoniapi.com/api.php/provide/vod/" },
];
const KV_KEY = "catalog";
const UA = "okhttp/3.12.0";
const REFRESH_INTERVAL_MS = 20 * 3600 * 1000; // 超过 20 小时自动重建

// ------------------------------------------------------------
// 存储层：Blob 优先（免审批、首次调用自动创建命名空间）→ KV → 内存兜底
// Blob 用于：片单缓存(catalog)、详情缓存(d:*)。全部失败时仅内存可用（冷启动后需重建）。
// ------------------------------------------------------------
let memStore = new Map();
let blobState = null;   // null=未探测, false=不可用, 其余=store 实例
let backendName = "memory";

async function getBlobStore() {
  if (blobState !== null) return blobState;
  try {
    // 动态导入：优先用打包进函数目录的本地副本（部署平台 npm 解析不稳定），
    // 失败再回退到包名解析，最终降级为无 Blob 模式，不影响服务
    try {
      const mod = await import("./_vendor/edgeone-pages-blob.cjs");
      blobState = mod.getStore ? mod.getStore("super-source") : (mod.default?.getStore?.("super-source"));
      if (!blobState) throw new Error("vendor getStore failed");
    } catch {
      try {
        const mod = await import("@edgeone/pages-blob");
        blobState = mod.getStore("super-source");
      } catch {
        blobState = false;
      }
    }
    if (blobState) backendName = "blob";
  } catch {
    blobState = false;
  }
  return blobState || null;
}

async function cacheGet(env, key) {
  // 1) Blob（最终一致，秒级生效，适合缓存场景）
  const store = await getBlobStore();
  if (store) {
    try {
      const v = await store.get(key, { type: "json" });
      if (v !== null && v !== undefined) { backendName = "blob"; return v; }
    } catch { /* 降级 */ }
  }
  // 2) KV 绑定（审批通过后无需改代码自动生效）
  try {
    if (env.KV && typeof env.KV.get === "function") {
      const v = await env.KV.get(key, "json");
      if (v !== null && v !== undefined) { backendName = "kv"; return v; }
    }
  } catch { /* 降级 */ }
  // 3) 同实例内存
  backendName = memStore.has(key) ? backendName : "memory";
  return memStore.has(key) ? memStore.get(key) : null;
}

async function cachePut(env, key, value) {
  memStore.set(key, value); // 始终写内存，保证同实例请求零开销
  let ok = false;
  const store = await getBlobStore();
  if (store) {
    try { await store.setJSON(key, value); ok = true; backendName = "blob"; } catch { /* 降级 */ }
  }
  if (!ok) {
    try {
      if (env.KV && typeof env.KV.put === "function") {
        await env.KV.put(key, JSON.stringify(value));
        ok = true; backendName = "kv";
      }
    } catch { /* 忽略 */ }
  }
  return ok;
}

const FIXED_CLASSES = [
  { type_id: "c_all", type_name: "全部" },
  { type_id: "c_movie", type_name: "电影" },
  { type_id: "c_tv", type_name: "剧集" },
  { type_id: "c_variety", type_name: "综艺" },
  { type_id: "c_anime", type_name: "动漫" },
  { type_id: "c_doc", type_name: "纪录" },
];

const CAT_MAP = [
  [/电影|院线|动作|喜剧|爱情|科幻|恐怖|剧情|战争|犯罪|奇幻|冒险|灾难/, "c_movie"],
  [/剧|连续|国产|港台|韩|美|海外/, "c_tv"],
  [/综艺|娱乐|真人秀|脱口/, "c_variety"],
  [/动漫|动画|番剧|漫画/, "c_anime"],
  [/纪录|纪实|解说/, "c_doc"],
];

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

async function jfetch(url, ms = 8000, maxBytes = 1_500_000) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  try {
    const r = await fetch(url, { headers: { "User-Agent": UA }, signal: ctl.signal });
    if (!r.ok) return null;
    const txt = (await r.text()).slice(0, maxBytes);
    try { return JSON.parse(txt); } catch { return null; }
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// ------------------------------------------------------------
// 家宽视角探测（mode=probe）
// 从 EdgeOne 边缘节点发起请求，返回每个源 API 的国内可达性/延迟/片数。
// 边缘函数限制：单次运行最多 64 次 fetch、并发上限 8、读超时默认 15s。
// 因此调用方每批最多传 8 个源，本函数并发探测；分批多次调用即可覆盖全量。
// 仅允许探测 http(s) 且带白名单路径的采集 API，防止被当作开放 SSRF 代理。
// ------------------------------------------------------------
const PROBE_ALLOW_HOST = /(^|\.)(api\.php|cjapi|jscapi)$|api\.|[a-z0-9-]*api[a-z0-9-]*\./i;
function probeAllowed(u) {
  try {
    const x = new URL(u);
    if (!/^https?:$/.test(x.protocol)) return false;
    const h = x.hostname.toLowerCase();
    const path = x.pathname.toLowerCase();
    // 放行：主机名含 api/zy/cj，或路径是采集接口(api.php/cjapi/vod)；禁止私有地址
    if (/^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|localhost$)/.test(h)) return false;
    if (h.includes("api") || h.includes("zy") || h.includes("cj")) return true;
    return /api\.php|cjapi|provide\/vod|apijson/.test(path);
  } catch { return false; }
}

async function probeOne(item) {
  const api = String(item.api || "").trim();
  const type = Number(item.type || 0);
  const out = { api, ok: false, ms: null, count: 0, note: "" };
  if (!probeAllowed(api)) { out.note = "host 不允许探测"; return out; }
  let url = api;
  if (type === 3) {
    const ext = typeof item.ext === "string" ? item.ext : "";
    if (api.startsWith("csp_")) { out.ok = true; out.note = "内置CSP"; return out; }
    if (!ext.startsWith("http")) { out.note = "type3 无 ext"; return out; }
    url = ext;
  } else {
    url = api + (api.includes("?") ? "&" : "?") + (type === 1 ? "ac=list" : "ac=videolist");
  }
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 9000);
  const t0 = Date.now();
  try {
    const r = await fetch(url, {
      headers: { "User-Agent": UA },
      signal: ctl.signal,
      redirect: "follow",
    });
    out.ms = Date.now() - t0;
    if (!r.ok) { out.note = "HTTP " + r.status; return out; }
    const txt = (await r.text()).slice(0, 400000);
    const low = txt.slice(0, 2000).toLowerCase();
    if (low.includes("<video") || low.includes("<list")) {
      out.ok = true; out.count = (txt.match(/<video/g) || []).length; out.note = "XML";
    } else {
      try {
        const d = JSON.parse(txt);
        let lst = d && d.list;
        if (d && d.vod_list) lst = d.vod_list;
        if (Array.isArray(lst) && lst.length) {
          out.ok = true; out.count = lst.length; out.note = "JSON";
        } else out.note = "JSON 无片单";
      } catch { out.note = "非 XML/JSON"; }
    }
  } catch (e) {
    out.ms = Date.now() - t0;
    out.note = "超时/连接失败";
  } finally {
    clearTimeout(timer);
  }
  return out;
}

async function handleProbe(q, request) {
  let items = [];
  if (request.method === "POST") {
    try {
      const body = await request.json();
      items = Array.isArray(body.items) ? body.items : [];
    } catch { return json({ error: "invalid json" }, 400); }
  } else {
    // GET 兼容：?urls=<JSON数组>
    try { items = JSON.parse(q.get("items") || "[]"); } catch { items = []; }
  }
  items = items.slice(0, 8); // 单次最多 8 个（并发上限）
  if (!items.length) return json({ error: "empty items" }, 400);
  const results = await Promise.all(items.map((it) => probeOne(it)));
  return json({ ok: true, from: "edgeone", results });
}

// ------------------------------------------------------------
// 国内可达性探测（EdgeOne 定时任务触发，在国内节点执行）
// 数据流：流水线(Actions)提交 probe_candidates.json 到仓库 → 定时任务在国内节点
// 每小时轮询探测一批(32个) → 结果存 Blob → 流水线次日经 mode=probe_results 读取
// （Actions 只读不探——函数跟着调用方跑，从美国调用探测无意义）
// ------------------------------------------------------------
const PROBE_CANDIDATE_URLS = [
  "https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/probe_candidates.json",
  "https://ghproxy.net/https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/probe_candidates.json",
];
const PROBE_BATCH = 32;        // 每次定时触发探测的数量（8×4 波，控制在执行时限内）
const PROBE_FRESH_MS = 20 * 3600 * 1000;  // 20小时内测过的跳过，保证全池约 8 小时轮完一圈

async function fetchCandidates() {
  for (const u of PROBE_CANDIDATE_URLS) {
    try {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 8000);
      const r = await fetch(u + (u.includes("?") ? "&" : "?") + "_t=" + Date.now(),
                            { headers: { "User-Agent": UA }, signal: ctl.signal, redirect: "follow" });
      clearTimeout(timer);
      if (!r.ok) continue;
      const d = await r.json();
      const raw = Array.isArray(d) ? d : (d.items || d.sites || []);
      const items = raw.filter((x) => x && String(x.api || "").startsWith("http")
                                 && !String(x.api).includes("edgeone.cool"));
      if (items.length) return items;
    } catch { /* 换下一个镜像 */ }
  }
  return null;
}

async function handleCronProbe(request, env) {
  const mainland = request.headers.get("eo-is-mainland") || "?";
  const items = await fetchCandidates();
  if (!items || !items.length) return json({ ok: false, note: "probe_candidates.json 不可得" }, 503);
  const cursor = Number((await cacheGet(env, "probe_cursor")) || 0) % items.length;
  const results = (await cacheGet(env, "probe_results")) || {};
  const now = Date.now();
  // 从游标处取一批"最久没测过"的源
  const slice = [];
  for (let i = 0; i < items.length && slice.length < PROBE_BATCH; i++) {
    const it = items[(cursor + i) % items.length];
    const prev = results[it.api];
    if (prev && now - Date.parse(prev.ts || 0) < PROBE_FRESH_MS) continue;
    slice.push(it);
  }
  const probed = [];
  for (let i = 0; i < slice.length; i += 8) {
    const part = await Promise.all(slice.slice(i, i + 8).map(probeOne));
    probed.push(...part);
  }
  const ts = new Date(now).toISOString();
  for (const r of probed) results[r.api] = { ok: r.ok, ms: r.ms, count: r.count, note: r.note, ts };
  const okCount = probed.filter((r) => r.ok).length;
  await cachePut(env, "probe_results", results);
  await cachePut(env, "probe_cursor", (cursor + PROBE_BATCH) % items.length);
  await cachePut(env, "probe_meta", { ts, mainland, probed: probed.length, ok: okCount, pool: items.length });
  return json({ ok: true, mainland, probed: probed.length, chinaAlive: okCount, pool: items.length });
}

async function handleProbeResults(env) {
  const [results, meta] = await Promise.all([cacheGet(env, "probe_results"), cacheGet(env, "probe_meta")]);
  return json({ ok: true, meta, results: results || {} });
}

function normName(s) {
  return String(s || "").toLowerCase().replace(/[\s\p{P}\p{S}]+/gu, "");
}

function sep(api) {
  return api.includes("?") ? "&" : "?";
}

function shortSrc(api) {
  try {
    return new URL(api).hostname.replace(/^www\./, "").split(".")[0];
  } catch {
    return String(api).slice(0, 10);
  }
}

// ---------- 图片代理 ----------
// 问题：upstream 采集站的海报域名在国内被 DNS 污染（1.1.1.1）或解析失败，
//       OK影视 只能显示片名首字占位。由 EdgeOne 海外边缘节点代拉图片再转给客户端。
// 关键点：只对下列已知坏域名做代理，绝不把函数变成开放代理（防止被滥用）。
// 注意：需部署到「正式域名」（无 eo_token 预览鉴权），否则图片请求会 401。
const PROXY_PIC_HOSTS = [
  "taopianimage.xyz",     // 非标端口 43333，国内 DNS 污染成 1.1.1.1
  "un.shayu260522.top",   // 国内 DNS 污染成 1.1.1.1
  "fb.lj260522.top",      // 国内 DNS 解析失败
  "2609.senlin2026.com",  // 国内解析到但返回 404，一并代拉尝试
];

function needProxy(pic) {
  try {
    const h = new URL(pic).hostname.toLowerCase();
    return PROXY_PIC_HOSTS.some((x) => h === x || h.endsWith("." + x));
  } catch {
    return false;
  }
}

// 把坏域名海报 URL 转成经本函数代理的地址（origin 用请求进来的域名，自动适配绑定域名）
function imgProxy(pic, origin) {
  if (!pic || !needProxy(pic)) return pic;
  return `${origin}/api.js?ac=img&u=${encodeURIComponent(pic)}`;
}

// 图片代理：海外节点 DNS 干净，能解析真实 IP 拉图
async function handleImg(q) {
  const u = q.get("u") || "";
  if (!needProxy(u)) return new Response("forbidden", { status: 403 });
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 12000);
  try {
    let ref = u;
    try { ref = new URL(u).origin + "/"; } catch { /* ignore */ }
    const r = await fetch(u, {
      headers: { "User-Agent": "Mozilla/5.0", "Referer": ref },
      signal: ctl.signal,
    });
    if (!r.ok) return new Response("upstream " + r.status, { status: 502 });
    const buf = await r.arrayBuffer();
    return new Response(buf, {
      status: 200,
      headers: {
        "Content-Type": r.headers.get("Content-Type") || "image/jpeg",
        "Cache-Control": "public, max-age=604800", // 缓存 7 天，减少回源
      },
    });
  } catch (e) {
    return new Response("fetch failed: " + (e && e.message || e), { status: 502 });
  } finally {
    clearTimeout(timer);
  }
}

function hashId(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 131 + s.charCodeAt(i)) >>> 0;
  return "m" + h.toString(36);
}

function getUpstreams(env, latest, n) {
  return (latest?.sites || [])
    .filter((s) => (s.type === 0 || s.type === 1) && s.api && s.key !== "super") // 排除超级源自身
    .slice(0, n);
}

// 拉取 latest.json，多线路回退；全部失败返回 null（加时间戳穿透CDN缓存，保证改源后立即生效）
async function fetchLatest(env) {
  const urls = [env.LATEST_URL || DEFAULT_LATEST, ...LATEST_FALLBACKS];
  for (const u of urls) {
    const bust = (u.includes("?") ? "&" : "?") + "_cb=" + Date.now();
    const d = await jfetch(u + bust, 12000);
    if (d?.sites?.length) return d;
  }
  return null;
}

// 静态目录（流水线每日深挖生成）：优先同源静态文件（毫秒级），GitHub CDN 兜底
const CATALOG_URLS = [
  "https://cdn.jsdelivr.net/gh/wjs1987/tvbox-auto@main/super_catalog.json",
  "https://raw.githubusercontent.com/wjs1987/tvbox-auto/main/super_catalog.json",
  "https://gh-proxy.com/https://raw.githubusercontent.com/wjs1987/tvbox-auto/main/super_catalog.json",
];
const CATALOG_MAX_AGE_MS = 30 * 3600 * 1000; // 同源副本超过30h才去GitHub拿新的（目录本就每日更新）
let memCatalog = null;
let memCatalogAt = 0;

async function loadCatalogFrom(url, timeoutMs) {
  try {
    const d = await jfetch(url, timeoutMs, 12_000_000);
    return d?.items?.length ? d : null;
  } catch {
    return null;
  }
}

function catalogAge(d) {
  try {
    return Date.now() - new Date(d.updated).getTime();
  } catch {
    return Infinity;
  }
}

async function fetchFreshCatalog(env, origin) {
  // 1) 同源静态 catalog.json（部署时随函数一起发布，几乎零耗时）
  if (origin) {
    const d = await loadCatalogFrom(`${origin}/catalog.json`, 5000);
    if (d && catalogAge(d) < CATALOG_MAX_AGE_MS) return d;
  }
  // 2) GitHub CDN（jsDelivr 对国内友好，放前面）
  for (const u of CATALOG_URLS) {
    const d = await loadCatalogFrom(u, 15000);
    if (d) return d;
  }
  return null;
}

// ---------- 汇总重建 ----------

async function rebuild(env) {
  const latestUrl = env.LATEST_URL || DEFAULT_LATEST;
  const maxUp = parseInt(env.MAX_UPSTREAMS || "25", 10);
  const pages = parseInt(env.PAGES_PER_SOURCE || "20", 10);
  // 按分类深挖：每个源再并行抓它的电影/剧集等分类列表，大幅扩充资源量
  const catPages = parseInt(env.CAT_PAGES_PER_SOURCE || "12", 10);
  const maxCats = parseInt(env.MAX_CATS_PER_SOURCE || "20", 10);

  const latest = await fetchLatest(env);
  // latest.json 拉不到时用内置兜底列表，服务永不中断
  const upstreams = latest
    ? getUpstreams(env, latest, maxUp)
    : DEFAULT_UPSTREAMS.slice(0, maxUp);
  if (!upstreams.length) throw new Error("无可用上游源");

  function extract(out, list) {
    for (const v of list || []) {
      if (!v?.vod_name || v.vod_id == null) continue;
      out.items.push({
        oid: String(v.vod_id),
        name: String(v.vod_name),
        pic: String(v.vod_pic || ""),
        remarks: String(v.vod_remarks || ""),
        // 优先用条目自带的 type_name（如"电视剧/台湾剧"），其次查分类表
        cat: String(v.type_name || out.cats.get(String(v.type_id)) || ""),
      });
    }
  }

  const tasks = upstreams.map(async (site) => {
    const api = site.api;
    const out = { name: site.name || shortSrc(api), api, cats: new Map(), items: [] };
    const cls = await jfetch(`${api}${sep(api)}ac=list`);
    const catIds = [];
    if (cls && Array.isArray(cls.class)) {
      for (const c of cls.class) {
        if (c?.type_id != null) {
          const tid = String(c.type_id);
          out.cats.set(tid, String(c.type_name || ""));
          catIds.push(tid);
        }
      }
    }

    // 并行抓取：最新列表N页 + 各分类各M页（分类列表是片库主力，电影/剧集在这里）
    // 分类选择：主流分类（电影/剧/综艺/动漫）优先，避免抓一串"海外xx"冷门类
    const MAIN_CAT = /电影|剧集|电视剧|连续|综艺|动漫|动画|片/;
    const ordered = [...catIds].sort((a, b) => {
      const ma = MAIN_CAT.test(out.cats.get(a) || "") ? 0 : 1;
      const mb = MAIN_CAT.test(out.cats.get(b) || "") ? 0 : 1;
      return ma - mb;
    });
    const fetches = [];
    for (let pg = 1; pg <= pages; pg++) fetches.push(jfetch(`${api}${sep(api)}ac=list&pg=${pg}`));
    for (const tid of ordered.slice(0, maxCats)) {
      for (let pg = 1; pg <= catPages; pg++) {
        fetches.push(jfetch(`${api}${sep(api)}ac=list&t=${tid}&pg=${pg}`));
      }
    }
    const data = await Promise.allSettled(fetches);
    for (const d of data) {
      if (d.status === "fulfilled" && d.value) extract(out, d.value.list);
    }
    return out;
  });

  const results = await Promise.allSettled(tasks);

  const seen = new Map();
  for (const r of results) {
    if (r.status !== "fulfilled") continue;
    for (const it of r.value.items) {
      const key = normName(it.name);
      if (!key) continue;
      let cid = "c_all";
      // 备注带"第N期"的是典型综艺（第6期/第20261126期），优先级最高，避免被"爱情"等词误判成电影
      if (/第\d+期/.test(it.remarks || "")) cid = "c_variety";
      else for (const [re, id] of CAT_MAP) {
        if (re.test(it.cat || "") || re.test(it.name || "")) { cid = id; break; }
      }
      const entry = seen.get(key);
      if (entry) {
        // 同一片名出现在多个采集站：保留全部源，详情页聚合为多线路（vod_play_from$$$规范）
        if (entry.srcs.length < 4) entry.srcs.push({ oid: it.oid, api: r.value.api, name: r.value.name });
        continue;
      }
      seen.set(key, {
        id: hashId(key),
        name: it.name,
        pic: it.pic,
        remarks: it.remarks,
        cat: cid,
        // 全部来源（第一个为主线路），详情时并发拉取合并多线路
        srcs: [{ oid: it.oid, api: r.value.api, name: r.value.name }],
      });
    }
  }

  const catalog = {
    updated: new Date().toISOString(),
    upstreams: upstreams.map((s) => s.name || shortSrc(s.api)),
    classes: FIXED_CLASSES,
    items: [...seen.values()],
  };
  await cachePut(env, KV_KEY, catalog);
  return catalog;
}

// ---------- 请求处理 ----------

async function getCatalog(env, origin) {
  // 1) 内存缓存 20h 内直接用（同实例请求零开销）
  if (memCatalog && Date.now() - memCatalogAt < REFRESH_INTERVAL_MS) return memCatalog;
  // 2) 同源静态文件 → GitHub CDN 多级回退
  const fresh = await fetchFreshCatalog(env, origin);
  if (fresh) {
    memCatalog = fresh;
    memCatalogAt = Date.now();
    cachePut(env, KV_KEY, fresh).catch(() => {});
    return fresh;
  }
  // 3) 全部失败：退回 KV 缓存，再退回边端实时聚合
  const kv = await cacheGet(env, KV_KEY);
  if (kv?.items?.length) {
    memCatalog = kv;
    memCatalogAt = Date.now();
    return kv;
  }
  try {
    memCatalog = await rebuild(env);
    memCatalogAt = Date.now();
    return memCatalog;
  } catch {
    return memCatalog || kv || { updated: "", upstreams: [], classes: FIXED_CLASSES, items: [] };
  }
}

// ===== 国内网盘直采：盘搜站搜索 → 详情页提取真实网盘直链 =====
// 在 EdgeOne 国内节点执行，绕开国外机房看不到国内资源的限制
const PANSO_HOST = "https://panso.pro";
const panMem = new Map(); // 盘搜结果内存缓存（同实例秒开）
async function panSearch(wd, pg, want, env) {
  want = Math.min(want || 8, 12);
  // 盘搜结果缓存 6 小时（同一关键词重复搜索秒开，避免每次都爬详情）
  const ck = "pan:" + wd + ":" + pg;
  const mem = panMem.get(ck);
  if (mem && Date.now() - mem.t < 6 * 3600 * 1000) return mem.v.slice(0, want);
  if (env) {
    const cached = await cacheGet(env, ck);
    if (Array.isArray(cached) && cached.length) { panMem.set(ck, { t: Date.now(), v: cached }); return cached.slice(0, want); }
  }
  const searchUrl = `${PANSO_HOST}/search?exact=false&format=video&page=${pg}&q=${encodeURIComponent(wd)}`;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 8000);
  let html;
  try {
    const r = await fetch(searchUrl, { headers: { "User-Agent": UA }, signal: ctl.signal, redirect: "follow" });
    if (!r.ok) return [];
    html = await r.text();
  } catch { return []; }
  finally { clearTimeout(timer); }

  // 解析搜索结果：<article class="search-item"> ... 网盘类型 / 标题 / 详情链接
  const cands = [];
  for (const a of html.split('<article class="search-item">').slice(1)) {
    const mHref = a.match(/class="search-item-title" href="([^"]+)"/);
    const mTitle = a.match(/class="search-item-title"[^>]*title="([^"]*)"/);
    const mLogo = a.match(/class="search-item-logo"[^>]*>([^<]+)</);
    if (!mHref || !mTitle) continue;
    const href = mHref[1];
    if (!href.startsWith("/doc/")) continue;
    cands.push({
      href,
      title: mTitle[1].replace(/&amp;/g, "&").replace(/&quot;/g, '"').trim(),
      disk: mLogo ? mLogo[1].trim() : "网盘",
    });
    if (cands.length >= want) break;
  }
  if (!cands.length) return [];

  // 并发抓详情页，提取 jump-link 里的真实网盘地址
  const dctl = new AbortController();
  const dtimer = setTimeout(() => dctl.abort(), 9000);
  const parts = await Promise.allSettled(cands.map(async (c) => {
    try {
      const r = await fetch(PANSO_HOST + c.href, { headers: { "User-Agent": UA }, signal: dctl.signal });
      const d = await r.text();
      const m = d.match(/class="jump-link" href="(https?:\/\/[^"]+)"/);
      if (!m) return null;
      const link = m[1].replace(/&amp;/g, "&");
      if (!/pan\.quark\.cn|pan\.baidu\.com|aliyundrive\.com|115cdn\.com|xl\.apk\.tw|caiyun\.189\.cn|pan\.uc\.cn|www\.123pan\.com|www\.pikpak\.com/i.test(link)) return null;
      return { ...c, link };
    } catch { return null; }
  }));
  clearTimeout(dtimer);
  const out = parts.filter((p) => p.status === "fulfilled" && p.value).map((p) => p.value);
  if (out.length) {
    panMem.set(ck, { t: Date.now(), v: out });
    if (env) cachePut(env, ck, out).catch(() => {}); // 不等待远程写入，避免拖慢响应
  }
  return out;
}

// 网盘搜索首页：无搜索词时展示热门真实资源，避免电视端“找不到数据”
const PAN_HOT_WORDS = [
  { wd: "庆余年", cat: "c_tv" },
  { wd: "狂飙", cat: "c_tv" },
  { wd: "流浪地球", cat: "c_movie" },
  { wd: "奔跑吧", cat: "c_variety" },
  { wd: "蜡笔小新", cat: "c_anime" },
  { wd: "舌尖上的中国", cat: "c_doc" },
];
let panHomeMem = null;
async function panHome(env) {
  if (panHomeMem && Date.now() - panHomeMem.t < 6 * 3600 * 1000) return panHomeMem.v;
  if (env) {
    const cached = await cacheGet(env, "pan:home2");
    if (Array.isArray(cached) && cached.length) {
      panHomeMem = { t: Date.now(), v: cached };
      return cached;
    }
  }
  // 各热门词并发抓取，每个词取 3 条，减少子请求数
  const groups = await Promise.allSettled(PAN_HOT_WORDS.map((h) => panSearch(h.wd, 1, 3, env)));
  const out = [];
  groups.forEach((g, i) => {
    if (g.status !== "fulfilled" || !Array.isArray(g.value)) return;
    for (const p of g.value) {
      if (out.some((x) => x.link === p.link)) continue; // 热门词交叉时按链接去重
      out.push({ ...p, cat: PAN_HOT_WORDS[i].cat, title: p.title.replace(/^\d+\s*[.．、]\s*/, "") });
    }
  });
  if (out.length) {
    panHomeMem = { t: Date.now(), v: out };
    if (env) cachePut(env, "pan:home2", out).catch(() => {});
  }
  return out;
}
function panCatOf(p) {
  if (p.cat) return p.cat;
  for (const [re, id] of CAT_MAP) if (re.test(p.title || "")) return id;
  return "c_all";
}

// 网盘结果的 id：pan:<网盘类型>|<片名>|<网盘链接>（均 URL 编码）
function makePanId(p) {
  return "pan:" + [p.disk, p.title, p.link].map((x) => encodeURIComponent(x)).join("|");
}
function parsePanId(id) {
  const parts = id.slice(4).split("|").map((x) => { try { return decodeURIComponent(x); } catch { return x; } });
  return { disk: parts[0] || "网盘", title: parts[1] || "网盘资源", link: parts[2] || "" };
}

// ===== 云网关：夸克/UC 分享链接 → 可播直链（跑在边缘节点，电视端零负担、无需开电脑） =====
// 流程与开源 quark-auto-save 一致：stoken → 列文件 → 转存到自己的临时目录 →
// 轮询任务 → file/download 取直链 → 删除临时文件（转存只是取直链的跳板）。
// 需要用户自己的网盘 Cookie，通过 mode=panck&set=quark=<cookie> 配置一次即可。
const PAN_GW = {
  "夸克网盘": { ck: "quark", base: "https://drive-pc.quark.cn", playBase: "https://drive.quark.cn", ref: "https://pan.quark.cn/" },
  "UC网盘": { ck: "uc", base: "https://pc-api.uc.cn", playBase: "https://pc-api.uc.cn", ref: "https://pc.uc.cn/" },
};
const PAN_VIDEO_RE = /\.(mp4|mkv|avi|rmvb|ts|flv|wmv|mov|m2ts)$/i;
// 清晰度优先级：从高到低取第一个 accessable 的档位（免费账号通常只有 low）
const PAN_RES_ORDER = ["4k", "2k", "super", "high", "normal", "low"];
const panResMem = new Map();
const panSleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function panApi(cfg, cookie, path, { method = "GET", body, params, base } = {}) {
  const u = new URL((base || cfg.base) + path);
  u.searchParams.set("pr", "ucpro");
  u.searchParams.set("fr", "pc");
  for (const [k, v] of Object.entries(params || {})) u.searchParams.set(k, String(v));
  const r = await fetch(u.toString(), {
    method,
    headers: { cookie, "content-type": "application/json", "user-agent": UA, referer: cfg.ref },
    body: body ? JSON.stringify(body) : undefined,
  });
  return r.json().catch(() => ({}));
}

// 分享目录分页列举
async function panListShare(cfg, cookie, pwdId, stoken, pdirFid) {
  const out = [];
  for (let page = 1; page <= 3; page++) {
    const d = await panApi(cfg, cookie, "/1/clouddrive/share/sharepage/detail", { params: {
      pwd_id: pwdId, stoken, pdir_fid: pdirFid, force: 0, _page: page, _size: 50,
      _fetch_banner: 0, _fetch_share: 0, _fetch_total: 1, _sort: "file_type:asc,updated_at:desc", ver: 2,
    } });
    const list = d?.data?.list || [];
    out.push(...list);
    if (!list.length || out.length >= (d?.metadata?._total || 0)) break;
  }
  return out;
}

// 递归收集分享里的视频文件（最多 2 层目录，最多 100 个文件）
async function panCollectVideos(cfg, cookie, pwdId, stoken, pdirFid, depth, out, budget) {
  if (depth > 2 || budget.n <= 0) return;
  const items = await panListShare(cfg, cookie, pwdId, stoken, pdirFid);
  for (const it of items) {
    if (it.dir) {
      if (budget.folder-- > 0) await panCollectVideos(cfg, cookie, pwdId, stoken, it.fid, depth + 1, out, budget);
      if (budget.n <= 0) return;
    } else if (PAN_VIDEO_RE.test(it.file_name || "") && it.fid && it.share_fid_token) {
      out.push({ fid: it.fid, token: it.share_fid_token, name: it.file_name });
      if (--budget.n <= 0) return;
    }
  }
}

// 主流程：分享链接 → 转码流剧集列表（HLS m3u8，经边缘代理带 Cookie 转发）。无 Cookie 返回 null；失败返回 {error}
async function panResolveDirect(p, env, origin) {
  const cfg = PAN_GW[p.disk];
  if (!cfg) return null;
  const cks = (await cacheGet(env, "pan:cookies")) || {};
  const cookie = cks[cfg.ck];
  if (!cookie) return null;
  const m = (p.link || "").match(/\/s\/([0-9a-zA-Z]+)/);
  if (!m) return null;
  const key = "pan:res:" + cfg.ck + ":" + m[1];
  const mem = panResMem.get(key);
  if (mem && Date.now() - mem.t < 30 * 60 * 1000) return mem.v;
  if (env) {
    const c = await cacheGet(env, key);
    if (c && Date.now() - new Date(c.t).getTime() < 30 * 60 * 1000) { panResMem.set(key, { t: Date.now(), v: c }); return c; }
  }
  try {
    const tk = await panApi(cfg, cookie, "/1/clouddrive/share/sharepage/token", { method: "POST", body: { pwd_id: m[1], passcode: "" } });
    if (tk?.code !== 0 || !tk?.data?.stoken) return { error: tk?.message || "取分享token失败" };
    await panSleep(500); // 网盘接口限频，紧跟着的列表请求太快会 400
    const stoken = tk.data.stoken;
    const vids = [];
    await panCollectVideos(cfg, cookie, m[1], stoken, "0", 0, vids, { n: 100, folder: 6 });
    if (!vids.length) return { error: "分享里没有视频文件" };
    // 转存到自己的临时目录（转码播放接口只认自己账号的文件）
    const mk = await panApi(cfg, cookie, "/1/clouddrive/file", { method: "POST", body: { pdir_fid: "0", file_name: "", dir_path: "/TVBox直链缓存", dir_init_lock: false } });
    const tmpFid = mk?.data?.fid;
    if (!tmpFid) return { error: mk?.message || "创建临时目录失败" };
    const save = await panApi(cfg, cookie, "/1/clouddrive/share/sharepage/save", { method: "POST", params: { uc_param_str: "", app: "clouddrive" }, body: {
      fid_list: vids.map((v) => v.fid), fid_token_list: vids.map((v) => v.token),
      to_pdir_fid: tmpFid, pwd_id: m[1], stoken, pdir_fid: "0", scene: "link",
    } });
    if (save?.code !== 0 || !save?.data?.task_id) return { error: save?.message || "转存失败" };
    let done = false;
    for (let i = 0; i < 10; i++) {
      await panSleep(500);
      const tq = await panApi(cfg, cookie, "/1/clouddrive/task", { params: { task_id: save.data.task_id, retry_index: i, uc_param_str: "" } });
      if (tq?.data?.status === 2) { done = true; break; }
    }
    if (!done) return { error: "转存超时" };
    // 在临时目录下找转存后的真实文件（分享根可能直接是文件，也可能套一层文件夹）
    const files = [];
    async function walk(fid, d) {
      if (d > 2 || files.length >= 100) return;
      for (let page = 1; page <= 3 && files.length < 100; page++) {
        const d2 = await panApi(cfg, cookie, "/1/clouddrive/file/sort", { params: { pdir_fid: fid, _page: page, _size: 100, _fetch_total: 1, _sort: "file_name:asc" } });
        const list = d2?.data?.list || [];
        for (const it of list) {
          if (it.dir) await walk(it.fid, d + 1);
          else if (PAN_VIDEO_RE.test(it.file_name || "")) files.push({ fid: it.fid, name: it.file_name });
        }
        if (list.length < 100) break;
      }
    }
    await walk(tmpFid, 0);
    if (!files.length) return { error: "转存后未找到视频文件" };
    // 逐集调转码播放接口（file/v2/play），小并发避免限频
    const eps = [];
    const BATCH = 4;
    for (let i = 0; i < files.length; i += BATCH) {
      const batch = files.slice(i, i + BATCH).filter((f) => f.fid);
      const parts = await Promise.allSettled(batch.map(async (f) => {
        const pl = await panApi(cfg, cookie, "/1/clouddrive/file/v2/play", { base: cfg.playBase, method: "POST", body: { fid: f.fid, resolutions: "normal,low,high,super,2k,4k", supports: "fmp4" } });
        const vlist = ((pl?.data || {}).video_list || []).filter((v) => v.accessable && v.video_info?.url);
        if (!vlist.length) return null;
        vlist.sort((a, b) => PAN_RES_ORDER.indexOf(a.resolution) - PAN_RES_ORDER.indexOf(b.resolution));
        return { name: f.name.replace(PAN_VIDEO_RE, ""), url: vlist[0].video_info.url };
      }));
      for (const pt of parts) if (pt.status === "fulfilled" && pt.value) eps.push(pt.value);
      if (i + BATCH < files.length) await panSleep(300);
    }
    // 清理临时目录（转码流由夸克 CDN 提供，转存文件已无用）
    panApi(cfg, cookie, "/1/clouddrive/file/delete", { method: "POST", params: { uc_param_str: "" }, body: { action_type: 2, filelist: [tmpFid], exclude_fids: [] } }).catch(() => {});
    if (!eps.length) return { error: "无可播放的转码流(免费号可能无权限)" };
    // 播放地址换成边缘代理（CDN 流需要 Cookie，播放器带不了）
    const proxy = `${origin}/api.js?mode=panstream&u=`;
    const v = { t: new Date().toISOString(), disk: p.disk, episodes: eps.map((e) => ({ name: e.name, url: proxy + encodeURIComponent(e.url) })) };
    panResMem.set(key, { t: Date.now(), v });
    if (env) cachePut(env, key, v).catch(() => {});
    return v;
  } catch (e) {
    return { error: String(e && e.message || e) };
  }
}

// Cookie 管理：mode=panck 查看/设置（set=quark=<cookie> 或 uc=<cookie>，值空删除）
async function handlePanCookies(q, env) {
  const set = (q.get("set") || "").trim();
  const cks = (await cacheGet(env, "pan:cookies")) || {};
  const META = { quark: PAN_GW["夸克网盘"], uc: PAN_GW["UC网盘"] };
  if (set) {
    const i = set.indexOf("=");
    const name = set.slice(0, i).trim(), val = set.slice(i + 1).trim();
    if (!META[name]) return json({ error: "unknown pan: " + name, allow: Object.keys(META) }, 400);
    if (val) cks[name] = val; else delete cks[name];
    await cachePut(env, "pan:cookies", cks);
  }
  const status = {};
  for (const [name, ck] of Object.entries(cks)) {
    const cfg = META[name];
    if (!cfg || !ck) continue;
    try {
      const d = await panApi(cfg, ck, "/1/clouddrive/member");
      status[name] = d?.code === 0 ? "OK" + (d?.data?.is_vip ? "(会员)" : "(免费账号)") : "失效:" + String(d?.message || d?.code || "未知").slice(0, 60);
    } catch (e) { status[name] = "ERR:" + String(e && e.message || e).slice(0, 80); }
  }
  return json({ ok: true, saved: Object.keys(cks), status });
}

async function handleList(q, env, cat, origin) {
  const wd = (q.get("wd") || "").trim();
  const t = q.get("t") || "";
  const pg = Math.max(1, parseInt(q.get("pg") || "1", 10));
  const limit = 40;

  // 全局搜索：优先本地目录检索（毫秒级），无结果才回源跨站实时搜
  if (wd) {
    const normWd = normName(wd);
    if (normWd) {
      const hits = (cat.items || []).filter((x) => normName(x.name).includes(normWd));
      if (hits.length) {
        // 片名以关键词开头（如"庆余年 第一季"命中"庆余年"）置顶
        hits.sort((a, b) => (normName(b.name).startsWith(normWd) ? 1 : 0) - (normName(a.name).startsWith(normWd) ? 1 : 0));
        const catName = (id) => (cat.classes || FIXED_CLASSES).find((c) => c.type_id === id)?.type_name || "全部";
        const list = hits.slice((pg - 1) * limit, pg * limit).map((x) => ({
          vod_id: x.id,
          vod_name: x.name,
          vod_pic: imgProxy(x.pic, origin),
          vod_remarks: x.remarks,
          type_id: x.cat,
          type_name: catName(x.cat),
        }));
        return json({
          code: 1, msg: "数据列表", page: pg, limit,
          total: hits.length, pagecount: Math.ceil(hits.length / limit) || 1,
          class: FIXED_CLASSES, list,
        });
      }
    }
    const latest = await fetchLatest(env);
    // 搜索回源放宽到 15 个源：本地目录没收录的冷门片，跨源搜索更容易命中
    const apis = latest ? getUpstreams(env, latest, 15) : DEFAULT_UPSTREAMS.slice(0, 15);
    const found = new Map();
    await Promise.allSettled(
      apis.map(async (site) => {
        const data = await jfetch(`${site.api}${sep(site.api)}ac=list&wd=${encodeURIComponent(wd)}`);
        for (const v of data?.list || []) {
          if (!v?.vod_name || v.vod_id == null) continue;
          const key = normName(v.vod_name);
          if (!key || found.has(key)) continue;
          found.set(key, {
            vod_id: "q:" + site.api + ":" + v.vod_id,
            vod_name: String(v.vod_name),
            vod_pic: imgProxy(String(v.vod_pic || ""), origin),
            vod_remarks: String(v.vod_remarks || "在线"),
            type_id: "c_all",
            type_name: "全部",
            srcApi: site.api,
          });
        }
      })
    );
    const all = [...found.values()];
    // 片名以关键词开头（如"庆余年 第一季"命中"庆余年"）置顶，其余保持源返回顺序
    all.sort((a, b) => (normName(b.vod_name).startsWith(normWd) ? 1 : 0) - (normName(a.vod_name).startsWith(normWd) ? 1 : 0));
    const list = all.slice((pg - 1) * limit, pg * limit).map((x) => {
      if (!x.panLink) return x;
      const { vod_id, vod_name, vod_remarks } = x;
      return { vod_id, vod_name, vod_pic: "", vod_remarks, type_id: "c_all", type_name: "网盘" };
    });
    return json({
      code: 1, msg: "数据列表", page: pg, limit,
      total: found.size, pagecount: Math.ceil(found.size / limit) || 1,
      class: FIXED_CLASSES, list,
    });
  }

  let items = (cat.items || []);
  if (t && t !== "c_all") items = items.filter((x) => x.cat === t);
  const total = items.length;
  // maccms 标准字段：播放器端按 vod_id/vod_name/vod_pic 解析，字段名不对会被整体丢弃
  const catName = (id) => (cat.classes || FIXED_CLASSES).find((c) => c.type_id === id)?.type_name || "全部";
  const list = items.slice((pg - 1) * limit, pg * limit).map((x) => ({
    vod_id: x.id,
    vod_name: x.name,
    vod_pic: imgProxy(x.pic, origin),
    vod_remarks: x.remarks,
    type_id: x.cat,
    type_name: catName(x.cat),
  }));
  return json({
    code: 1, msg: "数据列表", page: pg, limit,
    total, pagecount: Math.ceil(total / limit) || 1,
    class: cat.classes || FIXED_CLASSES,
    list,
  });
}

// 解析 "q:srcApi:oid" 形式的 id
function parseQId(first) {
  const rest = first.slice(2);
  const i = rest.lastIndexOf(":"); // srcApi 是完整 URL 含冒号，必须从右往左切
  return { srcApi: rest.slice(0, i), oid: rest.slice(i + 1) };
}

async function handleDetail(ids, cat, env, origin) {
  const first = String(ids).split(",")[0];
  // 网盘直采详情：优先走云网关转直链；没配 Cookie 或失败则回退分享链接
  if (first.startsWith("pan:")) {
    const p = parsePanId(first);
    if (!p.link) return json({ code: 1, list: [] });
    const lines = [p.link];
    let remark = p.disk;
    const res = await panResolveDirect(p, env, origin);
    if (res && !res.error && Array.isArray(res.episodes) && res.episodes.length) {
      // maccms 格式：vod_play_url 每组是 "集名$地址#集名$地址"，线路名只放 vod_play_from
      const eps = res.episodes.map((e, i) => `${e.name || "第" + (i + 1) + "集"}$${e.url}`).join("#");
      lines.unshift(eps);
      remark += "·可播";
    } else if (res?.error) {
      remark += "·" + String(res.error).slice(0, 16);
    }
    return json({ code: 1, list: [{
      vod_id: first,
      vod_name: p.title,
      vod_pic: "",
      vod_remarks: remark,
      vod_play_from: lines.length > 1 ? "直链播放$$$网盘" : "网盘",
      vod_play_url: lines.join("$$$"),
    }] });
  }
  let srcs; // [{oid, api, name}]，第一个为主线路
  if (first.startsWith("q:")) {
    const { srcApi, oid } = parseQId(first);
    srcs = [{ oid, api: srcApi, name: shortSrc(srcApi) }];
  } else {
    const item = (cat.items || []).find((x) => x.id === first);
    if (!item) return json({ code: 1, list: [] });
    srcs = item.srcs || (item.src ? [{ oid: item.src, api: item.srcApi, name: "" }] : []);
  }
  srcs = (srcs || []).filter((s) => s.api && s.oid != null);
  if (!srcs.length) return json({ code: 1, list: [] });

  // 详情缓存：6小时内点过的片直接秒开，不再回源
  const cacheKey = "d:" + first;
  const cached = await cacheGet(env, cacheKey);
  if (cached?.vod && Date.now() - new Date(cached.t).getTime() < 6 * 3600 * 1000) {
    return json({ code: 1, list: [cached.vod] });
  }

  // 并发拉取各线路的上游详情（官方规范：多线路用$$$拼接）
  const parts = await Promise.allSettled(
    srcs.slice(0, 4).map(async (s, i) => {
      const data = await jfetch(`${s.api}${sep(s.api)}ac=detail&ids=${s.oid}`);
      const v = data?.list?.[0];
      if (!v?.vod_play_url) return null;
      // 上游自身也可能带多线路（vod_play_from 与 vod_play_url 按$$$一一对应），逐组命名，保证数量对齐
      const flags = String(v.vod_play_from || "").split("$$$");
      const plays = String(v.vod_play_url).split("$$$");
      const srcName = s.name || shortSrc(s.api);
      const groups = [];
      for (let g = 0; g < Math.max(flags.length, plays.length); g++) {
        const tag = i === 0 && g === 0 ? "┃高速" : "┃备用";
        groups.push({
          label: `${srcName}·${(flags[g] || "线路" + (g + 1)).slice(0, 10)}${tag}`,
          play: plays[g] || "",
        });
      }
      return { groups, info: v };
    })
  );

  const ok = parts.filter((p) => p.status === "fulfilled" && p.value);
  if (!ok.length) return json({ code: 1, list: [] });
  const info = ok[0].value.info;
  const vod = {
    ...info,
    vod_id: first,
    vod_pic: imgProxy(info.vod_pic, origin),
    vod_play_from: ok.flatMap((p) => p.value.groups.map((g) => g.label)).join("$$$"),
    vod_play_url: ok.flatMap((p) => p.value.groups.map((g) => g.play)).join("$$$"),
  };
  cachePut(env, cacheKey, { t: new Date().toISOString(), vod }).catch(() => {});
  return json({ code: 1, list: [vod] });
}

export async function onRequest({ request, env }) {
  const url = new URL(request.url);
  const q = url.searchParams;
  let mode = q.get("mode") || "";
  // EdgeOne 定时任务只带 path+payload（无法保证 query），允许从 POST JSON body 里取 mode
  if (!mode && request.method === "POST") {
    try {
      const b = await request.clone().json();
      if (b && typeof b.mode === "string") mode = b.mode;
    } catch { /* 无 body 或非 JSON */ }
  }

  try {
    if (mode === "refresh") {
      const token = env.REFRESH_TOKEN || "";
      if (token && q.get("key") !== token) return json({ error: "unauthorized" }, 401);
      const cat = await rebuild(env);
      return json({ ok: true, total: cat.items.length, upstreams: cat.upstreams });
    }
    if (mode === "pansearch") {
      // 独立网盘搜索源：供魔屏作为单独站点并行调用，不拖慢主片单搜索
      const ac = q.get("ac") || "list";
      const ids = q.get("ids") || "";
      // 详情请求必须透传，否则点击网盘结果会落到这里返回空列表
      if (ac === "detail" && ids) {
        const cat = await getCatalog(env, url.origin);
        return await handleDetail(ids, cat, env, url.origin);
      }
      const wd = (q.get("wd") || "").trim();
      const pg = Math.max(1, parseInt(q.get("pg") || "1", 10));
      const t = (q.get("t") || "").trim();
      let pans;
      if (wd) {
        pans = await panSearch(wd, pg, 12, env);
      } else {
        pans = await panHome(env);
        if (t && t !== "c_all") pans = pans.filter((p) => panCatOf(p) === t);
      }
      const list = pans.map((p) => {
        const cid = panCatOf(p);
        return {
          vod_id: makePanId(p),
          vod_name: p.title,
          vod_pic: "",
          vod_remarks: "📀" + p.disk,
          type_id: cid,
          type_name: FIXED_CLASSES.find((c) => c.type_id === cid)?.type_name || "网盘",
        };
      });
      return json({ code: 1, msg: "数据列表", page: pg, limit: 40, total: list.length, pagecount: 1, class: FIXED_CLASSES, list });
    }
    if (mode === "panck") return await handlePanCookies(q, env);
    if (mode === "panstream") {
      // 边缘代理流：网盘 CDN 的 HLS 地址需要 Cookie，播放器带不了，
      // 由边缘函数带 Cookie 转发，并把 m3u8 里的分片地址改写成同样走本代理
      const u = q.get("u") || "";
      let tgt;
      try { tgt = new URL(u); } catch { return json({ error: "bad u" }, 400); }
      const h = tgt.hostname.toLowerCase();
      if (!/(^|\.)quark\.cn$/.test(h) && !/(^|\.)uc\.cn$/.test(h)) return json({ error: "host not allowed" }, 403);
      const cks = (await cacheGet(env, "pan:cookies")) || {};
      const cookie = h.endsWith("quark.cn") ? cks.quark : cks.uc;
      const headers = { "user-agent": UA, referer: "https://pan.quark.cn/", accept: "*/*" };
      if (cookie) headers.cookie = cookie;
      const rng = request.headers.get("range");
      if (rng) headers.range = rng;
      const r = await fetch(tgt.toString(), { headers });
      const ct = r.headers.get("content-type") || "";
      if (/mpegurl|m3u8/i.test(ct)) {
        // HLS 播放列表：改写每行分片地址与标签内嵌 URI，全部经由本代理
        const rewrite = (raw) => `${url.origin}/api.js?mode=panstream&u=${encodeURIComponent(new URL(raw, tgt).toString())}`;
        const out = (await r.text()).split("\n").map((line) => {
          if (!line.trim()) return line;
          if (line.trim().startsWith("#")) return line.replace(/URI="([^"]+)"/g, (m0, u1) => `URI="${rewrite(u1)}"`);
          return rewrite(line.trim());
        }).join("\n");
        return new Response(out, { status: 200, headers: { "content-type": "application/vnd.apple.mpegurl", "cache-control": "no-store" } });
      }
      // 二进制分片：流式透传 Range 语义
      const outH = new Headers();
      for (const k of ["content-type", "content-length", "content-range", "accept-ranges"]) {
        const v = r.headers.get(k);
        if (v) outH.set(k, v);
      }
      if (!outH.has("accept-ranges")) outH.set("accept-ranges", "bytes");
      return new Response(r.body, { status: r.status, headers: outH });
    }
    if (mode === "probe") return await handleProbe(q, request);
    if (mode === "crawl") {
      // 国内节点直采探针：只允许白名单站点，防内网探测；返回正文供验证/抓取
      const target = q.get("url") || "";
      let u;
      try { u = new URL(target); } catch { return json({ error: "bad url" }, 400); }
      if (!/^https?:$/.test(u.protocol)) return json({ error: "bad scheme" }, 400);
      const h = u.hostname.toLowerCase();
      const ALLOW = ["panso.pro", "www.baidu.com", "cn.bing.com", "www.sogou.com", "weixin.sogou.com", "www.xiongdipan.com", "wpzy.cc", "www.sharenice.net", "m.sharenice.net"];
      if (!ALLOW.some(x => h === x || h.endsWith("." + x))) return json({ error: "host not allowed: " + h }, 403);
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 12000);
      const t0 = Date.now();
      try {
        const r = await fetch(u.toString(), {
          headers: { "User-Agent": UA, "Accept": "text/html,application/json,*/*" },
          signal: ctl.signal,
          redirect: "follow",
        });
        const body = (await r.text()).slice(0, 60000);
        const hdrs = {};
        for (const [k, v] of r.headers.entries()) if (/^(eo|x-eo|x-region|content-type)/i.test(k)) hdrs[k] = v.slice(0, 120);
        return json({ ok: r.ok, status: r.status, ms: Date.now() - t0, host: h, mainland: request.headers.get("eo-is-mainland") || null, headers: hdrs, length: body.length, body });
      } catch (e) {
        return json({ ok: false, ms: Date.now() - t0, host: h, error: String(e && e.message || e) });
      } finally { clearTimeout(timer); }
    }
    if (mode === "cronprobe") return await handleCronProbe(request, env);
    if (mode === "probe_results") return await handleProbeResults(env);
    if (mode === "diag") {
      // 直接回显请求头与 EdgeOne 注入的 GEO 信息，判断节点位置（不依赖外部回显服务）
      const hdrs = {};
      for (const [k, v] of request.headers.entries()) {
        if (/^(eo|x-eo|x-region|cdn|client)/i.test(k)) hdrs[k] = v.slice(0, 120);
      }
      let geo = null;
      try { geo = request.eo ? JSON.stringify(request.eo).slice(0, 400) : null; } catch { geo = null; }
      return json({ ok: true, from: "edgeone", headers: hdrs, eo: geo });
    }
    if (mode === "status") {
      const cat = await getCatalog(env, url.origin);
      // 探测真实可用的存储后端（写一个小探针并回读，结果反映在 storage 字段）
      await cachePut(env, "probe", { t: Date.now() });
      const probe = await cacheGet(env, "probe");
      const storage = probe ? `${backendName}(读写OK)` : `${backendName}(只写不读!)`;
      return json({ updated: cat.updated, upstreams: cat.upstreams, total: (cat.items || []).length, storage });
    }

    const ac = q.get("ac") || "list";
    // 图片代理走海外节点拉取，不依赖目录，放最前面（无需 getCatalog，响应更快）
    if (ac === "img") return await handleImg(q);

    const cat = await getCatalog(env, url.origin);
    // FongMi type1 约定：ac=detail&ids=xxx 是查详情；ac=detail&t=分类&pg=页 是分类浏览
    if (ac === "detail" && q.get("ids")) return await handleDetail(q.get("ids"), cat, env, url.origin);
    return await handleList(q, env, cat, url.origin);
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 500);
  }
}
