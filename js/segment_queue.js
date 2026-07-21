import { app } from "../../scripts/app.js";

const THUMB_URL = "/sqr/image_thumb?file=";

function sqrThumbUrl(path) {
    const sep = THUMB_URL.includes("?") ? "&" : "?";
    return THUMB_URL + encodeURIComponent(path) + sep + "_ts=" + Date.now() + "_r=" + Math.random().toString(36).slice(2, 8);
}

function sqrParseRefGroups(value) {
    const raw = String(value || "").trim();
    if (!raw) return [];
    const normEntry = (v) => {
        if (v && typeof v === "object" && !Array.isArray(v)) {
            const path = String(v.path || v.image || v.file || "").trim();
            return path ? { path, bg: !!(v.bg || v.background || v.is_bg) } : null;
        }
        const path = String(v || "").trim();
        return path || null;
    };
    if (raw.startsWith("[")) {
        try {
            const parsed = JSON.parse(raw);
            if (Array.isArray(parsed)) {
                if (parsed.some(v => Array.isArray(v))) {
                    return parsed.map(group => Array.isArray(group) ? group : [group])
                        .map(group => group.map(normEntry).filter(Boolean))
                        .filter(group => group.length);
                }
                const flat = parsed.map(normEntry).filter(Boolean);
                return flat.length ? [flat] : [];
            }
        } catch (e) {
            console.warn("[SQR] Failed to parse reference image JSON; using legacy format:", e);
        }
    }
    const legacyMatches = [...raw.matchAll(/(?:^|,)\s*(.*?\.(?:png|jpe?g|webp|bmp))(?=,|$)/gi)]
        .map(match => match[1]?.trim())
        .filter(Boolean);
    const flat = legacyMatches.length ? legacyMatches : raw.split(",").map(v => v.trim()).filter(Boolean);
    return flat.length ? [flat] : [];
}

function sqrRefPath(entry) {
    if (entry && typeof entry === "object") return String(entry.path || entry.image || entry.file || "").trim();
    return String(entry || "").trim();
}

function sqrRefIsBg(entry) {
    return !!(entry && typeof entry === "object" && (entry.bg || entry.background || entry.is_bg));
}

function sqrRefEntry(path, bg=false) {
    path = String(path || "").trim();
    if (!path) return null;
    return bg ? { path, bg: true } : path;
}

function sqrFlattenRefGroups(groups) {
    return (groups || []).flatMap(group => Array.isArray(group) ? group : [group]).map(sqrRefPath).filter(Boolean);
}

function sqrParseRefPaths(value) {
    return sqrFlattenRefGroups(sqrParseRefGroups(value));
}

function sqrStoreRefGroups(groups) {
    const cleanEntry = (v) => {
        const path = sqrRefPath(v);
        if (!path) return null;
        return sqrRefIsBg(v) ? { path, bg: true } : path;
    };
    const cleaned = (groups || [])
        .map(group => (Array.isArray(group) ? group : [group]).map(cleanEntry).filter(Boolean))
        .filter(group => group.length);
    if (cleaned.length <= 1) return JSON.stringify(cleaned[0] || []);
    return JSON.stringify(cleaned);
}

function sqrStoreRefPaths(paths) {
    return sqrStoreRefGroups([(paths || []).map(v => String(v).trim()).filter(Boolean)]);
}

// ── Remote environment detection ─────────────────────────────────
function _sqrIsRemote() {
    const h = window.location.hostname;
    return h !== "localhost" && h !== "127.0.0.1" && h !== "::1";
}

/**
 * Remote mode: select images with the browser file picker and upload them to input/.
 * Returns Promise<string[]> of saved names relative to input/.
 */
function _sqrPickAndUploadImages() {
    return new Promise((resolve) => {
        const inp = document.createElement("input");
        inp.type = "file";
        inp.accept = "image/png,image/jpeg,image/webp,image/bmp,.png,.jpg,.jpeg,.webp,.bmp";
        inp.multiple = true;
        inp.style.display = "none";
        document.body.appendChild(inp);
        inp.onchange = async () => {
            document.body.removeChild(inp);
            const files = [...inp.files];
            if (!files.length) { resolve([]); return; }
            const prog = _sqrUploadProgressUI(`Uploading ${files.length} image(s)...`);
            try {
                const fd = new FormData();
                files.forEach(f => fd.append("files[]", f, f.name));
                const resp = await fetch("/sqr/upload_images", { method: "POST", body: fd });
                const data = await resp.json();
                prog.remove();
                if (data.error) { alert(`Upload error: ${data.error}`); resolve([]); return; }
                resolve(data.saved || []);
            } catch (e) {
                prog.remove();
                alert(`Upload failed: ${e.message}`);
                resolve([]);
            }
        };
        inp.oncancel = () => { document.body.removeChild(inp); resolve([]); };
        inp.click();
    });
}

/**
 * Remote mode: select a video with the browser file picker and upload it to input/.
 * Returns Promise<string> of the saved name, or "".
 */
function _sqrPickAndUploadVideo() {
    return new Promise((resolve) => {
        const inp = document.createElement("input");
        inp.type = "file";
        inp.accept = "video/mp4,video/quicktime,video/x-msvideo,video/webm,.mp4,.mov,.avi,.mkv,.webm";
        inp.multiple = false;
        inp.style.display = "none";
        document.body.appendChild(inp);
        inp.onchange = async () => {
            document.body.removeChild(inp);
            const file = inp.files[0];
            if (!file) { resolve(""); return; }
            const prog = _sqrUploadProgressUI(`Uploading video: ${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)...`);
            try {
                const fd = new FormData();
                fd.append("file", file, file.name);
                const resp = await fetch("/sqr/upload_video", { method: "POST", body: fd });
                const data = await resp.json();
                prog.remove();
                if (data.error) { alert(`Upload error: ${data.error}`); resolve(""); return; }
                resolve(data.saved || "");
            } catch (e) {
                prog.remove();
                alert(`Upload failed: ${e.message}`);
                resolve("");
            }
        };
        inp.oncancel = () => { document.body.removeChild(inp); resolve(""); };
        inp.click();
    });
}

/** Full-screen upload progress overlay. */
function _sqrUploadProgressUI(msg) {
    if (!document.getElementById("sqr-spin-style")) {
        const st = document.createElement("style");
        st.id = "sqr-spin-style";
        st.textContent = "@keyframes sqr-spin{to{transform:rotate(360deg)}}";
        document.head.appendChild(st);
    }
    const el = document.createElement("div");
    Object.assign(el.style, {
        position: "fixed", inset: "0", zIndex: "20000",
        background: "rgba(0,0,0,.65)",
        display: "flex", alignItems: "center", justifyContent: "center",
        flexDirection: "column", gap: "16px",
        color: "#fff", fontSize: "15px", fontWeight: "600",
    });
    const spinner = document.createElement("div");
    spinner.style.cssText = "width:44px;height:44px;border:4px solid rgba(255,255,255,.2);border-top-color:#4cf;border-radius:50%;animation:sqr-spin 0.8s linear infinite;";
    el.append(spinner, Object.assign(document.createElement("div"), { textContent: msg }));
    document.body.appendChild(el);
    return el;
}

// ── SQR 上游节点收集 ──────────────────────────────────────────────
function _sqrCollectUpstream(nodeId, promptOutput, visited) {
    if (visited.has(nodeId)) return;
    visited.add(nodeId);
    const node = promptOutput[nodeId];
    if (!node) return;
    for (const val of Object.values(node.inputs || {})) {
        if (Array.isArray(val) && val.length === 2) {
            const srcId = String(val[0]);
            if (promptOutput[srcId]) {
                _sqrCollectUpstream(srcId, promptOutput, visited);
            }
        }
    }
}


// ── Node ID setup dialog ─────────────────────────────────────────
function showNodeIdSelector(fields, onConfirm) {
    document.getElementById("sqr-nodeid-overlay")?.remove();
    const overlay=document.createElement("div");
    overlay.id="sqr-nodeid-overlay";
    Object.assign(overlay.style,{position:"fixed",inset:"0",zIndex:"10000",
        background:"rgba(0,0,0,.75)",display:"flex",alignItems:"center",justifyContent:"center"});
    const box=document.createElement("div");
    Object.assign(box.style,{background:"var(--comfy-menu-bg,#1e1e1e)",color:"var(--input-text,#eee)",
        border:"1px solid var(--border-color,#444)",borderRadius:"12px",
        padding:"20px 24px",width:"480px",
        display:"flex",flexDirection:"column",gap:"12px",
        boxShadow:"0 8px 40px rgba(0,0,0,.7)"});
    const mkDiv=(t,s)=>Object.assign(document.createElement("div"),{textContent:t,style:s||""});
    box.appendChild(mkDiv("Node IDs","font-size:14px;font-weight:600;"));
    box.appendChild(mkDiv("Enable node ID labels in ComfyUI settings if the IDs are hidden.","font-size:11px;opacity:.5;line-height:1.5;"));

    const inputs={};
    fields.forEach(({key,label,tooltip,value})=>{
        const row=document.createElement("div");
        row.style.cssText="display:flex;align-items:center;gap:10px;";
        const lbl=document.createElement("label");
        lbl.textContent=label; lbl.title=tooltip||"";
        lbl.style.cssText="font-size:12px;min-width:180px;flex-shrink:0;cursor:help;";
        const inp=document.createElement("input");
        inp.type="text"; inp.value=value||"";
        inp.style.cssText="flex:1;padding:5px 8px;border-radius:5px;border:1px solid var(--border-color,#555);background:var(--comfy-input-bg,#333);color:var(--input-text,#eee);font-size:12px;";
        inp.placeholder="Node ID";
        inputs[key]=inp; row.append(lbl,inp); box.appendChild(row);
    });

    const btns=document.createElement("div"); btns.style.cssText="display:flex;gap:8px;margin-top:4px;";
    const mkBtn=(t,s,fn)=>{const b=Object.assign(document.createElement("button"),{textContent:t});
        b.style.cssText=`flex:1;padding:6px 18px;border-radius:6px;cursor:pointer;${s}`;b.onclick=fn;return b;};
    btns.append(
        mkBtn("Cancel","",()=>overlay.remove()),
        mkBtn("Apply","background:#2a9;color:#fff;border:none;font-weight:600;",()=>{
            const result={};
            fields.forEach(({key})=>{result[key]=inputs[key]?.value||"";});
            onConfirm(result); overlay.remove();
        })
    );
    box.appendChild(btns);
    const _xBtn = document.createElement("button");
    _xBtn.textContent = "×";
    _xBtn.style.cssText = "position:absolute;top:10px;right:12px;background:none;border:none;font-size:20px;cursor:pointer;color:var(--input-text,#aaa);line-height:1;padding:0;";
    _xBtn.onmouseover = () => _xBtn.style.color = "#fff";
    _xBtn.onmouseout  = () => _xBtn.style.color = "var(--input-text,#aaa)";
    _xBtn.onclick = () => overlay.remove();
    box.style.position = "relative";
    box.appendChild(_xBtn);
    overlay.appendChild(box);
    overlay.onclick=e=>{if(e.target===overlay)overlay.remove();};
    document.body.appendChild(overlay);
}

// ── 仅保留平均分段：手动分段相关 UI 已移除 ───────────────────────────

// ── 注册扩展 ──────────────────────────────────────────────────────
async function _showPreSegmentDialog(sqrNode, onConfirm) {
return new Promise(resolve => {
    document.getElementById("sqr-preseg-overlay")?.remove();
    let selPaths = [];
    let dragSrcIdx = -1;

    const overlay = document.createElement("div");
    overlay.id = "sqr-preseg-overlay";
    Object.assign(overlay.style, {
        position:"fixed",inset:"0",zIndex:"10000",
        background:"rgba(0,0,0,.8)",display:"flex",alignItems:"center",justifyContent:"center"
    });
    const box = document.createElement("div");
    Object.assign(box.style, {
        background:"var(--comfy-menu-bg,#1e1e1e)",color:"var(--input-text,#eee)",
        border:"1px solid var(--border-color,#444)",borderRadius:"12px",
        padding:"20px 24px",width:"620px",maxHeight:"88vh",
        display:"flex",flexDirection:"column",gap:"8px",
        boxShadow:"0 8px 40px rgba(0,0,0,.7)"
    });
    const mkDiv=(t,s)=>Object.assign(document.createElement("div"),{textContent:t,style:s||""});
    box.appendChild(mkDiv("Resume Merge: Select Existing Clips","font-size:14px;font-weight:700;"));
    box.appendChild(mkDiv("Click videos to add them below. Drag to reorder, right-click to remove. The final video will be merged in this order.","font-size:11px;opacity:.6;"));

    const pathBar = document.createElement("div");
    Object.assign(pathBar.style, {
        fontSize:"11px",opacity:".6",padding:"4px 0",minHeight:"18px",
        borderBottom:"1px solid var(--border-color,#444)",marginBottom:"2px",
        display:"flex",alignItems:"center",gap:"4px",flexWrap:"wrap"
    });
    box.appendChild(pathBar);

    const selArea = document.createElement("div");
    Object.assign(selArea.style, {
        border:"1px solid var(--border-color,#444)",borderRadius:"8px",padding:"6px",
        minHeight:"52px",maxHeight:"140px",overflowY:"auto",
        display:"flex",flexWrap:"wrap",gap:"6px",alignItems:"flex-start"
    });

    function renderSel() {
        selArea.innerHTML = "";
        if (!selPaths.length) {
            selArea.appendChild(mkDiv("(None selected; the resumed result will be merged separately)","opacity:.35;font-size:11px;padding:4px;"));
            return;
        }
        selPaths.forEach((p, i) => {
            const card = document.createElement("div");
            Object.assign(card.style, { width:"72px",cursor:"grab",userSelect:"none",display:"flex",flexDirection:"column",alignItems:"center",gap:"2px",border:"1px solid var(--border-color,#555)",borderRadius:"6px",padding:"4px",background:"var(--comfy-input-bg,#2a2a2a)",position:"relative",fontSize:"10px" });
            const badge = mkDiv(String(i+1),"position:absolute;top:2px;left:2px;background:rgba(50,150,70,0.9);color:#fff;font-weight:700;font-size:9px;padding:0 4px;border-radius:3px;");
            const img = document.createElement("img"); img.src = `/sqr/video_thumb?file=${encodeURIComponent(p)}`; img.style.cssText = "width:64px;height:44px;object-fit:cover;border-radius:3px;"; img.draggable = false; img.onerror = () => { img.style.display="none"; };
            const name = mkDiv(p.split(/[/\\]/).pop(),"width:64px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:center;opacity:.7;"); name.title = p;
            card.append(badge, img, name); card.draggable = true;
            card.ondragstart = () => { dragSrcIdx = i; card.style.opacity=".4"; }; card.ondragend = () => { card.style.opacity="1"; };
            card.ondragover = e => { e.preventDefault(); card.style.borderColor="#4c6"; }; card.ondragleave = () => { card.style.borderColor="var(--border-color,#555)"; };
            card.ondrop = e => { e.preventDefault(); card.style.borderColor="var(--border-color,#555)"; if (dragSrcIdx >= 0 && dragSrcIdx !== i) { const [m] = selPaths.splice(dragSrcIdx, 1); selPaths.splice(i, 0, m); renderSel(); } };
            card.oncontextmenu = e => { e.preventDefault(); selPaths.splice(i,1); renderSel(); };
            selArea.appendChild(card);
        });
    }

    const browserWrap = document.createElement("div");
    Object.assign(browserWrap.style, { display:"grid",gridTemplateColumns:"repeat(auto-fill, minmax(90px,1fr))",gap:"6px",border:"1px solid var(--border-color,#444)",borderRadius:"8px",padding:"8px",maxHeight:"300px",overflowY:"auto",minHeight:"80px",alignContent:"flex-start" });
    box.appendChild(browserWrap);
    box.appendChild(mkDiv("Selected clips: drag to reorder, right-click to remove","font-size:11px;opacity:.5;margin-top:2px;"));
    box.appendChild(selArea); renderSel();

    async function loadDir(path) {
        browserWrap.innerHTML = '<div style="opacity:.5;font-size:12px;padding:8px;grid-column:1/-1;">Loading...</div>'; pathBar.innerHTML = "";
        try {
            const url = path ? `/sqr/browse_videos?path=${encodeURIComponent(path)}` : "/sqr/browse_videos";
            const data = await (await fetch(url)).json();
            if (data.type === "dir" || data.type === "roots") { const rootBtn = mkDiv("🏠","cursor:pointer;padding:2px 6px;border-radius:4px;background:var(--comfy-input-bg,#333);"); rootBtn.onclick=()=>loadDir(null); pathBar.appendChild(rootBtn);
                if (data.type === "dir") { pathBar.appendChild(mkDiv("›","opacity:.4;")); const sep = data.path.includes("\\") ? "\\" : "/"; let acc = data.path.match(/^[A-Za-z]:\\/)?.[0] || "/"; const parts = data.path.split(sep).filter(Boolean).slice(data.path.startsWith("/")?0:1);
                    parts.forEach((part,i) => { acc = acc + (acc.endsWith(sep)?"":sep) + part; const snap=acc; const b=mkDiv(part,"cursor:pointer;padding:2px 6px;border-radius:4px;background:var(--comfy-input-bg,#333);max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"); b.onclick=()=>loadDir(snap); pathBar.appendChild(b); if(i<parts.length-1) pathBar.appendChild(mkDiv("›","opacity:.4;")); }); } }
            browserWrap.innerHTML = ""; browserWrap.style.display = "grid";
            if (data.type === "roots") { data.roots.forEach(({label,path:p,is_drive})=>{ const icon = (p === "__drives__" || is_drive) ? "🖥" : "📁"; const row=document.createElement("div"); row.style.cssText="grid-column:1/-1;display:flex;align-items:center;gap:8px;padding:6px;cursor:pointer;border-radius:5px;font-size:12px;"; row.innerHTML=`<span>${icon}</span><span>${label}</span>`; row.onclick=()=>loadDir(p); row.onmouseover=()=>row.style.background="var(--comfy-input-bg,#333)"; row.onmouseout=()=>row.style.background=""; browserWrap.appendChild(row); });
            } else {
                if (data.parent) { const row=document.createElement("div"); row.style.cssText="grid-column:1/-1;display:flex;align-items:center;gap:8px;padding:6px;cursor:pointer;border-radius:5px;font-size:12px;"; row.innerHTML="<span>📁</span><span>.. Parent folder</span>"; row.onclick=()=>loadDir(data.parent); row.onmouseover=()=>row.style.background="var(--comfy-input-bg,#333)"; row.onmouseout=()=>row.style.background=""; browserWrap.appendChild(row); }
                data.folders.forEach(f=>{ const fp=(data.path.endsWith("/")||data.path.endsWith("\\"))?data.path+f:data.path+"/"+f; const row=document.createElement("div"); row.style.cssText="grid-column:1/-1;display:flex;align-items:center;gap:8px;padding:6px;cursor:pointer;border-radius:5px;font-size:12px;"; row.innerHTML=`<span>📁</span><span>${f}</span>`; row.onclick=()=>loadDir(fp); row.onmouseover=()=>row.style.background="var(--comfy-input-bg,#333)"; row.onmouseout=()=>row.style.background=""; browserWrap.appendChild(row); });
                if (!data.videos.length && !data.folders.length) { browserWrap.appendChild(mkDiv("(No video files or folders here)","opacity:.4;font-size:12px;padding:8px;grid-column:1/-1;")); } else if (!data.videos.length) { browserWrap.appendChild(mkDiv("(No videos here; open a folder)","opacity:.4;font-size:12px;padding:4px;grid-column:1/-1;")); }
                data.videos.forEach(f=>{ const fp=(data.path.endsWith("/")||data.path.endsWith("\\"))?data.path+f:data.path+"/"+f; const alreadySel = selPaths.includes(fp);
                    const card=document.createElement("div"); Object.assign(card.style,{ cursor:"pointer",border: alreadySel?"2px solid #4a6":"1px solid var(--border-color,#555)",borderRadius:"6px",padding:"6px 8px",background:"var(--comfy-input-bg,#2a2a2a)",display:"flex",flexDirection:"row",alignItems:"center",gap:"8px",fontSize:"11px",opacity: alreadySel?"0.55":"1",gridColumn:"1/-1" });
                    const img=document.createElement("img"); img.src=`/sqr/video_thumb?file=${encodeURIComponent(fp)}`; img.style.cssText="width:72px;height:48px;object-fit:cover;border-radius:4px;flex-shrink:0;"; img.draggable=false; img.onerror=()=>{img.style.display="none";};
                    const nmWrap=document.createElement("div"); nmWrap.style.cssText="flex:1;overflow:hidden;"; const nm=mkDiv(f,"font-size:11px;opacity:.9;word-break:break-word;overflow-wrap:anywhere;line-height:1.4;"); nm.title=fp; nmWrap.appendChild(nm); card.append(img,nmWrap);
                    card.onclick=()=>{ if (!selPaths.includes(fp)) { selPaths.push(fp); card.style.border="2px solid #4a6"; card.style.opacity="0.55"; } renderSel(); }; browserWrap.appendChild(card); });
            }
        } catch(e) { browserWrap.innerHTML=`<div style="opacity:.5;font-size:12px;padding:8px;grid-column:1/-1;">Failed to load: ${e.message}</div>`; }
    }

    const btns=document.createElement("div"); btns.style.cssText="display:flex;gap:8px;margin-top:4px;";
    const mkBtn=(t,s,fn)=>{const b=document.createElement("button");b.textContent=t;b.style.cssText=`flex:1;padding:7px 18px;border-radius:7px;cursor:pointer;font-size:13px;${s}`;b.onclick=fn;return b;};
    btns.append(
        mkBtn("Disable Resume","background:rgba(180,60,60,0.2);border:1px solid rgba(200,80,80,0.5);color:#f88;",()=>{
            sqrNode._sqrClearVideo?.();
            const setWidget = (name, value) => { const widget = sqrNode.widgets?.find(w => w.name === name); if (widget) widget.value = value; };
            setWidget("启用续跑", false);
            setWidget("续跑视频路径", "");
            setWidget("从第几段开始", 1);
            setWidget("sqr_frame_offset", -1);
            setWidget("sqr_pre_segments", "");
            sqrNode.setDirtyCanvas?.(true, true);
            overlay.remove();
            resolve({ cancelResume: true });
        }),
        mkBtn("Skip, Merge Current Only","",()=>{ overlay.remove(); resolve([]); }),
        mkBtn("Run","background:#2a9;color:#fff;border:none;font-weight:700;",()=>{ overlay.remove(); resolve(selPaths); })
    );
    const _xBtn2=document.createElement("button");_xBtn2.textContent="×";_xBtn2.style.cssText="position:absolute;top:10px;right:12px;background:none;border:none;font-size:20px;cursor:pointer;color:var(--input-text,#aaa);line-height:1;padding:0;";_xBtn2.onclick=()=>{overlay.remove();resolve(null);};
    box.style.position="relative"; box.appendChild(_xBtn2); box.appendChild(btns); overlay.appendChild(box); document.body.appendChild(overlay);
    fetch("/sqr/browse_videos").then(r=>r.json()).then(data=>{const o=data.roots?.find(r=>r.label==="ComfyUI output");loadDir(o?o.path:null);}).catch(()=>loadDir(null));
});
}


// ── 日志弹窗 ─────────────────────────────────────────────────────────
function _showLogOverlay(nodeId) {
    const pid = `sqr-log-${nodeId}`;
    const existed = document.getElementById(pid);
    if (existed) { existed.remove(); return; }

    const box = document.createElement("div"); box.id = pid;
    Object.assign(box.style, { position:"fixed",bottom:"20px",right:"20px",zIndex:"9990",width:"580px",height:"390px",background:"var(--comfy-menu-bg,#161616)",border:"1px solid var(--border-color,#3a3a3a)",borderRadius:"10px",boxShadow:"0 8px 36px rgba(0,0,0,.85)",display:"flex",flexDirection:"column",overflow:"hidden",resize:"both",userSelect:"text" });
    const hdr = document.createElement("div");
    Object.assign(hdr.style, { padding:"7px 12px",display:"flex",alignItems:"center",gap:"8px",borderBottom:"1px solid var(--border-color,#2a2a2a)",background:"rgba(255,255,255,0.03)",cursor:"move",flexShrink:"0",fontSize:"12px",fontWeight:"600",userSelect:"none" });
    let dx=0,dy=0,dragging=false;
    hdr.onmousedown=e=>{dragging=true;const r=box.getBoundingClientRect();dx=e.clientX-r.left;dy=e.clientY-r.top;document.onmousemove=e2=>{if(!dragging)return;box.style.left=(e2.clientX-dx)+"px";box.style.top=(e2.clientY-dy)+"px";box.style.right="auto";box.style.bottom="auto";};document.onmouseup=()=>{dragging=false;document.onmousemove=null;document.onmouseup=null;};};
    hdr.appendChild(Object.assign(document.createElement("span"),{textContent:"WanAni SQR Log"}));
    const dot=Object.assign(document.createElement("span"),{title:"Live updates"});dot.style.cssText="width:6px;height:6px;border-radius:50%;background:#2a9;flex-shrink:0;";hdr.appendChild(dot);
    hdr.appendChild(Object.assign(document.createElement("span"),{style:"flex:1"}));
    const clrBtn=document.createElement("button");clrBtn.textContent="Clear";clrBtn.title="Clear current log";clrBtn.style.cssText="padding:2px 9px;border-radius:4px;cursor:pointer;font-size:11px;background:rgba(255,255,255,0.07);border:1px solid var(--border-color,#444);color:var(--input-text,#aaa);";hdr.appendChild(clrBtn);
    const xBtn=document.createElement("button");xBtn.textContent="×";xBtn.style.cssText="padding:0 8px;font-size:18px;line-height:1.4;background:none;border:none;cursor:pointer;color:var(--input-text,#666);";xBtn.onmouseover=()=>xBtn.style.color="#fff";xBtn.onmouseout=()=>xBtn.style.color="var(--input-text,#666)";xBtn.onclick=e=>{e.stopPropagation();box.remove();};hdr.appendChild(xBtn);box.appendChild(hdr);
    const area=document.createElement("div");Object.assign(area.style,{flex:"1",overflowY:"auto",padding:"8px 12px",fontSize:"11px",lineHeight:"1.8",fontFamily:"'Consolas','Courier New',monospace",color:"var(--input-text,#bbb)",whiteSpace:"pre-wrap",wordBreak:"break-word",overflowWrap:"anywhere"});area.innerHTML="<div style='opacity:.4;'>Loading...</div>";box.appendChild(area);document.body.appendChild(box);
    function esc(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
    function lineHtml(r){const s=esc(r);if(/===/.test(r))return`<div style="color:#7cf;font-weight:700;padding-top:3px;">${s}</div>`;if(/---.*段.*---/.test(r))return`<div style="color:#adf;border-top:1px solid #222;margin-top:3px;padding-top:3px;">${s}</div>`;if(/✓/.test(r))return`<div style="color:#5d9;">${s}</div>`;if(/✗/.test(r))return`<div style="color:#f76;">${s}</div>`;if(/⚠/.test(r))return`<div style="color:#fa8;">${s}</div>`;if(/预览模式|全新生成|续跑模式|重新设计续跑模式/.test(r))return`<div style="color:#fd9;font-weight:600;">${s}</div>`;if(String(r).trim()==="")return`<div style="height:6px;"></div>`;return`<div>${s}</div>`;}
    function render(lines){if(!lines||!lines.length){area.innerHTML="<div style='opacity:.4;'>(No logs yet)</div>";return;}const atBot=area.scrollHeight-area.scrollTop-area.clientHeight<50;const html=[];for(const raw of lines){const parts=String(raw).split(/\r?\n/);for(const r of parts)html.push(lineHtml(r));}area.innerHTML=html.join("");if(atBot)area.scrollTop=area.scrollHeight;}
    let lastSig="";
    clrBtn.onclick=e=>{e.stopPropagation();fetch(`/sqr/logs/clear?uid=${nodeId}`,{method:"POST"}).catch(()=>{});area.innerHTML="<div style='opacity:.4;'>(Cleared)</div>";lastSig="";};
    async function poll(){if(!document.getElementById(pid))return;try{dot.style.opacity=".35";const d=await(await fetch(`/sqr/logs?uid=${nodeId}`)).json();dot.style.opacity="1";const logs=Array.isArray(d.logs)?d.logs:[];const sig=JSON.stringify(logs);if(sig!==lastSig){lastSig=sig;render(logs);}}catch(e){dot.style.opacity=".15";}if(document.getElementById(pid))setTimeout(poll,2000);}
    poll();
}


app.registerExtension({
    name: "WanAniSQRSegmentQueue.UI",

    async setup() {
        const origQueuePrompt = app.queuePrompt?.bind(app);
        if (!origQueuePrompt) return;

        app.queuePrompt = async function(number, batchCount) {
            const sqrNodes = (app.graph?.nodes || []).filter(n =>
                (n.type === "WanAniSQRSegmentQueue" || n.type === "WanAniDirector") && !n.muted && n.mode !== 4
            );
            if (sqrNodes.length === 0) {
                return origQueuePrompt(number, batchCount);
            }

            for (const sqrNode of sqrNodes) {
                const getNodeW = name => sqrNode.widgets?.find(w => w.name === name);
                const preW = getNodeW("sqr_pre_segments");
                if (preW) preW.value = "";

                const resumePath = getNodeW("续跑视频路径")?.value || "";
                const resumeEnabled = !!getNodeW("启用续跑")?.value;
                if (resumeEnabled && resumePath) {
                    const prePaths = await _showPreSegmentDialog(sqrNode);
                    if (prePaths === null) return;
                    if (prePaths?.cancelResume) {
                        if (preW) preW.value = "";
                        continue;
                    }
                    if (preW) preW.value = prePaths.join(",");
                }
            }

            let submitResult;
            try {
                const { output: fullOutput, workflow: lgWorkflow } = await app.graphToPrompt();
                const upstreamIds = new Set();
                for (const sqrNode of sqrNodes) { _sqrCollectUpstream(String(sqrNode.id), fullOutput, upstreamIds); }
                for (const sqrNode of sqrNodes) { const sqrId = String(sqrNode.id); for (const [nid, ndata] of Object.entries(fullOutput)) { const vals = Object.values(ndata.inputs || {}); if (vals.some(v => Array.isArray(v) && v.length === 2 && String(v[0]) === sqrId)) { upstreamIds.add(nid); } } }
                const strippedOutput = {};
                for (const nid of upstreamIds) { if (fullOutput[nid]) strippedOutput[nid] = fullOutput[nid]; }
                const clientId = app.api?.clientId ?? "";
                const res = await fetch("/prompt", { method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ client_id: clientId, prompt: strippedOutput, extra_data: { extra_pnginfo: { workflow: lgWorkflow, sqr_full_prompt: fullOutput, sqr_client_id: clientId } } }) });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                submitResult = await res.json();
            } catch (e) {
                console.warn("[SQR] 精简提交失败，回退到完整 prompt:", e);
                submitResult = await origQueuePrompt(number, batchCount);
            }
            return submitResult;
        };
    },

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "WanAniSQRSegmentQueue") return;

        const origCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function() {
            const r = origCreated ? origCreated.apply(this, arguments) : undefined;
            const node = this;
            const getW = name => node.widgets?.find(w => w.name === name);
            const SQR_TOPBAR_MIN_WIDTH = 900;
            if (node.size) node.size[0] = Math.max(node.size[0] || 0, SQR_TOPBAR_MIN_WIDTH);

            const sqrKeys = ["参考图节点ID","参考视频节点ID","输出节点ID","动作嵌入节点ID","分段参考图","续跑视频路径"];
            const resumeToggle = getW("启用续跑");
            const hideInternalWidget = (w) => {
                if (!w) return;
                w.computeSize = () => [0, 0];
                w.draw = () => {};
                w.mouse = () => false;
                w.type = "hidden";
                w.hidden = true;
                w.options = Object.assign({}, w.options || {}, { hidden: true });
            };
            hideInternalWidget(resumeToggle);
            sqrKeys.forEach(k => {
                const w = getW(k);
                hideInternalWidget(w);
            });
            {
                const _spw = getW("sqr_save_png");
                if (_spw) { _spw.computeSize = () => [0, -4]; _spw.draw = () => {}; }
            }

            const segW = getW("分段数");
            const startW = getW("从第几段开始");
            const _SQR_LAST_SEGMENTS_KEY = "sqr_last_segments";
            const _sqrLoadLastSegments = () => {
                try {
                    const value = Math.round(Number(localStorage.getItem(_SQR_LAST_SEGMENTS_KEY)));
                    return Number.isFinite(value) && value >= 1 ? Math.min(100, value) : null;
                } catch (e) {
                    return null;
                }
            };
            const _sqrSaveLastSegments = value => {
                const normalized = Math.max(1, Math.min(100, Math.round(Number(value) || 1)));
                try { localStorage.setItem(_SQR_LAST_SEGMENTS_KEY, String(normalized)); } catch (e) {}
                return normalized;
            };
            const _lastSegments = _sqrLoadLastSegments();
            if (segW && _lastSegments !== null) segW.value = _lastSegments;
            let segUiW = null;
            let startUiW = null;
            let multiRefW = getW("multi_ref_enabled");
            if (!multiRefW) {
                multiRefW = node.addWidget("toggle", "multi_ref_enabled", false, () => {
                    node.setDirtyCanvas?.(true, true);
                });
                multiRefW.serialize = true;
            }
            let replacementW = getW("replacement_enabled");
            if (!replacementW) {
                replacementW = node.addWidget("toggle", "replacement_enabled", false, () => {
                    node.setDirtyCanvas?.(true, true);
                });
                replacementW.serialize = true;
            }
            let startupFixW = getW("multi_ref_startup_fix");
            if (!startupFixW) {
                startupFixW = node.addWidget("toggle", "multi_ref_startup_fix", false, () => {
                    node.setDirtyCanvas?.(true, true);
                });
                startupFixW.serialize = true;
            }
            let transitionW = getW("启用过渡效果");
            if (!transitionW) {
                transitionW = node.addWidget("toggle", "启用过渡效果", false, () => {
                    node.setDirtyCanvas?.(true, true);
                });
                transitionW.serialize = true;
                const ti = node.widgets.indexOf(transitionW);
                const si = segW ? node.widgets.indexOf(segW) : -1;
                if (ti >= 0 && si >= 0 && ti !== si) {
                    node.widgets.splice(ti, 1);
                    node.widgets.splice(si, 0, transitionW);
                }
            }

            if (transitionW) {
                transitionW.computeSize = () => [0, -4];
                transitionW.draw = () => {};
            }
            if (multiRefW) {
                multiRefW.computeSize = () => [0, -4];
                multiRefW.draw = () => {};
            }
            if (replacementW) {
                replacementW.computeSize = () => [0, -4];
                replacementW.draw = () => {};
            }
            if (startupFixW) {
                startupFixW.computeSize = () => [0, -4];
                startupFixW.draw = () => {};
            }

            function _sqrApplySegMax() {
                const maxVal = Math.max(2, Math.min(100, node._sqrSettings?.segMax || 100));
                if (segW) {
                    segW.options.max = maxVal;
                    if (segW.value > maxVal) segW.value = maxVal;
                }
                if (segUiW) {
                    segUiW.options.max = maxVal;
                    segUiW.value = segW ? Number(segW.value || 1) : Number(segUiW.value || 1);
                }
                if (startW) {
                    const curSeg = segW ? Math.max(1, Math.round(segW.value)) : maxVal;
                    startW.options.max = curSeg;
                    if (startW.value > curSeg) startW.value = curSeg;
                }
                if (startUiW) {
                    const curSeg = segW ? Math.max(1, Math.round(segW.value)) : maxVal;
                    startUiW.options.max = curSeg;
                    startUiW.value = startW ? Number(startW.value || 1) : Number(startUiW.value || 1);
                }
                node.setDirtyCanvas?.(true, true);
            }

            function _sqrEnsureSegCapacity(required) {
                const need = Math.max(2, Math.min(100, Math.round(required || 0)));
                if (!need) return;
                if (!node._sqrSettings) node._sqrSettings = {};
                if ((node._sqrSettings.segMax || 0) < need) {
                    node._sqrSettings.segMax = need;
                    try { localStorage.setItem(_SQR_SEGMAX_KEY, String(need)); } catch(e) {}
                }
                _sqrApplySegMax();
            }

            if (segW) {
                const _origSegCb = segW.callback;
                segW.callback = function(v, ...args) {
                    const iv = Math.max(1, Math.round(v));
                    this.value = iv;
                    _sqrSaveLastSegments(iv);
                    if (startW) {
                        startW.options.max = iv;
                        if (startW.value > iv) startW.value = iv;
                    }
                    if (_origSegCb) return _origSegCb.call(this, iv, ...args);
                };
            }

            if (startW) {
                const _origStartCb = startW.callback;
                startW.callback = function(v, ...args) {
                    const mx = segW ? Math.max(1, Math.round(segW.value)) : 100;
                    if (v > mx) { this.value = mx; v = mx; }
                    if (_origStartCb) return _origStartCb.call(this, v, ...args);
                };
            }

            const SQR_NODE_ID_KEYS = ["参考图节点ID", "参考视频节点ID", "输出节点ID", "动作嵌入节点ID"];
            let execW = getW("执行");
            const getSqrStateWidgets = () => {
                const names = new Set([...sqrKeys, "sqr_save_png", "sqr_frame_offset", "sqr_pre_segments"]);
                const widgets = [];
                for (const name of names) {
                    const w = getW(name);
                    if (w) widgets.push(w);
                }
                for (const w of [transitionW, multiRefW, replacementW, startupFixW, execW, resumeToggle, segW, startW]) {
                    if (w && !widgets.includes(w)) widgets.push(w);
                }
                return widgets;
            };
            const SQR_NODE_ID_TYPES = {
                "参考图节点ID": ["LoadImage", "WanSQRMultiReference", "SQRScail2ReferenceBatchStack"],
                "参考视频节点ID": ["VHS_LoadVideo"],
                "输出节点ID": ["VHS_VideoCombine"],
                "动作嵌入节点ID": ["SQRSCAIL2TransitionToVideo", "SQRWanAnimateTransitionToVideo", "WanSCAILToVideo", "WanAnimateToVideo", "WanVideoAnimateEmbeds"],
            };
            const getSqr = k => getW(k)?.value || "";
            const isMatchingNodeId = (key, value) => {
                const id = Number.parseInt(String(value ?? "").trim(), 10);
                if (!Number.isFinite(id)) return false;
                const target = app.graph?.getNodeById?.(id);
                return !!target && SQR_NODE_ID_TYPES[key]?.includes(target.type);
            };
            const uniqueCandidateId = key => {
                const types = SQR_NODE_ID_TYPES[key] || [];
                const matches = (app.graph?._nodes || []).filter(n => n && n.mode !== 4 && types.includes(n.type));
                return matches.length === 1 ? String(matches[0].id) : "";
            };
            const ensureNodeIdStore = () => {
                node.properties ||= {};
                node.properties.sqr_node_ids ||= {};
                return node.properties.sqr_node_ids;
            };
            const persistNodeIds = () => {
                const store = ensureNodeIdStore();
                for (const key of SQR_NODE_ID_KEYS) store[key] = String(getW(key)?.value || "").trim();
                return store;
            };
            const ensureStateStore = () => {
                node.properties ||= {};
                node.properties.sqr_state ||= {};
                return node.properties.sqr_state;
            };
            const persistSqrState = () => {
                const state = ensureStateStore();
                for (const w of getSqrStateWidgets()) {
                    if (w?.name) state[w.name] = w.value;
                }
                state.version = 1;
                state.node_id = String(node.id ?? "");
                state.updated_at = Date.now();
                return state;
            };
            const restoreSqrState = source => {
                if (!source || typeof source !== "object") return false;
                let restored = false;
                for (const [name, value] of Object.entries(source)) {
                    const w = getW(name);
                    if (!w) continue;
                    w.value = value;
                    restored = true;
                }
                if (restored) {
                    node.properties ||= {};
                    node.properties.sqr_state = { ...source };
                    const resumePathW = getW(sqrKeys[5]);
                    const resumePath = String(resumePathW?.value || "").trim();
                    const rtw = resumeToggle;
                    if (rtw) rtw.value = !!resumePath && rtw.value !== false;
                    if (multiRefW) _sqrSyncMultiRefIdentityMode(multiRefW.value);
                    if (replacementW) _sqrSyncReplacementMode(replacementW.value);
                    const tw = node.widgets?.find(w => w.name === "_sqr_ref_thumbs");
                    if (tw) tw.syncPaths?.();
                    node.setDirtyCanvas?.(true, true);
                }
                return restored;
            };
            const restoreNodeIds = source => {
                if (!source || typeof source !== "object") return false;
                let restored = false;
                for (const key of SQR_NODE_ID_KEYS) {
                    if (!Object.prototype.hasOwnProperty.call(source, key)) continue;
                    const w = getW(key);
                    if (w) {
                        w.value = String(source[key] ?? "").trim();
                        restored = true;
                    }
                }
                if (restored) {
                    node.properties ||= {};
                    node.properties.sqr_node_ids = { ...source };
                    node.setDirtyCanvas?.(true, true);
                }
                return restored;
            };
            const setSqr = (k, v) => {
                const w = getW(k);
                if (w) w.value = v;
                if (SQR_NODE_ID_KEYS.includes(k)) ensureNodeIdStore()[k] = String(v ?? "").trim();
                persistSqrState();
            };

            const _origSqrSerialize = node.onSerialize;
            node.onSerialize = function(data) {
                if (_origSqrSerialize) _origSqrSerialize.call(this, data);
                const ids = persistNodeIds();
                const state = persistSqrState();
                data.properties ||= {};
                data.properties.sqr_node_ids = { ...ids };
                data.properties.sqr_state = { ...state };
                data.properties.sqr_ui_lang = node._sqrSettings?.lang || "en";
            };

            const _origSqrConfigure = node.onConfigure;
            node.onConfigure = function(data) {
                const result = _origSqrConfigure ? _origSqrConfigure.call(this, data) : undefined;
                const positional = Object.fromEntries(SQR_NODE_ID_KEYS.map(key => [key, String(getW(key)?.value || "").trim()]));
                const saved = data?.properties?.sqr_node_ids || this.properties?.sqr_node_ids;
                const savedState = data?.properties?.sqr_state || this.properties?.sqr_state;
                restoreSqrState(savedState);
                const lastSegments = _sqrLoadLastSegments();
                if (segW && lastSegments !== null) segW.value = lastSegments;
                setTimeout(() => {
                    const repaired = {};
                    for (const key of SQR_NODE_ID_KEYS) {
                        const savedValue = String(saved?.[key] ?? "").trim();
                        const positionalValue = positional[key];
                        repaired[key] = isMatchingNodeId(key, savedValue)
                            ? savedValue
                            : isMatchingNodeId(key, positionalValue)
                                ? positionalValue
                                : uniqueCandidateId(key);
                    }
                    restoreNodeIds(repaired);
                    persistNodeIds();
                    persistSqrState();
                }, 250);
                return result;
            };

            if (node.properties?.sqr_node_ids) restoreNodeIds(node.properties.sqr_node_ids);
            if (node.properties?.sqr_state) restoreSqrState(node.properties.sqr_state);

            const _SQR_PNG_KEY   = "sqr_save_png";
            const _SQR_SEGMAX_KEY = "sqr_seg_max";
            const _SQR_EXECGLOW_KEY = "sqr_exec_glow";
            const _SQR_LANG_KEY = "sqr_ui_lang";
            if (!node._sqrSettings) {
                const savedPng  = localStorage.getItem(_SQR_PNG_KEY);
                const savedSegMax = localStorage.getItem(_SQR_SEGMAX_KEY);
                const savedExecGlow = localStorage.getItem(_SQR_EXECGLOW_KEY);
                const savedLang = localStorage.getItem(_SQR_LANG_KEY);
                node._sqrSettings = {
                    savePng: savedPng === null ? true : (savedPng !== "false"),
                    segMax: savedSegMax ? parseInt(savedSegMax) : 10,
                    execGlow: savedExecGlow === null ? true : (savedExecGlow !== "false"),
                    lang: savedLang === "zh" ? "zh" : "en",
                };
            }
            if (node.properties?.sqr_ui_lang === "zh" || node.properties?.sqr_ui_lang === "en") {
                node._sqrSettings.lang = node.properties.sqr_ui_lang;
            }
            const _sqrText = {
                en: {
                    langBtn: "中",
                    settings: "Settings",
                    nodeIds: "Node IDs",
                    log: "Log",
                    multiRefOn: "Multi Ref ON",
                    multiRefOff: "Multi Ref OFF",
                    startupOn: "Startup Fix ON",
                    startupOff: "Startup Fix OFF",
                    replacementOn: "Replacement ON",
                    replacementOff: "Replacement OFF",
                    transitionOn: "Transition ON",
                    transitionOff: "Transition OFF",
                    executeMode: "Execute Mode",
                    previewMode: "Preview Mode",
                    settingsTitle: "WanAni SQR Settings",
                    segmentsLabel: "Segments",
                    startSegmentLabel: "Start Segment",
                    segmentControls: "Segment Controls",
                    segmentHint: "The segment slider always means the number of equal segments.",
                    segmentMax: "Segment slider max",
                    currentSegmentMax: "Current segment max",
                    nodeGlow: "Node glow while Execute is ON",
                    glowTrue: "True",
                    glowTrueDesc: "Show green node glow while executing",
                    glowFalse: "False",
                    glowFalseDesc: "Do not show node glow",
                    savePng: "Save png of first frame for metadata",
                    pngTrue: "True",
                    pngTrueDesc: "Save PNG",
                    pngFalse: "False",
                    pngFalseDesc: "Do not save PNG; clean automatically",
                    cancel: "Cancel",
                    apply: "Apply",
                    refGroups: "Reference Image Groups",
                    refGroupsHint: "Multi Ref ON: segment 1 uses group 1, segment 2 uses group 2, and so on. Each group can contain up to 5 images.",
                    addGroup: "Add Group",
                    flattenGroup: "Flatten To One Group",
                    addImages: "Add Images",
                    removeGroup: "Remove Group",
                    emptyGroup: "Drop or add images for this segment group",
                    refLimitExceeded: "Reference images exceed the current group limit. Extra images were moved to the next group.",
                    selectRefs: "Select Reference Images",
                    selectResume: "Select Resume Video",
                    manageResume: "Manage Resume Video",
                    viewLog: "View Log",
                },
                zh: {
                    langBtn: "EN",
                    settings: "设置",
                    nodeIds: "节点ID",
                    log: "日志",
                    multiRefOn: "多参考 开",
                    multiRefOff: "多参考 关",
                    startupOn: "开头修复 开",
                    startupOff: "开头修复 关",
                    replacementOn: "替换 开",
                    replacementOff: "替换 关",
                    transitionOn: "过渡 开",
                    transitionOff: "过渡 关",
                    executeMode: "执行模式",
                    previewMode: "预览模式",
                    settingsTitle: "WanAni SQR 设置",
                    segmentsLabel: "分段数",
                    startSegmentLabel: "从第几段开始",
                    segmentControls: "分段控制",
                    segmentHint: "分段滑块始终表示平均分段的段数。",
                    segmentMax: "分段滑块最大值",
                    currentSegmentMax: "当前分段最大值",
                    nodeGlow: "执行开启时节点发光",
                    glowTrue: "开启",
                    glowTrueDesc: "执行时显示绿色边框",
                    glowFalse: "关闭",
                    glowFalseDesc: "不显示节点发光",
                    savePng: "保存首帧 PNG 元数据图",
                    pngTrue: "开启",
                    pngTrueDesc: "保存 PNG",
                    pngFalse: "关闭",
                    pngFalseDesc: "不保存 PNG，并自动清理",
                    cancel: "取消",
                    apply: "应用",
                    refGroups: "参考图分组",
                    refGroupsHint: "多参考开启时：第 1 段使用第 1 组，第 2 段使用第 2 组，以此类推。每组最多 5 张图。",
                    addGroup: "添加分组",
                    flattenGroup: "合并为一组",
                    addImages: "添加图片",
                    removeGroup: "删除分组",
                    emptyGroup: "拖入或添加本段参考图",
                    refLimitExceeded: "参考图超过当前分组上限，多出的图片已移动到下一组。",
                    selectRefs: "选择参考图",
                    selectResume: "选择续跑视频",
                    manageResume: "管理续跑视频",
                    viewLog: "查看日志",
                },
            };
            const tr = key => (_sqrText[node._sqrSettings?.lang || "en"] || _sqrText.en)[key] || _sqrText.en[key] || key;

            function _sqrCurrentRefGroupLimit() {
                if (multiRefW?.value) return 5;
                return Math.max(1, Math.min(100, Math.round(Number(node._sqrSettings?.segMax || segW?.options?.max || segW?.value || 10))));
            }
            function _sqrNormalizeRefGroupsForMode(groups, notify=false) {
                const flatGroups = (groups || [])
                    .map(group => (Array.isArray(group) ? group : [group]).map(v => {
                        const path = sqrRefPath(v);
                        return path ? sqrRefEntry(path, sqrRefIsBg(v)) : null;
                    }).filter(Boolean));
                const limit = _sqrCurrentRefGroupLimit();
                const normalized = [];
                let overflowed = false;
                if (multiRefW?.value) {
                    for (const group of flatGroups.length ? flatGroups : [[]]) {
                        for (let i = 0; i < group.length; i += limit) {
                            const chunk = group.slice(i, i + limit);
                            if (chunk.length) normalized.push(chunk);
                            if (i > 0) overflowed = true;
                        }
                    }
                } else {
                    const flat = sqrFlattenRefGroups(flatGroups);
                    for (let i = 0; i < flat.length; i += limit) {
                        const chunk = flat.slice(i, i + limit);
                        if (chunk.length) normalized.push(chunk);
                        if (i > 0) overflowed = true;
                    }
                }
                if (!normalized.length) normalized.push([]);
                if (overflowed && notify) alert(tr("refLimitExceeded"));
                return normalized;
            }
            function _sqrNormalizeStoredRefsForMode(notify=false) {
                const normalized = _sqrNormalizeRefGroupsForMode(sqrParseRefGroups(getSqr(sqrKeys[4])), notify);
                setSqr(sqrKeys[4], sqrStoreRefGroups(normalized));
                const tw = node.widgets?.find(w => w.name === "_sqr_ref_thumbs");
                tw?.syncPaths?.();
                node.setDirtyCanvas?.(true, true);
                return normalized;
            }

            function _sqrHideNativeNumberWidget(w) {
                if (!w) return;
                w.computeSize = () => [0, -4];
                w.draw = () => {};
            }
            function _sqrSyncSegmentProxyLabels() {
                if (segUiW) segUiW.name = tr("segmentsLabel");
                if (startUiW) startUiW.name = tr("startSegmentLabel");
            }
            if (segW) {
                segUiW = node.addWidget("number", tr("segmentsLabel"), Number(segW.value || 1), (v) => {
                    const maxVal = Math.max(2, Math.min(100, node._sqrSettings?.segMax || 100));
                    const iv = Math.max(1, Math.min(maxVal, Math.round(Number(v) || 1)));
                    segUiW.value = iv;
                    segW.value = iv;
                    _sqrSaveLastSegments(iv);
                    segW.callback?.(iv);
                    if (startUiW && startW) {
                        startUiW.options.max = iv;
                        if (Number(startUiW.value || 1) > iv) startUiW.value = iv;
                        startW.value = Math.min(iv, Math.max(1, Math.round(Number(startUiW.value || 1))));
                    }
                    persistSqrState();
                    node.setDirtyCanvas?.(true, true);
                }, { min: 1, max: Math.max(2, Math.min(100, node._sqrSettings?.segMax || 100)), step: 1, precision: 0 });
                segUiW.serialize = false;
                _sqrHideNativeNumberWidget(segW);
            }
            if (startW) {
                startUiW = node.addWidget("number", tr("startSegmentLabel"), Number(startW.value || 1), (v) => {
                    const mx = segW ? Math.max(1, Math.round(Number(segW.value) || 1)) : 100;
                    const iv = Math.max(1, Math.min(mx, Math.round(Number(v) || 1)));
                    startUiW.value = iv;
                    startW.value = iv;
                    startW.callback?.(iv);
                    persistSqrState();
                    node.setDirtyCanvas?.(true, true);
                }, { min: 1, max: segW ? Math.max(1, Math.round(Number(segW.value) || 1)) : 100, step: 1, precision: 0 });
                startUiW.serialize = false;
                _sqrHideNativeNumberWidget(startW);
            }

            _sqrNormalizeStoredRefsForMode(false);
            _sqrApplySegMax();

            execW = execW || getW("执行");
            if (execW) {
                execW.computeSize = () => [0, -4];
                execW.draw = () => {};
            }

            const _origDrawBg = node.onDrawBackground;
            node.onDrawBackground = function(ctx) {
                if (_origDrawBg) _origDrawBg.call(this, ctx);
                if (!node._sqrSettings?.execGlow) return;
                const eW = getW("执行");
                if (!eW || !eW.value) return;
                ctx.save();
                ctx.strokeStyle = "rgba(60,200,130,0.7)";
                ctx.lineWidth = 1.5;
                ctx.shadowColor = "rgba(60,200,130,0.6)";
                ctx.shadowBlur = 8;
                ctx.beginPath();
                const r = 6;
                ctx.roundRect ? ctx.roundRect(-1, -LiteGraph.NODE_TITLE_HEIGHT - 1, this.size[0] + 2, this.size[1] + LiteGraph.NODE_TITLE_HEIGHT + 2, r)
                              : ctx.rect(-1, -LiteGraph.NODE_TITLE_HEIGHT - 1, this.size[0] + 2, this.size[1] + LiteGraph.NODE_TITLE_HEIGHT + 2);
                ctx.stroke();
                ctx.restore();
            };

            // ── Settings dialog ──
            const settingsBtn = node.addWidget("button", "Settings", null, () => {
                document.getElementById("sqr-settings-overlay")?.remove();
                const s = node._sqrSettings;
                const overlay = document.createElement("div");
                overlay.id = "sqr-settings-overlay";
                Object.assign(overlay.style, {
                    position:"fixed",inset:"0",zIndex:"10000",
                    background:"rgba(0,0,0,.72)",display:"flex",alignItems:"center",justifyContent:"center"
                });
                const box = document.createElement("div");
                Object.assign(box.style, {
                    background:"var(--comfy-menu-bg,#1e1e1e)",color:"var(--input-text,#eee)",
                    border:"1px solid var(--border-color,#444)",borderRadius:"12px",
                    padding:"22px 26px",width:"520px",display:"flex",flexDirection:"column",gap:"16px",
                    boxShadow:"0 8px 40px rgba(0,0,0,.7)"
                });
                const mkDiv=(t,st)=>Object.assign(document.createElement("div"),{textContent:t,style:st||""});
                box.appendChild(mkDiv(tr("settingsTitle"),"font-size:15px;font-weight:700;"));
                const mkRemoteHint = (text) => {
                    const el = document.createElement("div");
                    Object.assign(el.style, { padding:"10px 14px", borderRadius:"8px", fontSize:"12px", lineHeight:"1.7", border:"1px solid rgba(100,180,255,0.3)", background:"rgba(60,140,255,0.08)", color:"var(--input-text,#ccc)" });
                    el.innerHTML = `<span style="color:#7cf;font-weight:600;">Remote mode</span>&nbsp; ${text}`;
                    return el;
                };

                const isRemote = _sqrIsRemote();

                box.appendChild(Object.assign(document.createElement("div"),{style:"border-top:1px solid var(--border-color,#444);"}));
                box.appendChild(mkDiv(tr("segmentControls"),"font-size:13px;font-weight:600;margin-bottom:2px;"));
                box.appendChild(mkDiv(tr("segmentHint"),"font-size:10px;opacity:.45;line-height:1.5;margin-bottom:6px;"));

                if (!isRemote) {
                    const segMaxSection = document.createElement("div");
                    segMaxSection.style.cssText = "display:flex;align-items:center;gap:10px;margin-top:4px;";
                    const segMaxLabel = document.createElement("span"); segMaxLabel.textContent = tr("segmentMax"); segMaxLabel.style.cssText = "font-size:12px;opacity:.7;";
                    const segMaxInput = document.createElement("input"); segMaxInput.type = "number"; segMaxInput.min = "2"; segMaxInput.max = "100"; segMaxInput.value = String(s.segMax || 10);
                    Object.assign(segMaxInput.style, { width:"70px", padding:"5px 8px", borderRadius:"5px", border:"1px solid var(--border-color,#555)", background:"var(--comfy-input-bg,#333)", color:"var(--input-text,#eee)", fontSize:"13px" });
                    segMaxInput.onchange = () => { let v = parseInt(segMaxInput.value) || 10; v = Math.max(2, Math.min(100, v)); segMaxInput.value = v; s.segMax = v; };
                    const segMaxHint = document.createElement("span"); segMaxHint.textContent = "(2-100, default 10)"; segMaxHint.style.cssText = "font-size:11px;opacity:.4;";
                    segMaxSection.append(segMaxLabel, segMaxInput, segMaxHint);
                    box.appendChild(segMaxSection);
                } else {
                    box.appendChild(mkDiv(`${tr("currentSegmentMax")}: ${s.segMax}`,"font-size:12px;opacity:.7;padding:4px 0;"));
                }

                box.appendChild(Object.assign(document.createElement("div"),{style:"border-top:1px solid var(--border-color,#444);"}));
                box.appendChild(mkDiv(tr("nodeGlow"),"font-size:11px;opacity:.5;margin-bottom:2px;"));
                if (!isRemote) {
                    const glowRow = document.createElement("div"); glowRow.style.cssText = "display:flex;gap:10px;";
                    const mkGlowOpt = (value, label, desc) => {
                        const d = document.createElement("div"); const active = (s.execGlow === value);
                        Object.assign(d.style, { flex:"1", padding:"8px 12px", minHeight:"52px", boxSizing:"border-box", borderRadius:"8px", cursor:"pointer",
                            border: active ? "2px solid #4a9" : "2px solid var(--border-color,#555)", background: active ? "rgba(60,180,120,0.12)" : "transparent" });
                        d.innerHTML = `<div style="font-size:13px;font-weight:600;">${label}</div><div style="font-size:11px;opacity:.5;margin-top:2px;">${desc}</div>`;
                        d.dataset.glowval = String(value);
                        d.onclick = () => { s.execGlow = value; glowRow.querySelectorAll("div[data-glowval]").forEach(x => { const me = x.dataset.glowval === String(value); x.style.border = me ? "2px solid #4a9" : "2px solid var(--border-color,#555)"; x.style.background = me ? "rgba(60,180,120,0.12)" : "transparent"; }); };
                        return d;
                    };
                    glowRow.append(mkGlowOpt(true, tr("glowTrue"), tr("glowTrueDesc")), mkGlowOpt(false, tr("glowFalse"), tr("glowFalseDesc")));
                    box.appendChild(glowRow);
                } else {
                    box.appendChild(mkDiv(`Current: ${s.execGlow ? "On" : "Off"}`,"font-size:12px;opacity:.7;padding:4px 0;"));
                }
                box.appendChild(Object.assign(document.createElement("div"),{style:"border-top:1px solid var(--border-color,#444);"}));
                box.appendChild(mkDiv(tr("savePng"),"font-size:11px;opacity:.5;margin-bottom:2px;"));
                if (isRemote) {
                    const pngW = getW("sqr_save_png"); if (pngW) pngW.value = "false";
                    box.appendChild(mkRemoteHint("Locked to <b style='color:#aef;'>do not save PNG</b>. Metadata images are cleaned in remote mode."));
                } else {
                    const pngRow = document.createElement("div"); pngRow.style.cssText="display:flex;gap:10px;";
                    const mkPngOpt = (value, label, desc) => { const d = document.createElement("div"); const active = (s.savePng === value); Object.assign(d.style, { flex:"1", padding:"8px 12px", minHeight:"68px", boxSizing:"border-box", borderRadius:"8px", cursor:"pointer", border: active ? "2px solid #4a9" : "2px solid var(--border-color,#555)", background: active ? "rgba(60,180,120,0.12)" : "transparent" }); d.innerHTML = `<div style="font-size:13px;font-weight:600;">${label}</div><div style="font-size:11px;opacity:.5;margin-top:2px;">${desc}</div>`; d.dataset.pngval = String(value); d.onclick = () => { s.savePng = value; pngRow.querySelectorAll("div[data-pngval]").forEach(x => { const me = x.dataset.pngval === String(value); x.style.border = me ? "2px solid #4a9" : "2px solid var(--border-color,#555)"; x.style.background = me ? "rgba(60,180,120,0.12)" : "transparent"; }); }; return d; };
                    pngRow.append(mkPngOpt(true,tr("pngTrue"),tr("pngTrueDesc")),mkPngOpt(false,tr("pngFalse"),tr("pngFalseDesc")));
                    box.appendChild(pngRow);
                }

                const btns=document.createElement("div"); btns.style.cssText="display:flex;gap:8px;margin-top:4px;";
                const mkBtn=(t,st,fn)=>{const b=document.createElement("button");b.textContent=t;b.style.cssText=`flex:1;padding:7px 18px;border-radius:7px;cursor:pointer;font-size:13px;${st}`;b.onclick=fn;return b;};
                btns.append(
                    mkBtn(tr("cancel"),"",()=>overlay.remove()),
                    mkBtn(tr("apply"),"background:#2a9;color:#fff;border:none;font-weight:600;",()=>{
                        if (!isRemote) {
                            localStorage.setItem(_SQR_PNG_KEY, String(s.savePng));
                            localStorage.setItem(_SQR_SEGMAX_KEY, String(s.segMax));
                            localStorage.setItem(_SQR_EXECGLOW_KEY, String(s.execGlow));
                            const pngW = getW("sqr_save_png");
                            if (pngW) pngW.value = String(s.savePng);
                            _sqrApplySegMax();
                            _sqrNormalizeStoredRefsForMode(true);
                        }
                        overlay.remove();
                        node.setDirtyCanvas?.(true, true);
                    })
                );
                box.appendChild(btns);
                const _xBtn = document.createElement("button");
                _xBtn.textContent = "×";
                _xBtn.style.cssText = "position:absolute;top:10px;right:12px;background:none;border:none;font-size:20px;cursor:pointer;color:var(--input-text,#aaa);line-height:1;padding:0;";
                _xBtn.onmouseover = () => _xBtn.style.color = "#fff";
                _xBtn.onmouseout  = () => _xBtn.style.color = "var(--input-text,#aaa)";
                _xBtn.onclick = () => overlay.remove();
                box.style.position = "relative";
                box.appendChild(_xBtn);
                overlay.appendChild(box);
                overlay.onclick=e=>{if(e.target===overlay)overlay.remove();};
                document.body.appendChild(overlay);
            });
            settingsBtn.serialize = false;
            settingsBtn.computeSize = () => [0, -4];
            settingsBtn.draw = () => {};

            // ── Node ID dialog ──
            const nodeIdBtn = node.addWidget("button", "Node IDs", null, () => {
                showNodeIdSelector([
                    {key:"参考图节点ID",   label:"Reference / Multi Reference ID", tooltip:"LoadImage ID for OFF mode, Wan SQR Multi Reference ID for ON mode",              value:getSqr("参考图节点ID")},
                    {key:"参考视频节点ID", label:"Reference Video Load Video ID", tooltip:"Load Video target node ID",      value:getSqr("参考视频节点ID")},
                    {key:"输出节点ID",     label:"Output VHS_VideoCombine ID",   tooltip:"Main output VHS_VideoCombine ID",value:getSqr("输出节点ID")},
                    {key:"动作嵌入节点ID", label:"SQR WanAnimate Transition ID", tooltip:"SQR WanAnimate Transition node ID", value:getSqr("动作嵌入节点ID")},
                ], result=>{
                    Object.entries(result).forEach(([k,v]) => setSqr(k, v));
                    node.setDirtyCanvas?.(true, true);
                });
            });
            nodeIdBtn.serialize = false;
            nodeIdBtn.computeSize = () => [0, -4];
            nodeIdBtn.draw = () => {};

            const _sqrTopHit = {};
            function _sqrDrawTopButton(ctx, key, x, y, w, h, label, active, opts={}) {
                _sqrTopHit[key] = { x, y, localY: opts.hitY ?? y, w, h };
                const onColor = opts.onColor || "rgba(34,170,105,0.86)";
                const offColor = opts.offColor || "rgba(195,64,72,0.86)";
                const neutral = opts.neutral || "rgba(255,255,255,0.09)";
                ctx.save();
                ctx.fillStyle = opts.mode === "action" ? neutral : (active ? onColor : offColor);
                ctx.strokeStyle = opts.mode === "action" ? "rgba(255,255,255,0.22)" : (active ? "rgba(120,255,180,0.75)" : "rgba(255,150,150,0.75)");
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.rect(x, y, w, h);
                ctx.fill();
                ctx.stroke();
                ctx.fillStyle = opts.textColor || "#f4f4f4";
                ctx.font = "bold 10px sans-serif";
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                ctx.fillText(label, x + w / 2, y + h / 2 + 0.5);
                ctx.restore();
            }

            function _sqrSetNodeInputValue(target, inputName, value) {
                if (!target) return false;
                const widget = target.widgets?.find(w => w.name === inputName);
                if (widget) {
                    widget.value = value;
                    widget.callback?.(value);
                }
                target.inputs ||= {};
                target.properties ||= target.properties || {};
                if (target.widgets_values && widget) {
                    const idx = target.widgets.indexOf(widget);
                    if (idx >= 0) target.widgets_values[idx] = value;
                }
                target.setDirtyCanvas?.(true, true);
                return !!widget;
            }

            function _sqrSyncReplacementMode(value) {
                const enabled = !!value;
                const graphNodes = app.graph?._nodes || [];
                let changed = 0;
                const aeId = Number.parseInt(String(getSqr(sqrKeys[3]) || "").trim(), 10);
                const aeNode = Number.isFinite(aeId) ? app.graph?.getNodeById?.(aeId) : null;
                if (aeNode?.comfyClass === "SQRSCAIL2TransitionToVideo" || aeNode?.type === "SQRSCAIL2TransitionToVideo") {
                    if (_sqrSetNodeInputValue(aeNode, "replacement_mode", enabled)) changed++;
                }
                for (const gnode of graphNodes) {
                    const cls = gnode?.comfyClass || gnode?.type;
                    if (cls === "SQRScail2ColoredMaskAdvanced") {
                        if (_sqrSetNodeInputValue(gnode, "replacement_mode", enabled)) changed++;
                    } else if (cls === "SQRSCAIL2TransitionToVideo" && gnode !== aeNode) {
                        if (_sqrSetNodeInputValue(gnode, "replacement_mode", enabled)) changed++;
                    }
                }
                if (!changed) console.warn("[SQR] Replacement switch did not find SCAIL-2 replacement_mode widgets to sync.");
                return changed;
            }

            function _sqrSyncMultiRefIdentityMode(value) {
                const graphNodes = app.graph?._nodes || [];
                let changed = 0;
                for (const gnode of graphNodes) {
                    const cls = gnode?.comfyClass || gnode?.type;
                    if (cls === "SQRScail2ColoredMaskAdvanced") {
                        const current = gnode.widgets?.find(w => w.name === "identity_mode")?.value;
                        const identityMode = value && current === "multi_person_multi_reference"
                            ? "multi_person_multi_reference"
                            : (value ? "single_person_multi_reference" : "multi_person");
                        if (_sqrSetNodeInputValue(gnode, "identity_mode", identityMode)) changed++;
                    }
                }
                if (!changed) console.warn("[SQR] Multi Ref switch did not find SCAIL-2 identity_mode widgets to sync.");
                return changed;
            }

            const topBarWidget = {
                name: "_sqr_topbar",
                type: "sqr_topbar",
                serialize: false,
                computeSize(width) { return [Math.max(width || 0, SQR_TOPBAR_MIN_WIDTH), 30]; },
                draw(ctx, nodeRef, width, y) {
                    for (const key of Object.keys(_sqrTopHit)) delete _sqrTopHit[key];
                    if (nodeRef?.size) nodeRef.size[0] = Math.max(nodeRef.size[0] || 0, SQR_TOPBAR_MIN_WIDTH);
                    const barW = Math.max(width || 0, nodeRef?.size?.[0] || 0, SQR_TOPBAR_MIN_WIDTH);
                    const h = 22;
                    const topY = y + 4;
                    const gap = 5;
                    const langW = 34;
                    const actionW = 54;
                    const idsW = 58;
                    const logW = 42;
                    const rightX = barW - langW - actionW - idsW - logW - gap * 3 - 7;
                    _sqrDrawTopButton(ctx, "lang", rightX, topY, langW, h, tr("langBtn"), false, { mode: "action", hitY: 4 });
                    _sqrDrawTopButton(ctx, "settings", rightX + langW + gap, topY, actionW, h, tr("settings"), false, { mode: "action", hitY: 4 });
                    _sqrDrawTopButton(ctx, "nodeids", rightX + langW + actionW + gap * 2, topY, idsW, h, tr("nodeIds"), false, { mode: "action", hitY: 4 });
                    _sqrDrawTopButton(ctx, "log", rightX + langW + actionW + idsW + gap * 3, topY, logW, h, tr("log"), false, { mode: "action", hitY: 4 });

                    const toggleAreaX = 7;
                    const toggleAreaRight = Math.max(toggleAreaX, rightX - 8);
                    const toggleAreaW = Math.max(0, toggleAreaRight - toggleAreaX);
                    const toggleW = 112;
                    const totalW = toggleW * 5 + gap * 4;
                    const midX = toggleAreaX + Math.max(0, (toggleAreaW - totalW) / 2);
                    const multiRefOn = !!multiRefW?.value;
                    const startupFixOn = !!startupFixW?.value;
                    const replacementOn = !!replacementW?.value;
                    const transOn = !!transitionW?.value;
                    const execOn = !!execW?.value;
                    _sqrDrawTopButton(ctx, "multiref", midX, topY, toggleW, h, multiRefOn ? tr("multiRefOn") : tr("multiRefOff"), multiRefOn, { hitY: 4 });
                    _sqrDrawTopButton(ctx, "startupfix", midX + toggleW + gap, topY, toggleW, h, startupFixOn ? tr("startupOn") : tr("startupOff"), startupFixOn, { hitY: 4 });
                    _sqrDrawTopButton(ctx, "replacement", midX + (toggleW + gap) * 2, topY, toggleW, h, replacementOn ? tr("replacementOn") : tr("replacementOff"), replacementOn, { hitY: 4 });
                    _sqrDrawTopButton(ctx, "transition", midX + (toggleW + gap) * 3, topY, toggleW, h, transOn ? tr("transitionOn") : tr("transitionOff"), transOn, { hitY: 4 });
                    _sqrDrawTopButton(ctx, "execute", midX + (toggleW + gap) * 4, topY, toggleW, h, execOn ? tr("executeMode") : tr("previewMode"), execOn, { hitY: 4 });
                },
                mouse(event, pos, nodeRef) {
                    if (event.type !== "pointerdown" && event.type !== "mousedown") return false;
                    const x = pos?.[0], y = pos?.[1];
                    for (const [key, r] of Object.entries(_sqrTopHit)) {
                        const hitWidgetLocal = x >= r.x && x <= r.x + r.w && y >= r.localY && y <= r.localY + r.h;
                        const hitNodeLocal = x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h;
                        if (hitWidgetLocal || hitNodeLocal) {
                            if (key === "lang") {
                                node._sqrSettings.lang = node._sqrSettings.lang === "zh" ? "en" : "zh";
                                localStorage.setItem(_SQR_LANG_KEY, node._sqrSettings.lang);
                                node.properties ||= {};
                                node.properties.sqr_ui_lang = node._sqrSettings.lang;
                                _sqrSyncSegmentProxyLabels();
                                if (typeof refBtn !== "undefined" && refBtn) refBtn.name = tr("selectRefs");
                                if (typeof logBtn !== "undefined" && logBtn) logBtn.name = tr("viewLog");
                                if (typeof resumeBtn !== "undefined" && resumeBtn) resumeBtn.name = resumeBtn._sqrActive ? tr("manageResume") : tr("selectResume");
                            } else if (key === "multiref" && multiRefW) {
                                multiRefW.value = !multiRefW.value;
                                multiRefW.callback?.(multiRefW.value);
                                _sqrSyncMultiRefIdentityMode(multiRefW.value);
                                _sqrNormalizeStoredRefsForMode(true);
                                persistSqrState();
                            } else if (key === "startupfix" && startupFixW) {
                                startupFixW.value = !startupFixW.value;
                                startupFixW.callback?.(startupFixW.value);
                                persistSqrState();
                            } else if (key === "replacement" && replacementW) {
                                replacementW.value = !replacementW.value;
                                replacementW.callback?.(replacementW.value);
                                _sqrSyncReplacementMode(replacementW.value);
                                persistSqrState();
                            } else if (key === "transition" && transitionW) {
                                transitionW.value = !transitionW.value;
                                transitionW.callback?.(transitionW.value);
                                persistSqrState();
                            } else if (key === "execute" && execW) {
                                execW.value = !execW.value;
                                execW.callback?.(execW.value);
                                persistSqrState();
                            } else if (key === "settings") {
                                settingsBtn.callback?.();
                            } else if (key === "nodeids") {
                                nodeIdBtn.callback?.();
                            } else if (key === "log") {
                                _showLogOverlay(String(nodeRef.id));
                            }
                            nodeRef.setDirtyCanvas?.(true, true);
                            return true;
                        }
                    }
                    return false;
                },
            };
            node.addCustomWidget(topBarWidget);
            const _topIdx = node.widgets.indexOf(topBarWidget);
            if (_topIdx > 0) {
                node.widgets.splice(_topIdx, 1);
                node.widgets.unshift(topBarWidget);
            }

            // ── Log button ──
            const logBtn = node.addWidget("button", "View Log", null, () => {
                _showLogOverlay(String(node.id));
            });
            logBtn.serialize = false;
            logBtn.name = tr("viewLog");
            logBtn.computeSize = () => [0, -4];
            logBtn.draw = () => {};

            // ── Resume video manager ──
            const showVideoManager = (onConfirm) => {
                document.getElementById("sqr-vidmgr-overlay")?.remove();
                let curPath = getSqr("续跑视频路径") || "";
                const overlay = document.createElement("div");overlay.id = "sqr-vidmgr-overlay";Object.assign(overlay.style, {position:"fixed",inset:"0",zIndex:"10001",background:"rgba(0,0,0,.75)",display:"flex",alignItems:"center",justifyContent:"center"});
                const box = document.createElement("div");Object.assign(box.style, {background:"var(--comfy-menu-bg,#1e1e1e)",color:"var(--input-text,#eee)",border:"1px solid var(--border-color,#444)",borderRadius:"12px",padding:"18px 22px",width:"480px",display:"flex",flexDirection:"column",gap:"10px",boxShadow:"0 8px 40px rgba(0,0,0,.7)"});
                const mkDiv=(t,s)=>Object.assign(document.createElement("div"),{textContent:t,style:s||""});
                box.appendChild(mkDiv("Selected Resume Video","font-size:14px;font-weight:600;"));
                box.appendChild(mkDiv("Right-click to remove the selected video and return to normal mode.","font-size:11px;opacity:.5;"));
                const vidArea = document.createElement("div");Object.assign(vidArea.style,{padding:"10px",border:"1px solid var(--border-color,#444)",borderRadius:"8px",minHeight:"52px"});
                function renderVid() {
                    vidArea.innerHTML = "";
                    if (!curPath) { vidArea.appendChild(mkDiv("(No resume video selected; normal mode will be used)","opacity:.4;font-size:12px;padding:4px;")); } else {
                        const fname = curPath.split(/[/\\]/).pop(); const row = document.createElement("div"); Object.assign(row.style, {display:"flex",alignItems:"center",gap:"8px",padding:"8px 10px",borderRadius:"6px",background:"rgba(60,180,120,0.12)",border:"1px solid #4a9",cursor:"default"});
                        row.innerHTML = `<span style="font-size:18px">🎬</span><span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#6df;">${fname}</span><span style="opacity:.35;font-size:10px;flex-shrink:0;">Right-click to remove</span>`;
                        row.title = curPath; row.oncontextmenu = e => { e.preventDefault(); curPath = ""; renderVid(); }; vidArea.appendChild(row); } }
                renderVid(); box.appendChild(vidArea);
                const btns = document.createElement("div"); btns.style.cssText="display:flex;gap:8px;";
                const mkBtn=(t,s,fn)=>{const b=document.createElement("button");b.textContent=t;b.style.cssText=`flex:1;padding:7px 18px;border-radius:7px;cursor:pointer;font-size:13px;${s}`;b.onclick=fn;return b;};
                btns.append(mkBtn("Disable Resume","background:rgba(180,60,60,0.2);border:1px solid rgba(200,80,80,0.5);color:#f88;",()=>{onConfirm("");overlay.remove();}),mkBtn("Cancel","",()=>overlay.remove()),mkBtn("Apply","background:#2a9;color:#fff;border:none;font-weight:600;",()=>{onConfirm(curPath);overlay.remove();}));
                box.appendChild(btns);
                const _xBtn=document.createElement("button");_xBtn.textContent="×";_xBtn.style.cssText="position:absolute;top:10px;right:12px;background:none;border:none;font-size:20px;cursor:pointer;color:var(--input-text,#aaa);line-height:1;padding:0;";_xBtn.onmouseover=()=>_xBtn.style.color="#fff";_xBtn.onmouseout=()=>_xBtn.style.color="var(--input-text,#aaa)";_xBtn.onclick=()=>overlay.remove();box.style.position="relative";box.appendChild(_xBtn);overlay.appendChild(box);overlay.onclick=e=>{if(e.target===overlay)overlay.remove();};document.body.appendChild(overlay);
            };

            const _applyVideo = (result) => {
                if (!result) return;
                const rtw = getW("启用续跑"); if (rtw) rtw.value = true;
                setSqr("续跑视频路径", result);
                const fname = result.split(/[/\\]/).pop();
                const m = fname.match(/sqr_trans_[0-9_]+_seg(\d+)\.mp4$/i) || fname.match(/sqr_trans_[a-f0-9]+_seg(\d+)\.mp4$/i) || fname.match(/segment_transition_seg(\d+)\.mp4$/i);
                if (m) {
                    const seg = parseInt(m[1]) + 1;
                    const maxSeg = segW ? Math.round(segW.value) : 100;
                    const fromW = getW("从第几段开始");
                    if (seg <= maxSeg) {
                        if (fromW) fromW.value = seg;
                        resumeBtn.name = "Manage Resume Video";
                    } else {
                        resumeBtn.name = "Manage Resume Video";
                    }
                    setTimeout(() => { resumeBtn.name = "Manage Resume Video"; node.setDirtyCanvas?.(true,true); }, 3000);
                } else {
                    resumeBtn.name = "Manage Resume Video";
                }
                node.setDirtyCanvas?.(true, true);
                resumeBtn._sqrActive = true;
                persistSqrState();
            };

            const _resumeNative = async () => {
                // Use the browser dialog consistently across local and remote environments.
                try {
                    const saved = await _sqrPickAndUploadVideo();
                    if (saved) _applyVideo(saved);
                    showVideoManager(result => { if (result) _applyVideo(result); else _clearVideo(); });
                } catch(e) { console.warn("[SQR] Resume selection failed:", e); }
            };
            const _resumeSelectDirect = () => {
                _resumeNative();
            };

            const resumeBtn = node.addWidget("button", "Select Resume Video", null, async () => {
                if (_sqrIsRemote()) { _resumeSelectDirect(); return; }
                const uid = String(node.id);
                let ckpt = null;
                try {
                    const _rvp = _getRefVideoParams();
                    const refParams = _rvp ? encodeURIComponent(JSON.stringify(_rvp)) : "";
                    const resp = await fetch(`/sqr/checkpoint?uid=${uid}&ref_params=${refParams}`);
                    const data = await resp.json();
                    const c = data.checkpoint;
                    if (c?.transition_exists && c.next_seg <= c.total_segs) ckpt = c;
                } catch(e) {}
                if (!ckpt) { _resumeSelectDirect(); return; }
                _showResumeDialog(ckpt, null);
            });
            resumeBtn.serialize = false;
            resumeBtn.draw = function(ctx, node, widget_width, y, H) {
                const active = !!this._sqrActive;
                const label = active ? tr("manageResume") : tr("selectResume");
                ctx.fillStyle = active ? "rgba(40,160,100,0.35)" : "rgba(255,255,255,0.05)";
                ctx.beginPath();
                ctx.roundRect ? ctx.roundRect(4, y+2, widget_width-8, H-4, 4) : ctx.rect(4, y+2, widget_width-8, H-4);
                ctx.fill();
                if (active) { ctx.strokeStyle = "rgba(60,200,130,0.7)"; ctx.lineWidth = 1; ctx.stroke(); }
                ctx.fillStyle = active ? "#7fffb0" : "rgba(190,190,190,0.5)";
                ctx.font = "12px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
                ctx.fillText(label, widget_width/2, y + H/2);
                ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
            };

            const resumePathWidget = {
                name: "_sqr_resume_path",
                type: "sqr_resume_path",
                serialize: false,
                computeSize(width) {
                    return getSqr("续跑视频路径") ? [width, 34] : [width, 0];
                },
                draw(ctx, nodeRef, widget_width, y) {
                    const path = getSqr("续跑视频路径");
                    if (!path) return;
                    const pad = 8;
                    const boxX = 4, boxY = y + 2, boxW = widget_width - 8, boxH = 30;
                    ctx.save();
                    ctx.fillStyle = "rgba(60,180,120,0.09)";
                    ctx.strokeStyle = "rgba(70,190,130,0.38)";
                    ctx.lineWidth = 1;
                    ctx.beginPath();
                    ctx.roundRect ? ctx.roundRect(boxX, boxY, boxW, boxH, 5) : ctx.rect(boxX, boxY, boxW, boxH);
                    ctx.fill();
                    ctx.stroke();

                    ctx.font = "bold 9px sans-serif";
                    ctx.fillStyle = "rgba(165,255,205,0.86)";
                    ctx.textAlign = "left";
                    ctx.textBaseline = "top";
                    ctx.fillText("Resume Video Path", boxX + pad, boxY + 4);

                    let text = path;
                    ctx.font = "10px sans-serif";
                    const maxW = boxW - pad * 2;
                    if (ctx.measureText(text).width > maxW) {
                        const tail = path.split(/[/\\]/).pop() || path;
                        text = tail;
                        while (text.length > 3 && ctx.measureText("..." + text).width > maxW) text = text.slice(1);
                        text = "..." + text;
                    }
                    ctx.fillStyle = "rgba(220,245,255,0.78)";
                    ctx.fillText(text, boxX + pad, boxY + 17);
                    ctx.restore();
                },
            };
            node.addCustomWidget(resumePathWidget);

            const _clearVideo = () => {
                setSqr("续跑视频路径", "");
                resumeBtn._sqrActive = false;
                const rtw = getW("启用续跑"); if (rtw) rtw.value = false;
                const fromW2 = getW("从第几段开始");
                if (fromW2) fromW2.value = 1;
                const foW = getW("sqr_frame_offset"); if (foW) foW.value = -1;
                resumeBtn.name = "Select Resume Video";
                persistSqrState();
                node.setDirtyCanvas?.(true, true);
                setTimeout(() => {
                    resumeBtn.name = "Select Resume Video";
                    node.setDirtyCanvas?.(true, true);
                }, 3000);
            };
            node._sqrClearVideo = _clearVideo;

            {
                const w = getW("sqr_save_png");
                const stateHasPng = Object.prototype.hasOwnProperty.call(node.properties?.sqr_state || {}, "sqr_save_png");
                if (w && !stateHasPng) w.value = String(node._sqrSettings.savePng ?? true);
            }
            for (const _hk of ["sqr_frame_offset", "sqr_pre_segments"]) {
                const _hw = getW(_hk);
                if (_hw) { _hw.computeSize = () => [0, -4]; _hw.draw = () => {}; }
            }
            { const w = getW("sqr_frame_offset"); if (w) w.value = -1; }

            // ── Reference image manager ──
            const showRefManager = (onConfirm) => {
                document.getElementById("sqr-mgr-overlay")?.remove();
                let groups = sqrParseRefGroups(getSqr(sqrKeys[4]));
                if (!groups.length) groups = [[]];
                let dragInfo = null;
                const overlay = document.createElement("div");overlay.id = "sqr-mgr-overlay";Object.assign(overlay.style,{position:"fixed",inset:"0",zIndex:"10001",background:"rgba(0,0,0,.75)",display:"flex",alignItems:"center",justifyContent:"center"});
                const box = document.createElement("div");Object.assign(box.style,{background:"var(--comfy-menu-bg,#1e1e1e)",color:"var(--input-text,#eee)",border:"1px solid var(--border-color,#444)",borderRadius:"12px",padding:"18px 22px",width:"760px",maxHeight:"88vh",display:"flex",flexDirection:"column",gap:"10px",boxShadow:"0 8px 40px rgba(0,0,0,.7)"});
                const mkDiv=(t,s)=>Object.assign(document.createElement("div"),{textContent:t,style:s||""});
                box.appendChild(mkDiv(tr("refGroups"),"font-size:14px;font-weight:600;"));
                box.appendChild(mkDiv(tr("refGroupsHint"),"font-size:11px;opacity:.55;line-height:1.45;"));
                const wrap = document.createElement("div");Object.assign(wrap.style,{display:"flex",flexDirection:"column",gap:"10px",minHeight:"120px",maxHeight:"520px",overflowY:"auto",padding:"10px",border:"1px solid var(--border-color,#444)",borderRadius:"8px"});
                const normalizeGroups = (notify=false, keepEmpty=true) => {
                    const normalized = _sqrNormalizeRefGroupsForMode(groups, notify);
                    if (keepEmpty) {
                        let sourceIndex = 0;
                        const withEmpty = [];
                        for (const raw of groups) {
                            const cleaned = (Array.isArray(raw) ? raw : [raw]).map(v => {
                                const path = sqrRefPath(v);
                                return path ? sqrRefEntry(path, sqrRefIsBg(v)) : null;
                            }).filter(Boolean);
                            if (!cleaned.length) {
                                withEmpty.push([]);
                            } else {
                                const chunkCount = Math.max(1, Math.ceil(cleaned.length / _sqrCurrentRefGroupLimit()));
                                for (let i = 0; i < chunkCount && sourceIndex < normalized.length; i++) {
                                    withEmpty.push(normalized[sourceIndex++]);
                                }
                            }
                        }
                        while (sourceIndex < normalized.length) withEmpty.push(normalized[sourceIndex++]);
                        groups = withEmpty.length ? withEmpty : [[]];
                    } else {
                        groups = normalized.filter(group => group.length);
                        if (!groups.length) groups = [[]];
                    }
                };
                function moveImage(gidx, idx, dir) {
                    const target = gidx + dir;
                    if (target < 0 || target >= groups.length) return;
                    const limit = _sqrCurrentRefGroupLimit();
                    if ((groups[target] || []).length >= limit) { alert(tr("refLimitExceeded")); return; }
                    const [img] = groups[gidx].splice(idx, 1);
                    groups[target].push(img);
                    renderGroups();
                }
                function renderGroups() {
                    normalizeGroups(false, true);
                    wrap.innerHTML = "";
                    const groupLimit = _sqrCurrentRefGroupLimit();
                    groups.forEach((group, gidx) => {
                        const panel = document.createElement("div");Object.assign(panel.style,{border:"1px solid var(--border-color,#555)",borderRadius:"8px",padding:"8px",background:"rgba(255,255,255,0.025)"});
                        const header = document.createElement("div");Object.assign(header.style,{display:"flex",alignItems:"center",gap:"8px",marginBottom:"8px"});
                        header.appendChild(mkDiv(`Group ${gidx+1} - ${group.length}/${groupLimit}`,`font-size:12px;font-weight:700;color:#9fd;flex:1;`));
                        const addBtn = document.createElement("button");addBtn.textContent=tr("addImages");addBtn.style.cssText="padding:4px 8px;border-radius:5px;cursor:pointer;font-size:11px;";addBtn.onclick=async()=>{ const saved=await _sqrPickAndUploadImages(); const existing = new Set(sqrFlattenRefGroups(groups)); for(const name of saved){ if(!existing.has(name)) { group.push(name); existing.add(name); } } normalizeGroups(true); renderGroups(); };
                        const delBtn = document.createElement("button");delBtn.textContent=tr("removeGroup");delBtn.style.cssText="padding:4px 8px;border-radius:5px;cursor:pointer;font-size:11px;color:#f99;";delBtn.onclick=()=>{ if(groups.length<=1){ groups=[[]]; } else { groups.splice(gidx,1); } renderGroups(); };
                        header.append(addBtn, delBtn); panel.appendChild(header);
                        const grid = document.createElement("div");Object.assign(grid.style,{display:"flex",flexWrap:"wrap",gap:"8px",minHeight:"62px"});
                        if (!group.length) grid.appendChild(mkDiv(tr("emptyGroup"),"opacity:.35;font-size:12px;padding:8px;"));
                        group.forEach((entry, idx) => {
                            const path = sqrRefPath(entry);
                            const isBg = sqrRefIsBg(entry);
                            const fname = path.split(/[/\\]/).pop();
                            const cell = document.createElement("div");Object.assign(cell.style,{width:"112px",textAlign:"center",position:"relative",border:`2px solid ${isBg ? "#d8d8d8" : "var(--border-color,#555)"}`,borderRadius:"7px",padding:"4px",cursor:"grab",userSelect:"none",background:isBg?"rgba(255,255,255,0.06)":""});cell.draggable=true;
                            const badge = mkDiv(`${gidx+1}.${idx+1}`,"position:absolute;top:2px;left:2px;background:#3a9;color:#fff;border-radius:3px;padding:0 4px;font-size:10px;font-weight:bold;line-height:16px;z-index:1;");
                            const bgRow = document.createElement("label");bgRow.style.cssText="display:flex;align-items:center;justify-content:center;gap:4px;margin-top:4px;font-size:10px;cursor:pointer;";
                            const bgRadio = document.createElement("input");bgRadio.type="radio";bgRadio.name=`sqr-bg-${gidx}-${idx}`;bgRadio.checked=isBg;bgRadio.style.cssText="margin:0;";
                            bgRadio.onclick=e=>{e.stopPropagation();group[idx]=sqrRefEntry(path,!isBg);renderGroups();};
                            bgRow.append(bgRadio, document.createTextNode("BG"));
                            const img = new Image();img.src=sqrThumbUrl(path);Object.assign(img.style,{width:"104px",height:"92px",objectFit:"contain",display:"block",borderRadius:"4px",pointerEvents:"none"});
                            const res = mkDiv("loading","font-size:9px;margin-top:2px;color:#8fd;opacity:.72;");img.onload=()=>{res.textContent=`${img.naturalWidth}x${img.naturalHeight}`;};img.onerror=()=>{res.textContent="unknown";};
                            const lbl = mkDiv(fname.length>16?fname.slice(0,15)+"...":fname,"font-size:9px;margin-top:3px;word-break:break-all;opacity:.7;");lbl.title=path;
                            const tools=document.createElement("div");tools.style.cssText="display:flex;gap:3px;margin-top:4px;";
                            const mkMini=(t,fn,disabled=false)=>{const b=document.createElement("button");b.textContent=t;b.disabled=disabled;b.style.cssText=`flex:1;font-size:9px;padding:2px;border-radius:4px;cursor:${disabled?"default":"pointer"};opacity:${disabled?".38":"1"};`;b.onclick=e=>{e.stopPropagation();if(!disabled)fn();};return b;};
                            tools.append(mkMini("Prev",()=>moveImage(gidx,idx,-1),gidx<=0),mkMini("Next",()=>moveImage(gidx,idx,1),gidx>=groups.length-1),mkMini("Remove",()=>{group.splice(idx,1);renderGroups();}));
                            cell.ondragstart=e=>{e.stopPropagation();dragInfo={gidx,idx};setTimeout(()=>cell.style.opacity=".35",0);};cell.ondragend=e=>{e.stopPropagation();cell.style.opacity="1";dragInfo=null;};
                            cell.ondragover=e=>{e.preventDefault();e.stopPropagation();cell.style.borderColor="#4a9";};cell.ondragleave=()=>{cell.style.borderColor="var(--border-color,#555)";};
                            cell.ondrop=e=>{e.preventDefault();e.stopPropagation();cell.style.borderColor="var(--border-color,#555)";if(!dragInfo)return;const [m]=groups[dragInfo.gidx].splice(dragInfo.idx,1); if(gidx!==dragInfo.gidx && group.length>=groupLimit){groups[dragInfo.gidx].splice(dragInfo.idx,0,m); alert(tr("refLimitExceeded"));} else {groups[gidx].splice(idx,0,m);} renderGroups();};
                            cell.onclick=e=>{e.stopPropagation(); if(group.length>=groupLimit){alert(tr("refLimitExceeded"));return;} group.splice(idx+1,0,path);renderGroups();};
                            cell.oncontextmenu=e=>{e.preventDefault();e.stopPropagation();group.splice(idx,1);renderGroups();};
                            cell.append(badge,img,res,lbl,bgRow,tools);grid.appendChild(cell);
                        });
                        panel.appendChild(grid);wrap.appendChild(panel);
                    });
                }
                renderGroups();box.appendChild(wrap);
                const groupBtns=document.createElement("div");groupBtns.style.cssText="display:flex;gap:8px;";
                const mkBtn=(t,s,fn)=>{const b=document.createElement("button");b.textContent=t;b.style.cssText=`flex:1;padding:7px 18px;border-radius:7px;cursor:pointer;font-size:13px;${s}`;b.onclick=fn;return b;};
                groupBtns.append(mkBtn(tr("addGroup"),"",()=>{groups.push([]);renderGroups();}),mkBtn(tr("flattenGroup"),"",()=>{groups=_sqrNormalizeRefGroupsForMode([sqrFlattenRefGroups(groups)], true);renderGroups();}));
                box.appendChild(groupBtns);
                const btns=document.createElement("div");btns.style.cssText="display:flex;gap:8px;";
                btns.append(mkBtn(tr("cancel"),"",()=>overlay.remove()),mkBtn(tr("apply"),"background:#2a9;color:#fff;border:none;font-weight:600;",()=>{normalizeGroups(false, false);onConfirm(groups);overlay.remove();}));
                box.appendChild(btns);
                overlay.appendChild(box);overlay.onclick=e=>{if(e.target===overlay)overlay.remove();};document.body.appendChild(overlay);
            };

            const _refNative = async () => {
                // Use the browser dialog consistently across local and remote environments.
                try {
                    const saved = await _sqrPickAndUploadImages();
                    if (saved.length) {
                        const cur = sqrParseRefGroups(getSqr(sqrKeys[4]));
                        if (!cur.length) cur.push([]);
                        for (const name of saved) {
                            if (sqrFlattenRefGroups(cur).includes(name)) continue;
                            let group = cur[cur.length - 1];
                            group.push(name);
                        }
                        const normalized = _sqrNormalizeRefGroupsForMode(cur, true);
                        setSqr(sqrKeys[4], sqrStoreRefGroups(normalized));
                        refThumbWidget.syncPaths();
                    }
                    showRefManager(result => { const normalized = _sqrNormalizeRefGroupsForMode(result, true); setSqr(sqrKeys[4], sqrStoreRefGroups(normalized)); refThumbWidget.syncPaths(); persistSqrState(); node.setDirtyCanvas?.(true, true); });
                } catch(e) { console.warn("[SQR] Reference image selection failed:", e); }
            };
            const refBtn = node.addWidget("button", "Select Reference Images", null, () => {
                _refNative();
            });
            refBtn.serialize = false;
            refBtn.name = tr("selectRefs");

            // ── 缩略图预览行 ──
            const refThumbWidget = {
                name: "_sqr_ref_thumbs", type: "sqr_thumbs", serialize: false,
                _paths: [], _loaded: {}, _dragSrc: -1, _dragOver: -1,
                syncPaths() {
                    this._groups = sqrParseRefGroups(getSqr(sqrKeys[4]));
                    this._paths = sqrFlattenRefGroups(this._groups);
                    this._pathLabels = [];
                    this._bgFlags = [];
                    this._groups.forEach((group, gi) => group.forEach((entry, ii) => {
                        this._pathLabels.push(this._groups.length > 1 ? `${gi + 1}.${ii + 1}` : String(ii + 1));
                        this._bgFlags.push(sqrRefIsBg(entry));
                    }));
                    const nextLoaded = {};
                    this._paths.forEach(p => { const img = new Image(); img.src = sqrThumbUrl(p); img.onload = () => node.setDirtyCanvas?.(true, true); nextLoaded[p] = img; });
                    this._loaded = nextLoaded;
                },
                computeSize(width) { if (!this._paths.length) return [width, 0]; return [width, this._minH()]; },
                _minH() { return 20 + 16; },
                _getHeaderH(node) { let h = LiteGraph.NODE_TITLE_HEIGHT ?? 26; for (const w of (node.widgets || [])) { if (w === this) break; const sz = w.computeSize ? w.computeSize(node.size[0]) : [0, LiteGraph.NODE_WIDGET_HEIGHT ?? 20]; h += (sz[1] ?? 20) + 4; } return h; },
                _getAvailH(node, width) { const headerH = this._getHeaderH(node); const totalH = node.size[1] || 300; return Math.max(this._minH(), totalH - headerH - 8); },
                _calcLayout(width, availH) { const n = this._paths.length; if (!n) return { rows: 0, cols: 0, slot: 48, n }; const gap = 6, pad = 8; const MIN_SLOT = 54, MAX_SLOT = 150; const aW = width - pad * 2; const preferredCols = Math.min(5, Math.max(2, n)); let cols = Math.min(preferredCols, Math.max(2, Math.floor((aW + gap) / (MIN_SLOT + gap)))); cols = Math.max(1, Math.min(cols, n)); let rows = Math.ceil(n / cols); let slot = Math.floor((aW - gap * (cols - 1)) / cols); slot = Math.max(MIN_SLOT, Math.min(MAX_SLOT, slot)); const maxRowsByHeight = Math.max(1, Math.floor((Math.max(this._minH(), availH) - 16 + gap) / (MIN_SLOT + gap))); while (rows > maxRowsByHeight && cols < Math.min(5, n)) { cols++; rows = Math.ceil(n / cols); slot = Math.floor((aW - gap * (cols - 1)) / cols); slot = Math.max(MIN_SLOT, Math.min(MAX_SLOT, slot)); } return { rows, cols, slot, n }; },
                _layout(width) { const availH = this._getAvailH(node, width); const { rows, cols, slot, n } = this._calcLayout(width, availH); const gap = 6, pad = 8, padV = 8; const totalW = cols * slot + (cols-1) * gap; const ox = pad + Math.max(0, (width - pad*2 - totalW) / 2); return this._paths.map((p, i) => { const col = i % cols, row = Math.floor(i / cols); const x = ox + col * (slot + gap); const y = padV + row * (slot + gap); return { p, x, y: y, w: slot, h: slot }; }); },
                draw(ctx, node, width, y) {
                    if (!this._paths.length) return;
                    const curH = node.size[1]; if (this._lastWidth !== width || this._lastHeight !== curH) { this._lastWidth = width; this._lastHeight = curH; }
                    const layout = this._layout(width);
                    layout.forEach(({p, x, y: ly, w, h}, i) => {
                        const ty = y + ly; const img = this._loaded[p];
                        if (this._dragOver === i && this._dragSrc !== i) { ctx.strokeStyle = "#4c6"; ctx.lineWidth = 2; ctx.strokeRect(x-2, ty-2, w+4, h+4); }
                        const labelH = Math.min(16, Math.max(12, Math.floor(h * 0.16)));
                        const imageH = Math.max(20, h - labelH);
                        if (img?.complete && img.naturalWidth) { const iw = img.naturalWidth, ih = img.naturalHeight; const scale = Math.min(w/iw, imageH/ih); const dw = iw*scale, dh = ih*scale; ctx.save(); if (this._dragSrc === i) ctx.globalAlpha = 0.35; ctx.drawImage(img, x+(w-dw)/2, ty+(imageH-dh)/2, dw, dh); ctx.restore(); } else { ctx.fillStyle = "#2a2a2a"; ctx.fillRect(x, ty, w, imageH); ctx.fillStyle = "#666"; ctx.font = "11px sans-serif"; ctx.textAlign = "center"; ctx.fillText("…", x+w/2, ty+imageH/2+4); }
                        ctx.fillStyle = "rgba(50,150,70,0.92)"; ctx.fillRect(x, ty, 15, 15); ctx.fillStyle = "#fff"; ctx.font = "bold 9px sans-serif"; ctx.textAlign = "center"; ctx.fillText(this._pathLabels?.[i] || String(i+1), x+7.5, ty+11);
                        if (this._bgFlags?.[i]) { ctx.fillStyle = "rgba(245,245,245,0.95)"; ctx.fillRect(x + w - 24, ty, 24, 15); ctx.fillStyle = "#111"; ctx.font = "bold 9px sans-serif"; ctx.textAlign = "center"; ctx.fillText("BG", x + w - 12, ty + 11); }
                        const res = img?.complete && img.naturalWidth ? `${img.naturalWidth}x${img.naturalHeight}` : "";
                        if (res) {
                            ctx.fillStyle = "rgba(0,0,0,0.45)";
                            ctx.fillRect(x, ty + h - labelH, w, labelH);
                            ctx.fillStyle = "rgba(210,245,255,0.9)";
                            ctx.font = `${Math.max(8, Math.min(11, labelH - 3))}px sans-serif`;
                            ctx.textAlign = "center";
                            ctx.textBaseline = "middle";
                            ctx.fillText(res, x + w / 2, ty + h - labelH / 2);
                        }
                    });
                    ctx.textAlign = "left";
                },
                _idxAt(lx, ly, width) { return this._layout(width).findIndex(({x, y: iy, w, h}) => lx >= x && lx <= x+w && ly >= iy && ly <= iy+h); },
                mouse(evt, pos, node) {
                    if (!this._paths.length) return false;
                    if ((this._groups?.length || 0) > 1) return false;
                    const lx = pos[0], ly = pos[1], w = node.size[0];
                    if (evt.type === "mousedown" && evt.button === 0) { const i = this._idxAt(lx, ly, w); if (i >= 0) { this._dragSrc = i; this._dragOver = i; return true; } }
                    if (evt.type === "mousemove" && this._dragSrc >= 0) { const i = this._idxAt(lx, ly, w); if (i >= 0) this._dragOver = i; node.setDirtyCanvas?.(true, true); return true; }
                    if (evt.type === "mouseup" && this._dragSrc >= 0) { const src = this._dragSrc, over = this._dragOver; this._dragSrc = -1; this._dragOver = -1; if (src !== over && over >= 0) { const arr = [...this._paths]; const [m] = arr.splice(src, 1); arr.splice(over, 0, m); setSqr("分段参考图", sqrStoreRefPaths(arr)); this.syncPaths(); } node.setDirtyCanvas?.(true, true); return true; }
                    return false;
                }
            };
            node.addCustomWidget(refThumbWidget);

            setTimeout(() => {
                refThumbWidget.syncPaths();
                const p = getSqr("续跑视频路径");
                if (p) {
                    const fname = p.split(/[/\\]/).pop();
                    const _availPx2 = Math.max(40, (node.size?.[0] || 200) - 62);
                    const _tc2 = document.createElement("canvas").getContext("2d");
                    _tc2.font = "13px sans-serif";
                    let _dn2 = fname;
                    while (_dn2.length > 2 && _tc2.measureText(_dn2 + "…").width > _availPx2) { _dn2 = _dn2.slice(0, -1); }
                    if (_dn2 !== fname) _dn2 = _dn2.slice(0, -1) + "…";
                    resumeBtn.name = "Manage Resume Video";
                    resumeBtn._sqrActive = true;
                }
                node.setDirtyCanvas?.(true, true);
            }, 100);

            function _getRefVideoParams() {
                try {
                    const vidNodeId = getSqr("参考视频节点ID"); if (!vidNodeId) return null;
                    const vidNode = app.graph?.getNodeById?.(parseInt(vidNodeId)); if (!vidNode) return null;
                    const getW2 = name => vidNode.widgets?.find(w => w.name === name);
                    const videoW = getW2("video") || vidNode.widgets?.[0];
                    return { video: videoW?.value ? String(videoW.value).split(/[/\\]/).pop() : "", force_rate: getW2("force_rate")?.value ?? 0, frame_load_cap: getW2("frame_load_cap")?.value ?? 0, skip_first_frames: getW2("skip_first_frames")?.value ?? 0, select_every_nth: getW2("select_every_nth")?.value ?? 1 };
                } catch(e) { return null; }
            }
            function _getRefVideoName() { return _getRefVideoParams()?.video || ""; }

            if (!_sqrIsRemote()) {
                setTimeout(async () => {
                    const uid = String(node.id); if (!uid || uid === "undefined") return;
                    try {
                        const _rvp = _getRefVideoParams(); const refParams = _rvp ? encodeURIComponent(JSON.stringify(_rvp)) : "";
                        const resp = await fetch(`/sqr/checkpoint?uid=${uid}&ref_params=${refParams}`);
                        const data = await resp.json(); const ckpt = data.checkpoint;
                        if (!ckpt) return; if (!ckpt.transition_exists) return; if (ckpt.next_seg > ckpt.total_segs) return;
                        _showCheckpointBanner(ckpt);
                    } catch(e) {}
                }, 300);
            }

            function _showCheckpointBanner(ckpt) {
                if (node._sqrCheckpointBanner) return; node._sqrCheckpointBanner = true;
                const bannerBtn = node.addWidget("button", `Interrupted at segment ${ckpt.completed_seg}/${ckpt.total_segs}; choose resume mode`, null, () => _showResumeDialog(ckpt, bannerBtn));
                bannerBtn.serialize = false;
                bannerBtn.draw = function(ctx, node, widget_width, y, H) {
                    ctx.fillStyle = this._hover ? "rgba(255,160,0,0.45)" : "rgba(255,160,0,0.28)";
                    ctx.beginPath(); if (ctx.roundRect) ctx.roundRect(4, y+2, widget_width-8, H-4, 4); else ctx.rect(4, y+2, widget_width-8, H-4); ctx.fill();
                    ctx.strokeStyle = "rgba(255,160,0,0.8)"; ctx.lineWidth = 1; ctx.stroke();
                    ctx.fillStyle = "#ffcc00"; ctx.font = "bold 11px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
                    ctx.fillText(this.name, widget_width / 2, y + H / 2); ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
                };
                const idx = node.widgets.indexOf(bannerBtn); if (idx > 0) { node.widgets.splice(idx, 1); node.widgets.unshift(bannerBtn); }
                node.setDirtyCanvas?.(true, true);
            }

            function _showResumeDialog(ckpt, bannerWidget) {
                document.getElementById("sqr-ckpt-overlay")?.remove();
                const curSeg = Number(getW("分段数")?.value ?? ckpt.segments); const segChanged = curSeg !== Number(ckpt.segments);
                const lvBad = ckpt.ref_video_match === false; const ckptParams = ckpt.ref_video_params || {};
                const mNames = { video:"reference video", force_rate:"force rate", frame_load_cap:"frame load cap", skip_first_frames:"skip first frames", select_every_nth:"select every nth" };
                const lvStr = (ckpt.ref_video_mismatches||[]).map(k=>mNames[k]||k).join("、");

                const overlay = document.createElement("div");overlay.id = "sqr-ckpt-overlay";Object.assign(overlay.style,{position:"fixed",inset:"0",zIndex:"10000",background:"rgba(0,0,0,.75)",display:"flex",alignItems:"center",justifyContent:"center"});
                const box = document.createElement("div");Object.assign(box.style,{background:"var(--comfy-menu-bg,#1e1e1e)",color:"var(--input-text,#eee)",border:"2px solid rgba(255,160,0,0.6)",borderRadius:"12px",padding:"20px 24px",width:"500px",maxHeight:"90vh",overflowY:"auto",display:"flex",flexDirection:"column",gap:"10px",boxShadow:"0 8px 40px rgba(0,0,0,.7)",position:"relative"});
                const mkDiv=(t,s)=>Object.assign(document.createElement("div"),{textContent:t,style:s||""});

                box.appendChild(mkDiv("Interrupted Run Detected","font-size:15px;font-weight:700;color:#ffcc00;"));
                const infoDiv = document.createElement("div");infoDiv.style.cssText="font-size:12px;background:rgba(255,255,255,0.05);padding:8px 10px;border-radius:6px;line-height:1.9;";
                infoDiv.innerHTML = `Completed: segment ${ckpt.completed_seg} / ${ckpt.total_segs} &nbsp;·&nbsp; Segments: <span style="color:#6df">${ckpt.segments}</span> &nbsp;·&nbsp; Resume video: <span style="color:#6df">${ckpt.transition_video}</span> &nbsp;·&nbsp; Time: ${ckpt.timestamp}`;
                box.appendChild(infoDiv);

                const warns = [];
                if (segChanged) warns.push(`Segment count changed from ${ckpt.segments} to ${curSeg}; auto resume will restore ${ckpt.segments}.`);
                if (lvBad) warns.push(`Load Video settings changed (${lvStr}); auto resume will restore the original values.`);
                if (warns.length) { const w = document.createElement("div"); w.style.cssText="font-size:12px;color:#ffaa44;padding:6px 10px;border:1px solid rgba(255,160,0,0.35);border-radius:6px;display:flex;flex-direction:column;gap:3px;"; warns.forEach(t => w.appendChild(mkDiv(`⚠ ${t}`))); box.appendChild(w); }

                const applyAndClose = (mode, opts={}) => {
                    let fo;
                    if (mode === "auto") { const base = typeof ckpt.base_frame_offset === "number" && ckpt.base_frame_offset > 0 ? ckpt.base_frame_offset : -1; fo = base; }
                    else { const redesignFo = typeof ckpt.frame_offset_for_resume === "number" && ckpt.frame_offset_for_resume > 0 ? ckpt.frame_offset_for_resume : -1; fo = redesignFo; }
                    const foW = getW("sqr_frame_offset"); if (foW) foW.value = fo;
                    setSqr("续跑视频路径", ckpt.transition_video);
                    const rtw = getW("启用续跑"); if (rtw) rtw.value = true;
                    resumeBtn._sqrActive = true; resumeBtn.name = "Manage Resume Video";
                    const fromW = getW("从第几段开始"); const segWw = getW("分段数");
                    if (mode === "auto") {
                        _sqrEnsureSegCapacity(ckpt.segments);
                        if (segWw) segWw.value = ckpt.segments;
                        if (fromW) fromW.value = Math.min(ckpt.next_seg, ckpt.total_segs);
                        if (lvBad) { try { const vn = app.graph?.getNodeById?.(parseInt(getSqr("参考视频节点ID"))); if (vn) { const sv=(n,v)=>{const w=vn.widgets?.find(w=>w.name===n);if(w)w.value=v;}; sv("video",ckptParams.video);sv("force_rate",ckptParams.force_rate);sv("frame_load_cap",ckptParams.frame_load_cap);sv("skip_first_frames",ckptParams.skip_first_frames);sv("select_every_nth",ckptParams.select_every_nth);vn.setDirtyCanvas?.(true,true); } } catch(e) {} }
                        if (ckpt.ref_image_groups?.length) { const si = Math.min(ckpt.next_seg-1, ckpt.ref_image_groups.length-1); const sl = ckpt.ref_image_groups.slice(si); if (sl.length) setSqr(sqrKeys[4], sqrStoreRefGroups(sl)); }
                        else if (ckpt.ref_images?.length) { const si = Math.min(ckpt.next_seg-1, ckpt.ref_images.length-1); const sl = ckpt.ref_images.slice(si); if (sl.length) setSqr(sqrKeys[4], sqrStoreRefPaths(sl)); }
                    } else {
                        if (fromW) fromW.value = 1;
                        if (opts.newSegCount) _sqrEnsureSegCapacity(opts.newSegCount);
                        if (opts.newSegCount && segWw) segWw.value = opts.newSegCount;
                        if (opts.newRefs?.length) setSqr("分段参考图", sqrStoreRefPaths(opts.newRefs));
                    }
                    if (segWw && startW) { startW.options.max = Math.round(segWw.value); if (startW.value > startW.options.max) startW.value = startW.options.max; }
                    const tw = node.widgets?.find(w=>w.name==="_sqr_ref_thumbs"); if (tw) tw.syncPaths?.();
                    if (bannerWidget) { node._sqrCheckpointBanner = false; const bi = node.widgets?.indexOf(bannerWidget); if (bi>=0) node.widgets.splice(bi,1); }
                    persistSqrState();
                    overlay.remove(); node.setDirtyCanvas?.(true,true);
                };

                const mkCard = (emoji, title, hint, borderClr, clickFn, bodyEl) => {
                    const card = document.createElement("div");card.style.cssText=`border:1.5px solid ${borderClr};border-radius:8px;overflow:hidden;`;
                    const hdr = document.createElement("div");hdr.style.cssText="padding:10px 14px;cursor:pointer;display:flex;align-items:baseline;gap:8px;";
                    hdr.onmouseover=()=>hdr.style.background="rgba(255,255,255,0.05)";hdr.onmouseout=()=>hdr.style.background="";
                    hdr.appendChild(mkDiv(`${emoji}  ${title}`,`font-size:13px;font-weight:600;color:${borderClr};`));
                    hdr.appendChild(mkDiv(hint,"font-size:11px;opacity:.6;flex:1;"));
                    hdr.onclick = clickFn; card.appendChild(hdr);
                    if (bodyEl) { bodyEl.style.display="none"; card.appendChild(bodyEl); hdr.onclick = () => { bodyEl.style.display = bodyEl.style.display==="none" ? "block" : "none"; clickFn?.(); }; }
                    return card;
                };

                box.appendChild(mkCard("×","Disable Resume","Start fresh without stitching to the previous run.","rgba(200,80,80,0.7)",()=>{_clearVideo();overlay.remove();}));
                const autoHints = []; if (segChanged) autoHints.push(`restore ${ckpt.segments} segments`); if (lvBad) autoHints.push("restore Load Video settings");
                const autoHint = autoHints.length ? `Recommended · Will ${autoHints.join(", ")}` : "Recommended · Apply the checkpoint, then adjust references if needed.";
                box.appendChild(mkCard("✓","Auto Resume",autoHint,"rgba(30,170,130,0.8)",()=>applyAndClose("auto")));

                let newRefs = [];
                const redesignBody = document.createElement("div");redesignBody.style.cssText="padding:6px 14px 12px;border-top:1px solid rgba(255,255,255,0.08);display:flex;flex-direction:column;gap:8px;";
                const segRow=document.createElement("div");segRow.style.cssText="display:flex;align-items:center;gap:8px;";
                segRow.appendChild(mkDiv("Remaining segment count:","font-size:12px;flex-shrink:0;"));
                const segInp=document.createElement("input");segInp.type="number";segInp.min="1";segInp.max="100";
                segInp.value=String(getW("分段数")?.value??ckpt.segments);
                Object.assign(segInp.style,{width:"60px",padding:"4px 8px",borderRadius:"5px",fontSize:"13px",background:"var(--comfy-input-bg,#333)",color:"var(--input-text,#eee)",border:"1px solid var(--border-color,#555)"});
                segRow.appendChild(segInp);redesignBody.appendChild(segRow);
                const refRow=document.createElement("div");refRow.style.cssText="display:flex;align-items:center;gap:8px;flex-wrap:wrap;";
                refRow.appendChild(mkDiv("Resume reference images:","font-size:12px;flex-shrink:0;"));
                const refInfo=mkDiv("(None selected; use current node settings)","font-size:11px;opacity:.5;");
                const refPickBtn=document.createElement("button");refPickBtn.textContent="Select";refPickBtn.style.cssText="padding:4px 10px;border-radius:5px;cursor:pointer;font-size:12px;";
                refPickBtn.onclick=async()=>{
                    // Use the browser dialog consistently across local and remote environments.
                    try { const saved = await _sqrPickAndUploadImages(); if (saved.length) { newRefs = saved; refInfo.textContent=`${newRefs.length} selected`; refInfo.style.opacity="1"; } } catch(e) {}
                };
                refRow.append(refPickBtn,refInfo);redesignBody.appendChild(refRow);
                const confirmRD=document.createElement("button");confirmRD.textContent="Apply Redesigned Resume";confirmRD.style.cssText="flex:1;padding:8px 14px;border-radius:7px;cursor:pointer;font-size:13px;background:#2a9;color:#fff;border:none;font-weight:600;margin-top:2px;";
                confirmRD.onclick=()=>applyAndClose("redesign",{newSegCount:Math.max(1,parseInt(segInp.value)||1),newRefs:newRefs.length?newRefs:null});
                redesignBody.appendChild(confirmRD);
                box.appendChild(mkCard("Edit","Redesign Resume","Customize remaining segment count and reference images.","rgba(200,150,30,0.8)", null, redesignBody));
                box.appendChild(mkCard("File","Manual Resume","Choose a video file without checkpoint guidance.","rgba(120,120,120,0.7)", ()=>{ overlay.remove(); _resumeSelectDirect(); }));

                const _xBtn=document.createElement("button");_xBtn.textContent="×";_xBtn.style.cssText="position:absolute;top:10px;right:12px;background:none;border:none;font-size:20px;cursor:pointer;color:var(--input-text,#aaa);line-height:1;padding:0;";_xBtn.onmouseover=()=>_xBtn.style.color="#fff";_xBtn.onmouseout=()=>_xBtn.style.color="var(--input-text,#aaa)";_xBtn.onclick=()=>overlay.remove();
                box.appendChild(_xBtn);overlay.appendChild(box);overlay.onclick=e=>{if(e.target===overlay)overlay.remove();};document.body.appendChild(overlay);
            }

            return r;
        };
    }
});
