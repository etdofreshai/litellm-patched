"""Patch litellm map_system_message_pt to flatten Anthropic-style content blocks.

The upstream impl does `m["content"] + " " + next_m["content"]` which crashes
when `content` is a list of structured blocks (Anthropic system arrays). We
flatten any list content to plain text first.
"""
from typing import Any


def _flatten_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict):
                txt = blk.get("text") or blk.get("content")
                if isinstance(txt, str):
                    parts.append(txt)
            elif isinstance(blk, str):
                parts.append(blk)
        return "\n\n".join(p for p in parts if p)
    return str(content)


def patched_map_system_message_pt(messages: list) -> list:
    new_messages = []
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            sys_text = _flatten_content(m.get("content"))
            if i < len(messages) - 1:
                next_m = messages[i + 1]
                next_role = next_m.get("role")
                if next_role in ("user", "assistant"):
                    next_text = _flatten_content(next_m.get("content"))
                    next_m["content"] = (sys_text + " " + next_text).strip()
                elif next_role == "system":
                    new_messages.append({"role": "user", "content": sys_text})
            else:
                new_messages.append({"role": "user", "content": sys_text})
        else:
            new_messages.append(m)
    return new_messages
