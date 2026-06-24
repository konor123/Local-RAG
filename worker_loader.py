
import sys
import os
import json
import traceback
import contextlib
from langchain_community.document_loaders import (
    PyPDFLoader,
    Docx2txtLoader,
    UnstructuredExcelLoader,
    TextLoader,
    UnstructuredHTMLLoader
)
from langchain_core.documents import Document
import pandas as pd

# Define Loaders (Must match ingest.py roughly)
LOADERS = {
    ".pdf": PyPDFLoader,
    ".docx": Docx2txtLoader,
    ".xlsx": UnstructuredExcelLoader,
    ".xls": UnstructuredExcelLoader,
    ".txt": TextLoader,
    ".html": UnstructuredHTMLLoader,
    ".htm": UnstructuredHTMLLoader,
}

def classify_error_text(text: str) -> str:
    lower = (text or "").lower()
    if "no module named 'xlrd'" in lower or "missing optional dependency" in lower or "xlrd" in lower:
        return "missing_dependency"
    if "unicode" in lower or "codec" in lower or "decode" in lower or "encoding" in lower:
        return "decode_error"
    if "timeout" in lower or "timed out" in lower:
        return "timeout"
    if "winerror" in lower or "network" in lower or "경로" in lower or "semaphore" in lower:
        return "network_error"
    if "encrypted" in lower or "password" in lower:
        return "empty_or_encrypted"
    if "unsupported" in lower:
        return "unsupported_extension"
    if lower.strip():
        return "parse_error"
    return "unknown_error"

def make_error(category: str, detail: str) -> dict:
    return {
        "__loader_error__": True,
        "category": category,
        "detail": detail[:1000]
    }

def is_temporary_office_file(file_path: str) -> bool:
    return os.path.basename(file_path).startswith("~$")

def load_file(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if is_temporary_office_file(file_path):
        return make_error("temporary_file", "Office temporary lock file")
    
    # Custom Pandas Loader for Excel (More Stable)
    if ext in [".xlsx", ".xls"]:
        try:
            # Load workbook explicitly to get sheet names (lazy check?)
            # Pandas read_excel with sheet_name=None reads ALL sheets. This can be OOM.
            # Safe approach: Read sheet names first?
            # Creating ExcelFile object is safer.
            xls = pd.ExcelFile(file_path)
            
            json_docs = []
            MAX_ROWS = 2000 # Safety Cap: Don't read millions of rows
            MAX_SHEETS = 20 # Safety Cap: Don't read 1000 sheets
            
            sheet_names = xls.sheet_names[:MAX_SHEETS]
            
            for sheet_name in sheet_names:
                try:
                    # Read only top N rows to prevent OOM on massive datasets
                    df = pd.read_excel(xls, sheet_name=sheet_name, header=None, nrows=MAX_ROWS)
                    df = df.fillna("")
                    
                    # Convert to text
                    # Limit columns? If too many cols, text is unreadable.
                    if len(df.columns) > 50:
                        df = df.iloc[:, :50]
                        
                    sheet_text = df.to_csv(sep="\t", index=False, header=False)
                    
                    # Truncate text if huge
                    if len(sheet_text) > 50000:
                        sheet_text = sheet_text[:50000] + "\n...(Truncated for stability)..."
                        
                    metadata = {
                        "source": file_path, 
                        "sheet": sheet_name,
                        "file_type": "xlsx"
                    }
                    
                    json_docs.append({
                        "page_content": f"[Sheet: {sheet_name}]\n{sheet_text}",
                        "metadata": metadata
                    })
                    
                except Exception as sheet_e:
                    # Skip problematic sheet
                    continue

            return json_docs
            
        except Exception as e:
            err_str = str(e).lower()
            if "encrypted" in err_str or "password" in err_str:
                return make_error("empty_or_encrypted", str(e))
            raise e

    loader_cls = LOADERS.get(ext)
    
    if not loader_cls:
        # Fallback for others or custom types handled in main?
        # For now, just return empty to signal "handled by main" or "unsupported"
        return make_error("unsupported_extension", f"Unsupported extension: {ext}")

    try:
        if ext == ".txt":
            loader = loader_cls(file_path, encoding="utf-8")
        else:
            loader = loader_cls(file_path)
            
        docs = loader.load()
        
        # Serialize docs to JSON
        json_docs = []
        for d in docs:
            json_docs.append({
                "page_content": d.page_content,
                "metadata": d.metadata
            })
        return json_docs
    except Exception as e:
        # Check for encryption
        err_str = str(e).lower()
        if "encrypted" in err_str or "password" in err_str:
             return make_error("empty_or_encrypted", str(e))
        else:
            return make_error(classify_error_text(str(e)), str(e))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python worker_loader.py <file_path>")
        sys.exit(1)
        
    file_path = sys.argv[1]
    
    # Validation
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        sys.exit(1)
        
    try:
        # Redirect stdout to avoid polluting output unless it's the JSON
        original_stdout = sys.stdout
        with contextlib.redirect_stdout(sys.stderr):
            result = load_file(file_path)
        sys.stdout = original_stdout
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0)
    except Exception as e:
        # Print error to stderr
        print(json.dumps(make_error(classify_error_text(str(e)), str(e)), ensure_ascii=False))
        sys.exit(0)
