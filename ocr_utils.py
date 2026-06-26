"""PaddleOCR helpers for PDF/image OCR.

OCR is intentionally lazy and main-process-only. Importing this module should not
load PaddleOCR; the heavy model is initialized on first OCR use and then kept in
memory for subsequent documents.
"""
from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from langchain_core.documents import Document
from runtime_paths import runtime_path


_ocr_instance = None
_ocr_lock = threading.Lock()
_PAGE_MIN_CHARS = int(os.environ.get("PDF_OCR_PAGE_MIN_CHARS", "40") or 40)


@dataclass
class OCRPageResult:
    page_number: int
    text: str
    confidence: Optional[float] = None
    error: Optional[str] = None


def _log_ocr(message: str) -> None:
    try:
        log_path = runtime_path("logs", "ocr.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"{message}\n")
    except Exception:
        pass


def get_ocr():
    """Return the process-wide PaddleOCR singleton, initializing it lazily."""
    global _ocr_instance
    if _ocr_instance is not None:
        return _ocr_instance

    with _ocr_lock:
        if _ocr_instance is not None:
            return _ocr_instance
        try:
            from paddleocr import PaddleOCR

            _log_ocr("Initializing PaddleOCR(lang='korean')")
            _ocr_instance = PaddleOCR(lang="korean")
            _log_ocr("PaddleOCR initialized")
            return _ocr_instance
        except TypeError:
            from paddleocr import PaddleOCR

            _log_ocr("PaddleOCR(lang='korean') failed; retrying default constructor")
            _ocr_instance = PaddleOCR()
            _log_ocr("PaddleOCR initialized with default constructor")
            return _ocr_instance
        except Exception as e:
            _log_ocr(f"PaddleOCR initialization failed: {e}")
            raise


def _normalize_text(text: str) -> str:
    return "\n".join(line.strip() for line in (text or "").splitlines() if line.strip())


def _collect_paddle_text(result, texts: List[str], scores: List[float]) -> None:
    if result is None:
        return
    if isinstance(result, dict):
        rec_texts = result.get("rec_texts")
        if isinstance(rec_texts, list):
            texts.extend(str(text) for text in rec_texts if text)
            rec_scores = result.get("rec_scores") or []
            scores.extend(float(score) for score in rec_scores if isinstance(score, (int, float)))
            return
        if result.get("text"):
            texts.append(str(result["text"]))
            score = result.get("score")
            if isinstance(score, (int, float)):
                scores.append(float(score))
            return
        for value in result.values():
            _collect_paddle_text(value, texts, scores)
        return
    if isinstance(result, (list, tuple)):
        if len(result) >= 2 and isinstance(result[1], (list, tuple)) and result[1] and isinstance(result[1][0], str):
            texts.append(result[1][0])
            if len(result[1]) > 1 and isinstance(result[1][1], (int, float)):
                scores.append(float(result[1][1]))
            return
        for item in result:
            _collect_paddle_text(item, texts, scores)
        return
    for attr in ("to_dict", "json"):
        method = getattr(result, attr, None)
        if callable(method):
            try:
                _collect_paddle_text(method(), texts, scores)
                return
            except Exception:
                pass


def extract_text_from_paddle_result(result) -> Tuple[str, Optional[float]]:
    texts: List[str] = []
    scores: List[float] = []
    _collect_paddle_text(result, texts, scores)
    text = _normalize_text("\n".join(texts))
    confidence = sum(scores) / len(scores) if scores else None
    return text, confidence


def _run_ocr_image(image_path: str) -> Tuple[str, Optional[float]]:
    ocr = get_ocr()
    with _ocr_lock:
        if hasattr(ocr, "predict"):
            result = ocr.predict(image_path)
        else:
            try:
                result = ocr.ocr(image_path, cls=True)
            except TypeError:
                result = ocr.ocr(image_path)
    return extract_text_from_paddle_result(result)


def ocr_image(image_path: str) -> str:
    """OCR a single image path and return extracted text."""
    try:
        text, _ = _run_ocr_image(image_path)
        return text
    except Exception as e:
        _log_ocr(f"Image OCR failed for {image_path}: {e}")
        return ""


def ocr_pdf_pages(pdf_path: str | Path, page_numbers: Iterable[int] | None = None, dpi: int = 200) -> List[OCRPageResult]:
    """OCR PDF pages and return one result per 1-based page number."""
    try:
        import pypdfium2 as pdfium
    except Exception as e:
        _log_ocr(f"pypdfium2 import failed: {e}")
        return [OCRPageResult(page_number=0, text="", error=str(e))]

    pdf_path = Path(pdf_path)
    results: List[OCRPageResult] = []
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
        total_pages = len(pdf)
        selected_pages = list(page_numbers) if page_numbers is not None else list(range(1, total_pages + 1))

        for page_number in selected_pages:
            if page_number < 1 or page_number > total_pages:
                results.append(OCRPageResult(page_number=page_number, text="", error="page out of range"))
                continue
            page = None
            bitmap = None
            image = None
            temp_name = None
            try:
                page = pdf[page_number - 1]
                bitmap = page.render(scale=dpi / 72)
                image = bitmap.to_pil()
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                    temp_name = temp_file.name
                image.save(temp_name)
                text, confidence = _run_ocr_image(temp_name)
                results.append(OCRPageResult(page_number=page_number, text=text, confidence=confidence))
            except Exception as e:
                _log_ocr(f"PDF OCR page failed ({pdf_path}, page {page_number}): {e}")
                results.append(OCRPageResult(page_number=page_number, text="", error=str(e)))
            finally:
                if image is not None:
                    try:
                        image.close()
                    except Exception:
                        pass
                if bitmap is not None and hasattr(bitmap, "close"):
                    try:
                        bitmap.close()
                    except Exception:
                        pass
                if page is not None and hasattr(page, "close"):
                    try:
                        page.close()
                    except Exception:
                        pass
                if temp_name:
                    try:
                        os.unlink(temp_name)
                    except Exception:
                        pass
        if hasattr(pdf, "close"):
            pdf.close()
    except Exception as e:
        _log_ocr(f"PDF OCR failed for {pdf_path}: {e}")
        return [OCRPageResult(page_number=0, text="", error=str(e))]
    return results


def ocr_pdf(pdf_path: str) -> str:
    """OCR a PDF and return all page text concatenated."""
    return "\n\n".join(result.text for result in ocr_pdf_pages(pdf_path) if result.text)


def _doc_page_number(doc: Document, fallback_index: int) -> int:
    page = doc.metadata.get("page") if doc.metadata else None
    if isinstance(page, int):
        return page + 1
    page_number = doc.metadata.get("page_number") if doc.metadata else None
    if isinstance(page_number, int):
        return page_number
    return fallback_index + 1


def augment_pdf_documents_with_ocr(docs: List[Document], pdf_path: str, min_chars: int = _PAGE_MIN_CHARS) -> List[Document]:
    """Run OCR for every PDF page and replace weak extracted pages with OCR text."""
    ocr_pages = ocr_pdf_pages(pdf_path)
    ocr_by_page = {result.page_number: result for result in ocr_pages if result.page_number > 0}
    if not docs:
        created = []
        for result in ocr_pages:
            if result.text:
                created.append(Document(
                    page_content=result.text,
                    metadata={
                        "source": pdf_path,
                        "page": result.page_number - 1,
                        "file_type": "pdf_ocr",
                        "ocr_applied": True,
                        "ocr_engine": "paddleocr",
                        "ocr_confidence": result.confidence,
                    },
                ))
        return created

    augmented: List[Document] = []
    for index, doc in enumerate(docs):
        page_number = _doc_page_number(doc, index)
        ocr_result = ocr_by_page.get(page_number)
        content = doc.page_content or ""
        metadata = dict(doc.metadata or {})
        if ocr_result and ocr_result.text and len(content.strip()) < min_chars:
            metadata.update({
                "file_type": "pdf_ocr",
                "ocr_applied": True,
                "ocr_engine": "paddleocr",
                "ocr_confidence": ocr_result.confidence,
            })
            if content.strip():
                content = f"{content.strip()}\n\n[OCR Text]\n{ocr_result.text}"
            else:
                content = ocr_result.text
        augmented.append(Document(page_content=content, metadata=metadata))
    return augmented
