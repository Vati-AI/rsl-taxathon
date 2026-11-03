"""Model-free LangGraph install/interrupt smoke test, not an investigation engine."""
import os
os.environ['LANGSMITH_TRACING']='false'
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

class State(TypedDict):
    case_id: str
    reviewed: bool

def review(state):
    result=interrupt({'case_id':state['case_id'],'message':'Synthetic developer smoke review'})
    return {'reviewed':result is True}

builder=StateGraph(State);builder.add_node('review',review);builder.add_edge(START,'review');builder.add_edge('review',END)
graph=builder.compile(checkpointer=InMemorySaver())
config={'configurable':{'thread_id':'synthetic-install-smoke'}}
paused=graph.invoke({'case_id':'SYN-001','reviewed':False},config)
assert paused.get('__interrupt__') and paused['reviewed'] is False
resumed=graph.invoke(Command(resume=True),config)
assert resumed['reviewed'] is True
print('LangGraph import, checkpoint, interrupt and resume passed (in-memory synthetic smoke only)')
