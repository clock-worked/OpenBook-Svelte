"""
VibeVoice local inference wrapper for generating audio from text with a voice sample.
"""

import os
import sys
import types
from pathlib import Path
from typing import Optional, Tuple, Any

DEFAULT_DATA_DIR = Path(__file__).parent / "openbook_parser" / "data"
DEFAULT_VIBEVOICE_MODEL_PATH = str(DEFAULT_DATA_DIR / "VibeVoice-Large-Q8")
DEFAULT_VIBEVOICE_REPO_PATH = str(DEFAULT_DATA_DIR / "VibeVoice-ComfyUI")


def ensure_transformers_flash_attention_compat() -> None:
    """Provide a minimal compatibility shim for older transformers versions.

    Some embedded VibeVoice modules import `FlashAttentionKwargs` from
    `transformers.modeling_flash_attention_utils`, which is absent in older
    transformers builds. The symbol is used only for typing in these modules,
    so a lightweight runtime shim is sufficient.
    """
    try:
        import transformers.modeling_flash_attention_utils  # noqa: F401,WPS433
        return
    except Exception:
        pass

    compat_module_name = "transformers.modeling_flash_attention_utils"
    if compat_module_name in sys.modules:
        return

    shim = types.ModuleType(compat_module_name)
    shim.FlashAttentionKwargs = dict
    sys.modules[compat_module_name] = shim


def normalize_text(text: str) -> str:
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = " ".join(text.split())
    return text.strip()


def format_script(text: str) -> str:
    safe_text = text.replace("\u2019", "'")
    return f"Speaker 1: {safe_text}"


def detect_default_device() -> str:
    try:
        import torch  # noqa: WPS433

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        return "cpu"
    return "cpu"


def resolve_attention_implementation(device: str) -> str:
    forced_attn_impl = os.getenv("VIBEVOICE_ATTN_IMPL", "").strip().lower()
    if forced_attn_impl in {"sdpa", "flash_attention_2", "eager"}:
        return forced_attn_impl

    if device == "cuda" and os.name == "nt":
        return "sdpa"
    if device == "cuda":
        return "flash_attention_2"
    return "sdpa"


def resolve_device(device: str, torch_module) -> Tuple[str, Any, str]:
    if device.lower() == "mpx":
        device = "mps"
    if device == "mps" and not torch_module.backends.mps.is_available():
        print("Warning: MPS not available, falling back to CPU.")
        device = "cpu"

    attn_impl = resolve_attention_implementation(device)
    if device == "mps":
        return device, torch_module.float32, "sdpa"
    if device == "cuda":
        return device, torch_module.bfloat16, attn_impl
    return "cpu", torch_module.float32, "sdpa"


def load_vibevoice_backend(
    model_path: str,
    device: Optional[str],
    ddpm_steps: int,
    repo_path: Optional[str] = None,
) -> Tuple[Any, Any, str]:
    ensure_transformers_flash_attention_compat()

    import torch  # noqa: WPS433
    import sys
    from transformers.utils import logging

    # Prefer explicit/local repo path first.
    if repo_path and os.path.isdir(repo_path):
        if repo_path not in sys.path:
            sys.path.append(repo_path)
        vvembed_repo_path = os.path.join(repo_path, "vvembed")
        if os.path.isdir(vvembed_repo_path) and vvembed_repo_path not in sys.path:
            sys.path.append(vvembed_repo_path)

    # Try importing, if fail, add default local repo path under py_services.
    try:
        from vibevoice.modular.modeling_vibevoice_inference import (
            VibeVoiceForConditionalGenerationInference,
        )
        from vibevoice.processor.vibevoice_processor import VibeVoiceProcessor
    except ImportError:
        comfy_vibevoice_path = DEFAULT_VIBEVOICE_REPO_PATH

        # Check for vvembed (embedded version)
        vvembed_path = os.path.join(comfy_vibevoice_path, "vvembed")
        if os.path.exists(vvembed_path):
            if vvembed_path not in sys.path:
                sys.path.append(vvembed_path)

            try:
                from modular.modeling_vibevoice_inference import (
                    VibeVoiceForConditionalGenerationInference,
                )
                from processor.vibevoice_processor import VibeVoiceProcessor
            except ImportError as ie:
                print(f"Failed to import from vvembed: {ie}")
                raise ie
        else:
            # Fallback to repo root if not embedded?
            if os.path.exists(comfy_vibevoice_path) and comfy_vibevoice_path not in sys.path:
                sys.path.append(comfy_vibevoice_path)

            from vibevoice.modular.modeling_vibevoice_inference import (
                VibeVoiceForConditionalGenerationInference,
            )
            from vibevoice.processor.vibevoice_processor import VibeVoiceProcessor

    logging.set_verbosity_error()

    if device is None:
        device = detect_default_device()

    device, torch_dtype, attn_impl = resolve_device(device, torch)
    if device == "cuda" and attn_impl == "sdpa" and os.name == "nt":
        print("Using SDPA attention for VibeVoice CUDA on Windows.")

    model_path_abs = os.path.abspath(model_path)
    if not os.path.isdir(model_path_abs):
        raise FileNotFoundError(
            "VibeVoice model folder not found: "
            f"{model_path_abs}. Set VIBEVOICE_MODEL_PATH to a valid local folder."
        )

    processor = VibeVoiceProcessor.from_pretrained(
        model_path_abs,
        local_files_only=True,
    )
    try:
        if device == "mps":
            model = VibeVoiceForConditionalGenerationInference.from_pretrained(
                model_path_abs,
                torch_dtype=torch_dtype,
                attn_implementation=attn_impl,
                device_map=None,
                local_files_only=True,
            )
            model.to("mps")
        elif device == "cuda":
            model = VibeVoiceForConditionalGenerationInference.from_pretrained(
                model_path_abs,
                torch_dtype=torch_dtype,
                device_map="cuda",
                attn_implementation=attn_impl,
                local_files_only=True,
            )
        else:
            model = VibeVoiceForConditionalGenerationInference.from_pretrained(
                model_path_abs,
                torch_dtype=torch_dtype,
                device_map="cpu",
                attn_implementation=attn_impl,
                local_files_only=True,
            )
    except Exception as exc:
        if attn_impl == "flash_attention_2":
            print(f"[WARN] {type(exc).__name__}: {exc}")
            print("Retrying with SDPA attention (quality may differ)...")
            model = VibeVoiceForConditionalGenerationInference.from_pretrained(
                model_path_abs,
                torch_dtype=torch_dtype,
                device_map=(device if device in ("cuda", "cpu") else None),
                attn_implementation="sdpa",
                local_files_only=True,
            )
            if device == "mps":
                model.to("mps")
        else:
            raise

    model.eval()
    model.set_ddpm_inference_steps(num_steps=ddpm_steps)
    return model, processor, device


class VibeVoiceLocalService:
    def __init__(
        self,
        model_path: Optional[str] = None,
        repo_path: Optional[str] = None,
        device: Optional[str] = None,
        ddpm_steps: int = 20,
        cfg_scale: float = 1.3,
    ) -> None:
        self.model_path = model_path or os.getenv(
            "VIBEVOICE_MODEL_PATH", DEFAULT_VIBEVOICE_MODEL_PATH
        )
        self.repo_path = repo_path or os.getenv(
            "VIBEVOICE_REPO_PATH", DEFAULT_VIBEVOICE_REPO_PATH
        )
        self.device = device
        self.ddpm_steps = ddpm_steps
        self.cfg_scale = cfg_scale
        self._model = None
        self._processor = None
        self._device = "cpu"

    def _ensure_backend(self) -> None:
        if self._model is not None and self._processor is not None:
            return
        self._model, self._processor, self._device = load_vibevoice_backend(
            self.model_path,
            self.device,
            self.ddpm_steps,
            repo_path=self.repo_path,
        )

    def generate_audio(
        self,
        text: str,
        sample_path: str,
        output_path: str,
        seed: Optional[int] = None,
    ) -> None:
        # Check for sample existence, trying common audio extensions if specific file not found
        # Also try exact path first (in case extension is already provided)
        search_paths = [sample_path]
        if not sample_path.lower().endswith(('.wav', '.mp3', '.flac', '.m4a', '.ogg')):
            search_paths.extend(
                [sample_path + ext for ext in ['.wav', '.mp3', '.flac', '.m4a', '.ogg']])

        final_path = None
        for path in search_paths:
            if os.path.exists(path):
                final_path = path
                break

        if not final_path:
            # Look for partial matches in the parent directory to help user debug
            parent_dir = os.path.dirname(sample_path)
            filename = os.path.basename(sample_path)
            candidates = []
            if os.path.exists(parent_dir):
                try:
                    candidates = [f for f in os.listdir(
                        parent_dir) if f.startswith(filename)]
                except:
                    pass

            msg = f"Voice sample not found at: {sample_path}"
            if candidates:
                msg += f". Did you mean one of these? {candidates}"
            elif not os.path.exists(parent_dir):
                msg += f". The directory also does not exist: {parent_dir}"

            raise FileNotFoundError(msg)

        sample_path = final_path

        self._ensure_backend()
        if self._model is None or self._processor is None:
            raise RuntimeError("VibeVoice backend not initialized")

        import torch  # noqa: WPS433

        if seed is not None:
            torch.manual_seed(seed)

        script = format_script(normalize_text(text))
        inputs = self._processor(
            text=[script],
            voice_samples=[[sample_path]],
            padding=True,
            return_tensors="pt",
            return_attention_mask=True,
        )

        target_device = self._device if self._device != "cpu" else "cpu"
        for key, value in inputs.items():
            if torch.is_tensor(value):
                inputs[key] = value.to(target_device)

        outputs = self._model.generate(
            **inputs,
            max_new_tokens=None,
            cfg_scale=self.cfg_scale,
            tokenizer=self._processor.tokenizer,
            generation_config={"do_sample": False},
        )

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        self._processor.save_audio(
            outputs.speech_outputs[0],
            output_path=output_path,
        )
