import json
import sys

targets = {
    "telechargement.jpg",
    "12516554_10207519563300831_634996911_n.jpg",
    "NB_medicare.png",
}

for path in sys.argv[1:]:
    with open(path) as f:
        d = json.load(f)
    print(f"##### {path}")
    for r in d.get("files", []):
        base = r.get("file", "").split("/")[-1]
        if base not in targets:
            continue
        print(f"=== {base}  score={r.get('score')}")
        for cat, items in r.get("matches", {}).items():
            if cat.startswith("identifier"):
                print("   ", cat, [i.get("value") for i in items])
    print()
