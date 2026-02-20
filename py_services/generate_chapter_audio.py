import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Tuple
from tqdm import tqdm

DEFAULT_COMFY_URL = "http://127.0.0.1:8000"
DEFAULT_COMFY_ROOT = "C:/Users/Chad/Documents/ComfyUI"
DEFAULT_WORKFLOW_PATH = (
    "C:/Users/Chad/Documents/ComfyUI/custom_nodes/"
    "VibeVoice-ComfyUI/examples/Single-Speaker.json"
)
DEFAULT_MODEL_PATH = "C:/Users/Chad/Documents/ComfyUI/models/vibevoice/VibeVoice-Large"

# Hardcoded speaker -> sample mapping for the first pass.
# Update these paths to point at your actual WAV samples.
SPEAKER_SAMPLES = {
    "narrator": "C:\\Users\\Chad\\Documents\\Code\\Python\\Useful-Scripts\\Data\\Resources\\Primal-Hunter\\Audio Samples\\narrator-b13-c1-20.wav",
    "jake": "C:\\Users\\Chad\\Documents\\Code\\Python\\Useful-Scripts\\Data\\Resources\\Primal-Hunter\\Audio Samples\\jake-b13-c24-15.wav",
    "carmen": "C:\\Users\\Chad\\Documents\\Code\\Python\\Useful-Scripts\\Data\\Resources\\Primal-Hunter\\Audio Samples\\carmen-b13-c24-60.wav",
    
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate per-line audio from dialogue.json using VibeVoice"
    )
    parser.add_argument(
        "--dialogue",
        required=True,
        help="Path to dialogue.json",
    )
    parser.add_argument(
        "--backend",
        choices=["vibevoice", "comfy"],
        default="vibevoice",
        help="Backend to use (default: vibevoice)",
    )
    parser.add_argument(
        "--output_dir",
        default=None,
        help="Where to save per-line WAVs (default: <chapter>/audio_lines)",
    )
    parser.add_argument(
        "--workflow",
        default=DEFAULT_WORKFLOW_PATH,
        help="Path to Single-Speaker workflow JSON",
    )
    parser.add_argument(
        "--comfy_url",
        default=DEFAULT_COMFY_URL,
        help="ComfyUI base URL (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--comfy_root",
        default=DEFAULT_COMFY_ROOT,
        help="ComfyUI root folder (default: C:/Users/Chad/Documents/ComfyUI)",
    )
    parser.add_argument(
        "--model_path",
        default=DEFAULT_MODEL_PATH,
        help="Local VibeVoice model folder path",
    )
    parser.add_argument(
        "--vibevoice_repo",
        default=os.path.join(DEFAULT_COMFY_ROOT, "custom_nodes", "VibeVoice-ComfyUI"),
        help="Path to VibeVoice-ComfyUI repo for importing modules",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Device for inference: cuda | mps | cpu (default: auto)",
    )
    parser.add_argument(
        "--cfg_scale",
        type=float,
        default=1.3,
        help="Classifier-Free Guidance scale (VibeVoice)",
    )
    parser.add_argument(
        "--ddpm_steps",
        type=int,
        default=20,
        help="Number of DDPM inference steps (VibeVoice)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed (VibeVoice)",
    )
    parser.add_argument(
        "--start_id",
        type=int,
        default=1,
        help="Start from line id (inclusive)",
    )
    parser.add_argument(
        "--line_id",
        type=int,
        default=None,
        help="Generate only a single line id",
    )
    parser.add_argument(
        "--end_id",
        type=int,
        default=None,
        help="Stop at line id (inclusive)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of lines processed",
    )
    parser.add_argument(
        "--skip_existing",
        action="store_true",
        help="Skip lines that already have output WAVs",
    )
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="Validate inputs without calling ComfyUI",
    )
    return parser.parse_args()


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def normalize_text(text: str) -> str:
    # Replace common typography with ASCII to avoid VibeVoice hiccups.
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": "\"",
        "\u201d": "\"",
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


def resolve_device(device: str, torch_module) -> tuple[str, "torch.dtype", str]:
    if device.lower() == "mpx":
        device = "mps"
    if device == "mps" and not torch_module.backends.mps.is_available():
        print("Warning: MPS not available, falling back to CPU.")
        device = "cpu"

    if device == "mps":
        return device, torch_module.float32, "sdpa"
    if device == "cuda":
        return device, torch_module.bfloat16, "flash_attention_2"
    return "cpu", torch_module.float32, "sdpa"


def load_vibevoice_backend(
    model_path: str,
    device: Optional[str],
    ddpm_steps: int,
    repo_path: Optional[str] = None,
) -> Tuple[Any, Any, str]:
    import torch  # noqa: WPS433
    from transformers.utils import logging
    
    # Try to add VibeVoice repo to sys.path if provided
    if repo_path and os.path.isdir(repo_path):
        if repo_path not in sys.path:
            sys.path.append(repo_path)
            
        # Also check for vvembed in repo_path
        vvembed_path = os.path.join(repo_path, "vvembed")
        if os.path.isdir(vvembed_path) and vvembed_path not in sys.path:
            sys.path.append(vvembed_path)

    try:
        try:
            from vibevoice.modular.modeling_vibevoice_inference import (
                VibeVoiceForConditionalGenerationInference,
            )
            from vibevoice.processor.vibevoice_processor import VibeVoiceProcessor
        except ImportError:
            # Try importing from 'modular' directly (vvembed style)
            from modular.modeling_vibevoice_inference import (
                VibeVoiceForConditionalGenerationInference,
            )
            from processor.vibevoice_processor import VibeVoiceProcessor
            
    except ImportError:
        # Fallback: try default ComfyUI path if repo_path wasn't enough or wasn't provided
        default_path = "C:/Users/Chad/Documents/ComfyUI/custom_nodes/VibeVoice-ComfyUI"
        
        # Check for vvembed
        default_vvembed = os.path.join(default_path, "vvembed")
        if os.path.isdir(default_vvembed):
            if default_vvembed not in sys.path:
                sys.path.append(default_vvembed)
            
            from modular.modeling_vibevoice_inference import (
                VibeVoiceForConditionalGenerationInference,
            )
            from processor.vibevoice_processor import VibeVoiceProcessor
        else:
            if default_path not in sys.path and os.path.exists(default_path):
                sys.path.append(default_path)
            
            from vibevoice.modular.modeling_vibevoice_inference import (
                VibeVoiceForConditionalGenerationInference,
            )
            from vibevoice.processor.vibevoice_processor import VibeVoiceProcessor

    logging.set_verbosity_error()

    if device is None:
        device = detect_default_device()

    device, torch_dtype, attn_impl = resolve_device(device, torch)

    processor = VibeVoiceProcessor.from_pretrained(model_path)
    try:
        if device == "mps":
            model = VibeVoiceForConditionalGenerationInference.from_pretrained(
                model_path,
                torch_dtype=torch_dtype,
                attn_implementation=attn_impl,
                device_map=None,
            )
            model.to("mps")
        elif device == "cuda":
            model = VibeVoiceForConditionalGenerationInference.from_pretrained(
                model_path,
                torch_dtype=torch_dtype,
                device_map="cuda",
                attn_implementation=attn_impl,
            )
        else:
            model = VibeVoiceForConditionalGenerationInference.from_pretrained(
                model_path,
                torch_dtype=torch_dtype,
                device_map="cpu",
                attn_implementation=attn_impl,
            )
    except Exception as exc:
        if attn_impl == "flash_attention_2":
            print(f"[WARN] {type(exc).__name__}: {exc}")
            print("Retrying with SDPA attention (quality may differ)...")
            model = VibeVoiceForConditionalGenerationInference.from_pretrained(
                model_path,
                torch_dtype=torch_dtype,
                device_map=(device if device in ("cuda", "cpu") else None),
                attn_implementation="sdpa",
            )
            if device == "mps":
                model.to("mps")
        else:
            raise

    model.eval()
    model.set_ddpm_inference_steps(num_steps=ddpm_steps)
    return model, processor, device


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def file_size(path: str) -> Optional[int]:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def ensure_comfy_input(sample_path: str, comfy_input_dir: str) -> str:
    if not os.path.exists(sample_path):
        raise FileNotFoundError(f"Voice sample not found: {sample_path}")

    ensure_dir(comfy_input_dir)
    sample_path = os.path.abspath(sample_path)
    comfy_input_dir = os.path.abspath(comfy_input_dir)

    if os.path.commonpath([sample_path, comfy_input_dir]) == comfy_input_dir:
        return os.path.basename(sample_path)

    filename = os.path.basename(sample_path)
    target_path = os.path.join(comfy_input_dir, filename)
    if os.path.exists(target_path):
        if file_size(target_path) == file_size(sample_path):
            return filename
        name, ext = os.path.splitext(filename)
        filename = f"{name}_copy{ext}"
        target_path = os.path.join(comfy_input_dir, filename)

    shutil.copy2(sample_path, target_path)
    return filename


def patch_workflow(
    workflow: Dict[str, Any],
    sample_filename: str,
    text: str,
) -> Dict[str, Any]:
    patched = deepcopy(workflow)
    nodes: List[Dict[str, Any]] = patched.get("nodes", [])

    for node in nodes:
        if node.get("id") == 15 and node.get("type") == "LoadAudio":
            widgets = node.get("widgets_values")
            if isinstance(widgets, list) and widgets:
                widgets[0] = sample_filename
        if node.get("id") == 44 and node.get("type") == "VibeVoiceSingleSpeakerNode":
            widgets = node.get("widgets_values")
            if isinstance(widgets, list) and widgets:
                widgets[0] = text

    return patched


def post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ComfyUI HTTP {exc.code}: {body}") from exc


def get_json(url: str) -> Dict[str, Any]:
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_for_audio(
    comfy_url: str,
    prompt_id: str,
    timeout_s: int = 300,
    poll_s: float = 1.0,
) -> Tuple[str, Dict[str, Any]]:
    history_url = f"{comfy_url}/history/{urllib.parse.quote(prompt_id)}"
    start = time.time()
    while time.time() - start < timeout_s:
        history = get_json(history_url)
        if prompt_id in history:
            outputs = history[prompt_id].get("outputs", {})
            audio = find_audio_output(outputs)
            if audio is not None:
                return prompt_id, audio
        time.sleep(poll_s)
    raise TimeoutError(f"Timed out waiting for prompt {prompt_id}")


def find_audio_output(outputs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for node_output in outputs.values():
        audio_list = node_output.get("audio")
        if isinstance(audio_list, list) and audio_list:
            return audio_list[0]
    return None


def download_audio(
    comfy_url: str,
    audio_meta: Dict[str, Any],
    output_path: str,
) -> None:
    filename = audio_meta.get("filename")
    if not filename:
        raise ValueError("No filename found in audio metadata")

    subfolder = audio_meta.get("subfolder", "")
    file_type = audio_meta.get("type", "output")

    query = urllib.parse.urlencode(
        {"filename": filename, "subfolder": subfolder, "type": file_type}
    )
    url = f"{comfy_url}/view?{query}"

    ensure_dir(os.path.dirname(output_path))
    urllib.request.urlretrieve(url, output_path)


def workflow_to_prompt(workflow: Dict[str, Any]) -> Dict[str, Any]:
    if "nodes" not in workflow or "links" not in workflow:
        raise ValueError("Workflow JSON missing nodes/links for conversion")

    links = workflow.get("links", [])
    link_map: Dict[int, Tuple[int, int]] = {}
    for link in links:
        if not isinstance(link, list) or len(link) < 4:
            continue
        link_id = link[0]
        from_node = link[1]
        from_slot = link[2]
        link_map[link_id] = (from_node, from_slot)

    prompt: Dict[str, Any] = {}
    for node in workflow.get("nodes", []):
        node_id = node.get("id")
        node_type = node.get("type")
        if node_id is None or node_type is None:
            continue

        inputs: Dict[str, Any] = {}
        widget_values = list(node.get("widgets_values") or [])
        widget_index = 0

        for input_def in node.get("inputs", []):
            name = input_def.get("name")
            if not name:
                continue

            link_id = input_def.get("link")
            if link_id is not None and link_id in link_map:
                from_node, from_slot = link_map[link_id]
                inputs[name] = [str(from_node), from_slot]
                continue

            if "widget" in input_def:
                if widget_index < len(widget_values):
                    inputs[name] = widget_values[widget_index]
                widget_index += 1

        prompt[str(node_id)] = {
            "class_type": node_type,
            "inputs": inputs,
        }

    return prompt


def iter_lines(dialogue: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for line in dialogue.get("lines", []):
        yield line


def render_progress(current: int, total: int, avg_s: float) -> str:
    if total <= 0:
        return ""


def main() -> None:
    args = parse_args()
    dialogue = load_json(args.dialogue)

    if args.line_id is not None:
        args.start_id = args.line_id
        args.end_id = args.line_id
        args.skip_existing = False

    chapter_dir = os.path.dirname(args.dialogue)
    output_dir = args.output_dir or os.path.join(chapter_dir, "audio_lines")
    ensure_dir(output_dir)

    if args.backend == "comfy":
        workflow = load_json(args.workflow)
        comfy_input_dir = os.path.join(args.comfy_root, "input")
    else:
        workflow = {}
        comfy_input_dir = ""

    vibevoice_model = None
    vibevoice_processor = None
    vibevoice_device = "cpu"
    if args.backend == "vibevoice" and not args.dry_run:
        vibevoice_model, vibevoice_processor, vibevoice_device = load_vibevoice_backend(
            args.model_path,
            args.device,
            args.ddpm_steps,
            repo_path=args.vibevoice_repo,
        )

    manifest: Dict[str, Any] = {
        "dialogue": args.dialogue,
        "output_dir": output_dir,
        "lines": [],
    }

    candidates: List[Dict[str, Any]] = []
    for line in iter_lines(dialogue):
        line_id = line.get("id")
        if not isinstance(line_id, int):
            continue
        if line_id < args.start_id:
            continue
        if args.end_id is not None and line_id > args.end_id:
            continue
        if not normalize_text(line.get("text", "")):
            continue
        candidates.append(line)

    processed = 0
    total = len(candidates)
    start_wall = time.time()
    with tqdm(total=total, desc="Generating", unit="line") as progress:
        for index, line in enumerate(candidates, start=1):
        line_id = line.get("id")
        if not isinstance(line_id, int):
            continue
        if line_id < args.start_id:
            continue
        if args.end_id is not None and line_id > args.end_id:
            continue

        text = normalize_text(line.get("text", ""))
        if not text:
            continue

        character_id = line.get("characterId", "narrator")
        sample_path = SPEAKER_SAMPLES.get(character_id)
        if not sample_path:
            raise KeyError(
                f"No sample mapping for characterId: {character_id}")

        if args.backend == "comfy":
            sample_filename = ensure_comfy_input(sample_path, comfy_input_dir)
            patched_workflow = patch_workflow(workflow, sample_filename, text)
            if "nodes" in patched_workflow and "links" in patched_workflow:
                prompt = workflow_to_prompt(patched_workflow)
            elif "prompt" in patched_workflow:
                prompt = patched_workflow["prompt"]
            else:
                prompt = patched_workflow
        else:
            prompt = {}

        output_name = f"{line_id:04d}_{character_id}.wav"
        output_path = os.path.join(output_dir, output_name)

        if args.skip_existing and os.path.exists(output_path):
            manifest["lines"].append(
                {
                    "id": line_id,
                    "characterId": character_id,
                    "text": text,
                    "output": output_name,
                    "skipped": True,
                }
            )
            progress.update(1)
            continue

        if args.dry_run:
            manifest["lines"].append(
                {
                    "id": line_id,
                    "characterId": character_id,
                    "text": text,
                    "output": output_name,
                    "skipped": True,
                }
            )
            processed += 1
            progress.update(1)
        else:
            if args.backend == "comfy":
                response = post_json(
                    f"{args.comfy_url}/prompt",
                    {
                        "prompt": prompt,
                        "extra_data": {"extra_pnginfo": {"workflow": workflow}},
                    },
                )
                prompt_id = response.get("prompt_id")
                if not prompt_id:
                    raise RuntimeError(
                        f"Unexpected ComfyUI response: {response}")

                _, audio_meta = wait_for_audio(args.comfy_url, prompt_id)
                download_audio(args.comfy_url, audio_meta, output_path)
            else:
                if vibevoice_model is None or vibevoice_processor is None:
                    raise RuntimeError("VibeVoice backend not initialized")

                import torch  # noqa: WPS433

                if args.seed is not None:
                    torch.manual_seed(args.seed)

                script = format_script(text)
                inputs = vibevoice_processor(
                    text=[script],
                    voice_samples=[[sample_path]],
                    padding=True,
                    return_tensors="pt",
                    return_attention_mask=True,
                )

                target_device = vibevoice_device if vibevoice_device != "cpu" else "cpu"
                for key, value in inputs.items():
                    if torch.is_tensor(value):
                        inputs[key] = value.to(target_device)

                outputs = vibevoice_model.generate(
                    **inputs,
                    max_new_tokens=None,
                    cfg_scale=args.cfg_scale,
                    tokenizer=vibevoice_processor.tokenizer,
                    generation_config={"do_sample": False},
                )

                ensure_dir(os.path.dirname(output_path))
                vibevoice_processor.save_audio(
                    outputs.speech_outputs[0],
                    output_path=output_path,
                )

            manifest["lines"].append(
                {
                    "id": line_id,
                    "characterId": character_id,
                    "text": text,
                    "output": output_name,
                    "skipped": False,
                }
            )
            processed += 1

        progress.update(1)

        if args.limit is not None and processed >= args.limit:
            break

    manifest_path = os.path.join(output_dir, "manifest.json")
    write_json(manifest_path, manifest)

    print(f"Done. Outputs in: {output_dir}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
