"""Dynamic LoRA Loader Stack for ComfyUI.

Two nodes in one pack:
- single strength (applied to both model and CLIP)
- split strength (strength_model_XX and strength_clip_XX)

Both have a typed slot count (1-50, default 5) and a per-slot enable
checkbox that skips loading without clearing the selector.
Also supports an optional preset input (`config_loras`) in the same format
as `loaded_loras` output.
"""

import math
import re
import folder_paths
from nodes import LoraLoader

try:
    from server import PromptServer
except Exception:
    PromptServer = None

MAX_LORAS = 50


def _lora_list():
    try:
        names = folder_paths.get_filename_list("loras")
    except Exception:
        names = []
    return ["None"] + list(names)


def _get_available_loras_map():
    try:
        names = folder_paths.get_filename_list("loras")
    except Exception:
        names = []
    mapping = {}
    ci_mapping = {}
    for n in names:
        mapping[n] = n
        slash = n.replace("\\", "/")
        backslash = n.replace("/", "\\")
        mapping[slash] = n
        mapping[backslash] = n

        # Case-insensitive mapping for cross-OS workflows (Windows/macOS -> Linux)
        ci_mapping.setdefault(n.lower(), n)
        ci_mapping.setdefault(slash.lower(), n)
        ci_mapping.setdefault(backslash.lower(), n)
    return mapping, ci_mapping


def _validate_strength_value(val_str, line_idx, param_name="strength"):
    try:
        val = float(val_str)
        if math.isnan(val) or math.isinf(val):
            raise ValueError()
    except (ValueError, TypeError):
        return None, f"Invalid {param_name} value '{val_str}' on line {line_idx}: not a valid float."

    if val < -100.0 or val > 100.0:
        return None, (
            f"{param_name} {val} on line {line_idx} is out of allowed range [-100.0, 100.0]."
        )

    # Step validation: step is 0.01 (maximum 2 decimal places)
    clean_str = val_str.strip().lstrip("+-")
    if "." in clean_str:
        decimal_digits = clean_str.split(".", 1)[1]
        if len(decimal_digits) > 2:
            return None, (
                f"Invalid {param_name} precision on line {line_idx}: '{val_str}'. "
                f"Maximum precision is 2 decimal places (step 0.01)."
            )

    rounded = round(val, 2)
    if abs(val - rounded) > 1e-6:
        return None, (
            f"Invalid {param_name} step on line {line_idx}: '{val_str}'. "
            f"Must be a multiple of step 0.01."
        )

    return rounded, None


def _parse_preset(preset_str, is_config=False):
    """Parse and validate preset or loaded_loras string.

    Returns:
        (parsed_items, error_message)
        - If preset_str is None or empty/whitespace: returns (None, None) (preset inactive, keep existing LoRAs).
        - If valid: returns (list_of_dict, None).
        - If invalid: returns (None, error_str).
    """
    if preset_str is None:
        return None, None
    raw = str(preset_str).strip()
    if not raw:
        return None, None

    if raw.lower() == "(no loras applied)":
        if is_config:
            return None, (
                "Invalid config_loras: minimum config_loras is 1 LoRA. "
                "Leave it empty or disconnected to keep existing LoRAs."
            )
        return [], None

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return None, None

    header = lines[0]
    header_match = re.match(r"^(\d+)\s+loaded:\s*$", header, re.IGNORECASE)
    if not header_match:
        if is_config:
            return None, (
                f"Invalid config_loras header '{header}'. First line must match '<N> loaded:' "
                f"with at least 1 LoRA (or leave empty/disconnected to keep existing LoRAs)."
            )
        return None, (
            f"Invalid preset header '{header}'. First line must match '<N> loaded:' "
            f"or '(no loras applied)'."
        )

    declared_count = int(header_match.group(1))
    if is_config and declared_count < 1:
        return None, (
            f"Invalid config_loras: minimum config_loras is 1 LoRA (declared {declared_count}). "
            f"Leave it empty or disconnected to keep existing LoRAs."
        )

    lora_lines = lines[1:]

    if is_config and len(lora_lines) < 1:
        return None, (
            "Invalid config_loras: minimum config_loras is 1 LoRA. Found 0 LoRA lines. "
            "Leave it empty or disconnected to keep existing LoRAs."
        )

    if len(lora_lines) != declared_count:
        return None, (
            f"Preset count mismatch: header declares {declared_count} loaded LoRA(s), "
            f"but found {len(lora_lines)} LoRA line(s)."
        )

    parsed = []
    for idx, line in enumerate(lora_lines, start=1):
        parts = [p.strip() for p in line.split(",")]
        if any(p == "" for p in parts) or len(parts) < 2 or len(parts) > 3:
            return None, (
                f"Invalid format on line {idx}: '{line}'. "
                f"Expected '<lora_name>,strength=<val>' or '<lora_name>,model=<val>,clip=<val>'."
            )

        lora_name = parts[0]
        # Security validation on lora_name (prevent path traversal, null bytes, shell injections)
        if (
            not lora_name
            or ".." in lora_name
            or lora_name.startswith(("/", "\\", ".", "~"))
            or ":" in lora_name
            or any(ord(c) < 32 or ord(c) == 127 for c in lora_name)
            or re.search(r'[;&|`$<>"*?]', lora_name)
        ):
            return None, (
                f"Security check failed on line {idx}: LoRA name '{lora_name}' "
                f"contains unsafe characters or directory traversal."
            )

        if len(parts) == 2:
            param = parts[1]
            if not param.startswith("strength="):
                return None, (
                    f"Invalid parameter on line {idx}: '{param}'. Expected 'strength=<number>'."
                )
            val_str = param[len("strength="):].strip()
            val, err = _validate_strength_value(val_str, idx, "strength")
            if err:
                return None, err
            parsed.append({"name": lora_name, "mode": "single", "strength": val})

        else:
            kv = {}
            for p in parts[1:]:
                if "=" not in p:
                    return None, f"Invalid parameter '{p}' on line {idx}: missing '='."
                k, v = [x.strip() for x in p.split("=", 1)]
                if k not in ("model", "clip"):
                    return None, (
                        f"Unknown parameter '{k}' on line {idx}. Expected 'model' or 'clip'."
                    )
                if k in kv:
                    return None, f"Duplicate parameter '{k}' on line {idx}."
                val, err = _validate_strength_value(v, idx, k)
                if err:
                    return None, err
                kv[k] = val
            if "model" not in kv or "clip" not in kv:
                return None, (
                    f"Split format on line {idx} requires both 'model=<val>' and 'clip=<val>'."
                )
            parsed.append({
                "name": lora_name,
                "mode": "split",
                "strength_model": kv["model"],
                "strength_clip": kv["clip"],
            })

    return parsed, None


def _merge_loaded(prev, applied):
    """Combine a previous loaded_loras string with this node's applied list.

    Validates and sanitizes prev to prevent character and code injection.
    """
    earlier = []
    if prev and str(prev).strip():
        items, err = _parse_preset(prev, is_config=False)
        if err:
            raise ValueError(f"Invalid prev_loaded_loras: {err}")
        if items:
            for item in items:
                if item["mode"] == "single":
                    earlier.append(f"{item['name']},strength={item['strength']:g}")
                else:
                    earlier.append(
                        f"{item['name']},model={item['strength_model']:g},clip={item['strength_clip']:g}"
                    )
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
        "config_loras": (
            "STRING",
            {
                "forceInput": True,
                "tooltip": "Optional preset string (in loaded_loras format) to configure which LoRAs and strengths to apply. Overrides slot widgets if provided.",
            },
        ),
        "prev_loaded_loras": (
            "STRING",
            {
                "forceInput": True,
                "tooltip": "Wire the previous node's loaded_loras here so this output includes the whole chain.",
            },
        ),
    }
    hidden = {
        "unique_id": "UNIQUE_ID",
    }
    return required, optional, hidden, loras


class DynamicLoraLoaderStack:
    """Load up to 50 LoRAs. One strength per slot, applied to model and CLIP."""

    @classmethod
    def INPUT_TYPES(cls):
        required, optional, hidden, loras = _base_inputs()
        optional.update(_slot_inputs(loras, "single"))
        return {"required": required, "optional": optional, "hidden": hidden}

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        config_loras = kwargs.get("config_loras")
        if config_loras is None:
            config_loras = kwargs.get("load_loras") or kwargs.get("loaded_loras")
        if config_loras is not None and isinstance(config_loras, str):
            if config_loras.strip():
                _, err = _parse_preset(config_loras, is_config=True)
                if err:
                    return f"config_loras error: {err}"

        prev_loaded = kwargs.get("prev_loaded_loras")
        if prev_loaded is not None and isinstance(prev_loaded, str):
            if prev_loaded.strip():
                _, err = _parse_preset(prev_loaded, is_config=False)
                if err:
                    return f"prev_loaded_loras error: {err}"

        return True

    RETURN_TYPES = ("MODEL", "CLIP", "STRING")
    RETURN_NAMES = ("MODEL", "CLIP", "loaded_loras")
    FUNCTION = "load_loras"
    CATEGORY = "loaders"
    DESCRIPTION = (
        "Stack LoRAs with a slot count (1-50, default 5) and a checkbox per slot. "
        "One strength is applied to both model and CLIP. Disabling a slot skips it "
        "without removing the selected LoRA. Supports preset string input (config_loras)."
    )

    def load_loras(
        self,
        model,
        lora_count,
        clip=None,
        prev_loaded_loras=None,
        config_loras=None,
        unique_id=None,
        **kwargs,
    ):
        preset_raw = (
            config_loras
            if config_loras is not None
            else (kwargs.get("config_loras") or kwargs.get("load_loras") or kwargs.get("loaded_loras"))
        )
        preset_items, err = _parse_preset(preset_raw, is_config=True)
        if err:
            raise ValueError(f"DynamicLoraLoaderStack preset error: {err}")

        prev_loaded = prev_loaded_loras if prev_loaded_loras is not None else kwargs.get("prev_loaded_loras")
        if prev_loaded and str(prev_loaded).strip():
            _, prev_err = _parse_preset(prev_loaded, is_config=False)
            if prev_err:
                raise ValueError(f"DynamicLoraLoaderStack prev_loaded_loras error: {prev_err}")

        loader = LoraLoader()
        applied = []

        if preset_items is not None:
            available, ci_available = _get_available_loras_map()
            slots_to_sync = []
            slot_idx = 1
            for item in preset_items[:MAX_LORAS]:
                lora_name = item["name"]
                canonical_name = available.get(lora_name) or ci_available.get(lora_name.lower())
                if not canonical_name or canonical_name == "None":
                    continue

                if item["mode"] == "single":
                    strength = item["strength"]
                else:
                    strength = item["strength_model"]

                if strength == 0.0:
                    continue

                model, clip = loader.load_lora(model, clip, canonical_name, strength, strength)
                applied.append(f"{canonical_name},strength={strength:g}")
                slots_to_sync.append({
                    "index": slot_idx,
                    "enabled": True,
                    "lora": canonical_name,
                    "strength": strength,
                })
                slot_idx += 1

            uid = unique_id if unique_id is not None else kwargs.get("unique_id")
            if uid is not None and PromptServer is not None and hasattr(PromptServer, "instance") and PromptServer.instance is not None:
                try:
                    PromptServer.instance.send_sync(
                        "dynamic_lora_sync_ui",
                        {
                            "node_id": str(uid),
                            "mode": "single",
                            "slots": slots_to_sync,
                        },
                    )
                except Exception:
                    pass
        else:
            try:
                count = int(lora_count)
            except (TypeError, ValueError):
                count = 5
            count = max(1, min(MAX_LORAS, count))

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

        return (model, clip, _merge_loaded(prev_loaded, applied))


class DynamicLoraLoaderStackSplit:
    """Load up to 50 LoRAs with separate model and CLIP strength."""

    @classmethod
    def INPUT_TYPES(cls):
        required, optional, hidden, loras = _base_inputs()
        optional.update(_slot_inputs(loras, "split"))
        return {"required": required, "optional": optional, "hidden": hidden}

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        config_loras = kwargs.get("config_loras")
        if config_loras is None:
            config_loras = kwargs.get("load_loras") or kwargs.get("loaded_loras")
        if config_loras is not None and isinstance(config_loras, str):
            if config_loras.strip():
                _, err = _parse_preset(config_loras, is_config=True)
                if err:
                    return f"config_loras error: {err}"

        prev_loaded = kwargs.get("prev_loaded_loras")
        if prev_loaded is not None and isinstance(prev_loaded, str):
            if prev_loaded.strip():
                _, err = _parse_preset(prev_loaded, is_config=False)
                if err:
                    return f"prev_loaded_loras error: {err}"

        return True

    RETURN_TYPES = ("MODEL", "CLIP", "STRING")
    RETURN_NAMES = ("MODEL", "CLIP", "loaded_loras")
    FUNCTION = "load_loras"
    CATEGORY = "loaders"
    DESCRIPTION = (
        "Stack LoRAs with a slot count (1-50, default 5) and a checkbox per slot. "
        "Each slot has strength_model and strength_clip. Disabling a slot skips it "
        "without removing the selected LoRA. Supports preset string input (config_loras)."
    )

    def load_loras(
        self,
        model,
        lora_count,
        clip=None,
        prev_loaded_loras=None,
        config_loras=None,
        unique_id=None,
        **kwargs,
    ):
        preset_raw = (
            config_loras
            if config_loras is not None
            else (kwargs.get("config_loras") or kwargs.get("load_loras") or kwargs.get("loaded_loras"))
        )
        preset_items, err = _parse_preset(preset_raw, is_config=True)
        if err:
            raise ValueError(f"DynamicLoraLoaderStackSplit preset error: {err}")

        prev_loaded = prev_loaded_loras if prev_loaded_loras is not None else kwargs.get("prev_loaded_loras")
        if prev_loaded and str(prev_loaded).strip():
            _, prev_err = _parse_preset(prev_loaded, is_config=False)
            if prev_err:
                raise ValueError(f"DynamicLoraLoaderStackSplit prev_loaded_loras error: {prev_err}")

        loader = LoraLoader()
        applied = []

        if preset_items is not None:
            available, ci_available = _get_available_loras_map()
            slots_to_sync = []
            slot_idx = 1
            for item in preset_items[:MAX_LORAS]:
                lora_name = item["name"]
                canonical_name = available.get(lora_name) or ci_available.get(lora_name.lower())
                if not canonical_name or canonical_name == "None":
                    continue

                if item["mode"] == "split":
                    strength_model = item["strength_model"]
                    strength_clip = item["strength_clip"]
                else:
                    strength_model = item["strength"]
                    strength_clip = item["strength"]

                if strength_model == 0.0 and strength_clip == 0.0:
                    continue

                model, clip = loader.load_lora(
                    model, clip, canonical_name, strength_model, strength_clip
                )
                applied.append(
                    f"{canonical_name},model={strength_model:g},clip={strength_clip:g}"
                )
                slots_to_sync.append({
                    "index": slot_idx,
                    "enabled": True,
                    "lora": canonical_name,
                    "strength_model": strength_model,
                    "strength_clip": strength_clip,
                })
                slot_idx += 1

            uid = unique_id if unique_id is not None else kwargs.get("unique_id")
            if uid is not None and PromptServer is not None and hasattr(PromptServer, "instance") and PromptServer.instance is not None:
                try:
                    PromptServer.instance.send_sync(
                        "dynamic_lora_sync_ui",
                        {
                            "node_id": str(uid),
                            "mode": "split",
                            "slots": slots_to_sync,
                        },
                    )
                except Exception:
                    pass
        else:
            try:
                count = int(lora_count)
            except (TypeError, ValueError):
                count = 5
            count = max(1, min(MAX_LORAS, count))

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

        return (model, clip, _merge_loaded(prev_loaded, applied))


NODE_CLASS_MAPPINGS = {
    "DynamicLoraLoaderStack": DynamicLoraLoaderStack,
    "DynamicLoraLoaderStackSplit": DynamicLoraLoaderStackSplit,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DynamicLoraLoaderStack": "LoRA Loader Stack (Dynamic)",
    "DynamicLoraLoaderStackSplit": "LoRA Loader Stack Split (Dynamic)",
}
