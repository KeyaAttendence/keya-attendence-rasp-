#!/bin/bash

# Keya Fusion Attendance System - Full Setup Script
# This script handles system deps, venv, and AI models.

set -e

echo "🚀 Starting Full Setup..."

# 1. Install System Dependencies
echo "📦 Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgtk-3-dev \
    libgl1 \
    python3-dev \
    libpq-dev \
    pkg-config

# 2. Setup Virtual Environment
echo "🐍 Setting up Python environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

# 3. Upgrade pip and setuptools
pip install --upgrade pip "setuptools<70"

# 4. Install Requirements
echo "📥 Installing Python packages (this may take a few minutes for dlib)..."
pip install -r requirements.txt

# 5. Inject AI Models
echo "🧠 Injecting AI models into the environment..."
MODEL_PATH=$(python3 -c "import face_recognition_models; import os; print(os.path.dirname(face_recognition_models.__file__))")
mkdir -p "$MODEL_PATH/models"

if [ -d "models" ]; then
    cp models/*.dat "$MODEL_PATH/models/"
    echo "✅ Models successfully injected into $MODEL_PATH"
else
    echo "⚠️ Warning: Local 'models' folder not found. Relying on pip version."
fi

# 6. Database Initialization (Optional)
echo "📂 Ensuring database directory exists..."
mkdir -p data

echo "✨ Setup Complete!"
echo "👉 Run the app with: source .venv/bin/activate && python app.py"
