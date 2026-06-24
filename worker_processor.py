import sys
import os
import json
import uuid
# Suppress warnings
os.environ["TOKENIZERS_PARALLELISM"] = "false"
try:
    from langchain_community.embeddings import HuggingFaceEmbeddings
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_core.documents import Document
except ImportError:
    sys.exit(1)

def clean_metadata_string(value):
    if isinstance(value, str):
        # Remove null bytes
        value = value.replace('\x00', '')
        # Remove surrogates
        value = value.encode('utf-8', 'ignore').decode('utf-8')
    return value

def process_and_embed(input_file):
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        raw_docs = data.get("docs", [])
        if not raw_docs:
            print(json.dumps({"ids": [], "embeddings": [], "metadatas": [], "texts": []}))
            return

        # 1. Reconstruct Dosc
        docs = []
        for d in raw_docs:
            docs.append(Document(page_content=d.get("page_content", ""), metadata=d.get("metadata", {})))
            
        # 2. Text Splitting
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            add_start_index=True,
        )
        splits = text_splitter.split_documents(docs)
        
        if not splits:
            print(json.dumps({"ids": [], "embeddings": [], "metadatas": [], "texts": []}))
            return

        texts = []
        metadatas = []
        ids = []
        
        for s in splits:
            # Clean text
            txt = s.page_content.replace('\x00', '')
            texts.append(txt)
            
            # Clean metadata
            meta = s.metadata
            for k, v in meta.items():
                if isinstance(v, list): v = str(v)
                meta[k] = clean_metadata_string(v)
            metadatas.append(meta)
            
            ids.append(str(uuid.uuid4()))
            
        # 3. Embedding
        model = HuggingFaceEmbeddings(
            model_name="dragonkue/multilingual-e5-small-ko",
            model_kwargs={'device': 'cpu'},
            encode_kwargs={'normalize_embeddings': True}
        )
        
        embeddings = model.embed_documents(texts)
        
        # 4. Return Result
        result = {
            "ids": ids,
            "embeddings": embeddings,
            "metadatas": metadatas,
            "texts": texts
        }
        
        print(json.dumps(result))
        
    except Exception as e:
        sys.stderr.write(str(e))
        sys.exit(1)

# --- Persistent Worker Mode ---
def main_loop():
    # 1. Initialize logic ONCE
    try:
        model = HuggingFaceEmbeddings(
            model_name="dragonkue/multilingual-e5-small-ko",
            model_kwargs={'device': 'cpu'},
            encode_kwargs={'normalize_embeddings': True}
        )
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            add_start_index=True,
        )
        # Signal ready
        print("READY", flush=True)
    except Exception as e:
        sys.stderr.write(f"Init Error: {e}\n")
        sys.exit(1)

    while True:
        try:
            # Block until input received
            line = sys.stdin.readline()
            if not line: break # EOF
            
            input_file = line.strip()
            if not input_file: continue
            
            # Application Logic
            try:
                with open(input_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                raw_docs = data.get("docs", [])
                
                # Reconstruct
                docs = [Document(page_content=d.get("page_content", ""), metadata=d.get("metadata", {})) for d in raw_docs]
                
                # Split
                splits = text_splitter.split_documents(docs)
                
                if not splits:
                     print(json.dumps({"ids": [], "embeddings": [], "metadatas": [], "texts": []}), flush=True)
                     continue

                texts = []
                metadatas = []
                ids = []
                for s in splits:
                    txt = s.page_content.replace('\x00', '')
                    texts.append(txt)
                    meta = s.metadata
                    for k, v in meta.items():
                        if isinstance(v, list): v = str(v)
                        meta[k] = clean_metadata_string(v)
                    metadatas.append(meta)
                    ids.append(str(uuid.uuid4()))
                    
                # Embed
                embeddings = model.embed_documents(texts)
                
                # Result
                result = {
                    "ids": ids,
                    "embeddings": embeddings,
                    "metadatas": metadatas,
                    "texts": texts
                }
                print(json.dumps(result), flush=True)

            except Exception as task_e:
                # Task failed, but worker stays alive if possible?
                # Actually, if tokenizer crashes, the whole process dies.
                # If python error, we report error.
                sys.stderr.write(f"Task Error: {task_e}\n")
                # Return empty to signal failure handled
                print(json.dumps({"error": str(task_e)}), flush=True)
                
        except KeyboardInterrupt:
            break
        except Exception as e:
            sys.stderr.write(f"Loop Error: {e}\n")
            break

if __name__ == "__main__":
    main_loop()
