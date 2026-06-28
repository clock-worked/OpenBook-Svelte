import json

# 1. Load your raw text
with open("raw_text.txt", "r", encoding="utf-8") as f:
    raw_text = f.read()

# 2. Load your dialogue JSON
with open("dialogue.json", "r", encoding="utf-8") as f:
    dialogue_data = json.load(f)

# The system prompt we will use for every example
system_prompt = (
    "You are an expert dialogue attribution AI. Read the context and the target text. "
    "Output ONLY the characterId of the speaker, or 'narrator' if it is narration. "
    "Do not include any other text."
)

jsonl_lines = []
CONTEXT_WINDOW = 400 # Number of characters to grab before and after the quote

# 3. Iterate through every span in your JSON
for item in dialogue_data:
    start = item["span"]["start"]
    end = item["span"]["end"]
    character_id = item["characterId"]
    
    # Extract the exact target quote/text
    target_text = raw_text[start:end]
    
    # Extract the surrounding context (with boundaries to prevent errors)
    context_start = max(0, start - CONTEXT_WINDOW)
    context_end = min(len(raw_text), end + CONTEXT_WINDOW)
    context_text = raw_text[context_start:context_end]
    
    # Construct the OpenAI Chat format
    user_content = f"Context:\n{context_text}\n\nTarget Text:\n{target_text}"
    
    message_row = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": character_id} # The exact ID is the only output
        ]
    }
    
    jsonl_lines.append(message_row)

# 4. Save to a JSONL file ready for Fine-Tuning
with open("training_data.jsonl", "w", encoding="utf-8") as f:
    for line in jsonl_lines:
        f.write(json.dumps(line) + "\n")

print(f"Successfully generated {len(jsonl_lines)} training examples!")