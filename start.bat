@echo off
chcp 65001 > nul

if not exist logs mkdir logs

rem Vector backend configuration
set VECTOR_BACKEND=turbovec
set TURBOVEC_INDEX_DIR=./turbovec_index
set PROCESSED_FILES_PATH=processed_files_turbovec.txt
set ACTIVITY_LOG_PATH=logs/activity_turbovec.log

rem Local AI provider configuration
if "%OSL_RAG_OLLAMA_BASE_URL%"=="" set OSL_RAG_OLLAMA_BASE_URL=http://localhost:11434

rem Native UI
cd /D "%~dp0"
start "OSL AI Assistant" /B /D "%~dp0" cmd /C "py -3.12 native_ui.py 1> logs\native_ui_debug.log 2>&1"

exit
