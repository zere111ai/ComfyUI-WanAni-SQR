const { app } = window.comfyAPI.app;

const WAD_KEYS = {
  totalFrames: "总帧数",
  frameRate: "帧率",
  transition: "启用过渡效果",
  segments: "分段数",
  start: "从第几段开始",
  execute: "执行",
  resume: "启用续跑",
  refVideoId: "参考视频节点ID",
  outputId: "输出节点ID",
  animateId: "动作嵌入节点ID",
  refId: "参考图节点ID",
  refs: "分段参考图",
  resumePath: "续跑视频路径",
  multiRef: "multi_ref_enabled",
  replacement: "replacement_enabled",
  data: "director_data",
};

function wadHideWidget(w) {
  if (!w) return;
  w.hidden = true;
  w.computeSize = () => [0, 0];
  w.draw = () => {};
  w.mouse = () => false;
  w.options = Object.assign({}, w.options || {}, { hidden: true });
  if (w.element) w.element.style.display = "none";
}

function wadGetWidget(node, name) {
  return node.widgets?.find(w => w.name === name);
}

function wadSetWidget(node, name, value) {
  const w = wadGetWidget(node, name);
  if (!w) return;
  w.value = value;
  w.callback?.(value);
  node.setDirtyCanvas?.(true, true);
}

function wadReadBool(node, name) {
  return !!wadGetWidget(node, name)?.value;
}

function wadReadInt(node, name, fallback = 0) {
  const v = Number.parseInt(wadGetWidget(node, name)?.value ?? "", 10);
  return Number.isFinite(v) ? v : fallback;
}

function wadReadString(node, name) {
  return String(wadGetWidget(node, name)?.value ?? "");
}

function wadInjectStyles() {
  if (document.getElementById("wan-ani-director-styles")) return;
  const style = document.createElement("style");
  style.id = "wan-ani-director-styles";
  style.textContent = `
    .wad-root { width:100%; box-sizing:border-box; display:flex; flex-direction:column; gap:8px; color:#e8e8e8; font-family:ui-sans-serif,system-ui,Segoe UI,Arial; }
    .wad-head { display:flex; align-items:center; justify-content:space-between; gap:8px; }
    .wad-title { font-size:13px; font-weight:700; letter-spacing:.02em; }
    .wad-time { font:12px ui-monospace,SFMono-Regular,Consolas,monospace; color:#9fd; }
    .wad-row { display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
    .wad-btn { border:1px solid #333; background:#242424; color:#ddd; border-radius:5px; padding:5px 8px; font-size:11px; cursor:pointer; }
    .wad-btn:hover { background:#303030; border-color:#555; }
    .wad-btn.on { background:#1d5138; border-color:#43b978; color:#f2fff7; }
    .wad-btn.warn.on { background:#5a3820; border-color:#d78d45; }
    .wad-num, .wad-text { background:#1d1d1d; border:1px solid #333; color:#e8e8e8; border-radius:5px; padding:4px 6px; font-size:11px; box-sizing:border-box; }
    .wad-num { width:64px; }
    .wad-text { width:100%; min-width:0; }
    .wad-label { font-size:10px; color:#999; margin-right:2px; }
    .wad-panel { border:1px solid #262626; background:#191919; border-radius:7px; padding:8px; display:flex; flex-direction:column; gap:7px; }
    .wad-canvas { width:100%; height:156px; border-radius:6px; background:#242424; border:1px solid #101010; display:block; }
    .wad-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:6px; }
    .wad-small { font-size:10px; color:#aaa; line-height:1.35; }
    .wad-area { height:56px; resize:vertical; font-family:ui-monospace,SFMono-Regular,Consolas,monospace; }
  `;
  document.head.appendChild(style);
}

class WanAniDirectorUI {
  constructor(node, container, widget) {
    this.node = node;
    this.widget = widget;
    this.container = container;
    this.canvas = null;
    this.ctx = null;
    this.build();
    this.syncFromWidgets();
    this.render();
  }

  build() {
    wadInjectStyles();
    const root = document.createElement("div");
    root.className = "wad-root";
    this.root = root;

    const head = document.createElement("div");
    head.className = "wad-head";
    head.innerHTML = `<div class="wad-title">WAN ANI DIRECTOR</div><div class="wad-time">00:00 / 0 frames</div>`;
    this.timeEl = head.lastElementChild;
    root.appendChild(head);

    const toggles = document.createElement("div");
    toggles.className = "wad-row";
    this.multiBtn = this.makeToggle("Multi Ref", WAD_KEYS.multiRef);
    this.replaceBtn = this.makeToggle("Replacement", WAD_KEYS.replacement, "warn");
    this.transBtn = this.makeToggle("Transition", WAD_KEYS.transition);
    this.execBtn = this.makeToggle("Execute", WAD_KEYS.execute, "warn");
    toggles.append(this.multiBtn, this.replaceBtn, this.transBtn, this.execBtn);
    root.appendChild(toggles);

    const panel = document.createElement("div");
    panel.className = "wad-panel";
    const row = document.createElement("div");
    row.className = "wad-row";
    row.append(this.label("Segments"), this.numberInput(WAD_KEYS.segments, 1, 100), this.label("Start"), this.numberInput(WAD_KEYS.start, 1, 100));
    panel.appendChild(row);

    this.canvas = document.createElement("canvas");
    this.canvas.className = "wad-canvas";
    this.ctx = this.canvas.getContext("2d");
    panel.appendChild(this.canvas);

    const ids = document.createElement("div");
    ids.className = "wad-grid";
    ids.append(
      this.textInput("Ref ID", WAD_KEYS.refId),
      this.textInput("Video ID", WAD_KEYS.refVideoId),
      this.textInput("Output ID", WAD_KEYS.outputId),
      this.textInput("Motion ID", WAD_KEYS.animateId),
    );
    panel.appendChild(ids);

    const refLabel = document.createElement("div");
    refLabel.className = "wad-small";
    refLabel.textContent = "Reference groups JSON / selected image list";
    panel.appendChild(refLabel);
    this.refsArea = document.createElement("textarea");
    this.refsArea.className = "wad-text wad-area";
    this.refsArea.value = wadReadString(this.node, WAD_KEYS.refs);
    this.refsArea.addEventListener("input", () => {
      wadSetWidget(this.node, WAD_KEYS.refs, this.refsArea.value);
      this.persist();
    });
    panel.appendChild(this.refsArea);

    root.appendChild(panel);
    this.container.appendChild(root);
    this.resizeObserver = new ResizeObserver(() => this.render());
    this.resizeObserver.observe(this.container);
  }

  label(text) {
    const el = document.createElement("span");
    el.className = "wad-label";
    el.textContent = text;
    return el;
  }

  makeToggle(label, widgetName, extra = "") {
    const btn = document.createElement("button");
    btn.className = `wad-btn ${extra}`;
    btn.addEventListener("click", () => {
      const next = !wadReadBool(this.node, widgetName);
      wadSetWidget(this.node, widgetName, next);
      this.syncFromWidgets();
      this.persist();
      this.render();
    });
    btn.dataset.widgetName = widgetName;
    btn.dataset.label = label;
    return btn;
  }

  numberInput(widgetName, min, max) {
    const input = document.createElement("input");
    input.className = "wad-num";
    input.type = "number";
    input.min = String(min);
    input.max = String(max);
    input.value = String(wadReadInt(this.node, widgetName, min));
    input.addEventListener("change", () => {
      let v = Number.parseInt(input.value || min, 10);
      v = Math.max(min, Math.min(max, Number.isFinite(v) ? v : min));
      input.value = String(v);
      wadSetWidget(this.node, widgetName, v);
      this.persist();
      this.render();
    });
    return input;
  }

  textInput(label, widgetName) {
    const wrap = document.createElement("label");
    wrap.className = "wad-small";
    wrap.textContent = label;
    const input = document.createElement("input");
    input.className = "wad-text";
    input.value = wadReadString(this.node, widgetName);
    input.addEventListener("change", () => {
      wadSetWidget(this.node, widgetName, input.value.trim());
      this.persist();
    });
    wrap.appendChild(input);
    return wrap;
  }

  syncFromWidgets() {
    for (const btn of [this.multiBtn, this.replaceBtn, this.transBtn, this.execBtn]) {
      const on = wadReadBool(this.node, btn.dataset.widgetName);
      btn.classList.toggle("on", on);
      btn.textContent = `${btn.dataset.label} ${on ? "ON" : "OFF"}`;
    }
    if (this.refsArea && this.refsArea.value !== wadReadString(this.node, WAD_KEYS.refs)) {
      this.refsArea.value = wadReadString(this.node, WAD_KEYS.refs);
    }
  }

  persist() {
    const data = {
      version: 1,
      segments: wadReadInt(this.node, WAD_KEYS.segments, 1),
      start: wadReadInt(this.node, WAD_KEYS.start, 1),
      multi_ref: wadReadBool(this.node, WAD_KEYS.multiRef),
      replacement: wadReadBool(this.node, WAD_KEYS.replacement),
      transition: wadReadBool(this.node, WAD_KEYS.transition),
      execute: wadReadBool(this.node, WAD_KEYS.execute),
      refs: wadReadString(this.node, WAD_KEYS.refs),
      updated_at: Date.now(),
    };
    wadSetWidget(this.node, WAD_KEYS.data, JSON.stringify(data));
  }

  render() {
    if (!this.canvas || !this.ctx) return;
    const rect = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(320, Math.floor(rect.width || this.node.size?.[0] || 640));
    const h = 156;
    this.canvas.width = Math.floor(w * dpr);
    this.canvas.height = Math.floor(h * dpr);
    this.canvas.style.height = `${h}px`;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const ctx = this.ctx;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#222";
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "#999";
    ctx.font = "10px ui-monospace,Consolas,monospace";

    const total = Math.max(1, wadReadInt(this.node, WAD_KEYS.totalFrames, 0));
    const segments = Math.max(1, wadReadInt(this.node, WAD_KEYS.segments, 1));
    const fps = Math.max(1, Number(wadGetWidget(this.node, WAD_KEYS.frameRate)?.value || 24));
    const duration = total / fps;
    this.timeEl.textContent = `${duration.toFixed(2)}s / ${total} frames`;

    const pad = 12;
    const y = 48;
    const barH = 52;
    ctx.fillStyle = "#141414";
    ctx.fillRect(pad, y, w - pad * 2, barH);
    for (let i = 0; i < segments; i++) {
      const x0 = pad + (w - pad * 2) * i / segments;
      const x1 = pad + (w - pad * 2) * (i + 1) / segments;
      ctx.fillStyle = i % 2 ? "#31594d" : "#3f6c5d";
      ctx.fillRect(x0 + 1, y + 1, Math.max(1, x1 - x0 - 2), barH - 2);
      ctx.fillStyle = "#f2fff7";
      ctx.textAlign = "center";
      ctx.fillText(`SEG ${i + 1}`, (x0 + x1) / 2, y + 25);
      const start = Math.round(total * i / segments);
      const end = Math.round(total * (i + 1) / segments);
      ctx.fillStyle = "rgba(255,255,255,.72)";
      ctx.fillText(`${start}-${end}`, (x0 + x1) / 2, y + 40);
    }
    const startSeg = wadReadInt(this.node, WAD_KEYS.start, 1);
    if (startSeg >= 1 && startSeg <= segments) {
      const x0 = pad + (w - pad * 2) * (startSeg - 1) / segments;
      ctx.strokeStyle = "#ffd166";
      ctx.lineWidth = 2;
      ctx.strokeRect(x0 + 2, y + 2, (w - pad * 2) / segments - 4, barH - 4);
    }
  }

  destroy() {
    this.resizeObserver?.disconnect();
  }
}

app.registerExtension({
  name: "WanAniDirector.UI",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "WanAniDirector") return;

    const origCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = origCreated?.apply(this, arguments);
      this.size[0] = Math.max(this.size?.[0] || 0, 760);
      this.size[1] = Math.max(this.size?.[1] || 0, 520);

      for (const name of [
        WAD_KEYS.transition, WAD_KEYS.execute, WAD_KEYS.resume,
        WAD_KEYS.refVideoId, WAD_KEYS.outputId, WAD_KEYS.animateId,
        WAD_KEYS.refId, WAD_KEYS.refs, WAD_KEYS.resumePath,
        WAD_KEYS.multiRef, WAD_KEYS.replacement, WAD_KEYS.data,
        "sqr_save_png", "sqr_frame_offset", "sqr_pre_segments"
      ]) {
        wadHideWidget(wadGetWidget(this, name));
      }

      const container = document.createElement("div");
      const widget = this.addDOMWidget("wan_ani_director_ui", "wan_ani_director_ui", container, {
        getValue: () => "",
        setValue: () => {},
      });
      widget.computeSize = width => [width, 390];
      setTimeout(() => {
        this._wanAniDirector?.destroy();
        this._wanAniDirector = new WanAniDirectorUI(this, container, widget);
      }, 0);
      return r;
    };

    const origConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = origConfigure?.apply(this, arguments);
      setTimeout(() => {
        this._wanAniDirector?.syncFromWidgets();
        this._wanAniDirector?.render();
      }, 0);
      return r;
    };

    const origRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      this._wanAniDirector?.destroy();
      return origRemoved?.apply(this, arguments);
    };
  },
});
