const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

function installStyles() {
    if (document.getElementById("lh-detail-editor-style")) return;
    const style = document.createElement("style");
    style.id = "lh-detail-editor-style";
    style.textContent = `
      .lh-detail-preview{position:relative;display:flex;align-items:center;justify-content:center;width:100%;height:100%;overflow:hidden}
      .lh-detail-preview canvas{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);max-width:100%;max-height:78vh;object-fit:contain}
      .lh-detail-after{clip-path:inset(0 0 0 0)}
      .lh-detail-divider{position:absolute;z-index:3;top:0;bottom:0;width:2px;background:#fff;box-shadow:0 0 5px #000;pointer-events:none}
      .lh-detail-compare{display:flex!important;gap:6px;align-items:center!important;flex-direction:row!important}
      .lh-detail-compare input{flex:1}
      .lh-detail-topbar{display:flex;align-items:center;gap:7px}.lh-detail-topbar b{flex:1}.lh-detail-topbar button{white-space:nowrap}
    `;
    document.head.appendChild(style);
}

window.LHImageDetailEditor = async function ({ layer, source, lang = "zh", onApply = () => {} }) {
    installStyles();
    const zh = lang === "zh";
    const defaults = { exposure: 0, contrast: 1, highlights: 0, shadows: 0, whites: 0, blacks: 0, temperature: 0, tint: 0, hue: 0, saturation: 1, vibrance: 0, texture: 0, clarity: 0, sharpness: 1, denoise: 0, blur: 0 };
    const draft = {};
    for (const key in defaults) draft[key] = Number(layer[`detail_${key}`] ?? defaults[key]);
    const channels = ["y", "r", "g", "b"];
    const curves = {};
    for (const channel of channels) {
        const saved = layer[`detail_curve_${channel}`];
        curves[channel] = Array.isArray(saved) && saved.length >= 2 ? saved.map(point => [Number(point[0]), Number(point[1])]) : [[0, 0], [1, 1]];
    }
    const groups = [
        [zh ? "光线" : "Light", [["exposure", zh ? "曝光" : "Exposure", -3, 3, .05], ["contrast", zh ? "对比度" : "Contrast", .5, 1.5, .01], ["highlights", zh ? "高光" : "Highlights", -1, 1, .01], ["shadows", zh ? "阴影" : "Shadows", -1, 1, .01], ["whites", zh ? "白色色阶" : "Whites", -1, 1, .01], ["blacks", zh ? "黑色色阶" : "Blacks", -1, 1, .01]]],
        [zh ? "颜色" : "Color", [["temperature", zh ? "色温" : "Temperature", -100, 100, 1], ["tint", zh ? "色调" : "Tint", -100, 100, 1], ["hue", zh ? "色相" : "Hue", -180, 180, 1], ["saturation", zh ? "饱和度" : "Saturation", 0, 2, .01], ["vibrance", zh ? "自然饱和度" : "Vibrance", -1, 1, .01]]],
        [zh ? "细节" : "Detail", [["texture", zh ? "纹理" : "Texture", -1, 1, .01], ["clarity", zh ? "清晰度" : "Clarity", -1, 1, .01], ["sharpness", zh ? "锐度" : "Sharpness", 0, 3, .05], ["denoise", zh ? "降噪" : "Denoise", 0, 1, .01], ["blur", zh ? "模糊" : "Blur", 0, 20, .1]]],
    ];
    const controls = groups.flatMap(group => group[1]);
    const fields = items => items.map(([key, label, min, max, step]) => `<label>${label} <span data-value="${key}"></span><input data-detail="${key}" type="range" min="${min}" max="${max}" step="${step}"></label>`).join("");
    const editor = document.createElement("div");
    editor.className = "wad2-layer-edit-modal";
    editor.innerHTML = `<div class="wad2-layer-edit-box"><div class="wad2-layer-edit-stage"><div class="lh-detail-preview"><canvas data-before></canvas><canvas class="lh-detail-after" data-after></canvas><i class="lh-detail-divider"></i></div></div><div class="wad2-layer-edit-controls"><div class="lh-detail-topbar"><b>${zh ? "细节调整 · 曲线" : "Detail Adjustment · Curves"}</b><button data-undo disabled>${zh ? "撤销" : "Undo"} <span>0/15</span></button></div><label class="lh-detail-compare"><span>${zh ? "修改前" : "Before"}</span><input data-compare type="range" min="0" max="100" value="100"><span>${zh ? "修改后" : "After"}</span></label><div class="wad2-curve-panel"><div class="wad2-curve-toolbar"><span>${zh ? "通道" : "Channel"}</span>${channels.map(channel => `<button data-channel="${channel}">${channel.toUpperCase()}</button>`).join("")}<button data-curve-reset>${zh ? "当前通道复位" : "Reset Channel"}</button></div><canvas class="wad2-curve-canvas" width="720" height="360"></canvas><span class="wad2-curve-help">${zh ? "点击添加控制点，拖动调整，右键删除中间点。" : "Click to add, drag to adjust, right-click to delete a middle point."}</span></div><div class="wad2-detail-panels">${groups.map(([title, items]) => `<fieldset class="wad2-detail-group"><legend>${title}</legend>${fields(items)}</fieldset>`).join("")}</div><div class="wad2-compose-actions"><button data-reset>${zh ? "全部复位" : "Reset All"}</button><button data-apply>${zh ? "应用" : "Apply"}</button><button data-cancel>${zh ? "关闭" : "Close"}</button></div></div></div>`;
    document.body.appendChild(editor);

    const before = editor.querySelector("[data-before]");
    const after = editor.querySelector("[data-after]");
    const bctx = before.getContext("2d");
    const actx = after.getContext("2d");
    const iw = source.naturalWidth || source.width;
    const ih = source.naturalHeight || source.height;
    const ratio = Math.min(1, 900 / Math.max(iw, ih));
    before.width = after.width = Math.max(1, Math.round(iw * ratio));
    before.height = after.height = Math.max(1, Math.round(ih * ratio));
    bctx.drawImage(source, 0, 0, before.width, before.height);
    actx.drawImage(source, 0, 0, after.width, after.height);
    const base = bctx.getImageData(0, 0, before.width, before.height);
    const curveCanvas = editor.querySelector(".wad2-curve-canvas");
    const cctx = curveCanvas.getContext("2d");
    const history = [];
    const snapshot = () => ({ draft: { ...draft }, curves: Object.fromEntries(channels.map(channel => [channel, curves[channel].map(point => [...point])])) });
    const pushHistory = () => { history.push(snapshot()); if (history.length > 15) history.shift(); updateUndo() };
    const restore = state => { Object.assign(draft, state.draft); for (const channel of channels) curves[channel] = state.curves[channel].map(point => [...point]); refresh() };
    const updateUndo = () => { const button = editor.querySelector("[data-undo]"); button.disabled = !history.length; button.querySelector("span").textContent = `${history.length}/15` };
    const lutFor = channel => {
        const points = curves[channel].slice().sort((a, b) => a[0] - b[0]);
        const lut = new Uint8Array(256);
        for (let value = 0; value < 256; value++) {
            const x = value / 255;
            let right = points.findIndex(point => point[0] >= x);
            if (right < 0) right = points.length - 1;
            const left = Math.max(0, right - 1), a = points[left], b = points[right];
            const mix = a === b ? 0 : (x - a[0]) / Math.max(.0001, b[0] - a[0]);
            lut[value] = Math.round(clamp(a[1] + (b[1] - a[1]) * mix, 0, 1) * 255);
        }
        return lut;
    };
    const colors = { y: "#eee", r: "#ff4d55", g: "#40db62", b: "#4b76ff" };
    let active = "y", dragIndex = -1, frame = 0;
    const drawCurve = () => {
        const w = curveCanvas.width, h = curveCanvas.height;
        cctx.clearRect(0, 0, w, h); cctx.strokeStyle = "#34373d"; cctx.lineWidth = 1;
        for (let i = 0; i <= 10; i++) { cctx.beginPath(); cctx.moveTo(i * w / 10, 0); cctx.lineTo(i * w / 10, h); cctx.stroke(); cctx.beginPath(); cctx.moveTo(0, i * h / 10); cctx.lineTo(w, i * h / 10); cctx.stroke() }
        for (const channel of channels) {
            const points = curves[channel].slice().sort((a, b) => a[0] - b[0]);
            cctx.strokeStyle = colors[channel]; cctx.globalAlpha = channel === active ? 1 : .25; cctx.lineWidth = channel === active ? 3 : 1.5; cctx.beginPath();
            points.forEach((point, index) => index ? cctx.lineTo(point[0] * w, (1 - point[1]) * h) : cctx.moveTo(point[0] * w, (1 - point[1]) * h)); cctx.stroke();
            if (channel === active) for (const point of points) { cctx.fillStyle = "#ddd"; cctx.beginPath(); cctx.arc(point[0] * w, (1 - point[1]) * h, 6, 0, Math.PI * 2); cctx.fill() }
        }
        cctx.globalAlpha = 1;
    };
    const refresh = () => {
        if (frame) return;
        frame = requestAnimationFrame(() => {
            frame = 0;
            const output = new ImageData(new Uint8ClampedArray(base.data), base.width, base.height);
            const master = lutFor("y"), red = lutFor("r"), green = lutFor("g"), blue = lutFor("b");
            const exposure = Math.pow(2, draft.exposure);
            const temperature = draft.temperature / 100;
            const tint = draft.tint / 100;
            for (let i = 0; i < output.data.length; i += 4) {
                let r = base.data[i] / 255 * exposure;
                let g = base.data[i + 1] / 255 * exposure;
                let b = base.data[i + 2] / 255 * exposure;
                const luminance = clamp((r + g + b) / 3, 0, 1);
                const tone = draft.highlights * luminance * luminance * .35
                    + draft.shadows * (1 - luminance) * (1 - luminance) * .35
                    + draft.whites * Math.pow(luminance, 4) * .3
                    + draft.blacks * Math.pow(1 - luminance, 4) * .3;
                r = (r + tone - .5) * draft.contrast + .5;
                g = (g + tone - .5) * draft.contrast + .5;
                b = (b + tone - .5) * draft.contrast + .5;
                r *= 1 + .25 * temperature + .08 * tint;
                g *= 1 - .16 * tint;
                b *= 1 - .25 * temperature + .08 * tint;
                output.data[i] = red[master[Math.round(clamp(r, 0, 1) * 255)]];
                output.data[i + 1] = green[master[Math.round(clamp(g, 0, 1) * 255)]];
                output.data[i + 2] = blue[master[Math.round(clamp(b, 0, 1) * 255)]];
            }
            actx.putImageData(output, 0, 0);
            after.style.filter = `contrast(${Math.max(.1, 1 + draft.clarity * .18)}) saturate(${Math.max(0, draft.saturation * (1 + draft.vibrance * .35))}) hue-rotate(${draft.hue}deg) blur(${draft.blur * .25}px)`;
            for (const [key] of controls) { editor.querySelector(`[data-detail="${key}"]`).value = draft[key]; editor.querySelector(`[data-value="${key}"]`).textContent = Number(draft[key]).toFixed(["temperature", "tint", "hue"].includes(key) ? 0 : 2) }
            drawCurve();
        });
    };

    editor.querySelectorAll("[data-channel]").forEach(button => button.onclick = () => { active = button.dataset.channel; editor.querySelectorAll("[data-channel]").forEach(item => item.classList.toggle("on", item === button)); drawCurve() });
    editor.querySelector('[data-channel="y"]').click();
    const curvePoint = event => { const rect = curveCanvas.getBoundingClientRect(); return [clamp((event.clientX - rect.left) / rect.width, 0, 1), clamp(1 - (event.clientY - rect.top) / rect.height, 0, 1)] };
    curveCanvas.onpointerdown = event => { pushHistory(); const point = curvePoint(event), points = curves[active], nearest = points.reduce((best, item, index) => { const distance = Math.hypot((item[0] - point[0]) * curveCanvas.width, (item[1] - point[1]) * curveCanvas.height); return distance < best.distance ? { index, distance } : best }, { index: -1, distance: 18 }); if (nearest.index >= 0) dragIndex = nearest.index; else { points.push(point); points.sort((a, b) => a[0] - b[0]); dragIndex = points.indexOf(point) } curveCanvas.setPointerCapture(event.pointerId); refresh() };
    curveCanvas.onpointermove = event => { if (dragIndex < 0 || !curveCanvas.hasPointerCapture(event.pointerId)) return; const point = curvePoint(event), points = curves[active], endpoint = dragIndex === 0 || dragIndex === points.length - 1, min = dragIndex > 0 ? points[dragIndex - 1][0] + .001 : 0, max = dragIndex < points.length - 1 ? points[dragIndex + 1][0] - .001 : 1; points[dragIndex] = [endpoint ? points[dragIndex][0] : clamp(point[0], min, max), point[1]]; refresh() };
    curveCanvas.onpointerup = () => dragIndex = -1;
    curveCanvas.oncontextmenu = event => { event.preventDefault(); const point = curvePoint(event), points = curves[active], nearest = points.reduce((best, item, index) => { const distance = Math.hypot((item[0] - point[0]) * curveCanvas.width, (item[1] - point[1]) * curveCanvas.height); return distance < best.distance ? { index, distance } : best }, { index: -1, distance: 18 }); if (nearest.index > 0 && nearest.index < points.length - 1) { pushHistory(); points.splice(nearest.index, 1); refresh() } };
    editor.querySelector("[data-curve-reset]").onclick = () => { pushHistory(); curves[active] = [[0, 0], [1, 1]]; refresh() };
    for (const [key] of controls) { const input = editor.querySelector(`[data-detail="${key}"]`); input.onpointerdown = () => pushHistory(); input.onkeydown = event => { if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) pushHistory() }; input.oninput = event => { draft[key] = Number(event.target.value); refresh() } }
    editor.querySelector("[data-undo]").onclick = () => { const state = history.pop(); if (state) restore(state); updateUndo() };
    editor.querySelector("[data-reset]").onclick = () => { pushHistory(); Object.assign(draft, defaults); for (const channel of channels) curves[channel] = [[0, 0], [1, 1]]; refresh() };
    const compare = editor.querySelector("[data-compare]"), divider = editor.querySelector(".lh-detail-divider");
    compare.oninput = () => { const value = Number(compare.value); after.style.clipPath = `inset(0 ${100 - value}% 0 0)`; divider.style.left = `${value}%` };
    compare.oninput();
    editor.querySelector("[data-cancel]").onclick = () => { if (frame) cancelAnimationFrame(frame); editor.remove() };
    editor.querySelector("[data-apply]").onclick = () => { for (const key in defaults) layer[`detail_${key}`] = draft[key]; for (const channel of channels) layer[`detail_curve_${channel}`] = curves[channel].map(point => [...point]); delete layer.detail_brightness; onApply(); editor.remove() };
    refresh();
};
