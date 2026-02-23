#!/usr/bin/env bash
set -e
uvicorn robobloq_led.webapp:app --host 0.0.0.0 --port 8000 --reload
