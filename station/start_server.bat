@echo off
chcp 65001 >nul
REM ============================================================
REM  video-uniqueness 去重服务启动脚本
REM  使用带完整依赖(venv)的 Python 解释器，避免默认 python 缺
REM  typing_extensions / openai 导致 TTS 不可用。
REM ============================================================

SETLOCAL
SET "STATION_DIR=%~dp0"
SET "ROOT_DIR=%STATION_DIR%.."

REM 优先使用 WorkBuddy 托管 venv（已装 openai/sherpa_onnx/playwright/soundfile）
SET "VENV_PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

IF EXIST "%VENV_PY%" (
    SET "PY=%VENV_PY%"
) ELSE (
    REM 回退到 PATH 中的 python（需自行保证依赖已装）
    SET "PY=python"
)

echo [start] 使用解释器: %PY%
echo [start] 服务地址: http://127.0.0.1:8765/
"%PY%" "%ROOT_DIR%\station\server\mcp_server.py"
ENDLOCAL
