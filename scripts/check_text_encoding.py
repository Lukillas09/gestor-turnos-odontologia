from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".env",
    ".example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".svg",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_NAMES = {".gitignore", "LICENSE", "Procfile"}
IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "backups",
    "media",
    "node_modules",
    "staticfiles",
}
MOJIBAKE_MARKERS = (
    "\u00c3",
    "\u00c2",
    "\u00e2\u20ac",
    "\u00f0\u0178",
    "\ufffd",
)


def iter_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
            continue
        if path.name == ".env" or path.name.endswith(".env"):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES:
            yield path


def main():
    errors = []
    checked = 0

    for path in iter_text_files():
        checked += 1
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            errors.append((path, 0, f"UTF-8 inválido en byte {error.start}"))
            continue

        for line_number, line in enumerate(text.splitlines(), start=1):
            markers = [marker for marker in MOJIBAKE_MARKERS if marker in line]
            if markers:
                codepoints = ", ".join(
                    "+".join(f"U+{ord(character):04X}" for character in marker)
                    for marker in markers
                )
                errors.append((path, line_number, f"posible mojibake ({codepoints})"))

    if errors:
        print("Se detectaron problemas de codificación:")
        for path, line_number, reason in errors:
            relative = path.relative_to(ROOT)
            location = f":{line_number}" if line_number else ""
            print(f"- {relative}{location}: {reason}")
        raise SystemExit(1)

    print(f"Archivos de texto verificados: {checked}. Sin errores de codificación.")


if __name__ == "__main__":
    main()
