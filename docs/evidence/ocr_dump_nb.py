import sys
sys.path.insert(0, "/app")
from extractors.image_extractor import extract_image

for path in sys.argv[1:]:
    text = extract_image(path)
    print(f"=== {path} ===")
    print(repr(text))
    print()
