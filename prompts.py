

BASE_PROMPT = """
    Answer the question based on the context below. You have ability to reasoning.
    If not sure about the answer, solve the question without depending on the given context.
    Utilize the clues provided by the speaker to logically infer their current status, and explain the reasoning behind your conclusion in 2-3 sentences.
    Explain the intent behind the question.
    NOTE) You MUST answer like following format at the end.

    ### Example of desired format:
    [ANSWER]: (A) convolutional networks

    ### Context:
    {context}

    ### Question:
    {question}
    """

SG_PROMPT = """
    Answer the question based on the context below. First, explain the intent behind the question.
    You have ability to reasoning. 
    Infer their current status, and explain the reasoning behind your conclusion in 2-3 sentences.
    You MUST answer like following format at the end.

    ### Example of desired format:
    [ANSWER]: (A) convolutional networks

    ### Question:
    {question}
    """

MULTI_RETRIEVAL_ROUTER_TEMPLATE = """
    Given the input, choose the most appropriate model prompt based on the provided prompt descriptions.

    "Prompt Name": "Prompt Description"

    << FORMATTING >>
    Return a markdown code snippet with a JSON object formatted to look like:
    ```json
    {{{{
        "destination": string \ name of the retrieve to use or "DEFAULT"
        "next_inputs": string \ an original version of the original input
    }}}}
    ```

    REMEMBER: "destination" should be chosen based on the descriptions of the available prompts, or "DEFAULT" if no appropriate prompt is found.
    REMEMBER: "next_inputs" MUST be the original input.

    << CANDIDATE PROMPTS >>
    {destinations}

    << INPUT >>
    {{input}}

    << OUTPUT (remember to include the ```json)>>
    """

EWHA_PROMPT = """
    Answer the question based on the context below. You have ability to reasoning.
    If not sure about the answer, solve the question without depending on the given context.
    Utilize the clues provided by the speaker to logically infer their current status, and explain the reasoning behind your conclusion in 2-3 sentences.
    Explain the intent behind the question.
    NOTE) You MUST answer like following format at the end.

    ### Example of desired format:
    [ANSWER]: (A) convolutional networks

    ### Context:
    {context}

    ### Question:
    {question}
    """

MMLU_PROMPT = """
    Answer the question based on the context below. You have ability to reasoning.
    If not sure about the answer, solve the question without depending on the given context.
    Utilize the clues provided by the speaker to logically infer their current status, and explain the reasoning behind your conclusion in 2-3 sentences.
    Explain the intent behind the question.
    NOTE) You MUST answer like following format at the end.

    ### Example of desired format:
    [ANSWER]: (A) convolutional networks

    ### Context:
    {context}

    ### Question:
    {question}
    """

WIKI_KEYWORD_TEMPLATE = """
    Extract 2~3 keywords from given Context.
    NOTE) You MUST answer only keywords.
    ---
    ###Context:
    {context}
    """

#     
TEACHER_TEMPLATE = """
    You are a logical and intelligent teacher.
    You are teaching one student, and this student can be smart sometimes and not so bright at other times.
    You gave the student the following question:
    ---
    ###Question:
    {question}

    ---
    And you received the student’s answer:
    ###Answer:
    {answer}

    ---
    Please analyze whether the student’s answer is correct or incorrect, with an explanation to student step by step.
    NOTE) At the end, you MUST say either “Correct” or “Incorrect” regarding the student’s answer.
    """

TEACHER_SG_TEMPLATE = """
    You are a logical and intelligent teacher.
    You are teaching one student, and this student can be smart sometimes and not so bright at other times.
    You gave the student the following question:
    ---
    ###Question:
    {question}

    ---
    And you received the student’s answer:
    ###Answer:
    {answer}

    ---
    Please analyze whether the student’s answer is correct or incorrect, with an explanation to student step by step.
    If the student’s answer is incorrect, you should give correct answer with example format.
    NOTE) You MUST conclude correct answer like following format at the end. Remind that you can only one from multiple choices.
    
    ---

    ### Example of expected format:
    [ANSWER]: (A) convolutional networks
    """

RAPTOR_EWHA_TEMPLATE = """
    여기 이화여자대학교 학칙 문서가 있습니다.

    이 문서는 학칙의 핵심 내용을 포함하며, 총칙, 부설기관, 학사 운영, 학생 활동 및 행정 절차 등 주요 항목을 다룹니다.

    제공된 문서의 자세한 요약을 제공하십시오.
    ----
    #### 문서:
    {doc}
    """

RAPTOR_LAW_TEMPLATE = """
    Here is a collection of legal questions and answers from the StackExchange Law site.

    This contents contains various legal topics, including contract law, criminal law, intellectual property, and other domains of legal practice.

    Provide a detailed summary of the provided legal content.
    ----
    #### Doc:
    {doc}
"""

RAPTOR_PSYCHOLOGY_TEMPLATE = """
    Here is a collection of question-and-answer pairs based on a Bachelor level psychology course.

    The questions span a wide range of psychological topics including but not limited to:
    - Cognitive psychology
    - Clinical psychology
    - Social psychology
    - Developmental psychology
    - Biological psychology
    - Research methods and ethics

    Provide a clear and concise summary of the answer provided to the question.
    ----
    #### Doc:
    {doc}
"""
RAPTOR_BUSINESS_TEMPLATE = """
    Here is a collection of business excerpt - Reasons pairs on  business report excerpts which contain relevant confidential/sensitive information.

    This includes mentions of :
    - Internal Marketing Strategies.
    - Proprietary Product Composition.
    - License Internals.
    - Internal Sales Projections.
    - Confidential Patent Details.
    - Others.

    Provide a clear and concise summary of the answer provided to the question.
    ----
    #### Doc:
    {doc}
"""

RAPTOR_PHILOSOPHY_TEMPLATE = """
    Here is a collection of question-and-answer pairs about philosophy.
    Category is also mentioned for each question and answer pair.
    Please summarize up to 2 sentences that is concise but contains key points of the contents.
    ----
    #### Doc:
    {doc}
"""