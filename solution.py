import json
import os
import wave
import numpy as np

manifest_path = "/c/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-16/0091 - Chapter 1148 - The Power of Blood/audio_lines/manifest.json"
audio_dir = "/c/Users/Chad/Documents/Code/Python/Useful-Scripts/Data/Resources/Primal-Hunter/Book-16/0091 - Chapter 1148 - The Power of Blood/audio_lines/"

# Fix for Windows paths in bash if necessary, though python handles it mostly
manifest_path = manifest_path.replace('/c/', 'C:/')
audio_dir = audio_dir.replace('/c/', 'C:/')

with open(manifest_path, 'r') as f:
    data = json.load(f)

# The manifest contains line-clips, we need the first and last used.
# The user said "actually used by line-clips assembly".
# Manifest structure usually has a top level key like "clips" or similar.
# Let's see the keys first.
# print(data.keys()) 
# Assuming standard structure of these scripts:
clips = data.get('line_clips', [])
if not clips:
    # try another common key
    clips = data.get('clips', [])

if not clips:
    print("No clips found in manifest")
    exit(1)

first_clip = clips[0]
last_clip = clips[-1]

def get_trailing_silence(filepath, threshold=0.001):
    if not os.path.exists(filepath):
        return None
    with wave.open(filepath, 'rb') as wr:
        params = wr.getparams()
        n_frames = wr.getnframes()
        frames = wr.readframes(n_frames)
        sample_width = wr.getsampwidth()
        
        if sample_width == 2:
            samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        elif sample_width == 4:
            # Assuming float32 if 4 bytes, or could be int32. Standard is usually int16 or float32.
            samples = np.frombuffer(frames, dtype=np.float32)
        else:
            return None
        
        # If stereo, take average or max of channels
        if params.nchannels > 1:
            samples = samples.reshape(-1, params.nchannels).max(axis=1)

        # Scan backward
        for i in range(len(samples)-1, -1, -1):
            if abs(samples[i]) > threshold:
                silence_frames = len(samples) - 1 - i
                return silence_frames / params.framerate
        return len(samples) / params.framerate

first_path = os.path.join(audio_dir, first_clip['path'])
last_path = os.path.join(audio_dir, last_clip['path'])

first_silence = get_trailing_silence(first_path)
last_silence = get_trailing_silence(last_path)

print(f"First Clip: {first_clip['path']}")
print(f"Trailing Silence: {first_silence:.4f}s")
print(f"Last Clip: {last_clip['path']}")
print(f"Trailing Silence: {last_silence:.4f}s")
