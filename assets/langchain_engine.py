from __future__ import annotations
from dotenv import load_dotenv
import os
import re
import pickle
import threading
import itertools
import time
import json
import uuid
from tqdm import tqdm 
import torch 
from collections import Counter
from sys import exit
import random

from typing import List
from collections import defaultdict
from langchain.retrievers import MultiVectorRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_core.stores import InMemoryByteStore
from langchain_community.retrievers.wikipedia import WikipediaRetriever
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.output_parsers.openai_functions import JsonKeyOutputFunctionsParser
from langchain.retrievers.multi_vector import MultiVectorRetriever
from langchain.chains.router.multi_retrieval_qa import MultiRetrievalQAChain
from langchain_upstage import ChatUpstage, UpstageEmbeddings, UpstageLayoutAnalysisLoader, UpstageGroundednessCheck
from langchain_text_splitters import Language,RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS, Chroma

from langchain_community.retrievers import BM25Retriever
from langchain.retrievers import EnsembleRetriever
from langchain_core.runnables import RunnablePassthrough
from langchain.schema import Document
from datasets import load_dataset
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain import hub
from langchain.tools.retriever import create_retriever_tool
from transformers import AutoTokenizer, AutoModelForSequenceClassification

from engine.utils import *
from prompts import MULTI_RETRIEVAL_ROUTER_TEMPLATE, TEACHER_SG_TEMPLATE
from typing import Any, Dict, List, Mapping, Optional
from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import PromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain.chains import ConversationChain
from langchain.chains.base import Chain
from langchain.chains.conversation.prompt import DEFAULT_TEMPLATE
from langchain.chains.retrieval_qa.base import BaseRetrievalQA, RetrievalQA
from langchain.chains.router.base import MultiRouteChain
from langchain.chains.router.llm_router import LLMRouterChain, RouterOutputParser

def load_env(env_path=None):
    """Loads API keys"""
    global ROOT, UPSTAGE_API_KEY, LANGCHAIN_TRACING_V2, LANGCHAIN_ENDPOINT, LANGCHAIN_PROJECT, LANGCHAIN_API_KEY
    if env_path is not None:
        load_dotenv(env_path)
    else:
        load_dotenv(verbose=True)
    ROOT = os.getenv('ROOT')
    UPSTAGE_API_KEY = os.getenv('UPSTAGE_API_KEY')
    LANGCHAIN_TRACING_V2 = os.getenv('LANGCHAIN_TRACING_V2')
    LANGCHAIN_ENDPOINT = os.getenv('LANGCHAIN_ENDPOINT')
    LANGCHAIN_PROJECT = os.getenv('LANGCHAIN_PROJECT')
    LANGCHAIN_API_KEY = os.getenv('LANGCHAIN_API_KEY')
    #os.chdir(ROOT)

def load_docs(data_root):
    """Loads documentary using UpstageLayoutAnalysisLoader"""
    layzer = UpstageLayoutAnalysisLoader(
        api_key=UPSTAGE_API_KEY,
        file_path=os.path.join(data_root, 'ewha.pdf'), 
        output_type="text",
        split="page"
    )
    def loading_animation():
        for c in itertools.cycle(['|', '/', '-', '\\']):
            if not loading: 
                break
            print(f"\r[INFO] Loading documents... {c}", end="")
            time.sleep(0.1)
        print("\r[INFO] Loading documents... Done!    ")
    loading = True
    t = threading.Thread(target=loading_animation)
    t.start()

    docs = layzer.load()  # or layzer.lazy_load() 
    loading = False
    t.join()  
    return docs 

def split_docs(data_root="./data", chunk_size=300, chunk_overlap=100):
    docs = load_docs(data_root)

    """Returns splits of docs using given chunk size and overlap"""
    print("[INFO] Spliting documents...")
    text_splitter = RecursiveCharacterTextSplitter.from_language(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap, 
        language=Language.HTML
    )

    splits = text_splitter.split_documents(docs)
    print("[INFO] # of splits:", len(splits))
    return splits

def get_embedding():
    """Loads upstage embedding"""
    # returns upstage embedding
    print("[INFO] Loading embeddings...")
    return UpstageEmbeddings(api_key=UPSTAGE_API_KEY, model = 'solar-embedding-1-large')


def get_llm(temperature=0):
    """Loads LLM model from Upstage"""
    llm = ChatUpstage(api_key = UPSTAGE_API_KEY, temperature=temperature)
    return llm 

def get_qa_chain(llm, retriever, prompt_template=None):
    """
    Loads LLM chain. 
    If customed prompt template is not given, 
    this function will apply huggingface's hub QA prompt named rlm/rag-prompt as our prompt template.
    returns chain
    """
    if prompt_template is not None:
         prompt_template = PromptTemplate.from_template(prompt_template)
    else:
        prompt_template = hub.pull("rlm/rag-prompt") #QA prompt  https://smith.langchain.com/hub/rlm/rag-prompt?organizationId=5b2073af-2123-4ed3-b218-fa406e467d84 
    
    rag_chain = (
        {"context": retriever | format_docs , "question": RunnablePassthrough()}
        | prompt_template
        | llm
    )
    return rag_chain 

class newMultiVectorRetriever(MultiVectorRetriever):
    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
        """Get documents relevant to a query.
        Args:
            query: String to find relevant documents for
            run_manager: The callbacks handler to use
        Returns:
            List of relevant documents
        """
        results = self.vectorstore.similarity_search_with_score(
            query, **self.search_kwargs
        )

        # Map doc_ids to list of sub-documents, adding scores to metadata
        id_to_doc = defaultdict(list)
        for doc, score in results:
            doc_id = doc.metadata.get("doc_id")
            if doc_id:
                doc.metadata["score"] = score
                id_to_doc[doc_id].append(doc)

        # Fetch documents corresponding to doc_ids, retaining sub_docs in metadata
        docs = []
        for _id, sub_docs in id_to_doc.items():
            docstore_docs = self.docstore.mget([_id])
            if docstore_docs:
                if doc := docstore_docs[0]:
                    doc.metadata["sub_docs"] = sub_docs
                    docs.append(doc)
        return docs

class newMultiRetQAChain(MultiRetrievalQAChain):
    """A multi-route chain that uses an LLM router chain to choose amongst retrieval
    qa chains."""

    router_chain: LLMRouterChain
    """Chain for deciding a destination chain and the input to it."""
    destination_chains: Mapping[str, BaseRetrievalQA]
    """Map of name to candidate chains that inputs can be routed to."""
    default_chain: Chain
    """Default chain to use when router doesn't map input to one of the destinations."""

    @property
    def output_keys(self) -> List[str]:
        return ["result"]

    @classmethod
    def from_retrievers(
        cls,
        llm: BaseLanguageModel,
        retriever_infos: List[Dict[str, Any]],
        default_retriever: Optional[BaseRetriever] = None,
        default_prompt: Optional[PromptTemplate] = None,
        default_chain: Optional[Chain] = None,
        *,
        default_chain_llm: Optional[BaseLanguageModel] = None,
        **kwargs: Any,
    ) -> newMultiRetQAChain:
        if default_prompt and not default_retriever:
            raise ValueError(
                "`default_retriever` must be specified if `default_prompt` is "
                "provided. Received only `default_prompt`."
            )
        destinations = [f"{r['name']}: {r['description']}" for r in retriever_infos]
        destinations_str = "\n".join(destinations)

        router_template = MULTI_RETRIEVAL_ROUTER_TEMPLATE.format(
            destinations=destinations_str,
        )

        router_prompt = PromptTemplate(
            template=router_template,
            input_variables=["input"],
            output_parser=RouterOutputParser(next_inputs_inner_key="query"),
        )
        router_chain = LLMRouterChain.from_llm(llm, router_prompt)
        destination_chains = {}
        for r_info in retriever_infos:
            prompt = r_info.get("prompt")
            retriever = r_info["retriever"]
            
            chain = RetrievalQA.from_llm(llm, prompt=prompt, return_source_documents=True, retriever=retriever)
            name = r_info["name"]
            destination_chains[name] = chain
        if default_chain:
            _default_chain = default_chain
        elif default_retriever:
            _default_chain = RetrievalQA.from_llm(
                llm, prompt=default_prompt, return_source_documents=True, retriever=default_retriever
            )
        else:
            prompt_template = DEFAULT_TEMPLATE.replace("input", "query")
            prompt = PromptTemplate(
                template=prompt_template, input_variables=["history", "query"]
            )
            if default_chain_llm is None:
                raise NotImplementedError(
                    "conversation_llm must be provided if default_chain is not "
                    "specified. This API has been changed to avoid instantiating "
                    "default LLMs on behalf of users."
                    "You can provide a conversation LLM like so:\n"
                    "from langchain_openai import ChatOpenAI\n"
                    "llm = ChatOpenAI()"
                )
            _default_chain = ConversationChain(
                llm=default_chain_llm,
                prompt=prompt,
                input_key="query",
                output_key="result",
            )
        return cls(
            router_chain=router_chain,
            destination_chains=destination_chains,
            default_chain=_default_chain,
            **kwargs,
        )

def get_wiki(top_k=4):
    retriever = WikipediaRetriever(
        top_k_results=top_k,
    )
    return retriever

def get_wiki_chain(prompt_template=None):
    llm = get_llm(temperature=0)
    retriever = get_wiki()

    if prompt_template is not None:
         prompt_template = PromptTemplate.from_template(prompt_template)
    else:
        prompt_template = hub.pull("rlm/rag-prompt") #QA prompt  https://smith.langchain.com/hub/rlm/rag-prompt?organizationId=5b2073af-2123-4ed3-b218-fa406e467d84 
    
    chain = (
        {"context": retriever, "question": RunnablePassthrough()}
        | prompt_template
        | llm
    )
    return chain 

def route(llm, retrievers, prompt_template=None):
    """A multi-route chain that uses an LLM router chain to choose amongst retrieval
    qa chains."""

    if prompt_template is not None:
         prompt_template1 = PromptTemplate.from_template(prompt_template[0]) # EWHA # template=prompt_template[0], input_variables=["context", "question"]
         prompt_template2 = PromptTemplate.from_template(prompt_template[1]) # COMMON FOR MMLU
         prompt_template3 = PromptTemplate.from_template(prompt_template[2]) # BASE
    else:
        prompt_template1 = hub.pull("rlm/rag-prompt") #QA prompt  https://smith.langchain.com/hub/rlm/rag-prompt?organizationId=5b2073af-2123-4ed3-b218-fa406e467d84 
        prompt_template2 = prompt_template1
        
    retriever_infos = [
        {
            "name": "ewha_retriever",
            "description": "for 이화여자대학교 학칙(Rules of Ewha Womans University); 학교(대학)의 학과, 교과 과정, 입학, 졸업, 상벌, 총칙, 부설기관, 학사 운영, 학점, 성적, 학생 활동 및 행정 절차 등에 관한 규칙. A university is an institution of higher (or tertiary) education (undergraduate and postgraduate programs) and research which awards academic degrees in several academic disciplines(majors).",
            "retriever": retrievers[0],
            "prompt": prompt_template1
        },
        {
            "name": "law_retriever",
            "description": "An expert for law; a set of rules that are created and are enforceable by social or governmental institutions to regulate behavior,[1] with its precise definition a matter of longstanding debate. law is a system of rules established by governing authorities to regulate behavior, maintain order, and resolve disputes. Not related with university rules",
            "retriever": retrievers[1][0],
            "prompt": prompt_template2
        },
        {
            "name": "psychology_retriever",
            "description": "An expert for psychology; the scientific study of mind and behavior. Its subject matter includes the behavior of humans and nonhumans, both conscious and unconscious phenomena, and mental processes such as thoughts, feelings, and motives. psychology is the scientific study of the mind and behavior, exploring how individuals think, feel, and act. Not related with university rules",
            "retriever": retrievers[1][1],
            "prompt": prompt_template2
        },
        {
            "name": "philosophy_retriever",
            "description": "An expert for philosophy; a systematic study of general and fundamental questions concerning topics like existence, reason, knowledge, value, mind, and language. It is a rational and critical inquiry that reflects on its own methods and assumptions. Philosophy is the study of fundamental questions regarding existence, knowledge, ethics, and reason. Not related with university rules",
            "retriever": retrievers[1][2],
            "prompt": prompt_template2
        },
        {
            "name": "business_retriever",
            "description": "An expert for business; the practice of making one's living or making money by producing or buying and selling products (such as goods and services). It is also 'any activity or enterprise entered into for profit.' business involves the creation, management, and operation of organizations that provide goods or services for profit. Not related with university rules",
            "retriever": retrievers[1][3],
            "prompt": prompt_template2
        },
        {
            "name": "history_retriever",
            "description": "An expert for history; the systematic study and documentation of the human past. history is the study of past events and societies, examining how they have shaped the present and future. Human history is the record of humankind from prehistory to the present. Not related with university rules",
            "retriever": retrievers[1][4], 
            "prompt": prompt_template2
        },
    ]

    default_retriever = retrievers[-1]
    default_prompt = prompt_template1
    
    multi_retrieval_qa_chain = newMultiRetQAChain.from_retrievers(
        llm=llm,
        retriever_infos=retriever_infos,
        default_retriever=default_retriever,
        default_prompt=default_prompt,
        verbose=True
    )
    return multi_retrieval_qa_chain 


def get_teacher_chain():
    llm = get_llm(temperature=0)
    chain = (
        PromptTemplate.from_template(TEACHER_SG_TEMPLATE) | llm
        )
    return chain

def check_by_teacher(chain, response):
    response = chain.invoke({"question": response['input'], "answer": response['result']})
    #response.content = response.content.replace("the correct answer is", "[ANSWER]:")
    if 'incorrect' in response.content.lower():
        return False, response
    else: return True, response

def get_option(question, response, debug=False):
    answer = get_answers(response)
    if extract_answer(answer) is not None: return answer
    if 'Answer:\n' in answer:
        answer = answer.replace("Answer:\n", f"[ANSWER]: ")
        print("&&EUREKA:", "[ANSWER]: "+ answer.split('[ANSWER]:')[-1].strip())
    else:
        pattern = r'\(([A-Z])\)\s(.*?)[\u2028\n]'
        options = re.findall(pattern, question)
        print("&&OPTIONS:", options)
        candidate = answer.split('[ANSWER]:')[-1].strip()
        for (question_alphabet, question_text) in options:
            if debug:
                print(f"&&&{question_alphabet}:")
                print(" ", question_text.strip())
                print(" ", answer.split('[ANSWER]:')[-1].strip())
            if candidate.startswith(question_text.strip()) \
                or ''.join(candidate.split(' ')).startswith(''.join(question_text.strip().split(' '))):
                answer = answer.replace("[ANSWER]:", f"[ANSWER]: ({question_alphabet}) {question_text}")
                print("&&EUREKA:", "[ANSWER]: "+ answer.split('[ANSWER]:')[-1].strip())
                return answer
    answer = answer.replace("\u2028", "\n")
    return answer

def get_answers(response):
    if isinstance(response, str): answer = response
    else:
        try: answer = response['result']
        except: answer = response.content
    return answer

def get_responses(chain, safeguard, prompts, debug=False):
    # read samples.csv file
    responses = []
    teacher = get_teacher_chain()
    for prompt in tqdm(prompts, desc="Processing questions"):
        #prompt = PromptTemplate.from_template(prompt)
        # No relevant docs were retrieved using the relevance score threshold 0.8
        try:
            response = chain.invoke(prompt) # chain.invoke({"question": prompt, "context": context})
            if debug: print("&&ROUTE: ", response['result'])
            #is_correct_by_teacher, teacher_res = check_by_teacher(teacher, response)
            # (not is_correct_by_teacher or extract_answer(response['result']) is None)
            if extract_answer(response['result']) is None:
                response['result'] = get_option(prompt, response)
            if len(response['source_documents']) > 0 and extract_answer(response['result']) is None:
                response = safeguard[0].invoke(prompt)
                if debug: print("&&EWHA SAFEGUARD: ", response)
            if len(response['source_documents']) < 1 or extract_answer(response['result']) is None:
                response = safeguard[1].invoke(prompt)
                response = get_option(prompt, response)
                #response = teacher_res 
                #print("&&TEACHER: ", teacher_res)
                if debug: print("&&MMLU SAFEGUARD: ", response)
        except ValueError: # ValueError: Received invalid destination chain name 'education_retriever'
            response = safeguard[0].invoke(prompt)
            if debug: print("&&SAFEGUARD: ", response)
        responses.append(get_answers(response))
    return responses

def get_agent_responses(agent, prompts):
    """Get responses from given agent"""
    responses = [] 
    for prompt in tqdm(prompts, desc="Processing questions"):
        response = agent.invoke({"input": prompt}) 
        responses.append(response)
    return responses 

def get_faiss_vs(splits, embeddings):
    vectorstore = FAISS.from_documents(documents=splits, embedding=embeddings) 
    return vectorstore 
       
def get_faiss(splits, save_dir="./db/ewha/ewha_faiss_fix", chunk_size=None, chunk_overlap=None, top_k=4, thres=0.8): 
    # returns retriever FAISS 
    embeddings = get_embedding()
    print("[INFO] Get retriever FAISS ...")
    if not os.path.exists(save_dir):
        vectorstore = get_faiss_vs(splits, embeddings)
        os.mkdir(save_dir) 
        vectorstore.save_local(save_dir)
        print("[INFO] Successfully saved Vectorscore to local!")
    else:
        # If the db already exists, load from local.
        vectorstore = FAISS.load_local(save_dir, embeddings, allow_dangerous_deserialization=True) 
        print(f"[INFO] Load DB from {save_dir}...") 

    retriever = vectorstore.as_retriever(
                    search_type="similarity_score_threshold", 
                    search_kwargs={"score_threshold": thres, 
                    "k": top_k}) # Modified(Su)
    return retriever 

def get_bm25(splits, save_dir="./db/bm25", chunk_size=None, chunk_overlap=None, top_k=4):
    # Where to save BM25
    bm25_path = os.path.join(save_dir, "bm25.pkl")

    # Load BM25 from local
    print("[INFO] Get retriever BM25 ...")
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)
    if not os.path.exists(bm25_path):
        print("[INFO] Creating BM25 index...")
        # Make BM25
        bm25_retriever = BM25Retriever.from_documents(documents=splits)
        bm25_retriever.k = top_k
        # Save BM25
        with open(bm25_path, "wb") as f:
            pickle.dump(bm25_retriever, f)
        print(f"[INFO] Successfully saved BM25 index to {bm25_path}!")
    else:
        # Load BM25 from local
        with open(bm25_path, "rb") as f:
            bm25_retriever = pickle.load(f)
        print(f"[INFO] Load BM25 index from {bm25_path}...")

    bm25_retriever.k = top_k
    return bm25_retriever

def get_ensemble_retriever(retrievers, weights):
    ensemble_retriever = EnsembleRetriever(retrievers=retrievers, weights=weights)
    return ensemble_retriever  

def remove_header(text):
    return text.replace("이화여자대학교 학칙", "")

def to_document(text: str, meta):
    if meta:
        return Document(id=meta, page_content=remove_header(text), metadata={"p_id": meta})
    else: return Document(id=meta, page_content=text, metadata={"p_id": meta})
                
def load_ewha(data_root, chunk_size=1000, chunk_overlap=100, json_name = "ewha_chunk_doc_fix.json"):
    """
    Corrects the spacing in the given documents using chain and save as json file
    returns splits list
    """
    ewha_chunks_path = os.path.join(data_root, json_name)

    if not os.path.exists(ewha_chunks_path):
        filename = os.path.join(data_root, "ewha_full_text.txt")
        f = open(filename, 'r')
        text = f.read()

        pattern1 = r"제\d+장"
        pattern2 = r'\[별표 \d+\] '
        matches1 = re.findall(pattern1, text)
        splitted = re.split(pattern1, text)
        matches2 = re.findall(pattern2, splitted[-1]) 
        
        splits = [to_document(splitted[0], 0)] \
            + [to_document(p + chunk, i+1) for i, (p, chunk) in enumerate(zip(matches1, splitted[1:-1]))] \
            + [to_document(p + chunk, i+len(matches1)+1) for i, (p, chunk) in enumerate(zip(matches2, re.split(pattern2, splitted[-1])))]
        llm = get_llm(temperature=0) 

        chain = (
                {"doc": lambda x: x.page_content} # Use as a context
                | ChatPromptTemplate.from_template("Please correct the spacing in the given text. It is a university regulation document. Just give only answer.\n\n{doc}")
                | llm
                | StrOutputParser()
            )
        for _ in range(5):
            splits[-1].page_content = chain.invoke(splits[-1])
            last_chunk = splits[-1].page_content.split(' ')
            if max(Counter(last_chunk).values()) < 3: break

        with open(ewha_chunks_path, 'w') as f:
            for doc in splits:
                f.write(doc.json() + '\n')
    else:
        splits = []
        # If already exists, load the file from local
        with open(ewha_chunks_path, 'r') as f:
            for line in f:
                data = json.loads(line)
                obj = Document(**data)
                splits.append(obj)
    print(f"[INFO] # of splits: {len(splits)}")
    #exit(-1)
    return splits 

def load_arc():
    """Loads allenai/ai2_arc dataset and make it as metadata"""
    ds = load_dataset("allenai/ai2_arc", "ARC-Easy")
    train_data = ds['train']
    train_docs = []
    for entry in train_data:
        doc_content = format_arc_doc(entry)
        doc = Document(page_content=doc_content, metadata={"question": entry['question'], "choices": entry['choices']})
        train_docs.append(doc)
    return train_docs 

def load_customed_datasets(type):
    train_docs = []
    if type == "law": # 24.4k
        print("[INFO] Load ymoslem/Law-StackExchange dataset...")
        ds_law = load_dataset("ymoslem/Law-StackExchange")
        train_data_law = ds_law['train'] 
        sample_size = 2000 
        train_data_list = list(train_data_law)
        sampled_data_law = random.sample(train_data_list, sample_size) 

        for entry in tqdm(sampled_data_law):
            if len(entry['answers']) == 0:
                continue 
            doc_content = format_law_docs(entry) 
            doc = Document(page_content=doc_content,
                            metadata={"question": entry['question_title'], 
                                      "details": entry['question_body'], 
                                      "answers": entry["answers"]})
            train_docs.append(doc) 
        return train_docs  
    
    elif type == "psychology": # 197k
        print("[INFO] Load BoltMonkey/psychology-question-answer dataset...")
        ds_psy = load_dataset("BoltMonkey/psychology-question-answer")
        train_data_psy = ds_psy['train'] 
        sample_size = 2000 # Only use 2.0k because of memory issue 

        train_data_list = list(train_data_psy)
        sampled_data_psy = random.sample(train_data_list, sample_size)

        for entry in tqdm(sampled_data_psy):
            doc_content = format_psy_docs(entry) 
            doc = Document(page_content=doc_content,
                           metadata={"question": entry['question'],
                                     "answer": entry['answer']}) 
            train_docs.append(doc) 
        return train_docs 
    
    elif type == "business": # 1.02k
        print("[INFO] Load Rohit-D/synthetic-confidential-information-injected-business-excerpts dataset ...")
        ds_bis = load_dataset("Rohit-D/synthetic-confidential-information-injected-business-excerpts")
        train_data_bis = ds_bis['train']
        for entry in tqdm(train_data_bis):
            doc_content = format_bis_docs(entry)
            doc = Document(page_content=doc_content,
                           metadata={"excerpt": entry['Excerpt'],
                                     "reason": entry["Reason"]})
            train_docs.append(doc) 
        return train_docs 
    
    elif type == "philosophy": # 134k
        print("[INFO] Load sayhan/strix-philosophy-qa dataset ...")
        ds_phi = load_dataset("sayhan/strix-philosophy-qa") 
        train_data_phi = ds_phi['train'] 
        sample_size = 2000 # Only use 1.8k because of memory issue --> 2.0k 4004tokens error(> 4000tokens limitation)
        train_data_list = list(train_data_phi)
        sampled_data_phi = random.sample(train_data_list, sample_size)

        for entry in tqdm(sampled_data_phi):
            doc_content = format_phi_docs(entry) 
            doc = Document(page_content = doc_content,
                           metadata= {"category": entry['category'],
                                      "question": entry['question'],
                                      "answer": entry['answer']})
            train_docs.append(doc) 
        return train_docs 
    
    elif type == "history":
        print("[INFO] Load nielsprovos/world-history-1500-qa dataset...") 
        ds_hist = load_dataset("nielsprovos/world-history-1500-qa")
        train_data_hist = list(ds_hist['train'])[0]['qa_pairs'] # 376
        for entry in tqdm(train_data_hist):
            doc_content = format_hist_docs(entry)
            doc = Document(page_content = doc_content,
                            metadata={"question": entry['question'],
                                      "answer": entry['answer']})

            train_docs.append(doc) 
        return train_docs
    
    assert len(train_docs) !=0, "Input correct type!"

def load_custom_dataset(dataset_name):
    """Load a custom dataset by name."""
    if dataset_name == "arc": # use arc dataset
        return load_arc()
    elif dataset_name == "other dataset": # new dataset(ex:cosmosqa)
        # add new load_dataset()
        return load_other_dataset(dataset_name)
    else:
        raise ValueError(f"[ERROR] Unsupported dataset: {dataset_name}") 
    
def load_other_dataset(dataset_name):
    """Loads *** dataset and make it as metadata"""
    ds = load_dataset(dataset_name)
    train_data = ds['train']
    train_docs = []
    return train_docs

def get_arc_faiss(arc_data, save_dir="./db/arc_faiss", top_k=4):
    """Get FAISS retriever from arc dataset"""
    retriever_arc = get_faiss(arc_data, save_dir, top_k) 
    return retriever_arc 

def get_arc_chroma(arc_data, save_dir="./db/arc_chroma", top_k=4, collection_name="chroma"):
    """Get Chroma retriever from arc dataset"""
    retriever_arc = get_chroma(arc_data, save_dir, top_k=top_k, collection_name=collection_name)   
    return retriever_arc
    # ex. arc_retriever = get_arc_chroma(arc_data, save_dir="./db/arc_chroma", top_k=top_k, collection_name="arc_chroma") 

def get_agent_executor(llm, r1, r2):
    """Make agent executor with given two retrievers, r1 and r2."""
    retriever_tool_1 = create_retriever_tool(r1, "ewha_search", "Searches any questions related to school rules. Always use this tool when user query is related to EWHA or school rules!") 
    retriever_tool_2 = create_retriever_tool(r2, "arc_search", "Searches any questions related to science. Always use this tool when user query is related to science!")
    tools = [retriever_tool_1, retriever_tool_2]
    
    prompt = hub.pull("hwchase17/openai-functions-agent")
    agent = create_tool_calling_agent(llm, tools, prompt)
    agent_executor = AgentExecutor(agent=agent, tools=tools)
    
    return agent_executor

def get_chroma_vs(save_dir, embeddings, collection_name, cosine=False):
    if cosine:
        vectorstore = Chroma(
                persist_directory=save_dir,
                embedding_function=embeddings,
                collection_name=collection_name,
                collection_metadata = {'hnsw:space': 'cosine'},
            )    
    else:
        vectorstore = Chroma(
                persist_directory=save_dir,
                embedding_function=embeddings,
                collection_name=collection_name,
            )    
    return vectorstore
    
def get_MultiVecRetriever(vectorstore, store, id_key, top_k):
    retriever = newMultiVectorRetriever(
        vectorstore=vectorstore,
        byte_store=store,
        id_key=id_key,
        search_kwargs={"k": top_k},
    )
    return retriever

def get_chroma(splits, save_dir="./db/chroma", top_k=4, chunk_size=None, chunk_overlap=None, collection_name=""):
    embeddings = get_embedding() 
    if not os.path.exists(save_dir):
        os.mkdir(save_dir) 
        vectorstore = Chroma.from_documents(collection_name=collection_name, documents=splits, embedding=embeddings, persist_directory=save_dir) 
    else:
        # If the db already exists, load from local.
        vectorstore = get_chroma_vs(save_dir, embeddings, collection_name, cosine=False)
    retriever = vectorstore.as_retriever(search_kwargs={"k": top_k})
    return retriever 

def get_summ_docs(splits, doc_ids, id_key):
    llm = get_llm(temperature=0)
    chain = (
        {"doc": lambda x: x.page_content}
        | ChatPromptTemplate.from_template("Summarize the following document:\n\n{doc}")
        | llm
        | StrOutputParser()
    )
    summaries = chain.batch(splits, {"max_concurrency": 5})
    summary_docs = [
            Document(page_content=s, metadata={id_key: doc_ids[i]})
            for i, s in enumerate(summaries)
        ]
    return summary_docs

def get_child(splits, doc_ids, child_text_splitter, id_key):
    data = dict()
    sub_docs = []
    for i, doc in enumerate(splits):
        _id = doc_ids[i]
        _sub_docs = child_text_splitter.split_documents([doc])
        for _doc in _sub_docs:
            _doc.metadata[id_key] = _id
        data[doc.page_content] = [s.page_content for s in _sub_docs] 
        sub_docs.extend(_sub_docs) #Modified(Yoojin): fixed indentation error
    return sub_docs, data

def get_pc_chroma_cos(splits, save_dir="./db/pc_chroma_cos", top_k=4, chunk_size=1000, chunk_overlap=100, debug=False, thres=0.0001):
    """Parent Document Retreiver using Chroma"""
    embeddings = get_embedding() 
    #docstore_path = os.path.join(save_dir, "docstore_pc.pkl")
    os.makedirs(save_dir, exist_ok=True) 
    
    # The vectorstore to use to index the child chunks
    vectorstore = get_chroma_vs(save_dir, embeddings, "parent-child", cosine=True)

    # Layer to store parent document
    store = InMemoryByteStore()
    id_key = "doc_id"
    retriever = get_MultiVecRetriever(vectorstore, store, id_key, top_k)

    # splitter to make chunk
    parent_text_splitter = RecursiveCharacterTextSplitter(
                chunk_overlap=100,
                chunk_size=800)

    child_text_splitter = RecursiveCharacterTextSplitter(
                chunk_overlap=chunk_overlap,
                chunk_size=chunk_size)

    splits = parent_text_splitter.split_documents(splits)
    doc_ids = [str(uuid.uuid4()) for _ in splits]

    sub_docs, data = get_child(splits, doc_ids, child_text_splitter, id_key)
    retriever.vectorstore.add_documents(sub_docs)
    retriever.docstore.mset(list(zip(doc_ids, splits)))
    
    # Save as json file
    json_path = os.path.join(save_dir, f"./ewha_pc_{chunk_size}_{chunk_overlap}.json")
    with open(json_path, 'w') as f:
        json.dump(data, f, indent=4, ensure_ascii = False) 

    if debug:
        print("[DEBUG] Testing Parent-Child ...")
        retriever_test(vectorstore, retriever, "휴학은 최대 몇 년까지 할 수 있어?", "pc_chroma")
        retriever_test(vectorstore, retriever, "생활환경대학의 기존 이름은?", "pc_chroma")
    return retriever

def get_pc_chroma(splits, save_dir="./db/pc_chroma", top_k=4, chunk_size=1000, chunk_overlap=100, thres=0.4, debug=False):
    """Parent Document Retreiver using Chroma"""
    embeddings = get_embedding() 
    #docstore_path = os.path.join(save_dir, "docstore_pc.pkl")
    os.makedirs(save_dir, exist_ok=True) 
    
    # The vectorstore to use to index the child chunks
    vectorstore = get_chroma_vs(save_dir, embeddings, "parent-child", cosine=False)

    # Layer to store parent document
    store = InMemoryByteStore()
    id_key = "doc_id"
    retriever = get_MultiVecRetriever(vectorstore, store, id_key, top_k)
    doc_ids = [str(uuid.uuid4()) for _ in splits]

    #Modified(Yoojin)
    json_path = os.path.join(save_dir, f"./ewha_pc_{chunk_size}_{chunk_overlap}.json")
    sub_docs_path = os.path.join(save_dir, "./sub_docs.json")
    
    # If the sub_docs, data already exists, load them. Otherwise make it!
    if os.path.exists(sub_docs_path) and os.path.exists(json_path): 
        with open(sub_docs_path, "r", encoding="utf-8") as f:
            sub_docs = json.load(f)
            sub_docs = [Document(metadata=doc["metadata"], page_content=doc["page_content"]) for doc in sub_docs]
        with open(json_path, 'r') as f:
            data = json.load(f) 
    else:
        # splitter to make child chunk
        child_text_splitter = RecursiveCharacterTextSplitter(
                    chunk_overlap=chunk_overlap,
                    chunk_size=chunk_size)
        
        sub_docs, data = get_child(splits, doc_ids, child_text_splitter, id_key) 
        # Save as json file
        json_path = os.path.join(save_dir, f"./ewha_pc_{chunk_size}_{chunk_overlap}.json")
        with open(json_path, 'w') as f:
            json.dump(data, f, indent=4, ensure_ascii = False) 

        with open("sub_docs.json", "w", encoding="utf-8") as f:
            json.dump([document_to_dict(doc) for doc in sub_docs], f, ensure_ascii=False, indent=4)

    retriever.vectorstore.add_documents(sub_docs)
    retriever.docstore.mset(list(zip(doc_ids, splits)))

    if debug:
        print("[DEBUG] Testing Parent-Child ...")
        retriever_test(vectorstore, retriever, "휴학은 최대 몇 년까지 할 수 있어?", "pc_chroma")
        retriever_test(vectorstore, retriever, "생활환경대학의 기존 이름은?", "pc_chroma")
    return retriever

def get_summ_chroma(splits, save_dir="./db/summ_chroma", top_k=4, chunk_size=None, chunk_overlap=None, debug=False):
    """Parent Document Retreiver using Chroma with summarization"""
    embeddings = get_embedding() 
    docstore_path = os.path.join(save_dir, "docstore_summ.pkl")
    os.makedirs(save_dir, exist_ok=True) 

    # The vectorstore to use to index the child chunks
    vectorstore = get_chroma_vs(save_dir, embeddings, "summaries")
    # Layer to store parent document
    id_key = "doc_id"
    store = InMemoryByteStore()    

    if not os.path.exists(docstore_path):
        # The retriever (empty to start)
        retriever = get_MultiVecRetriever(vectorstore, store, id_key, top_k)
        doc_ids = [str(uuid.uuid4()) for _ in splits]

        summary_docs = get_summ_docs(splits, doc_ids, id_key)

        retriever.vectorstore.add_documents(summary_docs)
        retriever.docstore.mset(list(zip(doc_ids, splits)))

        # Save the vectorstore and docstore to disk
        retriever.vectorstore.persist()
        with open(docstore_path, "wb") as file:
            pickle.dump(retriever.byte_store.store, file, pickle.HIGHEST_PROTOCOL)
        print("[INFO] Successfully saved Vectorscore to local!")
    else:
        with open(docstore_path, "rb") as file:
            store_dict = pickle.load(file)
        store.mset(list(store_dict.items()))
        retriever = get_MultiVecRetriever(vectorstore, store, id_key, top_k)
        print(f"[INFO] Load DB from {save_dir}...") 
    
    if debug:
        print("[DEBUG] Testing Parent-Child Summarization...")
        retriever_test(vectorstore, retriever, "휴학은 최대 몇 년까지 할 수 있어?", "summ_chroma")
        retriever_test(vectorstore, retriever, "생활환경대학의 기존 이름은?", "summ_chroma")
    return retriever
        
def get_pc_faiss(splits, save_dir="./db/pc_faiss", top_k=4, chunk_size=1000, chunk_overlap=100, debug=False):
    embeddings = get_embedding() 

    docstore_path = os.path.join(save_dir, "docstore_pc.pkl")
    os.makedirs(save_dir, exist_ok=True) 
    id_key = "doc_id"
    store = InMemoryByteStore()
    
    if not os.path.exists(docstore_path):
        doc_ids = [str(uuid.uuid4()) for _ in splits]

        # splitter to make child chunk
        child_text_splitter = RecursiveCharacterTextSplitter(
                    chunk_overlap=chunk_overlap,
                    chunk_size=chunk_size)
        
        sub_docs, data = get_child(splits, doc_ids, child_text_splitter, id_key)
        vectorstore = get_faiss_vs(sub_docs, embeddings)

        # The retriever (empty to start)
        retriever = get_MultiVecRetriever(vectorstore, store, id_key, top_k)

        #retriever.vectorstore.add_documents(sub_docs)
        retriever.docstore.mset(list(zip(doc_ids, splits)))
        vectorstore.save_local(save_dir)
        with open(docstore_path, "wb") as file:
            pickle.dump(retriever.byte_store.store, file, pickle.HIGHEST_PROTOCOL)
        print("[INFO] Successfully saved Vectorscore to local!")

        #retriever.vectorstore.add_documents(summary_docs)
        retriever.docstore.mset(list(zip(doc_ids, splits)))
        # Save the vectorstore and docstore to disk
        vectorstore.save_local(save_dir)
        with open(docstore_path, "wb") as file:
            pickle.dump(retriever.byte_store.store, file, pickle.HIGHEST_PROTOCOL)
        print("[INFO] Successfully saved Vectorscore to local!")
        
    else:
        with open(docstore_path, "rb") as file:
            store_dict = pickle.load(file)
        store.mset(list(store_dict.items()))

        vectorstore = FAISS.load_local(save_dir, embeddings, allow_dangerous_deserialization=True) 
        retriever = get_MultiVecRetriever(vectorstore, store, id_key, top_k)
        print(f"[INFO] Load DB from {save_dir}...") 

    if debug:
        print("[DEBUG] Testing Parent-Child with FAISS...")
        retriever_test(vectorstore, retriever, "휴학은 최대 몇 년까지 할 수 있어?", "pc_faiss")
        retriever_test(vectorstore, retriever, "생활환경대학의 기존 이름은?", "pc_faiss")
    return retriever
        

def get_summ_faiss(splits, save_dir="./db/summ_faiss", top_k=4, chunk_size=None, chunk_overlap=None, debug=False):
    embeddings = get_embedding() 
    docstore_path = os.path.join(save_dir, "docstore_summ.pkl")
    os.makedirs(save_dir, exist_ok=True) 
    id_key = "doc_id"
    store = InMemoryByteStore()
    
    if not os.path.exists(docstore_path):
        doc_ids = [str(uuid.uuid4()) for _ in splits]
        summary_docs = get_summ_docs(splits, doc_ids, id_key)
        vectorstore = get_faiss_vs(summary_docs, embeddings)
        
        # The retriever (empty to start)
        retriever = get_MultiVecRetriever(vectorstore, store, id_key, top_k)
    
        #retriever.vectorstore.add_documents(summary_docs)
        retriever.docstore.mset(list(zip(doc_ids, splits)))
        # Save the vectorstore and docstore to disk
        vectorstore.save_local(save_dir)
        with open(docstore_path, "wb") as file:
            pickle.dump(retriever.byte_store.store, file, pickle.HIGHEST_PROTOCOL)
        print("[INFO] Successfully saved Vectorscore to local!")
    else:
        with open(docstore_path, "rb") as file:
            store_dict = pickle.load(file)
        store.mset(list(store_dict.items()))

        vectorstore = FAISS.load_local(save_dir, embeddings, allow_dangerous_deserialization=True) 
        retriever = get_MultiVecRetriever(vectorstore, store, id_key, top_k)
        print(f"[INFO] Load DB from {save_dir}...") 
    
    if debug:
        print("[DEBUG] Testing Parent-Child with summarization using FAISS...")
        retriever_test(vectorstore, retriever, "휴학은 최대 몇 년까지 할 수 있어?", "summ_faiss")
        retriever_test(vectorstore, retriever, "생활환경대학의 기존 이름은?", "summ_faiss")
    return retriever

def load_cross_encoder(model_name="cross-encoder/ms-marco-MiniLM-L-6-v2"):
    """Load the cross-encoder model and tokenizer."""
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(model_name)
    return tokenizer, model

def re_rank_with_cross_encoder(query, docs, tokenizer, model, device="cpu"):
    """
    Re-rank documents using a cross-encoder model.
    Args:
        query: Search query string.
        docs: List of Document objects to rank.
        tokenizer: Cross-encoder tokenizer.
        model: Cross-encoder model.
        device: Device to run the model on ('cpu' or 'cuda').
    Returns:
        Ranked list of Document objects.
    """
    model.to(device)
    inputs = [
        tokenizer(
            query,
            doc.page_content,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=512
        )
        for doc in docs
    ]
    scores = []
    with torch.no_grad():
        for input_batch in inputs:
            input_batch = {k: v.to(device) for k, v in input_batch.items()}
            outputs = model(**input_batch)
            scores.append(outputs.logits.squeeze().item())

    # Sort documents by score (descending)
    ranked_docs = [doc for _, doc in sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)]
    return ranked_docs
 

def retriever_test(vectorstore, retriever, question, retriever_name):
    """Test the Parent-Child structure"""
    sub_docs = vectorstore.similarity_search(question)
    print(f"<Question> {question}")
    print(f"====== {retriever_name} child docs result ========")
    for i, doc in enumerate(sub_docs):
        processed_content = doc.page_content.replace('\n', ' ')
        print(f"[문서 {i}][{len(doc.page_content)}] {processed_content}")
    print()
    retrieved_docs = retriever.invoke(question)
    print(f"====== {retriever_name} parent docs result ========")
    for i, doc in enumerate(retrieved_docs):
        processed_content = doc.page_content.replace('\n', ' ')
        print(f"[문서 {i}][{len(doc.page_content)}] {processed_content}")
    print()
    print()

# Old version.
def get_chain(llm, prompt, retriever=None):
    # You are an assistant for question-answering tasks. Use the following pieces of retrieved context to answer the question. If you don't know the answer, just say that you don't know. 
    prompt_template = PromptTemplate.from_template(prompt)
    if retriever is not None:
        chain = (
            {"context": retriever, "question": RunnablePassthrough()}
            | prompt_template
            | llm )
    else:
        chain = prompt_template | llm | StrOutputParser()
    return chain

def retrieve(db, query, tokenizer=None, model=None, device="cpu", use_reranking=False):
    """
    Perform a query on the retriever.
    If db is an EnsembleRetriever, use its invoke method.
    Otherwise, access the vectorstore directly.
    Optionally re-rank results using a cross-encoder.
    """
    if isinstance(db, EnsembleRetriever):
        docs = db.invoke(query)
    else:
        docs = db.vectorstore.similarity_search(query)
    
    # Apply Cross-encoder re-ranking if enabled
    if use_reranking and tokenizer and model:
        print("[INFO] Applying Cross-encoder re-ranking...")
        docs = re_rank_with_cross_encoder(query, docs, tokenizer, model, device)
    
    return docs

def grounded_check(context, answer):
    groundedness_check = UpstageGroundednessCheck()
    request_input = {
        "context": context,
        "answer": answer,
    }
    response = groundedness_check.invoke(request_input)
    #print(response)
    return response == "grounded" # grounded, notGrounded, or notSure

def get_pc_responses(db, chain, prompts, use_grounded):
    responses = []
    for prompt in prompts:
        context = retrieve(db, prompt)
        response = chain.invoke({"question": prompt, "context": context})
        if use_grounded:
            if not grounded_check(context, response.content) or "[ANSWER]:" not in response.content: # mmlu
                response = chain.invoke({"question": prompt, "context": context})
        responses.append(response) # response.content 
    return responses