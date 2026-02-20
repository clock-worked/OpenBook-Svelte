#!/bin/bash
cd "$(dirname "$0")"
uvicorn api_server:app --reload --reload-exclude '**/.venv/**' --reload-exclude '**/__pycache__/**' --reload-exclude '**/*.venv/**'

