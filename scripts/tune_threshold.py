"""Pick CACHE_THRESHOLD for the real embedding model.
Run:  pip install fastembed numpy  &&  python scripts/tune_threshold.py
Paraphrases should score ABOVE the threshold, different questions BELOW it."""
import sys

import numpy as np

sys.path.insert(0, ".")
from app.cache import FastEmbedder  # noqa: E402

e = FastEmbedder("BAAI/bge-small-en-v1.5")
base = "What is Docker used for?"
should_hit = ["What is Docker used for", "Why do people use Docker?", "what are docker containers for"]
should_miss = ["Explain Kubernetes pods", "What is Docker Compose?", "How do I bake bread?"]

a = e.embed(base)
for label, group in [("HIT ", should_hit), ("MISS", should_miss)]:
    for t in group:
        print(f"{label} {float(np.dot(a, e.embed(t))):.3f}  {t}")
print("\nChoose a threshold between the lowest HIT and the highest MISS score.")
