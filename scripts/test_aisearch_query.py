"""Quick smoke test for the Azure AI Search index.

Runs a hybrid (BM25 + vector) query with the semantic configuration enabled,
prints the top K results.

Usage:
    uv run python scripts/test_aisearch_query.py "what was discussed about dividends"
    uv run python scripts/test_aisearch_query.py "board chair election" -k 3

Env: same as scripts/setup_aisearch_index.py (AZURE_SEARCH_ENDPOINT,
SEARCH_INDEX_NAME, PROJECT_ENDPOINT, EMBEDDING_DEPLOYMENT, optional
AZURE_SEARCH_API_KEY, AZURE_OPENAI_API_VERSION).
"""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlparse

from openai import AzureOpenAI
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery, QueryType
from dotenv import load_dotenv

load_dotenv()

SEMANTIC_CONFIG = "mtn-semantic"


def _require(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        print(f"Missing required env var: {name}", file=sys.stderr)
        sys.exit(1)
    return v


def _aad() -> DefaultAzureCredential:
    return DefaultAzureCredential()


def make_search_client() -> SearchClient:
    endpoint = _require("AZURE_SEARCH_ENDPOINT").rstrip("/")
    index = _require("SEARCH_INDEX_NAME")
    key = os.getenv("AZURE_SEARCH_API_KEY", "").strip()
    cred = AzureKeyCredential(key) if key else _aad()
    return SearchClient(endpoint=endpoint, index_name=index, credential=cred)


def make_embeddings_client() -> AzureOpenAI:
    project_endpoint = _require("PROJECT_ENDPOINT")
    parsed = urlparse(project_endpoint)
    azure_endpoint = f"{parsed.scheme}://{parsed.netloc}"
    token_provider = get_bearer_token_provider(_aad(), "https://cognitiveservices.azure.com/.default")
    return AzureOpenAI(
        azure_endpoint=azure_endpoint,
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        azure_ad_token_provider=token_provider,
    )


def embed(query: str) -> list[float]:
    client = make_embeddings_client()
    deployment = os.getenv("EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
    resp = client.embeddings.create(model=deployment, input=[query])
    return resp.data[0].embedding


def run(query: str, k: int) -> None:
    vec = embed(query)
    search = make_search_client()
    results = search.search(
        search_text=query,
        vector_queries=[VectorizedQuery(vector=vec, k_nearest_neighbors=k, fields="content_vector")],
        query_type=QueryType.SEMANTIC,
        semantic_configuration_name=SEMANTIC_CONFIG,
        select=["id", "title", "source", "chunk_index", "content"],
        top=k,
    )

    print(f"\nQuery: {query!r}   (top {k}, hybrid + semantic)\n" + "-" * 72)
    for i, r in enumerate(results, 1):
        score = r.get("@search.score")
        rerank = r.get("@search.reranker_score")
        snippet = (r.get("content") or "").strip().replace("\n", " ")
        if len(snippet) > 240:
            snippet = snippet[:240] + "..."
        print(f"\n[{i}] {r.get('title')}  (chunk {r.get('chunk_index')})")
        print(f"    source : {r.get('source')}")
        print(f"    score  : {score:.4f}" + (f"   rerank: {rerank:.4f}" if rerank is not None else ""))
        print(f"    text   : {snippet}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Hybrid + semantic search smoke test")
    ap.add_argument("query", nargs="+", help="Query text")
    ap.add_argument("-k", type=int, default=5, help="Top K results (default 5)")
    args = ap.parse_args()
    run(" ".join(args.query), args.k)


if __name__ == "__main__":
    main()
