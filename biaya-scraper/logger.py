from __future__ import annotations
import os
import sys
import time

LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_current_level = LEVELS.get(os.getenv("LOG_LEVEL", "INFO").upper(), 20)
_log_file_path: str | None = None

def setup(log_file_path: str | None, level: str | None = None):
    global _current_level, _log_file_path
    if level:
        _current_level = LEVELS.get(level.upper(), _current_level)
    _log_file_path = log_file_path

def ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

def _write(line: str):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    if _log_file_path:
        try:
            with open(_log_file_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

def log(level: str, msg: str):
    lv = LEVELS.get(level, 20)
    if lv < _current_level:
        return
    _write(f"[{level}] {ts()} | {msg}")

def debug(msg: str): log("DEBUG", msg)
def info(msg: str): log("INFO", msg)
def warn(msg: str): log("WARN", msg)
def error(msg: str): log("ERROR", msg)
