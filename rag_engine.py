import os
os.environ['ANONYMIZED_TELEMETRY'] = 'False'

import chromadb
from chromadb.config import Settings
try:
    from langchain_ollama import ChatOllama
except ImportError:  # Backward compatibility until requirements are installed.
    from langchain_community.chat_models import ChatOllama
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from config_manager import get_local_ai_config

# Configuration
VECTOR_STORE_PATH = "./chroma_db_ko"  # Korean-optimized embeddings
EMBEDDING_MODEL = "dragonkue/multilingual-e5-small-ko"
_LOCAL_AI_CONFIG = get_local_ai_config()
LLM_MODEL = _LOCAL_AI_CONFIG["model"]
OLLAMA_BASE_URL = _LOCAL_AI_CONFIG["base_url"]
LLM_NUM_CTX = _LOCAL_AI_CONFIG["num_ctx"]
LLM_NUM_PREDICT = _LOCAL_AI_CONFIG["num_predict"]
LLM_REQUEST_TIMEOUT = _LOCAL_AI_CONFIG["request_timeout"]

# Embedding Singleton
_embeddings_instance = None

def get_embeddings():
    global _embeddings_instance
    if _embeddings_instance is None:
        _embeddings_instance = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={'device': 'cpu'},
            encode_kwargs={'normalize_embeddings': True}
        )
    return _embeddings_instance

def get_vectorstore():
    # Use singleton embeddings
    embeddings = get_embeddings()
    
    chroma_settings = Settings(anonymized_telemetry=False)
    client = chromadb.PersistentClient(
        path=VECTOR_STORE_PATH,
        settings=chroma_settings
    )
    
    return Chroma(
        client=client,
        collection_name="langchain",
        embedding_function=embeddings
    )

def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)

def get_rag_chain():
    vectorstore = get_vectorstore()
    retriever = vectorstore.as_retriever(search_kwargs={"k": 20})  # Fetch more to allow re-ranking
    
    llm_kwargs = {
        "model": LLM_MODEL,
        "base_url": OLLAMA_BASE_URL,
        "temperature": 0.2,
        "keep_alive": -1,
        "streaming": True,
        "num_thread": 10,
        "num_gpu": -1,
        "num_ctx": LLM_NUM_CTX,
        "num_predict": LLM_NUM_PREDICT,
    }
    if ChatOllama.__module__.startswith("langchain_ollama"):
        llm_kwargs["sync_client_kwargs"] = {"timeout": LLM_REQUEST_TIMEOUT}
    else:
        llm_kwargs["timeout"] = LLM_REQUEST_TIMEOUT
    llm = ChatOllama(**llm_kwargs)

    # Simple RAG prompt
    system_prompt = """우리 회사는 오에스엘이엔지(OSL ENG)라고 합니다. 당신은 우리 회사의 내부 문서를 검색하여 답변하는 AI 어시스턴트입니다.
Context에 있는 내용만 사용하여 답변하세요. 모르면 모른다고 하세요.

Context: {context}"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{question}")
    ])

    # LCEL chain
    def create_chain_with_sources(question, chat_history=None):
        # Retrieve docs
        docs = retriever.invoke(question)
        
        # Re-rank: Penalize Recycle Bin content
        normal_docs = []
        recycle_docs = []
        
        for doc in docs:
            source = doc.metadata.get('source', '')
            if any(x in source for x in ['$Recycle.Bin', 'Recycle.Bin', '휴지통', '#recycle', '구 자료', '구자료']):
                recycle_docs.append(doc)
            else:
                normal_docs.append(doc)
        
        # Combine: Normal docs first, then Recycle Bin docs
        # Slice to keep top 4 (original k)
        final_docs = (normal_docs + recycle_docs)[:4]
        
        context = format_docs(final_docs)
        
        # Build messages
        messages = prompt.format_messages(
            context=context,
            question=question,
            chat_history=chat_history or []
        )
        
        # Get answer
        response = llm.invoke(messages)
        
        return {
            "answer": response.content,
            "source_documents": final_docs
        }
    
    return create_chain_with_sources
