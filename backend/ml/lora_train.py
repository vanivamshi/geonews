"""LoRA fine-tune a small open model to replace the prompted classifier.

  uv sync --extra train
  uv run python -m ml.lora_train --base Qwen/Qwen2.5-1.5B-Instruct --out models/classifier-lora

Runs on one 24GB GPU (Modal/RunPod). Then serve with ml/serve_classifier.py, set
GEONEWS_CLASSIFIER_BACKEND=lora, and compare with `geonews-eval run --suite classify` against the
prompted baseline on accuracy, cost per 1k articles, and p95 latency.
"""

import argparse
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--out", default="models/classifier-lora")
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--rank", type=int, default=16)
    args = ap.parse_args()

    from datasets import load_dataset
    from peft import LoraConfig
    from trl import SFTConfig, SFTTrainer

    ds = load_dataset("json", data_files={"train": str(DATA / "classify_train.jsonl"),
                                          "validation": str(DATA / "classify_val.jsonl")})
    peft_config = LoraConfig(
        r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05, task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    config = SFTConfig(
        output_dir=args.out, num_train_epochs=args.epochs, per_device_train_batch_size=8,
        gradient_accumulation_steps=2, learning_rate=2e-4, lr_scheduler_type="cosine",
        warmup_ratio=0.03, logging_steps=10, eval_strategy="epoch", save_strategy="epoch",
        bf16=True, max_length=2048,
        assistant_only_loss=True,  # learn the label JSON, not to reproduce the article
    )
    trainer = SFTTrainer(model=args.base, args=config, train_dataset=ds["train"],
                         eval_dataset=ds["validation"], peft_config=peft_config)
    trainer.train()
    trainer.save_model(args.out)
    print(f"saved adapter to {args.out}")


if __name__ == "__main__":
    main()
