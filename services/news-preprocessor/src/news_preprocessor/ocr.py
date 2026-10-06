import csv
import io
import math
import os
import subprocess
import time

import httpx
from PIL import Image

from news_preprocessor.settings import OCRSettings


def image_text(client: httpx.Client, url: str, settings: OCRSettings) -> str:
    image_bytes = bytearray()
    deadline = time.monotonic() + settings.download_timeout
    with client.stream(
        "GET", url, headers={"Accept": "image/*"}, timeout=settings.download_timeout
    ) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            if time.monotonic() >= deadline:
                raise httpx.ReadTimeout(
                    "image download exceeded total time limit", request=response.request
                )
            image_bytes.extend(chunk)
            if len(image_bytes) > settings.max_image_bytes:
                raise ValueError("image exceeds download size limit")

    with Image.open(io.BytesIO(image_bytes)) as image:
        if image.width * image.height > settings.max_image_pixels:
            raise ValueError("image exceeds pixel limit")
        normalized = image.convert("RGB")
        normalized.thumbnail((2400, 2400))
        buffer = io.BytesIO()
        normalized.save(buffer, format="PNG")

    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", settings.languages, "tsv"],
        input=buffer.getvalue(),
        capture_output=True,
        check=True,
        timeout=settings.timeout,
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
    )
    words = []
    confidence_total = 0.0
    character_count = 0
    for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t"):
        if row.get("level") != "5":
            continue
        word = (row.get("text") or "").strip()
        if not word:
            continue
        confidence = float(row.get("conf") or "")
        if not math.isfinite(confidence) or not 0 <= confidence <= 100:
            raise ValueError("invalid OCR confidence")
        words.append(word)
        confidence_total += confidence * len(word)
        character_count += len(word)

    text = " ".join(" ".join(words).split())
    readable_characters = sum(character.isalnum() for character in text)
    if readable_characters < settings.min_characters:
        raise ValueError("OCR text is empty or too short")
    if confidence_total / character_count < settings.min_confidence:
        raise ValueError("OCR confidence is too low")
    return text
