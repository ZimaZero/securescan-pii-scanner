import sys, importlib.util
sys.path.insert(0, "/app")

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

before = load("/app/docs/evidence/health_card_detector_before.py", "before_mod")
after = load("/app/detectors/health_card_detector.py", "after_mod")

from extractors.image_extractor import extract_image

images = {
    "telechargement.jpg": "/mnt/c/Users/aleks/OneDrive/Desktop/Shared folder/DEMO/telechargement.jpg",
    "12516554_10207519563300831_634996911_n.jpg": "/mnt/c/Users/aleks/OneDrive/Desktop/Shared folder/DEMO/12516554_10207519563300831_634996911_n.jpg",
    "NB_medicare.png": "/mnt/c/Users/aleks/OneDrive/Desktop/Shared folder/DEMO/NB_medicare.png",
}

for name, path in images.items():
    text = extract_image(path)
    print(f"=== {name} ===")
    print("OCR text:", repr(text))
    print("BEFORE:", before.detect_health_cards(text))
    print("AFTER: ", after.detect_health_cards(text))
    print()
