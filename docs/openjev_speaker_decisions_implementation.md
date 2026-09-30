# Open-Jev Speaker Decision Provider: Implementation Plan

This document outlines the implementation plan for integrating the Open-Jev typed decision model as a new local provider for speaker attribution in OpenBook. This plan follows the recommendations and decisions from the council memo `docs/council_openjev_speaker_decisions.md`.

## 1. Architecture Overview

The Open-Jev provider will be integrated as a new, parallel service within the existing `py_services` backend, mirroring the current `local_dialogue_ai_service.py` structure.

**Key Components:**

1.  **Open-Jev Model Server**: A separate, managed process running the upstream `jev.server` to host the 9B model. It will listen on `127.0.0.1:8791`.
2.  **`openjev_local_service.py`**: A new Python service class responsible for:
    *   Constructing the `state` and `questions` payload for Open-Jev.
    *   Calling the model server's `/v1/systemone` endpoint.
    *   Translating the probabilistic output into the `LocalDialogueAiLineResult` format.
    *   Managing request status and lifecycle, similar to `LocalDialogueAiService`.
3.  **`openjev_client.py`**: A thin, reusable HTTP client for interacting with the `/v1/systemone` endpoint to keep the service layer clean.
4.  **`parser_router.py`**: New API endpoints (`/api/openjev-dialogue-ai` and `/api/openjev-dialogue-ai-status/{request_id}`) will be added to expose the service.
5.  **Frontend Integration**:
    *   A new provider option in the UI (e.g., in `ChapterLocalAiPanel.svelte`).
    *   Methods in `apiClient.ts` to call the new endpoints.
    *   A new store `openjevDialogueAi.ts` (or modifications to `localDialogueAi.ts`) to manage state for the Open-Jev workflow.
6.  **Benchmark Harness**: The evaluation script `evaluate_dialogue_ai_against_curations.py` will be modified to accept a `--provider` flag (`gemini`, `local`, `openjev`) to allow for direct, comparable performance measurement.

## 2. Model Serving and Management

-   **Model**: `ZefanCai/Open-Jev-9B` (adapter) on `Qwen/Qwen3.5-9B` (base).
-   **Serving Stack**: The upstream `python -m jev.server` will be used as the primary serving method. It is lightweight and purpose-built. vLLM is a fallback if needed.
-   **Configuration**:
    *   `--device cuda:0` (will run on one RTX 3090).
    *   `--max-length 4096`.
    *   `--batch-size 1`.
    *   `--no-prefix-cache`.
    *   The server will run on `127.0.0.1:8791`.
-   **Process Management**: The server will be launched and managed as an external process. Documentation will be provided on how to start/stop it. For initial development, it will be run manually in a separate terminal.

## 3. Context Budget and Payload Construction

Open-Jev has a hard 4096-token limit for the entire request payload (`state` + `questions` including all option labels). The model server **rejects** requests that exceed this limit, it does not truncate them. Therefore, a strict token budgeting strategy is required.

The context will be assembled into the `state` string, and the character roster will form the `options` for a `choice` question.

**Context (`state`) Components (in order of priority):**

1.  **Current Paragraph & Quote**: The paragraph containing the target dialogue line, with the line wrapped in `<quote>` tags. (Highest Priority)
2.  **Scene Roster**: A list of characters present in the scene, including their names, aliases, and gender. (High Priority)
3.  **Recent Turn History**: The speaker of the last ~5 dialogue lines to capture turn-taking flow. (Medium Priority)
4.  **Previous/Next Paragraphs**: The full text of the paragraphs immediately preceding and succeeding the current one. (Low Priority)

**Token Budgeting Strategy:**

A `tiktoken` tokenizer will be used to measure the payload size before sending the request.

-   **Roster (Options)**: Max ~1500 tokens. Each character name/alias will be truncated if necessary. With a max of 52 options, this allows ~28 tokens per character.
-   **Context (State)**: Max ~2500 tokens.
    -   Current Paragraph: ~512 tokens
    -   Turn History: ~512 tokens
    -   Previous/Next Paragraphs: ~512 tokens each
-   **Truncation/Omission Logic**: If the total estimated tokens exceed ~4000 (leaving a safety margin), components will be removed in reverse order of priority:
    1.  Omit `nextParagraphText`.
    2.  Omit `previousParagraphText`.
    3.  Truncate turn history.
    4.  Truncate current paragraph context (outside the `<quote>`).

## 4. Roster Accuracy and Verification

To address the critical need for accurate speaker collection, a new verification script will be created.

**`py_services/openbook_parser/audit_roster_coverage.py`**

This script will:

1.  Take a book root and chapter regex as input.
2.  For each chapter, it will load the curated `dialogue.json` (ground truth) and the chapter text.
3.  It will use the `_build_scene_roster` function from `dialogue_ai_context.py` to generate the scene roster that the AI would see.
4.  **Audit Logic**: It will iterate through every dialogue line in the ground truth file. For each line, it will check if the true `characterId` is present in the generated scene roster.
5.  **Output**: The script will produce a per-chapter and aggregate report containing:
    *   **Roster Coverage %**: The percentage of dialogue lines where the correct speaker was included in the scene roster.
    *   **Missed Characters**: A list of characters that spoke but were missed by the roster generation logic.
    *   This provides a crucial "upper bound" on the model's accuracy. If a character isn't even an option, the model cannot be correct.

## 5. Implementation Phases

**Phase 1: MVP - Backend and Harness (This implementation)**

1.  **Model Download**: Complete the download of the model and loader repo to `C:\Users\Chad\openjev_models`.
2.  **Serving Spike**: Manually run the `jev.server` and confirm it loads the model and responds to a sample query via `curl` or a simple Python script.
3.  **Harness Modification**: Add the `--provider` flag to `evaluate_dialogue_ai_against_curations.py`. Create a simple adapter that allows the harness to score a `LocalDialogueAiService`-style provider.
4.  **Service Implementation**:
    *   Create `openjev_client.py`.
    *   Create `openjev_local_service.py` with the context builder and token budgeting logic.
    *   The service will map the highest probability option from Open-Jev to a `suggestion` or `keep_existing` outcome. A confidence score (the probability) will be attached.
5.  **Router Integration**: Add endpoints to `parser_router.py`.
6.  **Initial Benchmark**: Run the evaluation harness against Books 14-17 to get a baseline accuracy score for the Open-Jev provider.

**Phase 2: Frontend Integration**

-   Implement the UI toggle to allow selecting the "Open-Jev (Local)" provider.
-   Wire up the `apiClient` and a new `openjevDialogueAi.ts` store.
-   Implement the logic to display suggestions and handle auto-apply based on a configurable threshold (initially matching the 0.92 from the Gemini service).

**Phase 3: Roster Audit and Improvement**

-   Implement the `audit_roster_coverage.py` script.
-   Run the audit across the development books.
-   Use the results to tune the `_build_scene_roster` logic in `dialogue_ai_context.py` to improve coverage.
