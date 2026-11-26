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
  { name: "索尼 高清", type: 1, api: "https://suoniapi.com/api.php/provide/vod/?ac=list" },
  { name: "新浪资源(切)", type: 1, api: "http://api.xinlangapi.com/xinlangapi.php/provide/vod/" },
  { name: "光速资源(切)", type: 1, api: "https://api.guangsuapi.com/api.php/provide/vod/" },
  { name: "非凡 资源", type: 1, api: "http://ffzy1.tv/api.php/provide/vod/" },
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

// 拉取 latest.json，多线路回退；全部失败返回 null
async function fetchLatest(env) {
  const urls = [env.LATEST_URL || DEFAULT_LATEST, ...LATEST_FALLBACKS];
  for (const u of urls) {
    const d = await jfetch(u, 12000);
    if (d?.sites?.length) return d;
  }
  return null;
}

// ---------- 汇总重建 ----------

async function rebuild(env) {
  const latestUrl = env.LATEST_URL || DEFAULT_LATEST;
  const maxUp = parseInt(env.MAX_UPSTREAMS || "6", 10);
  const pages = parseInt(env.PAGES_PER_SOURCE || "2", 10);

  const latest = await fetchLatest(env);
  // latest.json 拉不到时用内置兜底列表，服务永不中断
  const upstreams = latest
    ? getUpstreams(env, latest, maxUp)
    : DEFAULT_UPSTREAMS.slice(0, maxUp);
  if (!upstreams.length) throw new Error("无可用上游源");

  const tasks = upstreams.map(async (site) => {
    const api = site.api;
    const out = { name: site.name || shortSrc(api), api, cats: new Map(), items: [] };
    const cls = await jfetch(`${api}${sep(api)}ac=list`);
    if (cls && Array.isArray(cls.class)) {
      for (const c of cls.class) {
        if (c?.type_id != null) out.cats.set(String(c.type_id), String(c.type_name || ""));
      }
    }
    for (let pg = 1; pg <= pages; pg++) {
      const data = await jfetch(`${api}${sep(api)}ac=list&pg=${pg}`);
      const list = data?.list;
      if (!Array.isArray(list) || !list.length) break;
      for (const v of list) {
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
      if (data.pagecount && pg >= Number(data.pagecount)) break;
    }
    return out;
  });

  const results = await Promise.allSettled(tasks);

  const seen = new Map();
  for (const r of results) {
    if (r.status !== "fulfilled") continue;
    for (const it of r.value.items) {
      const key = normName(it.name);
      if (!key || seen.has(key)) continue;
      let cid = "c_all";
      for (const [re, id] of CAT_MAP) {
        if (re.test(it.cat || "") || re.test(it.name || "")) { cid = id; break; }
      }
      seen.set(key, {
        id: hashId(key),
        name: it.name,
        pic: it.pic,
        remarks: it.remarks,
        cat: cid,
        src: it.oid,
        srcApi: r.value.api,
        srcName: r.value.name,
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

async function getCatalog(env) {
  const cat = await kvGet(env, KV_KEY);
  if (cat && cat.updated && Date.now() - new Date(cat.updated).getTime() < REFRESH_INTERVAL_MS) {
    return cat;
  }
  try {
    return await rebuild(env);
  } catch {
    return cat || { updated: "", upstreams: [], classes: FIXED_CLASSES, items: [] };
  }
}

async function handleList(q, env, cat) {
  const wd = (q.get("wd") || "").trim();
  const t = q.get("t") || "";
  const pg = Math.max(1, parseInt(q.get("pg") || "1", 10));
  const limit = 40;

  // 全局搜索：并发搜所有上游，按片名去重
  if (wd) {
    const latest = await fetchLatest(env);
    const apis = latest ? getUpstreams(env, latest, 6) : DEFAULT_UPSTREAMS.slice(0, 6);
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
            vod_pic: String(v.vod_pic || ""),
            vod_remarks: String(v.vod_remarks || "在线"),
            type_id: "c_all",
            type_name: "全部",
            srcApi: site.api,
          });
        }
      })
    );
    const list = [...found.values()].slice((pg - 1) * limit, pg * limit);
    return json({
      code: 1, msg: "数据列表", page: pg, limit,
      total: found.size, pagecount: Math.ceil(found.size / limit) || 1,
      class: FIXED_CLASSES, list,
    });
  }

  let items = cat.items || [];
  if (t && t !== "c_all") items = items.filter((x) => x.cat === t);
  const total = items.length;
  // maccms 标准字段：播放器端按 vod_id/vod_name/vod_pic 解析，字段名不对会被整体丢弃
  const catName = (id) => (cat.classes || FIXED_CLASSES).find((c) => c.type_id === id)?.type_name || "全部";
  const list = items.slice((pg - 1) * limit, pg * limit).map((x) => ({
    vod_id: x.id,
    vod_name: x.name,
    vod_pic: x.pic,
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

async function handleDetail(ids, cat) {
  const first = String(ids).split(",")[0];
  let srcApi, oid;
  if (first.startsWith("q:")) {
    const rest = first.slice(2);
    const i = rest.lastIndexOf(":"); // srcApi 是完整 URL 含冒号，必须从右往左切
    srcApi = rest.slice(0, i);
    oid = rest.slice(i + 1);
  } else {
    const item = (cat.items || []).find((x) => x.id === first);
    if (!item) return json({ code: 1, list: [] });
    srcApi = item.srcApi;
    oid = item.src;
  }
  if (!srcApi || oid == null) return json({ code: 1, list: [] });

  const data = await jfetch(`${srcApi}${sep(srcApi)}ac=detail&ids=${oid}`);
  const v = data?.list?.[0];
  if (!v) return json({ code: 1, list: [] });
  return json({
    code: 1,
    list: [{ ...v, vod_id: first, vod_play_from: `${shortSrc(srcApi)}┃高速` }],
  });
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
      const cat = await getCatalog(env);
      return json({ updated: cat.updated, upstreams: cat.upstreams, total: (cat.items || []).length });
    }

    const cat = await getCatalog(env);
    const ac = q.get("ac") || "list";
    // FongMi type1 约定：ac=detail&ids=xxx 是查详情；ac=detail&t=分类&pg=页 是分类浏览
    if (ac === "detail" && q.get("ids")) return await handleDetail(q.get("ids"), cat);
    return await handleList(q, env, cat);
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 500);
  }
}
