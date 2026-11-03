"""DPO on pairwise summary preferences collected in the labeling UI.

  uv run python -m ml.export dpo
  uv run python -m ml.dpo_train --base Qwen/Qwen2.5-3B-Instruct --out models/summarizer-dpo

Needs a few hundred pairs before it's worth running; check that the chosen side wins for a
consistent reason (look at the data) before training, or DPO learns noise.
"""

import argparse
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="Qwen/Qwen2.5-3B-Instruct")
    ap.add_argument("--out", default="models/summarizer-dpo")
    ap.add_argument("--beta", type=float, default=0.1)
    args = ap.parse_args()

    from datasets import load_dataset
    from peft import LoraConfig
    from trl import DPOConfig, DPOTrainer

    ds = load_dataset("json", data_files=str(DATA / "dpo.jsonl"))["train"].train_test_split(0.1, seed=0)
    config = DPOConfig(
        output_dir=args.out, beta=args.beta, num_train_epochs=1, per_device_train_batch_size=4,
        gradient_accumulation_steps=4, learning_rate=5e-6, logging_steps=10, eval_strategy="epoch",
        bf16=True, max_length=3072,
    )
    trainer = DPOTrainer(
        model=args.base, args=config, train_dataset=ds["train"], eval_dataset=ds["test"],
        peft_config=LoraConfig(r=16, lora_alpha=32, task_type="CAUSAL_LM",
                               target_modules=["q_proj", "k_proj", "v_proj", "o_proj"]),
    )
    trainer.train()
    trainer.save_model(args.out)
    print(f"saved adapter to {args.out}; eval rewards/accuracies is the number to watch")


if __name__ == "__main__":
    main()
