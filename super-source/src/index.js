// ============================================================
// 超级源（Super Source）
// 对外表现为一个标准 maccms JSON 采集站（type 1），
// 片单内容 = 自动抓取 latest.json 里排名前 N 的采集站，
// 按片名去重合并，跨源全局搜索，播放时实时代理回原始源。
// 部署于 Cloudflare Workers（免费额度即可运行）。
// ============================================================

const REPO_LATEST = "https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/latest.json";
const KV_KEY = "catalog";
const UA = "okhttp/3.12.0";
const FIXED_CLASSES = [
  { type_id: "c_all", type_name: "全部" },
  { type_id: "c_movie", type_name: "电影" },
  { type_id: "c_tv", type_name: "剧集" },
  { type_id: "c_variety", type_name: "综艺" },
  { type_id: "c_anime", type_name: "动漫" },
  { type_id: "c_doc", type_name: "纪录" },
];

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

// ---------- 基础工具 ----------

async function jfetch(url, ms = 8000, maxBytes = 1_500_000) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  try {
    const r = await fetch(url, {
      headers: { "User-Agent": UA },
      signal: ctl.signal,
    });
    if (!r.ok) return null;
    const raw = await r.text();
    const txt = raw.slice(0, maxBytes);
    try {
      return JSON.parse(txt);
    } catch {
      return null;
    }
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function normName(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/[\s\p{P}\p{S}]+/gu, "");
}

function sep(api) {
  return api.includes("?") ? "&" : "?";
}

function shortSrc(api) {
  try {
    return new URL(api).hostname.replace(/^www\./, "").split(".")[0];
  } catch {
    return api.slice(0, 10);
  }
}

function hashId(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 131 + s.charCodeAt(i)) >>> 0;
  }
  return "m" + h.toString(36);
}

// ---------- 汇总重建 ----------

async function rebuild(env) {
  const maxUp = parseInt(env.MAX_UPSTREAMS || "6", 10);
  const pages = parseInt(env.PAGES_PER_SOURCE || "2", 10);

  const latest = await jfetch(REPO_LATEST, 15000);
  const upstreams = (latest?.sites || [])
    .filter((s) => (s.type === 0 || s.type === 1) && s.api)
    .slice(0, maxUp);
  if (!upstreams.length) throw new Error("无可用上游源");

  // 并发抓取每个源的前 N 页完整片单
  const tasks = upstreams.map(async (site) => {
    const api = site.api;
    const out = { name: site.name || shortSrc(api), rank: site.api, cats: new Map(), items: [] };
    // 分类表
    const cls = await jfetch(`${api}${sep(api)}ac=list`);
    if (cls && Array.isArray(cls.class)) {
      for (const c of cls.class) {
        if (c?.type_id != null) out.cats.set(String(c.type_id), String(c.type_name || ""));
      }
    }
    // 前 N 页
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
          cat: out.cats.get(String(v.type_id)) || "",
        });
      }
      if (data.pagecount && pg >= Number(data.pagecount)) break;
    }
    return out;
  });

  const results = await Promise.allSettled(tasks);

  // 按片名去重（排名靠前的源优先），并归类到固定分类
  const CAT_MAP = [
    [/电影|院线|动作|喜剧|爱情|科幻|恐怖|剧情|战争|犯罪|奇幻|冒险|灾难/, "c_movie"],
    [/剧|连续|国产|港台|韩|美|日剧|海外/, "c_tv"],
    [/综艺|娱乐|真人秀|脱口/, "c_variety"],
    [/动漫|动画|番剧|漫画/, "c_anime"],
    [/纪录|纪实|解说/, "c_doc"],
  ];
  const seen = new Map(); // normName -> item
  for (const r of results) {
    if (r.status !== "fulfilled") continue;
    for (const it of r.value.items) {
      const key = normName(it.name);
      if (!key || seen.has(key)) continue;
      let cid = "c_all";
      for (const [re, id] of CAT_MAP) {
        if (re.test(it.cat || it.name)) { cid = id; break; }
      }
      seen.set(key, {
        id: hashId(key),
        name: it.name,
        pic: it.pic,
        remarks: it.remarks,
        cat: cid,
        src: it.oid, // 占位，下一步覆盖
      });
      seen.get(key).srcApi = r.value.rank;
      seen.get(key).srcName = r.value.name;
    }
  }

  const items = [...seen.values()];
  const catalog = {
    updated: new Date().toISOString(),
    upstreams: upstreams.map((s) => s.name),
    classes: FIXED_CLASSES,
    items,
  };
  await env.CATALOG.put(KV_KEY, JSON.stringify(catalog));
  return { total: items.length, upstreams: catalog.upstreams };
}

// ---------- 请求处理 ----------

async function getCatalog(env) {
  const raw = await env.CATALOG.get(KV_KEY);
  if (raw) return JSON.parse(raw);
  await rebuild(env); // 首次访问懒重建
  const raw2 = await env.CATALOG.get(KV_KEY);
  return raw2 ? JSON.parse(raw2) : { classes: FIXED_CLASSES, items: [] };
}

async function handleList(q, cat) {
  const wd = (q.get("wd") || "").trim();
  const t = q.get("t") || "";
  const pg = Math.max(1, parseInt(q.get("pg") || "1", 10));
  const limit = 40;

  // ---- 全局搜索：并发搜所有上游，去重汇总 ----
  if (wd) {
    const latest = await jfetch(REPO_LATEST, 15000);
    const apis = (latest?.sites || []).filter((s) => (s.type === 0 || s.type === 1) && s.api).slice(0, 6);
    const found = new Map();
    await Promise.allSettled(
      apis.map(async (site) => {
        const data = await jfetch(`${site.api}${sep(site.api)}ac=list&wd=${encodeURIComponent(wd)}`);
        for (const v of data?.list || []) {
          if (!v?.vod_name || v.vod_id == null) continue;
          const key = normName(v.vod_name);
          if (!key || found.has(key)) continue;
          found.set(key, {
            id: "q:" + site.api + ":" + v.vod_id,
            name: String(v.vod_name),
            pic: String(v.vod_pic || ""),
            remarks: String(v.vod_remarks || "在线"),
            cat: "c_all",
            srcApi: site.api,
            srcName: site.name || shortSrc(site.api),
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

  // ---- 分类浏览 ----
  let items = cat.items || [];
  if (t && t !== "c_all") items = items.filter((x) => x.cat === t);
  const total = items.length;
  const list = items.slice((pg - 1) * limit, pg * limit);
  return json({
    code: 1, msg: "数据列表", page: pg, limit,
    total, pagecount: Math.ceil(total / limit) || 1,
    class: FIXED_CLASSES, list,
  });
}

async function handleDetail(ids, cat) {
  const first = String(ids).split(",")[0];
  let srcApi, oid;
  if (first.startsWith("q:")) {
    const rest = first.slice(2);
    const i = rest.lastIndexOf(":"); // srcApi 是完整 URL，含冒号，必须从右往左切
    srcApi = rest.slice(0, i);
    oid = rest.slice(i + 1); // 搜索结果：直接代理
  } else {
    const item = (cat.items || []).find((x) => x.id === first);
    if (!item) return json({ code: 1, list: [] });
    srcApi = item.srcApi;
    oid = item.src; // 汇总条目里存的原始 vod_id
  }
  if (!srcApi || oid == null) return json({ code: 1, list: [] });

  const data = await jfetch(`${srcApi}${sep(srcApi)}ac=detail&ids=${oid}`);
  const v = data?.list?.[0];
  if (!v) return json({ code: 1, list: [] });
  return json({
    code: 1,
    list: [{
      ...v,
      vod_id: first,
      vod_play_from: `${shortSrc(srcApi)}┃高速`,
    }],
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const q = url.searchParams;

    if (url.pathname === "/refresh") {
      const token = env.REFRESH_TOKEN || "";
      if (token && q.get("key") !== token) return json({ error: "unauthorized" }, 401);
      const r = await rebuild(env);
      return json({ ok: true, ...r });
    }

    if (url.pathname === "/status") {
      const cat = await getCatalog(env);
      return json({ updated: cat.updated, upstreams: cat.upstreams, total: (cat.items || []).length });
    }

    const ac = q.get("ac") || "list";
    const cat = await getCatalog(env);
    if (ac === "detail") return handleDetail(q.get("ids") || "", cat);
    return handleList(q, cat);
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(rebuild(env));
  },
};
