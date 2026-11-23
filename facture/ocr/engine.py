import base64
import time
from abc import ABC, abstractmethod

import pymupdf
import requests

from facture.config import OCR_API_URL, OCR_API_KEY

DPI = 200
MAX_DIMENSION = 1540


class OcrEngine(ABC):
    @abstractmethod
    def extract(self, image_bytes: bytes) -> str:
        pass


class LightOnOCR(OcrEngine):
    def __init__(self, api_url: str = OCR_API_URL, api_key: str = OCR_API_KEY):
        self.api_url = api_url
        self.api_key = api_key

    def extract(self, image_bytes: bytes) -> str:
        b64 = base64.b64encode(image_bytes).decode()
        payload = {
            "model": "lightonocr-3-1b-test",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            "max_tokens": 4096,
            "temperature": 0.2,
            "top_p": 0.9,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(self.api_url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def pdf_page_to_png(pdf_path: str, page_num: int = 0) -> bytes:
    doc = pymupdf.open(pdf_path)
    page = doc[page_num]
    zoom = DPI / 72
    mat = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)

    if max(pix.width, pix.height) > MAX_DIMENSION:
        scale = MAX_DIMENSION / max(pix.width, pix.height)
        mat = pymupdf.Matrix(zoom * scale, zoom * scale)
        pix = page.get_pixmap(matrix=mat)

    png_bytes = pix.tobytes("png")
    doc.close()
    return png_bytes


def ocr_pdf(pdf_path: str, engine: OcrEngine = None) -> list[dict]:
    if engine is None:
        engine = LightOnOCR()

    doc = pymupdf.open(pdf_path)
    page_count = len(doc)
    doc.close()

    results = []
    for page_num in range(page_count):
        png_bytes = pdf_page_to_png(pdf_path, page_num)
        start = time.time()
        text = engine.extract(png_bytes)
        duration = time.time() - start
        results.append({
            "page": page_num,
            "text": text,
            "duration_seconds": round(duration, 2),
        })

    return results
