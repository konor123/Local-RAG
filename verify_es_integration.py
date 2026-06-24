import time
from elasticsearch_client import index_document, search_hybrid
from rag_engine import get_embeddings

def test_es_integration():
    print("1. Loading Embeddings...")
    embeddings = get_embeddings()
    vector = embeddings.embed_query("테스트 문서입니다.")
    
    print("2. Indexing Document...")
    index_document(
        es=None, # get_es_client() called internally? No, need to pass es or fix function
        doc_id="test_doc_1",
        text="이것은 Elasticsearch 통합 테스트를 위한 문서입니다. 유도등 설치 기준을 포함합니다.",
        source="test.txt",
        vector=vector,
        metadata={"category": "test"}
    )
    # Wait for refresh
    time.sleep(2)
    
    print("3. Searching Document...")
    results = search_hybrid(vector, "유도등 설치", k=1)
    
    if results:
        print(f"✅ Search Success: Found {len(results)} docs")
        print(f"Content: {results[0]['content']}")
        print(f"Score: {results[0]['score']}")
    else:
        print("❌ Search Failed: No results found")

if __name__ == "__main__":
    # Fix index_document signature usage in script
    from elasticsearch_client import get_es_client
    es = get_es_client()
    
    print("1. Loading Embeddings...")
    embeddings = get_embeddings()
    vector = embeddings.embed_query("테스트 문서입니다.")
    
    print("2. Indexing Document...")
    index_document(
        es=es,
        doc_id="test_doc_1",
        text="이것은 Elasticsearch 통합 테스트를 위한 문서입니다. 유도등 설치 기준을 포함합니다.",
        source="test.txt",
        vector=vector,
        metadata={"category": "test"}
    )
    
    import time
    time.sleep(2)
    
    print("3. Searching Document...")
    # Generate vector for query
    q_vector = embeddings.embed_query("유도등 설치")
    results = search_hybrid(q_vector, "유도등 설치", k=1)
    
    if results:
        print(f"✅ Search Success: Found {len(results)} docs")
        print(f"Content: {results[0]['content']}")
    else:
        print("❌ Search Failed")
