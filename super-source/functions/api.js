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

// KV 尚未绑定/审批中时的内存兜底缓存（同一隔离实例内多请求共享，冷启动后需重建）
let memStore = new Map();

async function kvGet(env, key) {
  try {
    if (env.KV && typeof env.KV.get === "function") return await env.KV.get(key, "json");
  } catch { /* KV 异常时退回内存 */ }
  return memStore.has(key) ? memStore.get(key) : null;
}

async function kvPut(env, key, value) {
  memStore.set(key, value); // 始终写内存，保证无 KV 时同实例请求可直接复用
  try {
    if (env.KV && typeof env.KV.put === "function") await env.KV.put(key, JSON.stringify(value));
  } catch { /* 忽略 KV 写入失败 */ }
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
  return `${origin}/api?ac=img&u=${encodeURIComponent(pic)}`;
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

// 成人内容运行时过滤（旧目录可能残留，双保险；流水线侧已整站+整条排除）
const ADULT_RE = /麻豆|扣扣|91md|91fans|91传媒|天美|果冻传媒|星空传媒|乌鸦传媒|蜜桃|精东|杏吧|swag|女优|无码|有码|人妻|巨乳|淫|兽性|肉欲|情色|风月|mdx|做爱|性爱|嫖|妓|菠萝传媒|兔子传媒|爱娘|翰丝传媒/i;
const isAdult = (s) => ADULT_RE.test(String(s || ""));

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
      if (isAdult(it.name)) continue; // 成人内容排除
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
  await kvPut(env, KV_KEY, catalog);
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
    kvPut(env, KV_KEY, fresh).catch(() => {});
    return fresh;
  }
  // 3) 全部失败：退回 KV 缓存，再退回边端实时聚合
  const kv = await kvGet(env, KV_KEY);
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

async function handleList(q, env, cat, origin) {
  const wd = (q.get("wd") || "").trim();
  const t = q.get("t") || "";
  const pg = Math.max(1, parseInt(q.get("pg") || "1", 10));
  const limit = 40;

  // 全局搜索：优先本地目录检索（毫秒级），无结果才回源跨站实时搜
  if (wd) {
    const normWd = normName(wd);
    if (normWd) {
      const hits = (cat.items || []).filter((x) => !isAdult(x.name) && normName(x.name).includes(normWd));
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
    const list = all.slice((pg - 1) * limit, pg * limit);
    return json({
      code: 1, msg: "数据列表", page: pg, limit,
      total: found.size, pagecount: Math.ceil(found.size / limit) || 1,
      class: FIXED_CLASSES, list,
    });
  }

  let items = (cat.items || []).filter((x) => !isAdult(x.name));
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
  const cached = await kvGet(env, cacheKey);
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
  kvPut(env, cacheKey, { t: new Date().toISOString(), vod }).catch(() => {});
  return json({ code: 1, list: [vod] });
}

export async function onRequest({ request, env }) {
  const url = new URL(request.url);
  const q = url.searchParams;
  const mode = q.get("mode") || "";

  try {
    if (mode === "refresh") {
      const token = env.REFRESH_TOKEN || "";
      if (token && q.get("key") !== token) return json({ error: "unauthorized" }, 401);
      const cat = await rebuild(env);
      return json({ ok: true, total: cat.items.length, upstreams: cat.upstreams });
    }
    if (mode === "status") {
      const cat = await getCatalog(env, url.origin);
      return json({ updated: cat.updated, upstreams: cat.upstreams, total: (cat.items || []).length });
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
