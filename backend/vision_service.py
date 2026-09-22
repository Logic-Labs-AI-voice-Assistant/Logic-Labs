import os
import io
import numpy as np
import cv2
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, Request, HTTPException
from dotenv import load_dotenv
load_dotenv()

try:
    from backend.agent_service import call_jarvis_vision
    from backend.auth import get_current_customer
except ImportError:
    from agent_service import call_jarvis_vision
    from auth import get_current_customer

try:
    import pypdf
except ImportError:
    pypdf = None

router = APIRouter(prefix="/api/vision", tags=["vision"])

_reader = None

def get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    return _reader

@router.post("/analyze")
async def analyze_image(
    request: Request,
    file: UploadFile = File(...),
    prompt: Optional[str] = Form(None)
):
    customer = get_current_customer(request)
    filename = file.filename or "uploaded_file"
    content_type = file.content_type or ""

    is_pdf = content_type == "application/pdf" or filename.lower().endswith(".pdf")
    is_image = content_type.startswith("image/") or any(
        filename.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff"]
    )

    if not (is_pdf or is_image):
        raise HTTPException(status_code=400, detail="Only image files (PNG, JPG, etc.) and PDF documents are allowed.")

    contents = await file.read()
    extracted_text = ""

    if is_pdf:
        if pypdf:
            try:
                pdf_file = io.BytesIO(contents)
                pdf_reader = pypdf.PdfReader(pdf_file)
                pages_text = []
                for page in pdf_reader.pages:
                    text = page.extract_text()
                    if text:
                        pages_text.append(text)
                extracted_text = "\n".join(pages_text).strip()
            except Exception as e:
                print(f"Error reading PDF with pypdf: {e}")

        if not extracted_text:
            extracted_text = f"[PDF Document: {filename}]"
    else:
        try:
            nparr = np.frombuffer(contents, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if img is not None:
                reader = get_reader()
                results = reader.readtext(img, detail=0, paragraph=True)
                extracted_text = "\n".join(results).strip()
        except Exception as e:
            print(f"Error doing OCR on image: {e}")

        if not extracted_text:
            extracted_text = f"[Image File: {filename}]"

    user_query = prompt.strip() if prompt and prompt.strip() else "What does this document say and is it compliant with internal policy? Explain clearly."

    combined_prompt = f"""Document / Image Content ({filename}):
{extracted_text}

User Request: {user_query}"""

    result = call_jarvis_vision(combined_prompt, history=[], customer=customer)
    answer_text = result.get("answer", "") if isinstance(result, dict) else str(result)

    return {
        "filename": filename,
        "ocr_text": extracted_text,
        "answer": answer_text
    }