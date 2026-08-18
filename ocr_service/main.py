from fastapi import FastAPI, UploadFile, File
from paddleocr import PaddleOCR
import numpy as np
import cv2

app = FastAPI()

# Initialize OCR engine once at startup
print("Initializing PaddleOCR...")
ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
print("PaddleOCR ready!")

@app.post("/ocr")
async def run_ocr(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        nparr = np.frombuffer(contents, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return {"ok": False, "error": "Invalid image file"}
        
        result = ocr.ocr(img, cls=True)
        lines = []
        for page in (result or []):
            for det in (page or []):
                text = det[1][0]
                if text:
                    lines.append(text)
        return {"ok": True, "lines": lines}
    except Exception as e:
        return {"ok": False, "error": f"{e.__class__.__name__}: {str(e)}"}

@app.get("/health")
def health():
    return {"status": "healthy"}
