import { app } from "../../scripts/app.js";
import "./lh_image_detail_editor.js";

const NODE_NAME = "LHImageEditor";

function widget(node, name) {
    return node.widgets?.find((item) => item.name === name);
}

function hideWidget(item) {
    if (!item) return;
    item.hidden = true;
    item.computeSize = () => [0, -4];
    item.draw = () => {};
    if (item.element) item.element.style.display = "none";
}

function setWidget(item, value) {
    if (!item || item.value === value) return;
    item.value = value;
    item.callback?.(value);
}

function parseSegment(node) {
    const data = widget(node, "editor_data")?.value;
    try {
        const parsed = JSON.parse(String(data || "{}"));
        if (parsed && typeof parsed === "object") {
            parsed.references = Array.isArray(parsed.references) ? parsed.references : [];
            parsed.multi_ref = false;
            return parsed;
        }
    } catch {}
    return { references: [], multi_ref: false, image_editor_state: null };
}

function compositePath(segment) {
    const item = (segment.references || []).find((ref) => ref?.composite);
    return String(item?.path || "");
}

function buildPanel(node) {
    const root = document.createElement("div");
    root.style.cssText = "width:100%;height:100%;display:flex;flex-direction:column;gap:8px;padding:8px;box-sizing:border-box;background:#181818;color:#ddd;font:12px system-ui";
    root.innerHTML = `<button data-open style="padding:8px;border:1px solid #555;border-radius:6px;background:#292929;color:#eee;cursor:pointer">Open LH Image Editor</button><div style="flex:1;min-height:210px;border:1px solid #383838;border-radius:6px;background:#0d0d0d;display:flex;align-items:center;justify-content:center;overflow:hidden"><img data-preview style="width:100%;height:100%;object-fit:contain;display:none"><span data-empty style="color:#777">No composite saved</span></div><div data-path style="font:10px ui-monospace,monospace;color:#999;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></div>`;
    const preview = root.querySelector("[data-preview]");
    const empty = root.querySelector("[data-empty]");
    const pathLabel = root.querySelector("[data-path]");
    let segment = parseSegment(node);

    const refresh = () => {
        const path = compositePath(segment) || String(widget(node, "image_path")?.value || "");
        pathLabel.textContent = path ? `input/${path}` : "";
        if (path) {
            preview.src = `/sqr/image_thumb?file=${encodeURIComponent(path)}&t=${Date.now()}`;
            preview.style.display = "block";
            empty.style.display = "none";
        } else {
            preview.removeAttribute("src");
            preview.style.display = "none";
            empty.style.display = "block";
        }
    };

    const persist = (updated) => {
        segment = updated;
        setWidget(widget(node, "editor_data"), JSON.stringify(segment));
        const path = compositePath(segment);
        setWidget(widget(node, "image_path"), path);
        node.setDirtyCanvas?.(true, true);
        refresh();
    };

    root.querySelector("[data-open]").onclick = () => {
        if (typeof window.LHImageEditorOpen !== "function") {
            alert("LH Image Editor UI is not ready. Refresh the ComfyUI page.");
            return;
        }
        window.LHImageEditorOpen(segment, { lang: "zh", onChange: persist });
    };
    refresh();
    return root;
}

app.registerExtension({
    name: "WanAniSQR.LHImageEditor",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalCreated?.apply(this, arguments);
            hideWidget(widget(this, "image_path"));
            hideWidget(widget(this, "editor_data"));
            this.size = [Math.max(360, this.size?.[0] || 0), Math.max(360, this.size?.[1] || 0)];
            const panel = buildPanel(this);
            const domWidget = this.addDOMWidget("lh_image_editor_panel", "LHImageEditorPanel", panel, {
                getValue: () => "",
                setValue: () => {},
            });
            domWidget.computeSize = (width) => [width, 300];
            return result;
        };
    },
});
