import os
from typing import Optional
from PIL import Image, ImageOps, ImageFilter
import pytesseract


pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

def ocr_image(file_path: str, lang: str = "eng") -> str:
    """
    Simple, robust OCR for screenshots and photos.
    Basic pre-processing: grayscale, contrast boost, light denoise.
    """
    img = Image.open(file_path)
    img = ImageOps.grayscale(img)
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.MedianFilter(size=3))
    text = pytesseract.image_to_string(img, lang=lang, config="--psm 6")
    # normalize a bit
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
