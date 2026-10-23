"""PreToolUse hook: block access to .env files, except `*.example` templates.

Permission deny rules cannot carve out exceptions (deny always wins), so this hook
implements "block .env and .env.*, allow .env*.example". It exits with code 2 to
block the tool call; Claude Code shows stderr to the model.

Shell commands are checked best-effort by scanning the command text.
"""

import json
import re
import sys

ALLOWED_SUFFIX = ".example"

# `.env` or `.env.<suffix>` as a whole path component (not `.venv`, `.envrc`, `os.environ`).
ENV_NAME = re.compile(r"(?<![\w.-])\.env(?:\.[\w.-]+)?(?![\w-])")


def _is_blocked_name(name: str) -> bool:
    return not name.endswith(ALLOWED_SUFFIX)


def _blocked_path(path: str) -> str | None:
    basename = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
    if ENV_NAME.fullmatch(basename) and _is_blocked_name(basename):
        return basename
    return None


def _blocked_in_text(text: str) -> str | None:
    for match in ENV_NAME.finditer(text):
        if _is_blocked_name(match.group(0)):
            return match.group(0)
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print("block_env_files: invalid hook input; blocking.", file=sys.stderr)
        return 2

    tool_input = payload.get("tool_input") or {}
    hit = None
    for key in ("file_path", "notebook_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and (hit := _blocked_path(value)):
            break
    if not hit:
        for key in ("command", "glob", "pattern"):
            value = tool_input.get(key)
            if isinstance(value, str) and (hit := _blocked_in_text(value)):
                break

    if hit:
        print(
            f"Blocked by project policy: access to '{hit}' is not allowed "
            f"(only templates ending in '{ALLOWED_SUFFIX}' may be read or edited).",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
