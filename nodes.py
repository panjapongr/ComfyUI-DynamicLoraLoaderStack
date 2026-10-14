"""Dynamic LoRA Loader Stack for ComfyUI.

Two nodes in one pack:
- single strength (applied to both model and CLIP)
- split strength (strength_model_XX and strength_clip_XX)

Both have a typed slot count (1-50, default 5) and a per-slot enable
checkbox that skips loading without clearing the selector.
"""

import folder_paths
from nodes import LoraLoader

MAX_LORAS = 50


def _lora_list():
    try:
        names = folder_paths.get_filename_list("loras")
    except Exception:
        names = []
    return ["None"] + list(names)


def _merge_loaded(prev, applied):
    """Combine a previous loaded_loras string with this node's applied list."""
    earlier = []
    if prev and str(prev).strip() and str(prev).strip() != "(no loras applied)":
        body = str(prev).split(":", 1)[-1] if ":" in str(prev) else str(prev)
        parts = body.replace("|", "\n").splitlines()
        earlier = [part.strip() for part in parts if part.strip()]
    all_items = earlier + applied
    if not all_items:
        return "(no loras applied)"
    return f"{len(all_items)} loaded:\n" + "\n".join(all_items)


def _slot_inputs(loras, strength_mode):
    optional = {}
    for i in range(1, MAX_LORAS + 1):
        n = f"{i:02d}"
        optional[f"enabled_{n}"] = (
            "BOOLEAN",
            {
                "default": True,
                "label_on": "on",
                "label_off": "off",
                "tooltip": "Uncheck to skip this LoRA without clearing the selector.",
            },
        )
        optional[f"lora_{n}"] = (
            loras,
            {"default": "None", "tooltip": "LoRA file. Kept even when this slot is disabled or hidden."},
        )
        if strength_mode == "single":
            optional[f"strength_{n}"] = (
                "FLOAT",
                {
                    "default": 1.0,
                    "min": -100.0,
                    "max": 100.0,
                    "step": 0.01,
                    "tooltip": "Applied to both model and CLIP.",
                },
            )
        else:
            optional[f"strength_model_{n}"] = (
                "FLOAT",
                {
                    "default": 1.0,
                    "min": -100.0,
                    "max": 100.0,
                    "step": 0.01,
                    "tooltip": "Model strength only.",
                },
            )
            optional[f"strength_clip_{n}"] = (
                "FLOAT",
                {
                    "default": 1.0,
                    "min": -100.0,
                    "max": 100.0,
                    "step": 0.01,
                    "tooltip": "CLIP strength only.",
                },
            )
    return optional


def _base_inputs():
    loras = _lora_list()
    required = {
        "model": ("MODEL",),
        "lora_count": (
            "INT",
            {
                "default": 5,
                "min": 1,
                "max": MAX_LORAS,
                "step": 1,
                "display": "number",
                "tooltip": "How many LoRA selectors to show and consider. Default 5, max 50.",
            },
        ),
    }
    optional = {
        "clip": (
            "CLIP",
            {"tooltip": "Optional. Leave disconnected to apply LoRAs to the model only."},
        ),
        "prev_loaded_loras": (
            "STRING",
            {
                "forceInput": True,
                "tooltip": "Wire the previous node's loaded_loras here so this output includes the whole chain.",
            },
        ),
    }
    return required, optional, loras


class DynamicLoraLoaderStack:
    """Load up to 50 LoRAs. One strength per slot, applied to model and CLIP."""

    @classmethod
    def INPUT_TYPES(cls):
        required, optional, loras = _base_inputs()
        optional.update(_slot_inputs(loras, "single"))
        return {"required": required, "optional": optional}

    RETURN_TYPES = ("MODEL", "CLIP", "STRING")
    RETURN_NAMES = ("MODEL", "CLIP", "loaded_loras")
    FUNCTION = "load_loras"
    CATEGORY = "loaders"
    DESCRIPTION = (
        "Stack LoRAs with a slot count (1-50, default 5) and a checkbox per slot. "
        "One strength is applied to both model and CLIP. Disabling a slot skips it "
        "without removing the selected LoRA."
    )

    def load_loras(self, model, lora_count, clip=None, prev_loaded_loras=None, **kwargs):
        try:
            count = int(lora_count)
        except (TypeError, ValueError):
            count = 5
        count = max(1, min(MAX_LORAS, count))

        loader = LoraLoader()
        applied = []

        for i in range(1, count + 1):
            n = f"{i:02d}"
            enabled = kwargs.get(f"enabled_{n}", True)
            if enabled is None:
                enabled = True
            lora_name = kwargs.get(f"lora_{n}") or "None"
            try:
                strength = float(kwargs.get(f"strength_{n}", 1.0) or 0.0)
            except (TypeError, ValueError):
                strength = 0.0

            if not enabled or lora_name == "None" or strength == 0.0:
                continue

            model, clip = loader.load_lora(model, clip, lora_name, strength, strength)
            applied.append(f"{lora_name},strength={strength:g}")

        return (model, clip, _merge_loaded(prev_loaded_loras, applied))


class DynamicLoraLoaderStackSplit:
    """Load up to 50 LoRAs with separate model and CLIP strength."""

    @classmethod
    def INPUT_TYPES(cls):
        required, optional, loras = _base_inputs()
        optional.update(_slot_inputs(loras, "split"))
        return {"required": required, "optional": optional}

    RETURN_TYPES = ("MODEL", "CLIP", "STRING")
    RETURN_NAMES = ("MODEL", "CLIP", "loaded_loras")
    FUNCTION = "load_loras"
    CATEGORY = "loaders"
    DESCRIPTION = (
        "Stack LoRAs with a slot count (1-50, default 5) and a checkbox per slot. "
        "Each slot has strength_model and strength_clip. Disabling a slot skips it "
        "without removing the selected LoRA."
    )

    def load_loras(self, model, lora_count, clip=None, prev_loaded_loras=None, **kwargs):
        try:
            count = int(lora_count)
        except (TypeError, ValueError):
            count = 5
        count = max(1, min(MAX_LORAS, count))

        loader = LoraLoader()
        applied = []

        for i in range(1, count + 1):
            n = f"{i:02d}"
            enabled = kwargs.get(f"enabled_{n}", True)
            if enabled is None:
                enabled = True
            lora_name = kwargs.get(f"lora_{n}") or "None"
            try:
                strength_model = float(kwargs.get(f"strength_model_{n}", 1.0) or 0.0)
                strength_clip = float(kwargs.get(f"strength_clip_{n}", 1.0) or 0.0)
            except (TypeError, ValueError):
                continue

            if not enabled or lora_name == "None":
                continue
            if strength_model == 0.0 and strength_clip == 0.0:
                continue

            model, clip = loader.load_lora(
                model, clip, lora_name, strength_model, strength_clip
            )
            applied.append(
                f"{lora_name},model={strength_model:g},clip={strength_clip:g}"
            )

        return (model, clip, _merge_loaded(prev_loaded_loras, applied))


NODE_CLASS_MAPPINGS = {
    "DynamicLoraLoaderStack": DynamicLoraLoaderStack,
    "DynamicLoraLoaderStackSplit": DynamicLoraLoaderStackSplit,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DynamicLoraLoaderStack": "LoRA Loader Stack (Dynamic)",
    "DynamicLoraLoaderStackSplit": "LoRA Loader Stack Split (Dynamic)",
}
