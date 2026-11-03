"""Serve the LoRA classifier behind the contract geonews.llm.classifier.classify_lora expects.

  uv run uvicorn ml.serve_classifier:app --port 8001
  env: CLASSIFIER_BASE=Qwen/Qwen2.5-1.5B-Instruct CLASSIFIER_ADAPTER=models/classifier-lora

For production throughput, serve the merged model with vLLM instead and keep this contract.
"""

import json
import os

from fastapi import FastAPI
from pydantic import BaseModel

from geonews.llm.classifier import SYSTEM

BASE = os.environ.get("CLASSIFIER_BASE", "Qwen/Qwen2.5-1.5B-Instruct")
ADAPTER = os.environ.get("CLASSIFIER_ADAPTER", "models/classifier-lora")
app = FastAPI()
_state: dict = {}


def _load():
    if not _state:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        tok = AutoTokenizer.from_pretrained(BASE)
        model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.bfloat16, device_map="auto")
        _state.update(tok=tok, model=PeftModel.from_pretrained(model, ADAPTER).eval())
    return _state["tok"], _state["model"]


class In(BaseModel):
    title: str
    body: str


@app.post("/v1/classify")
def classify(body: In) -> dict:
    import torch

    tok, model = _load()
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Title: {body.title}\n\n{body.body}"}]
    inputs = tok.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(inputs, max_new_tokens=64, do_sample=False)
    text = tok.decode(out[0, inputs.shape[1]:], skip_special_tokens=True)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"topic": "other", "locality": "regional", "newsworthiness": 0.5}
    return {**data, "version": f"lora:{os.path.basename(ADAPTER)}"}
