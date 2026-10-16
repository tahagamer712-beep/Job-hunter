# -*- coding: utf-8 -*-
"""OCR integration for job ad images using Tesseract."""
import os
import subprocess
import tempfile
import logging

log = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
SUPPORTED_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}


def is_ocr_available() -> bool:
    """Check if tesseract binary exists."""
    try:
        r = subprocess.run(['tesseract', '--version'],
                           capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def get_ocr_langs() -> list:
    """List available OCR languages."""
    try:
        r = subprocess.run(['tesseract', '--list-langs'],
                           capture_output=True, text=True, timeout=10)
        lines = r.stdout.strip().split('\n')
        # Skip header line
        return [l.strip() for l in lines[1:] if l.strip()]
    except Exception:
        return []


def extract_text_from_image(image_path: str, langs: str = 'ara+eng') -> str | None:
    """
    Extract text from image using Tesseract.
    Returns extracted text or None on failure.
    Never raises — failures return None to preserve original text.
    """
    if not os.path.exists(image_path):
        return None
    if os.path.getsize(image_path) > MAX_IMAGE_SIZE:
        log.warning(f'Image too large: {image_path}')
        return None
    ext = os.path.splitext(image_path)[1].lower()
    if ext not in SUPPORTED_EXT:
        log.warning(f'Unsupported image type: {ext}')
        return None

    try:
        r = subprocess.run(
            ['tesseract', image_path, 'stdout', '-l', langs, '--psm', '6'],
            capture_output=True, text=True, timeout=60
        )
        text = r.stdout.strip()
        return text if text else None
    except subprocess.TimeoutExpired:
        log.warning(f'OCR timeout: {image_path}')
        return None
    except Exception as e:
        log.warning(f'OCR failed: {e}')
        return None


def process_ad_image(image_path: str) -> dict:
    """
    Process a job ad image: OCR + return both original and extracted.
    Returns: {'ocr_text': str|None, 'success': bool}
    """
    ocr_text = extract_text_from_image(image_path)
    return {
        'ocr_text': ocr_text,
        'success': ocr_text is not None and len(ocr_text) > 10,
    }
