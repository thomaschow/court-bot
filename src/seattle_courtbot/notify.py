from __future__ import annotations

import shutil
import subprocess

import httpx

from seattle_courtbot.logging import get_logger


def notify_macos(title: str, message: str, subtitle: str | None = None) -> None:
    log = get_logger(mode="notify")
    try:
        if shutil.which("terminal-notifier"):
            cmd = ["terminal-notifier", "-title", title, "-message", message]
            if subtitle:
                cmd += ["-subtitle", subtitle]
            subprocess.run(cmd, check=False, timeout=5)
            return
        script = (
            f'display notification {_q(message)} with title {_q(title)}'
            + (f' subtitle {_q(subtitle)}' if subtitle else "")
        )
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except Exception as exc:
        log.warning("notify.macos.failed", error=str(exc))


def notify_webhook(url: str, payload: dict) -> None:
    log = get_logger(mode="notify")
    try:
        with httpx.Client(timeout=5.0) as c:
            c.post(url, json=payload)
    except Exception as exc:
        log.warning("notify.webhook.failed", error=str(exc))


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
