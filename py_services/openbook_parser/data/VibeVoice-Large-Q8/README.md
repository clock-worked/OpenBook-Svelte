---
license: mit
language:
- en
- multilingual
library_name: transformers
tags:
- text-to-speech
- audio
- tts
- voice
- quantized
- 8bit
- bitsandbytes
- vibevoice
pipeline_tag: text-to-audio
model-index:
- name: VibeVoice-Large-Q8
  results: []
---

# VibeVoice-Large-Q8 - Selective 8bit Quantization

<div align="center">

**The first 8-bit VibeVoice model that actually works**

[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Model Size](https://img.shields.io/badge/size-11.6%20GB-green)](https://huggingface.co/FabioSarracino/VibeVoice-Large-Q8)
[![Quality](https://img.shields.io/badge/audio-identical%20quality-brightgreen)](https://huggingface.co/FabioSarracino/VibeVoice-Large-Q8)

[🤗 Model](https://huggingface.co/FabioSarracino/VibeVoice-Large-Q8) • [💻 ComfyUI](https://github.com/Enemyx-net/VibeVoice-ComfyUI) • [📖 Docs](https://github.com/Enemyx-net/VibeVoice-ComfyUI/blob/main/README.md)

</div>

---

## 🎯 Why This Model is Different

If you've tried other 8-bit quantized VibeVoice models, you probably got nothing but static noise. **This one actually works.**

The secret? **Selective quantization**: I only quantized the language model (the most robust part), while keeping audio-critical components (diffusion head, VAE, connectors) at full precision.

### Results
- ✅ Perfect audio, identical to the original model
- ✅ 11.6 GB instead of 18.7 GB (-38%)
- ✅ Uses ~12 GB VRAM instead of 20 GB
- ✅ Works on 12 GB GPUs (RTX 3060, 4070 Ti, etc.)

---

## 🚨 The Problem with Other 8-bit Models

Most 8-bit models you'll find online quantize **everything** aggressively:
**Result:** Audio components get quantized → numerical errors propagate → audio = pure noise.

---

## ✅ The Solution: Selective Quantization

I only quantized what can be safely quantized without losing quality.

**Result:** 52% of parameters quantized, 48% at full precision = perfect audio quality.

---

## 📊 Quick Comparison

| Model | Size | Audio Quality | Status |
|-------|------|---------------|--------|
| Original VibeVoice | 18.7 GB | ⭐⭐⭐⭐⭐ | Full precision |
| Other 8-bit models | 10.6 GB | 💥 NOISE | ❌ Don't work |
| **This model** | **11.6 GB** | ⭐⭐⭐⭐⭐ | ✅ **Perfect** |

+1.0 GB vs other 8-bit models = perfect audio instead of noise. Worth it.

---

## 💻 How to Use It

### With Transformers

```python
from transformers import AutoModelForCausalLM, AutoProcessor
import torch
import scipy.io.wavfile as wavfile

# Load model
model = AutoModelForCausalLM.from_pretrained(
    "FabioSarracino/VibeVoice-Large-Q8",
    device_map="auto",
    trust_remote_code=True,
    torch_dtype=torch.bfloat16,
)

processor = AutoProcessor.from_pretrained(
    "FabioSarracino/VibeVoice-Large-Q8",
    trust_remote_code=True
)

# Generate audio
text = "Hello, this is VibeVoice speaking."
inputs = processor(text, return_tensors="pt").to(model.device)
output = model.generate(**inputs, max_new_tokens=None)

# Save
audio = output.speech_outputs[0].cpu().numpy()
wavfile.write("output.wav", 24000, audio)
```

### With ComfyUI (recommended)

1. Install the custom node:
   ```bash
   cd ComfyUI/custom_nodes
   git clone https://github.com/Enemyx-net/VibeVoice-ComfyUI
   ```

2. Download this model to `ComfyUI/models/vibevoice/`

3. Restart ComfyUI and use it normally!

---

## 💾 System Requirements

### Minimum
- **VRAM:** 12 GB
- **RAM:** 16 GB
- **GPU:** NVIDIA with CUDA (required)
- **Storage:** 11 GB

### Recommended
- **VRAM:** 16+ GB
- **RAM:** 32 GB
- **GPU:** RTX 3090/4090, A5000 or better

⚠️ **Not supported:** CPU, Apple Silicon (MPS), AMD GPUs

---

## ⚠️ Limitations

1. **Requires NVIDIA GPU with CUDA** - won't work on CPU or Apple Silicon
2. **Inference only** - don't use for fine-tuning
3. **Requires:**
   - `transformers>=4.51.3`
   - `bitsandbytes>=0.43.0`

---

## 🆚 When to Use This Model

### ✅ Use this 8-bit if:
- You have 12-16 GB VRAM
- You want maximum quality with reduced size
- You need a production-ready model
- You want the best size/quality balance

### Use full precision (18.7 GB) if:
- You have unlimited VRAM (24+ GB)
- You're doing research requiring absolute precision

### Use 4-bit NF4 (~6.6 GB) if:
- You only have 8-10 GB VRAM
- You can accept a small quality trade-off

---

## 🔧 Troubleshooting

### "OutOfMemoryError" during loading

- Close other GPU applications
- Use `device_map="auto"`
- Reduce batch size to 1

### "BitsAndBytes not found"

```bash
pip install bitsandbytes>=0.43.0
```

### Audio sounds distorted

This shouldn't happen! If it does:
1. Verify you downloaded the correct model
2. Update transformers: `pip install --upgrade transformers`
3. Check CUDA: `torch.cuda.is_available()` should return `True`

---

## 📚 Citation

```bibtex
@misc{vibevoice-q8-2025,
  title={VibeVoice-Large-Q8: Selective 8-bit Quantization for Audio Quality},
  author={Fabio Sarracino},
  year={2025},
  url={https://huggingface.co/FabioSarracino/VibeVoice-Large-Q8}
}
```

### Original Model

```bibtex
@misc{vibevoice2024,
  title={VibeVoice: High-Quality Text-to-Speech with Large Language Models},
  author={Microsoft Research},
  year={2024},
  url={https://github.com/microsoft/VibeVoice}
}
```

---

## 🔗 Related Resources

- [Original Model](https://huggingface.co/aoi-ot/VibeVoice-Large) - Full precision base
- [ComfyUI Node](https://github.com/Enemyx-net/VibeVoice-ComfyUI) - ComfyUI integration

---

## 📜 License

MIT License.

---

## 🤝 Support

- **Issues:** [GitHub Issues](https://github.com/Enemyx-net/VibeVoice-ComfyUI/issues)
- **Questions:** [HuggingFace Discussions](https://huggingface.co/FabioSarracino/VibeVoice-Large-Q8/discussions)

If this model helped you, leave a ⭐ on GitHub!

---

<div align="center">

**Created by [Fabio Sarracino](https://github.com/Enemyx-net)**

*The first 8-bit VibeVoice model that actually works*

[🤗 HuggingFace](https://huggingface.co/FabioSarracino/VibeVoice-Large-Q8) • [💻 GitHub](https://github.com/Enemyx-net/VibeVoice-ComfyUI)

</div>
