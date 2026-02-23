import os
import json
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents import SearchClient
import openai
from azure.search.documents.indexes.models import (
        SearchIndex,
        SearchField,
        SearchFieldDataType,
        SimpleField,
        SearchableField,
        VectorSearch,
        VectorSearchProfile,
        HnswAlgorithmConfiguration,
        SemanticConfiguration,
        SemanticPrioritizedFields,
        SemanticField,
        SemanticSearch
    )
from dotenv import load_dotenv

load_dotenv()

search_endpoint = os.getenv("AZURE_SEARCH_SERVICE_ENDPOINT")
search_key = os.getenv("AZURE_SEARCH_API_KEY")
open_ai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
open_ai_key = os.getenv("AZURE_OPENAI_API_KEY")

def get_embeddings(text: str):    
    client = openai.AzureOpenAI(
        azure_endpoint=open_ai_endpoint,
        api_key=open_ai_key,
        api_version="2023-09-01-preview",
    )

    embedding = client.embeddings.create(input=[text], 
                                         model="text-embedding-ada-002")
    return embedding.data[0].embedding


def create_policy_index(name: str):
    credential = AzureKeyCredential(search_key)
    index_client = SearchIndexClient(search_endpoint, credential)
    
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(
            name="content",
            type=SearchFieldDataType.String,
        ),
        SearchableField(
            name="category",
            type=SearchFieldDataType.String,
            filterable=True
        ),
        SimpleField(
            name="mandatory",
            type=SearchFieldDataType.Boolean,
            filterable=True
        ),
        SearchField(
            name="vector_data",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=1536,
            vector_search_profile_name="uploaded-document-vector-config",
        )
    ]

    vector_search = VectorSearch(
        profiles=[VectorSearchProfile(name="uploaded-document-vector-config", algorithm_configuration_name="uploaded-document-algorithms-config")],
        algorithms=[HnswAlgorithmConfiguration(name="uploaded-document-algorithms-config")],
    )

    semantic_config = SemanticConfiguration(
        name="srconfig", 
        prioritized_fields=SemanticPrioritizedFields(
            title_field=SemanticField(field_name="id"),
            content_fields=[SemanticField(field_name="content")]
        )
    )

    semantic_search = SemanticSearch(configurations=[semantic_config])

    index = SearchIndex(name=name, fields=fields, vector_search=vector_search, semantic_search=semantic_search)
    index_client.create_or_update_index(index)


def upload_policies(index_name: str, policies: str):
    credential = AzureKeyCredential(search_key)
    client = SearchClient(search_endpoint, index_name, credential)

    with open(policies, 'r') as file:
        data = json.load(file)

    for entry in data:
        document = {
            "id": entry["header"].replace(" ", "_"),
            "content": entry["content"],
            "category": entry["category"],
            "mandatory": entry["mandatory"],
            "vector_data": get_embeddings(entry["content"])
        }

        client.upload_documents(documents=[document])
        print('document upload succeeded')


def search_index(policy_section: str):
    index_name = 'policy_index'
    credential = AzureKeyCredential(search_key)
    client = SearchClient(search_endpoint, index_name, credential)

    query = "*"

    results = client.search(query_type='simple', search_text=query, select=["id", "content", "category", "vector_data"], filter=f"category eq '{policy_section}'")
    results_list = list(results)

    records = []

    for result in results_list:
        header = result.get("id")
        content = result.get("content")
        mandatory = result.get("mandatory")
        # vector_data = result.get("vector_data")


        new_record = {
            "header": header,
            "content": content,
            "mandatory": mandatory
            # "vector_data": vector_data
        }

        records.append(new_record)

    return json.dumps(records)
