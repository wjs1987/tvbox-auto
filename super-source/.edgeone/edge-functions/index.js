
      let global = globalThis;
      globalThis.global = globalThis;

      if (typeof global.navigator === 'undefined') {
        global.navigator = {
          userAgent: 'edge-runtime',
          language: 'en-US',
          languages: ['en-US'],
        };
      } else {
        if (typeof global.navigator.language === 'undefined') {
          global.navigator.language = 'en-US';
        }
        if (!global.navigator.languages || global.navigator.languages.length === 0) {
          global.navigator.languages = [global.navigator.language];
        }
        if (typeof global.navigator.userAgent === 'undefined') {
          global.navigator.userAgent = 'edge-runtime';
        }
      }

      class MessageChannel {
        constructor() {
          this.port1 = new MessagePort();
          this.port2 = new MessagePort();
        }
      }
      class MessagePort {
        constructor() {
          this.onmessage = null;
        }
        postMessage(data) {
          if (this.onmessage) {
            setTimeout(() => this.onmessage({ data }), 0);
          }
        }
      }
      global.MessageChannel = MessageChannel;

      '__MIDDLEWARE_BUNDLE_CODE__'

      function recreateRequest(request, overrides = {}) {
        const cloned = typeof request.clone === 'function' ? request.clone() : request;
        const headers = new Headers(cloned.headers);

        if (overrides.headerPatches) {
          Object.keys(overrides.headerPatches).forEach((key) => {
            const value = overrides.headerPatches[key];
            if (value === null || typeof value === 'undefined') {
              headers.delete(key);
            } else {
              headers.set(key, value);
            }
          });
        }

        if (overrides.headers) {
          const extraHeaders = new Headers(overrides.headers);
          extraHeaders.forEach((value, key) => headers.set(key, value));
        }

        const url = overrides.url || cloned.url;
        const method = overrides.method || cloned.method || 'GET';
        const canHaveBody = method && method.toUpperCase() !== 'GET' && method.toUpperCase() !== 'HEAD';
        const body = overrides.body !== undefined ? overrides.body : canHaveBody ? cloned.body : undefined;

        // 如果rewrite传入的是完整URL（第三方地址），需要更新host
        if (overrides.url) {
          try {
            const newUrl = new URL(overrides.url, cloned.url);
            // 只有当新URL是绝对路径（包含协议和host）时才更新host
            if (overrides.url.startsWith('http://') || overrides.url.startsWith('https://')) {
              headers.set('host', newUrl.host);
            }
            // 相对路径时保持原有host不变
          } catch (e) {
            // URL解析失败时保持原有host
          }
        }

        const init = {
          method,
          headers,
          redirect: cloned.redirect,
          credentials: cloned.credentials,
          cache: cloned.cache,
          mode: cloned.mode,
          referrer: cloned.referrer,
          referrerPolicy: cloned.referrerPolicy,
          integrity: cloned.integrity,
          keepalive: cloned.keepalive,
          signal: cloned.signal,
        };

        if (canHaveBody && body !== undefined) {
          init.body = body;
        }

        if ('duplex' in cloned) {
          init.duplex = cloned.duplex;
        }

        return new Request(url, init);

      }

      
      async function executeMiddleware(context) {
        return null; // 没有中间件，继续执行后续函数
      }
    

      function usercode(ev, hookCtx) {
        hookCtx = hookCtx || { fetch: globalThis.fetch };
        const { fetch } = hookCtx;
        const globalthis = hookCtx;
        "use strict";
        // ↓ 用户原始代码
        return (async function handleRequest(context) {
          let routeParams = {};
          let pagesFunctionResponse = null;
          let request = context.request;
          const waitUntil = context.waitUntil;
          let urlInfo = new URL(request.url);
          const eo = request.eo || {};


          const normalizePathname = () => {
            if (urlInfo.pathname !== '/' && urlInfo.pathname.endsWith('/')) {
              urlInfo.pathname = urlInfo.pathname.slice(0, -1);
            }
          };

          function getSuffix(pathname = '') {
            // Use a regular expression to extract the file extension from the URL
            const suffix = pathname.match(/\.([^\.]+)$/);
            // If an extension is found, return it, otherwise return an empty string
            return suffix ? '.' + suffix[1] : null;
          }

          normalizePathname();

          let matchedFunc = false;

          
        const runEdgeFunctions = () => {
          
          if(!matchedFunc && '/api' === urlInfo.pathname) {
            matchedFunc = true;
              (() => {
  // functions/api.js
  var DEFAULT_LATEST = "https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/latest.json";
  var LATEST_FALLBACKS = [
    "https://cdn.jsdelivr.net/gh/WJS1987/tvbox-auto@main/latest.json",
    "https://gh-proxy.com/https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/latest.json"
  ];
  var DEFAULT_UPSTREAMS = [
    { name: "\u6DD8\u7247 \u91C7\u96C6", type: 1, api: "https://taopianapi.com/cjapi/mc/vod/json.html" },
    { name: "\u7D22\u5C3C \u9AD8\u6E05", type: 1, api: "https://suoniapi.com/api.php/provide/vod/?ac=list" },
    { name: "\u65B0\u6D6A\u8D44\u6E90(\u5207)", type: 1, api: "http://api.xinlangapi.com/xinlangapi.php/provide/vod/" },
    { name: "\u5149\u901F\u8D44\u6E90(\u5207)", type: 1, api: "https://api.guangsuapi.com/api.php/provide/vod/" },
    { name: "\u975E\u51E1 \u8D44\u6E90", type: 1, api: "http://ffzy1.tv/api.php/provide/vod/" },
    { name: "\u7D22\u5C3C \u8D44\u6E90", type: 1, api: "https://suoniapi.com/api.php/provide/vod/" }
  ];
  var KV_KEY = "catalog";
  var UA = "okhttp/3.12.0";
  var REFRESH_INTERVAL_MS = 20 * 3600 * 1e3;
  var memStore = /* @__PURE__ */ new Map();
  async function kvGet(env, key) {
    try {
      if (env.KV && typeof env.KV.get === "function")
        return await env.KV.get(key, "json");
    } catch {
    }
    return memStore.has(key) ? memStore.get(key) : null;
  }
  async function kvPut(env, key, value) {
    memStore.set(key, value);
    try {
      if (env.KV && typeof env.KV.put === "function")
        await env.KV.put(key, JSON.stringify(value));
    } catch {
    }
  }
  var FIXED_CLASSES = [
    { type_id: "c_all", type_name: "\u5168\u90E8" },
    { type_id: "c_movie", type_name: "\u7535\u5F71" },
    { type_id: "c_tv", type_name: "\u5267\u96C6" },
    { type_id: "c_variety", type_name: "\u7EFC\u827A" },
    { type_id: "c_anime", type_name: "\u52A8\u6F2B" },
    { type_id: "c_doc", type_name: "\u7EAA\u5F55" }
  ];
  var CAT_MAP = [
    [/电影|院线|动作|喜剧|爱情|科幻|恐怖|剧情|战争|犯罪|奇幻|冒险|灾难/, "c_movie"],
    [/剧|连续|国产|港台|韩|美|海外/, "c_tv"],
    [/综艺|娱乐|真人秀|脱口/, "c_variety"],
    [/动漫|动画|番剧|漫画/, "c_anime"],
    [/纪录|纪实|解说/, "c_doc"]
  ];
  function json(data, status = 200) {
    return new Response(JSON.stringify(data), {
      status,
      headers: { "Content-Type": "application/json; charset=utf-8" }
    });
  }
  async function jfetch(url, ms = 8e3, maxBytes = 15e5) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), ms);
    try {
      const r = await fetch(url, { headers: { "User-Agent": UA }, signal: ctl.signal });
      if (!r.ok)
        return null;
      const txt = (await r.text()).slice(0, maxBytes);
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
    for (let i = 0; i < s.length; i++)
      h = h * 131 + s.charCodeAt(i) >>> 0;
    return "m" + h.toString(36);
  }
  function getUpstreams(env, latest, n) {
    return (latest?.sites || []).filter((s) => (s.type === 0 || s.type === 1) && s.api).slice(0, n);
  }
  async function fetchLatest(env) {
    const urls = [env.LATEST_URL || DEFAULT_LATEST, ...LATEST_FALLBACKS];
    for (const u of urls) {
      const d = await jfetch(u, 12e3);
      if (d?.sites?.length)
        return d;
    }
    return null;
  }
  async function rebuild(env) {
    const latestUrl = env.LATEST_URL || DEFAULT_LATEST;
    const maxUp = parseInt(env.MAX_UPSTREAMS || "6", 10);
    const pages = parseInt(env.PAGES_PER_SOURCE || "2", 10);
    const latest = await fetchLatest(env);
    const upstreams = latest ? getUpstreams(env, latest, maxUp) : DEFAULT_UPSTREAMS.slice(0, maxUp);
    if (!upstreams.length)
      throw new Error("\u65E0\u53EF\u7528\u4E0A\u6E38\u6E90");
    const tasks = upstreams.map(async (site) => {
      const api = site.api;
      const out = { name: site.name || shortSrc(api), api, cats: /* @__PURE__ */ new Map(), items: [] };
      const cls = await jfetch(`${api}${sep(api)}ac=list`);
      if (cls && Array.isArray(cls.class)) {
        for (const c of cls.class) {
          if (c?.type_id != null)
            out.cats.set(String(c.type_id), String(c.type_name || ""));
        }
      }
      for (let pg = 1; pg <= pages; pg++) {
        const data = await jfetch(`${api}${sep(api)}ac=list&pg=${pg}`);
        const list = data?.list;
        if (!Array.isArray(list) || !list.length)
          break;
        for (const v of list) {
          if (!v?.vod_name || v.vod_id == null)
            continue;
          out.items.push({
            oid: String(v.vod_id),
            name: String(v.vod_name),
            pic: String(v.vod_pic || ""),
            remarks: String(v.vod_remarks || ""),
            // 优先用条目自带的 type_name（如"电视剧/台湾剧"），其次查分类表
            cat: String(v.type_name || out.cats.get(String(v.type_id)) || "")
          });
        }
        if (data.pagecount && pg >= Number(data.pagecount))
          break;
      }
      return out;
    });
    const results = await Promise.allSettled(tasks);
    const seen = /* @__PURE__ */ new Map();
    for (const r of results) {
      if (r.status !== "fulfilled")
        continue;
      for (const it of r.value.items) {
        const key = normName(it.name);
        if (!key || seen.has(key))
          continue;
        let cid = "c_all";
        for (const [re, id] of CAT_MAP) {
          if (re.test(it.cat || "") || re.test(it.name || "")) {
            cid = id;
            break;
          }
        }
        seen.set(key, {
          id: hashId(key),
          name: it.name,
          pic: it.pic,
          remarks: it.remarks,
          cat: cid,
          src: it.oid,
          srcApi: r.value.api,
          srcName: r.value.name
        });
      }
    }
    const catalog = {
      updated: (/* @__PURE__ */ new Date()).toISOString(),
      upstreams: upstreams.map((s) => s.name || shortSrc(s.api)),
      classes: FIXED_CLASSES,
      items: [...seen.values()]
    };
    await kvPut(env, KV_KEY, catalog);
    return catalog;
  }
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
    if (wd) {
      const latest = await fetchLatest(env);
      const apis = latest ? getUpstreams(env, latest, 6) : DEFAULT_UPSTREAMS.slice(0, 6);
      const found = /* @__PURE__ */ new Map();
      await Promise.allSettled(
        apis.map(async (site) => {
          const data = await jfetch(`${site.api}${sep(site.api)}ac=list&wd=${encodeURIComponent(wd)}`);
          for (const v of data?.list || []) {
            if (!v?.vod_name || v.vod_id == null)
              continue;
            const key = normName(v.vod_name);
            if (!key || found.has(key))
              continue;
            found.set(key, {
              id: "q:" + site.api + ":" + v.vod_id,
              name: String(v.vod_name),
              pic: String(v.vod_pic || ""),
              remarks: String(v.vod_remarks || "\u5728\u7EBF"),
              cat: "c_all",
              srcApi: site.api
            });
          }
        })
      );
      const list = [...found.values()].slice((pg - 1) * limit, pg * limit);
      return json({
        code: 1,
        msg: "\u6570\u636E\u5217\u8868",
        page: pg,
        limit,
        total: found.size,
        pagecount: Math.ceil(found.size / limit) || 1,
        class: FIXED_CLASSES,
        list
      });
    }
    let items = cat.items || [];
    if (t && t !== "c_all")
      items = items.filter((x) => x.cat === t);
    const total = items.length;
    return json({
      code: 1,
      msg: "\u6570\u636E\u5217\u8868",
      page: pg,
      limit,
      total,
      pagecount: Math.ceil(total / limit) || 1,
      class: cat.classes || FIXED_CLASSES,
      list: items.slice((pg - 1) * limit, pg * limit)
    });
  }
  async function handleDetail(ids, cat) {
    const first = String(ids).split(",")[0];
    let srcApi, oid;
    if (first.startsWith("q:")) {
      const rest = first.slice(2);
      const i = rest.lastIndexOf(":");
      srcApi = rest.slice(0, i);
      oid = rest.slice(i + 1);
    } else {
      const item = (cat.items || []).find((x) => x.id === first);
      if (!item)
        return json({ code: 1, list: [] });
      srcApi = item.srcApi;
      oid = item.src;
    }
    if (!srcApi || oid == null)
      return json({ code: 1, list: [] });
    const data = await jfetch(`${srcApi}${sep(srcApi)}ac=detail&ids=${oid}`);
    const v = data?.list?.[0];
    if (!v)
      return json({ code: 1, list: [] });
    return json({
      code: 1,
      list: [{ ...v, vod_id: first, vod_play_from: `${shortSrc(srcApi)}\u2503\u9AD8\u901F` }]
    });
  }
  async function onRequest({ request, env }) {
    const url = new URL(request.url);
    const q = url.searchParams;
    const mode = q.get("mode") || "";
    try {
      if (mode === "refresh") {
        const token = env.REFRESH_TOKEN || "";
        if (token && q.get("key") !== token)
          return json({ error: "unauthorized" }, 401);
        const cat2 = await rebuild(env);
        return json({ ok: true, total: cat2.items.length, upstreams: cat2.upstreams });
      }
      if (mode === "status") {
        const cat2 = await getCatalog(env);
        return json({ updated: cat2.updated, upstreams: cat2.upstreams, total: (cat2.items || []).length });
      }
      const cat = await getCatalog(env);
      const ac = q.get("ac") || "list";
      if (ac === "detail")
        return await handleDetail(q.get("ids") || "", cat);
      return await handleList(q, env, cat);
    } catch (e) {
      return json({ error: String(e && e.message || e) }, 500);
    }
  }

        pagesFunctionResponse = onRequest;
      })();
          }
        
        };
      

          
        const runMiddleware = typeof executeMiddleware !== 'undefined' ? executeMiddleware : async function() { return null; };
        let middlewareResponseHeaders = null; // 保存中间件设置的响应头
        const middlewareResponse = await runMiddleware({
          request,
          urlInfo: new URL(urlInfo.toString()),
          env: {"ProjectId":"makers-igzvfvnyc4mx","NG_CLI_ANALYTICS":"false","NUXT_TELEMETRY_DISABLED":"1","COREPACK_ENABLE_DOWNLOAD_PROMPT":"0","COREPACK_ENABLE_STRICT":"0","YARN_ENABLE_INTERACTIVE":"0","NPM_CONFIG_YES":"true","CI":"true","TMPDIR":"C:\\Users\\86186\\AppData\\Local\\Temp","EDGEONE_PROJECT_ID":"makers-igzvfvnyc4mx","PAGES_PROJECT_ID":"makers-igzvfvnyc4mx"},
          waitUntil,
          hookCtx
        });

        if (middlewareResponse) {
          const headers = middlewareResponse.headers;
          const hasNext = headers && headers.get('x-middleware-next') === '1';
          const rewriteTarget = headers && headers.get('x-middleware-rewrite');
          const requestHeadersOverride = headers && headers.get('x-middleware-request-headers');
          // Next.js 使用 x-middleware-override-headers 传递需要修改的请求头列表
          const overrideHeadersList = headers && headers.get('x-middleware-override-headers');

          if (rewriteTarget) {
            try {
              const rewrittenUrl = rewriteTarget.startsWith('http://') || rewriteTarget.startsWith('https://')
                ? rewriteTarget
                : new URL(rewriteTarget, urlInfo.origin).toString();
              request = recreateRequest(request, { url: rewrittenUrl });
              urlInfo = new URL(rewrittenUrl);
              normalizePathname();
            } catch (rewriteError) {
              console.error('Middleware rewrite error:', rewriteError);
            }
          }

          // 处理 Next.js 的 x-middleware-override-headers 机制
          if (overrideHeadersList) {
            try {
              const overrideKeys = overrideHeadersList.split(',').map(k => k.trim());
              for (const key of overrideKeys) {
                const newValue = headers.get('x-middleware-request-' + key);
                if (newValue !== null) {
                  request.headers.set(key, newValue);
                } else {
                  request.headers.delete(key);
                }
              }
            } catch (overrideError) {
              console.error('Middleware override headers error:', overrideError);
            }
          }
          // 处理旧的 x-middleware-request-headers 机制（兼容）
          else if (requestHeadersOverride) {
            try {
              const decoded = decodeURIComponent(requestHeadersOverride);
              const headerPatch = JSON.parse(decoded);
              Object.keys(headerPatch).forEach((key) => {
                const value = headerPatch[key];
                if (value === null || typeof value === 'undefined') {
                  request.headers.delete(key);
                } else {
                  request.headers.set(key, value);
                }
              });
            } catch (requestPatchError) {
              console.error('Middleware request header override error:', requestPatchError);
            }
          }

          if (!hasNext && !rewriteTarget) {
            return middlewareResponse;
          }

          if (hasNext || rewriteTarget) {
            middlewareResponseHeaders = new Headers();
            const skipHeaders = new Set([
              'x-middleware-next',
              'x-middleware-rewrite',
              'x-middleware-request-headers',
              'x-middleware-override-headers',
              'x-middleware-set-cookie',
              'date',
              'connection',
              'content-length',
              'content-encoding', // 避免中间件传递的压缩头覆盖到最终响应，破坏流式响应
              'transfer-encoding',
              'set-cookie', // Set-Cookie 需要特殊处理，避免重复
            ]);
            headers.forEach((value, key) => {
              const lowerKey = key.toLowerCase();
              // 过滤内部使用的 header：skipHeaders 中的 + x-middleware-request-* 前缀的请求头修改标记
              if (!skipHeaders.has(lowerKey) && !lowerKey.startsWith('x-middleware-request-')) {
                middlewareResponseHeaders.set(key, value);
              }
            });
            // 特殊处理 Set-Cookie，可能有多个，使用 getSetCookie 获取完整的 cookie 值
            const setCookies = headers.getSetCookie ? headers.getSetCookie() : [];
            setCookies.forEach(cookie => {
              middlewareResponseHeaders.append('Set-Cookie', cookie);
            });
          }
        }
      

          // 走到这里说明：
          // 1. 没有中间件响应（middlewareResponse 为 null/undefined）
          // 2. 或者中间件返回了 next
          // 需要判断是否命中边缘函数

          runEdgeFunctions();

          // 动态路由命中时，检查该路径的 runtime 是否为 edge
          // 如果不是 edge（如 node/file），则跳出边缘函数，走回源逻辑
          if (matchedFunc && routeParams.mode > 0 && hookCtx && hookCtx.getPathRuntime) {
            try {
              const pathRuntime = await hookCtx.getPathRuntime(urlInfo.pathname);
              if (pathRuntime && pathRuntime !== 'edge') {
                matchedFunc = false;
              }
            } catch(e) {
              // getPathRuntime 调用失败时不阻断，继续执行边缘函数
            }
          }

          //没有命中边缘函数，执行回源
          if (!matchedFunc) {
            const originResponse = await fetch(request);

            // 如果中间件设置了响应头，合并到回源响应中
            if (middlewareResponseHeaders) {
              const mergedHeaders = new Headers(originResponse.headers);
              // 删除可能导致问题的编码相关头
              mergedHeaders.delete('content-encoding');
              mergedHeaders.delete('content-length');
              middlewareResponseHeaders.forEach((value, key) => {
                if (key.toLowerCase() === 'set-cookie') {
                  mergedHeaders.append(key, value);
                } else {
                  mergedHeaders.set(key, value);
                }
              });
              return new Response(originResponse.body, {
                status: originResponse.status,
                statusText: originResponse.statusText,
                headers: mergedHeaders,
              });
            }

            return originResponse;
          }

          // 命中了边缘函数，继续执行边缘函数逻辑

          const params = {};
          if (routeParams.id) {
            if (routeParams.mode === 1) {
              const value = urlInfo.pathname.match(routeParams.left);
              for (let i = 1; i < value.length; i++) {
                params[routeParams.id[i - 1]] = value[i];
              }
            } else {
              const value = urlInfo.pathname.replace(routeParams.left, '');
              const splitedValue = value.split('/');
              if (splitedValue.length === 1) {
                params[routeParams.id] = splitedValue[0];
              } else {
                params[routeParams.id] = splitedValue;
              }
            }

          }
          const edgeFunctionResponse = await pagesFunctionResponse({request, params, env: {"ProjectId":"makers-igzvfvnyc4mx","NG_CLI_ANALYTICS":"false","NUXT_TELEMETRY_DISABLED":"1","COREPACK_ENABLE_DOWNLOAD_PROMPT":"0","COREPACK_ENABLE_STRICT":"0","YARN_ENABLE_INTERACTIVE":"0","NPM_CONFIG_YES":"true","CI":"true","TMPDIR":"C:\\Users\\86186\\AppData\\Local\\Temp","EDGEONE_PROJECT_ID":"makers-igzvfvnyc4mx","PAGES_PROJECT_ID":"makers-igzvfvnyc4mx"}, waitUntil, eo });

          // 如果中间件设置了响应头，合并到边缘函数响应中
          if (middlewareResponseHeaders && edgeFunctionResponse) {
            const mergedHeaders = new Headers(edgeFunctionResponse.headers);
            // 删除可能导致问题的编码相关头
            mergedHeaders.delete('content-encoding');
            mergedHeaders.delete('content-length');
            middlewareResponseHeaders.forEach((value, key) => {
              if (key.toLowerCase() === 'set-cookie') {
                mergedHeaders.append(key, value);
              } else {
                mergedHeaders.set(key, value);
              }
            });
            return new Response(edgeFunctionResponse.body, {
              status: edgeFunctionResponse.status,
              statusText: edgeFunctionResponse.statusText,
              headers: mergedHeaders,
            });
          }

          return edgeFunctionResponse;
        })({request: ev.request, params: {}, env: {"ProjectId":"makers-igzvfvnyc4mx","NG_CLI_ANALYTICS":"false","NUXT_TELEMETRY_DISABLED":"1","COREPACK_ENABLE_DOWNLOAD_PROMPT":"0","COREPACK_ENABLE_STRICT":"0","YARN_ENABLE_INTERACTIVE":"0","NPM_CONFIG_YES":"true","CI":"true","TMPDIR":"C:\\Users\\86186\\AppData\\Local\\Temp","EDGEONE_PROJECT_ID":"makers-igzvfvnyc4mx","PAGES_PROJECT_ID":"makers-igzvfvnyc4mx"}, waitUntil: ev.waitUntil.bind(ev) });
        // ↑ 用户原始代码结束
      }

      addEventListener('fetch', (event, hookCtx) => {
        const res = usercode(event, hookCtx);
        event.respondWith(res);
      });