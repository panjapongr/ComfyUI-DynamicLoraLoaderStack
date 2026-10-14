import { app } from "../../scripts/app.js";

const MAX_LORAS = 50;
const MIN_WIDTH = 340;
const NODES = {
    DynamicLoraLoaderStack: "single",
    DynamicLoraLoaderStackSplit: "split",
};

function pad(i) {
    return String(i).padStart(2, "0");
}

function setWidgetVisible(widget, visible) {
    if (!widget) return;
    if (!widget._dynOrig) {
        widget._dynOrig = {
            type: widget.type,
            computeSize: widget.computeSize,
            serializeValue: widget.serializeValue,
        };
    }
    widget.hidden = !visible;
    if (visible) {
        widget.type = widget._dynOrig.type;
        widget.computeSize = widget._dynOrig.computeSize;
        if (widget._dynOrig.serializeValue) {
            widget.serializeValue = widget._dynOrig.serializeValue;
        }
    } else {
        widget.type = "hidden";
        widget.computeSize = () => [0, -4];
        const origSerialize = widget._dynOrig.serializeValue;
        widget.serializeValue = function () {
            if (origSerialize) return origSerialize.apply(this, arguments);
            return this.value;
        };
    }
}

function syncSlots(node, mode) {
    if (!node.widgets) return;
    const countWidget = node.widgets.find((w) => w.name === "lora_count");
    let count = parseInt(countWidget?.value, 10);
    if (Number.isNaN(count)) count = 5;
    count = Math.max(1, Math.min(MAX_LORAS, count));

    for (let i = 1; i <= MAX_LORAS; i++) {
        const visible = i <= count;
        const n = pad(i);
        const enabled = node.widgets.find((w) => w.name === `enabled_${n}`);
        const lora = node.widgets.find((w) => w.name === `lora_${n}`);
        if (enabled) enabled.label = `enabled_${n}`;
        if (lora) lora.label = `lora_${n}`;
        setWidgetVisible(enabled, visible);
        setWidgetVisible(lora, visible);

        if (mode === "single") {
            const strength = node.widgets.find((w) => w.name === `strength_${n}`);
            if (strength) strength.label = `strength_${n}`;
            setWidgetVisible(strength, visible);
        } else {
            const strengthModel = node.widgets.find((w) => w.name === `strength_model_${n}`);
            const strengthClip = node.widgets.find((w) => w.name === `strength_clip_${n}`);
            if (strengthModel) strengthModel.label = `strength_model_${n}`;
            if (strengthClip) strengthClip.label = `strength_clip_${n}`;
            setWidgetVisible(strengthModel, visible);
            setWidgetVisible(strengthClip, visible);
        }
    }

    const size = node.computeSize();
    node.setSize([Math.max(node.size?.[0] || 0, MIN_WIDTH, size[0]), size[1]]);
    app.canvas?.setDirty(true, true);
}

function hookCount(node, mode) {
    const countWidget = node.widgets?.find((w) => w.name === "lora_count");
    if (!countWidget || countWidget._dynHooked) return;
    countWidget._dynHooked = true;
    const orig = countWidget.callback;
    countWidget.callback = function () {
        const r = orig?.apply(this, arguments);
        syncSlots(node, mode);
        return r;
    };
}

function installHooks(nodeType, mode) {
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const r = onNodeCreated?.apply(this, arguments);
        hookCount(this, mode);
        syncSlots(this, mode);
        return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
        const r = onConfigure?.apply(this, arguments);
        hookCount(this, mode);
        syncSlots(this, mode);
        return r;
    };

    const onDrawForeground = nodeType.prototype.onDrawForeground;
    nodeType.prototype.onDrawForeground = function () {
        const r = onDrawForeground?.apply(this, arguments);
        const countWidget = this.widgets?.find((w) => w.name === "lora_count");
        const count = parseInt(countWidget?.value, 10);
        if (!Number.isNaN(count) && count !== this._dynLastCount) {
            this._dynLastCount = count;
            syncSlots(this, mode);
        }
        return r;
    };
}

app.registerExtension({
    name: "dynamic.lora.loader.stack",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        const mode = NODES[nodeData.name];
        if (!mode) return;
        installHooks(nodeType, mode);
    },

    nodeCreated(node) {
        const mode = NODES[node.comfyClass];
        if (!mode) return;
        hookCount(node, mode);
        syncSlots(node, mode);
    },
});
