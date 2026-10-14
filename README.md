# Dynamic LoRA Loader Stack

A ComfyUI custom node pack in the spirit of **Lora Loader Stack (rgthree)**: several LoRA dropdowns in one node, with model and clip in and out.

You can **toggle each LoRA on or off without setting it to None**, and set a **dynamic number of LoRA slots** (1–50, default 5).

![Chain Dynamic LoRA Loader Stack](docs/screenshot1.jpg)

Two nodes ship together. One install from ComfyUI Manager gives you both.

| Node | Strength |
| --- | --- |
| **LoRA Loader Stack (Dynamic)** | One `strength_NN`, applied to both model and CLIP |
| **LoRA Loader Stack Split (Dynamic)** | Separate `strength_model_NN` and `strength_clip_NN` |

## Features

**Toggle a LoRA on or off without setting it to None.**  
Each slot has an `enabled_NN` checkbox. Uncheck it to skip that LoRA. The dropdown and strengths stay as they are, so you can turn it back on without picking the file again.

**Dynamic number of LoRA slots.**  
Type a count in `lora_count`. Default is **5**, range is **1–50**. The node shows only that many selectors and hides the rest. Lowering the count does not wipe the hidden slots.

Also:

- Numbering starts at `01`.
- Strength range **-100 to 100**, step `0.01`, same as the official Load LoRA node.
- `clip` is optional. Leave it disconnected to patch the model only.
- `loaded_loras` is a text summary of what was actually applied.
- Chain nodes by wiring the previous `loaded_loras` into the next `prev_loaded_loras`. The last output lists the whole chain.

## Install

1. Copy the `ComfyUI-InputThumbnails` folder into:

   `ComfyUI/custom_nodes/ComfyUI-InputThumbnails`

2. Restart ComfyUI.

## Slot layout

Single strength:

```
enabled_01
lora_01
strength_01
```

Split strength:

```
enabled_01
lora_01
strength_model_01
strength_clip_01
```

A slot loads only when it is within `lora_count`, enabled, not `None`, and at least one strength is non-zero.

## `loaded_loras` format

Single strength:

```
2 loaded:
lora03.safetensors,strength=2
lora04.safetensors,strength=-20
```

Split strength:

```
2 loaded:
lora01.safetensors,model=1,clip=1
lora02.safetensors,model=1.2,clip=1.5
```

Wire it to a Show Text node if you want to see it. Leave it disconnected if you do not.

## Security

This pack does not make network calls, run shell commands, read environment variables, or touch files outside ComfyUI's normal LoRA loader. It only calls ComfyUI's `LoraLoader` with names from your `models/loras` folder.

## License

[GPL-3.0-or-later](LICENSE.txt). See [`LICENSE.txt`](LICENSE.txt).
