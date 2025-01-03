from langchain.chains.retrieval_qa.base import RetrievalQA

from engine.utils import * 
from engine.langchain_engine import *
from prompts import *
import warnings
warnings.filterwarnings('ignore') 

"""
Example command line:
python main.py
"""
def main():
    # Load configs
    load_env(".env")
    config = load_yaml("config.yaml") 
    data_root = config['data_root']

    chunk_size = config['chunk_size'] 
    chunk_overlap = config['chunk_overlap']

    top_k = config['top_k']
    ewha_thres = config['ewha_thres'] 
    mmlu_thres = config['mmlu_thres']
    default_thres = config['default_thres'] 

    raptor_faiss_path = config['raptor_faiss_path'] # for ewha
    business_faiss_path = config['business_faiss_path']
    law_faiss_path = config['law_faiss_path']
    psychology_faiss_path = config['psychology_faiss_path']
    philosophy_faiss_path = config['philosophy_faiss_path']
    history_faiss_path = config['history_faiss_path']

    print(f"[INFO] Top k={top_k} | Thrs(Ewha,MMLU,Default)=({ewha_thres},{mmlu_thres},{default_thres}) | Chunk Size={chunk_size} | Chunk Overlap={chunk_overlap}")
    
    # Load and Split documents
    splits = [];
    ret_dict = {
        "rap_faiss": [split_docs, get_faiss, raptor_faiss_path],

        "biz_faiss": [load_custom_dataset, get_faiss, business_faiss_path],
        "law_faiss": [load_custom_dataset, get_faiss, law_faiss_path],
        "psy_faiss": [load_custom_dataset, get_faiss, psychology_faiss_path],
        "phil_faiss": [load_custom_dataset,get_faiss, philosophy_faiss_path],
        "hist_faiss": [load_custom_dataset, get_faiss, history_faiss_path]
    }

    # Get retriever for ewha
    ewha_ret = "rap_faiss"
    if not os.path.exists(ret_dict.get(ewha_ret)[2]):
        print(f"[INFO] {ret_dict.get(ewha_ret)[2]} not exists")
        splits = ret_dict.get(ewha_ret)[0](data_root, chunk_size, chunk_overlap) 
    ewha_retriever  = ret_dict.get(ewha_ret)[1](
                                    splits, 
                                    save_dir=ret_dict.get(ewha_ret)[2], 
                                    top_k=top_k, 
                                    chunk_size=chunk_size, 
                                    chunk_overlap=chunk_overlap, 
                                    thres=ewha_thres)

    # Get retriever for mmlu
    mmlu_ret_paths = [business_faiss_path, law_faiss_path, psychology_faiss_path, philosophy_faiss_path, history_faiss_path]
    mmlu_rets = []
    mmlu_data_splits = []
    for faiss_path in mmlu_ret_paths:
        type_name = str(os.path.basename(faiss_path)).split("_")[0] # ex) business
        if not os.path.exists(faiss_path):          
            mmlu_data_splits = load_custom_dataset(type_name) 
        ret = get_faiss(
                    mmlu_data_splits, 
                    faiss_path, 
                    chunk_size=chunk_size, 
                    chunk_overlap=chunk_overlap, 
                    top_k=top_k, 
                    thres=mmlu_thres) 
        mmlu_rets.append(ret) 
    assert len(mmlu_rets) == 5, "The number of retrievers should be 5."

    # Get default retriever (same with ewha retriever except threshold)
    default_retriever  = ret_dict.get(ewha_ret)[1](splits, 
                                    save_dir=ret_dict.get(ewha_ret)[2], 
                                    top_k=top_k, 
                                    chunk_size=chunk_size, 
                                    chunk_overlap=chunk_overlap, 
                                    thres=default_thres)

    # Make prompt template  
    templates = [EWHA_PROMPT, MMLU_PROMPT, BASE_PROMPT]

    # Make llm
    llm = get_llm(temperature=0)

    # rounting
    chain = get_router(llm, [ewha_retriever, mmlu_rets, default_retriever], templates)
    mmlu_safeguard_chain = PromptTemplate.from_template(SG_PROMPT) | llm
    ewha_safeguard_chain = RetrievalQA.from_llm(llm, 
                                prompt=PromptTemplate.from_template(EWHA_PROMPT), 
                                return_source_documents=True, 
                                retriever=ewha_retriever)

    # Get model's response from given prompts
    print("[INFO] Load test dataset...") 
    questions, answers = read_data(data_root, filename="testset.csv") 
    responses = get_responses(chain=chain, safeguard=[ewha_safeguard_chain, mmlu_safeguard_chain], prompts=questions, debug=True)
    eval(questions, answers, responses, debug=True) 
    print("All Done")

if __name__=="__main__":
    main()
