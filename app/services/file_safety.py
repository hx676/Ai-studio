import os
import re
import uuid


WINDOWS_RESERVED_FILE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_filename_stem(name, fallback="file", max_length=80):
    fallback_text = str(fallback or "file")
    text = str(name or "").replace("\\", "/")
    text = text.rsplit("/", 1)[-1]
    stem = os.path.splitext(text)[0] or fallback_text
    stem = re.sub(r"[\x00-\x1f\x7f]+", " ", stem)
    stem = re.sub(r'[\\/:*?"<>|]+', "_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    if not stem:
        stem = fallback_text
    stem = re.sub(r"[\x00-\x1f\x7f]+", " ", stem)
    stem = re.sub(r'[\\/:*?"<>|]+', "_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    if not stem:
        stem = "file"
    if stem.upper() in WINDOWS_RESERVED_FILE_NAMES:
        stem = f"_{stem}"
    stem = stem[: max(1, int(max_length or 80))].rstrip(" .")
    if not stem:
        stem = "file"
    if stem.upper() in WINDOWS_RESERVED_FILE_NAMES:
        stem = f"_{stem}"
    return stem


def safe_extension(ext, default=""):
    text = str(ext or default or "")
    if not text:
        return ""
    text = text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    text = text if text.startswith(".") else f".{text}"
    text = re.sub(r"[^A-Za-z0-9.]", "", text)
    if not text or text == ".":
        return ""
    return text[:16]


def sanitize_output_filename(name, fallback, ext):
    stem = safe_filename_stem(name, fallback)
    return f"{stem}_{uuid.uuid4().hex[:10]}{safe_extension(ext)}"
