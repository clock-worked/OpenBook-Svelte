import argparse
import os
import sys
from pathlib import Path


SCRIPTS_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_PYTHON_DIR))

from _path_setup import bootstrap_python_script_paths

PATHS = bootstrap_python_script_paths(__file__)
REPO_ROOT = PATHS["repoRoot"]


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
    return text.strip()


def format_script(text: str) -> str:
    safe_text = text.replace("\u2019", "'")
    return f"Speaker 1: {safe_text}"


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    default_model = REPO_ROOT / "py_services" / "openbook_parser" / "data" / "VibeVoice-Large-Q8"
    default_repo = REPO_ROOT / "py_services" / "openbook_parser" / "data" / "VibeVoice-ComfyUI"
    default_output = script_dir / "samples" / "vibevoice_smoke_test.wav"

    parser = argparse.ArgumentParser(description="Simple VibeVoice smoke test")
    parser.add_argument(
        "--model_path",
        default=str(default_model),
        help="Path to local VibeVoice model folder",
    )
    parser.add_argument(
        "--repo_path",
        default=str(default_repo) if default_repo.is_dir() else None,
        help="Optional path to VibeVoice Python modules (repo root or vvembed)",
    )
    parser.add_argument(
        "--sample_path",
        default=(
            "C:/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/"
            "Primal-Hunter/Audio Samples/narrator-b13-c1-20.wav"
        ),
        help="Narrator voice sample WAV path",
    )
    parser.add_argument(
        "--text",
        default="Hey there! Congrulations Chad you did it! You can now run vibevoice from open book.",
        help="Text to synthesize",
    )
    parser.add_argument(
        "--output_path",
        default=str(default_output),
        help="Output WAV file",
    )
    parser.add_argument(
        "--device",
        default="cuda",
        choices=["cuda", "cpu", "mps"],
        help="Inference device",
    )
    parser.add_argument("--ddpm_steps", type=int, default=20)
    parser.add_argument("--cfg_scale", type=float, default=1.3)
    return parser.parse_args()


def add_repo_paths(repo_path: str | None) -> None:
    if not repo_path:
        return

    repo_path = os.path.abspath(repo_path)
    if os.path.isdir(repo_path) and repo_path not in sys.path:
        sys.path.append(repo_path)

    vvembed_path = os.path.join(repo_path, "vvembed")
    if os.path.isdir(vvembed_path) and vvembed_path not in sys.path:
        sys.path.append(vvembed_path)


def import_vibevoice_modules() -> tuple[object, object]:
    try:
        from vibevoice.modular.modeling_vibevoice_inference import (
            VibeVoiceForConditionalGenerationInference,
        )
        from vibevoice.processor.vibevoice_processor import VibeVoiceProcessor

        return VibeVoiceForConditionalGenerationInference, VibeVoiceProcessor
    except ImportError:
        from modular.modeling_vibevoice_inference import (
            VibeVoiceForConditionalGenerationInference,
        )
        from processor.vibevoice_processor import VibeVoiceProcessor

        return VibeVoiceForConditionalGenerationInference, VibeVoiceProcessor


def main() -> None:
    args = parse_args()

    if not os.path.exists(args.sample_path):
        raise FileNotFoundError(f"Sample file not found: {args.sample_path}")

    if not os.path.isdir(args.model_path):
        raise FileNotFoundError(f"Model folder not found: {args.model_path}")

    add_repo_paths(args.repo_path)

    import torch
    from transformers.utils import logging

    logging.set_verbosity_error()

    model_cls, processor_cls = import_vibevoice_modules()

    torch_dtype = torch.bfloat16 if args.device == "cuda" else torch.float32

    print(f"Loading processor from: {args.model_path}")
    processor = processor_cls.from_pretrained(args.model_path)

    print(f"Loading model on {args.device} from: {args.model_path}")
    if args.device == "cuda":
        model = model_cls.from_pretrained(
            args.model_path,
            torch_dtype=torch_dtype,
            device_map="cuda",
            attn_implementation="sdpa",
        )
    elif args.device == "mps":
        model = model_cls.from_pretrained(
            args.model_path,
            torch_dtype=torch_dtype,
            device_map=None,
            attn_implementation="sdpa",
        )
        model.to("mps")
    else:
        model = model_cls.from_pretrained(
            args.model_path,
            torch_dtype=torch_dtype,
            device_map="cpu",
            attn_implementation="sdpa",
        )

    model.eval()
    model.set_ddpm_inference_steps(num_steps=args.ddpm_steps)

    script = format_script(normalize_text(args.text))
    inputs = processor(
        text=[script],
        voice_samples=[[args.sample_path]],
        padding=True,
        return_tensors="pt",
        return_attention_mask=True,
    )

    target_device = args.device if args.device != "cpu" else "cpu"
    for key, value in inputs.items():
        if torch.is_tensor(value):
            inputs[key] = value.to(target_device)

    print("Generating audio...")
    outputs = model.generate(
        **inputs,
        max_new_tokens=None,
        cfg_scale=args.cfg_scale,
        tokenizer=processor.tokenizer,
        generation_config={"do_sample": False},
    )

    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    processor.save_audio(outputs.speech_outputs[0], output_path=str(output_path))

    print(f"Done: {output_path}")


if __name__ == "__main__":
    main()
