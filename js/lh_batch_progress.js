import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const progressState = new Map();

api.addEventListener("lh/batch_progress", ({ detail }) => {
    const nodeId = Number(detail.node);
    progressState.set(nodeId, {
        progress: Math.max(0, Math.min(1, Number(detail.progress) || 0)),
        text: String(detail.text || ""),
    });
    app.canvas?.setDirty(true, true);
});

app.registerExtension({
    name: "ComfyUI-WanAni-SQR.LHBatchProgress",

    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "LHImagesFolderLoader") return;

        const originalDraw = nodeType.prototype.onDrawForeground;
        nodeType.prototype.onDrawForeground = function (ctx) {
            const result = originalDraw?.apply(this, arguments);
            const state = progressState.get(Number(this.id));
            if (!state?.text || this.flags?.collapsed) return result;

            const width = Math.max(220, this.size[0]);
            const height = 22;
            const y = -42;

            ctx.save();
            ctx.fillStyle = "#20242b";
            ctx.beginPath();
            ctx.roundRect(0, y, width, height, 5);
            ctx.fill();

            ctx.save();
            ctx.beginPath();
            ctx.roundRect(0, y, width, height, 5);
            ctx.clip();
            ctx.fillStyle = state.progress >= 1 ? "#2e9d58" : "#2878c8";
            ctx.fillRect(0, y, width * state.progress, height);
            ctx.restore();

            ctx.fillStyle = "#ffffff";
            ctx.font = "12px sans-serif";
            ctx.textBaseline = "middle";
            ctx.fillText(state.text, 7, y + height / 2, width - 14);
            ctx.restore();
            return result;
        };
    },
});
