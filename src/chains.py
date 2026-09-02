import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

load_dotenv()

def get_llm():
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        temperature=0
    )

def build_sql_chain(db, llm):
    template = """Based on the table schema below, write a syntactically correct MySQL query that answers the user's question.
Return only the raw SQL query on a single line. Do not include markdown code blocks or explanations.

Schema:
{schema}

Question: {question}
SQL Query:"""
    
    prompt = ChatPromptTemplate.from_template(template)
    
    return (
        RunnablePassthrough.assign(schema=lambda _: db.get_table_info())
        | prompt
        | llm.bind(stop=["\nSQLResult:"])
        | StrOutputParser()
    )

def build_full_chain(db, llm, sql_chain):
    answer_template = """Given the user question, corresponding SQL query, and SQL result, answer the user question accurately in plain English.

Question: {question}
SQL Query: {query}
SQL Result: {result}
Answer:"""
    
    answer_prompt = ChatPromptTemplate.from_template(answer_template)
    
    def run_query(query: str):
        try:
            clean_query = query.strip().replace("```sql", "").replace("```", "").strip()
            return db.run(clean_query)
        except Exception as e:
            return f"Execution error: {e}"

    return (
        RunnablePassthrough.assign(query=sql_chain)
        .assign(result=lambda x: run_query(x["query"]))
        | answer_prompt
        | llm
        | StrOutputParser()
    )