import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

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

function validateStrengthPrecision(valStr) {
    const clean = valStr.trim().replace(/^[+-]/, "");
    if (clean.includes(".")) {
        const decimals = clean.split(".")[1];
        if (decimals.length > 2) return false;
    }
    const val = parseFloat(valStr);
    if (Number.isNaN(val) || val < -100.0 || val > 100.0) return false;
    return true;
}

function parsePresetText(presetStr, mode) {
    if (!presetStr || typeof presetStr !== "string") return null;
    const raw = presetStr.trim();
    if (!raw || raw.toLowerCase() === "(no loras applied)") return null;

    const lines = raw.split(/\r\n|\r|\n/).map((l) => l.trim()).filter(Boolean);
    if (!lines.length) return null;

    const headerMatch = lines[0].match(/^(\d+)\s+loaded:\s*$/i);
    if (!headerMatch) return null;

    const declaredCount = parseInt(headerMatch[1], 10);
    if (Number.isNaN(declaredCount) || declaredCount < 1) return null;

    const loraLines = lines.slice(1);
    if (!loraLines.length || loraLines.length !== declaredCount) return null;
    const slots = [];
    for (let i = 0; i < loraLines.length && i < MAX_LORAS; i++) {
        const parts = loraLines[i].split(",").map((p) => p.trim());
        if (parts.length < 2 || parts.length > 3 || parts.some((p) => !p)) return null;

        const name = parts[0];
        if (
            !name ||
            name.includes("..") ||
            name.startsWith("/") ||
            name.startsWith("\\") ||
            name.startsWith(".") ||
            name.startsWith("~") ||
            name.includes(":") ||
            /[;&|`$<>"*?]/.test(name) ||
            /[\x00-\x1f\x7f]/.test(name)
        ) {
            return null;
        }
        let strength = 1.0;
        let strengthModel = 1.0;
        let strengthClip = 1.0;

        if (parts.length === 2) {
            const m = parts[1].match(/^strength=([+-]?\d+(?:\.\d+)?)$/);
            if (!m || !validateStrengthPrecision(m[1])) return null;
            strength = Math.round(parseFloat(m[1]) * 100) / 100;
            strengthModel = strength;
            strengthClip = strength;
        } else if (parts.length === 3) {
            const m1 = parts[1].match(/^(model|clip)=([+-]?\d+(?:\.\d+)?)$/);
            const m2 = parts[2].match(/^(model|clip)=([+-]?\d+(?:\.\d+)?)$/);
            if (!m1 || !m2 || m1[1] === m2[1]) return null;
            if (!validateStrengthPrecision(m1[2]) || !validateStrengthPrecision(m2[2])) return null;
            const val1 = Math.round(parseFloat(m1[2]) * 100) / 100;
            const val2 = Math.round(parseFloat(m2[2]) * 100) / 100;
            if (m1[1] === "model") strengthModel = val1;
            if (m1[1] === "clip") strengthClip = val1;
            if (m2[1] === "model") strengthModel = val2;
            if (m2[1] === "clip") strengthClip = val2;
            strength = strengthModel;
        }

        slots.push({
            index: i + 1,
            enabled: true,
            lora: name,
            strength: Number.isFinite(strength) ? strength : 1.0,
            strength_model: Number.isFinite(strengthModel) ? strengthModel : 1.0,
            strength_clip: Number.isFinite(strengthClip) ? strengthClip : 1.0,
        });
    }
    return slots;
}

function applySlotsToNode(node, mode, slots) {
    if (!node || !node.widgets || !slots) return;

    const targetCount = Math.max(1, Math.min(MAX_LORAS, slots.length));
    const countWidget = node.widgets.find((w) => w.name === "lora_count");
    if (countWidget) {
        countWidget.value = targetCount;
    }

    for (let i = 0; i < slots.length && i < MAX_LORAS; i++) {
        const slot = slots[i];
        const n = pad(slot.index || i + 1);
        const enabledWidget = node.widgets.find((w) => w.name === `enabled_${n}`);
        const loraWidget = node.widgets.find((w) => w.name === `lora_${n}`);

        if (enabledWidget) {
            enabledWidget.value = slot.enabled !== undefined ? slot.enabled : true;
        }

        if (loraWidget) {
            if (loraWidget.options?.values) {
                const targetNorm = slot.lora.replace(/\\/g, "/").toLowerCase();
                const found = loraWidget.options.values.find((v) => {
                    if (v === slot.lora) return true;
                    const vNorm = v.replace(/\\/g, "/").toLowerCase();
                    return vNorm === targetNorm || vNorm.endsWith("/" + targetNorm);
                });
                if (found) {
                    loraWidget.value = found;
                } else {
                    loraWidget.value = "None";
                }
            } else {
                loraWidget.value = slot.lora;
            }
        }

        if (mode === "single") {
            const strengthWidget = node.widgets.find((w) => w.name === `strength_${n}`);
            if (strengthWidget) {
                strengthWidget.value = slot.strength !== undefined ? slot.strength : 1.0;
            }
        } else {
            const strengthModelWidget = node.widgets.find((w) => w.name === `strength_model_${n}`);
            const strengthClipWidget = node.widgets.find((w) => w.name === `strength_clip_${n}`);
            if (strengthModelWidget) {
                strengthModelWidget.value = slot.strength_model !== undefined ? slot.strength_model : 1.0;
            }
            if (strengthClipWidget) {
                strengthClipWidget.value = slot.strength_clip !== undefined ? slot.strength_clip : 1.0;
            }
        }
    }

    syncSlots(node, mode);
    node.setDirtyCanvas(true, true);
    app.canvas?.setDirty(true, true);
}

function trySyncFromConnectedPreset(node, mode) {
    if (!node || !node.inputs) return;
    const input = node.inputs.find((i) => i.name === "config_loras" || i.name === "load_loras");
    if (!input || input.link == null) return;
    const link = app.graph?.links?.[input.link];
    if (!link) return;
    const originNode = app.graph?.getNodeById(link.origin_id);
    if (!originNode) return;

    let text = null;
    if (originNode.widgets) {
        for (const w of originNode.widgets) {
            if (typeof w.value === "string" && (w.value.includes("loaded:") || w.value.includes("loras applied"))) {
                text = w.value;
                break;
            }
        }
        if (text === null && originNode.widgets[0] && typeof originNode.widgets[0].value === "string") {
            text = originNode.widgets[0].value;
        }
    }

    // If disconnected or input is empty, do nothing to existing loras
    if (!text || !text.trim()) return;

    if (text !== node._lastAppliedPresetText) {
        node._lastAppliedPresetText = text;
        const slots = parsePresetText(text, mode);
        // Minimum config_loras is 1 LoRA
        if (slots && slots.length >= 1) {
            applySlotsToNode(node, mode, slots);
        }
    }

    if (originNode.widgets && !originNode._dynStackHooked) {
        originNode._dynStackHooked = true;
        for (const w of originNode.widgets) {
            const origCb = w.callback;
            w.callback = function () {
                const res = origCb?.apply(this, arguments);
                trySyncFromConnectedPreset(node, mode);
                return res;
            };
        }
    }
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
        setTimeout(() => {
            trySyncFromConnectedPreset(this, mode);
        }, 100);
        return r;
    };

    const onConnectionsChange = nodeType.prototype.onConnectionsChange;
    nodeType.prototype.onConnectionsChange = function (type, index, isConnected, link_info, input_info) {
        const r = onConnectionsChange?.apply(this, arguments);
        if (isConnected && type === 1) {
            setTimeout(() => {
                trySyncFromConnectedPreset(this, mode);
            }, 50);
        }
        return r;
    };

    const getExtraMenuOptions = nodeType.prototype.getExtraMenuOptions;
    nodeType.prototype.getExtraMenuOptions = function (_, options) {
        const r = getExtraMenuOptions?.apply(this, arguments);
        options.push(
            {
                content: "Apply Preset from Clipboard",
                callback: async () => {
                    try {
                        const text = await navigator.clipboard.readText();
                        const slots = parsePresetText(text, mode);
                        if (slots && slots.length >= 1) {
                            applySlotsToNode(this, mode, slots);
                        } else {
                            alert("Clipboard does not contain a valid LoRA preset (minimum 1 LoRA required, e.g. '1 loaded:\\n...').");
                        }
                    } catch (e) {
                        alert("Could not read clipboard: " + e.message);
                    }
                },
            },
            {
                content: "Apply Preset from Text...",
                callback: () => {
                    const text = prompt("Paste LoRA preset text:");
                    if (!text || !text.trim()) return;
                    const slots = parsePresetText(text, mode);
                    if (slots && slots.length >= 1) {
                        applySlotsToNode(this, mode, slots);
                    } else {
                        alert("Invalid LoRA preset format. Minimum 1 LoRA required (expected '<N> loaded:\\n...' with N >= 1).");
                    }
                },
            }
        );
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

api.addEventListener("dynamic_lora_sync_ui", (event) => {
    const data = event.detail;
    if (!data || !data.node_id || !data.slots || data.slots.length < 1) return;
    const nodeId = String(data.node_id);
    const node = app.graph?._nodes?.find((n) => String(n.id) === nodeId);
    if (!node) return;
    applySlotsToNode(node, data.mode, data.slots);
});
