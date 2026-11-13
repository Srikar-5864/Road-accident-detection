"""Image classifier. Uses your trained model if models/accident_classifier.pt exists,
otherwise falls back to zero-shot CLIP. Returns (label, confidence) or None if unavailable."""
import os
from pathlib import Path

MODEL_PATH = Path(os.getenv("MODEL_PATH", Path(__file__).parent / "models" / "accident_classifier.pt"))
CLASSES = ["no visible accident", "accident-related"]
NORMAL = ["a photo of a normal road with regular traffic",
          "a photo of a car driving on a road",
          "a view through a car window of an ordinary road",
          "a photo of an empty road",
          "a photo of a parked car"]
ACCIDENT = ["a photo of a car crash with damaged vehicles",
            "a photo of a road accident with wrecked cars",
            "a photo of overturned or collided vehicles on a road",
            "a photo of an accident scene with emergency responders"]
PROMPTS = NORMAL + ACCIDENT
ACCIDENT_THRESHOLD = 0.80   # at or above: accident-related; lower it if real crashes are missed
UNCERTAIN_THRESHOLD = 0.50  # between the two: flagged for human review
_model = _clip = None
_tried = False


def _load():
    global _model, _clip, _tried
    if _tried:
        return
    _tried = True
    try:
        import torch
        if MODEL_PATH.exists():
            from torchvision import models
            m = models.resnet18(weights=None)
            m.fc = torch.nn.Linear(m.fc.in_features, 2)
            m.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
            m.eval()
            _model = m
        else:
            from transformers import CLIPModel, CLIPProcessor
            name = "openai/clip-vit-base-patch32"
            _clip = (CLIPModel.from_pretrained(name).eval(), CLIPProcessor.from_pretrained(name))
    except Exception as e:
        print("AI model unavailable:", e)


def classify(path: str):
    _load()
    try:
        import torch
        from PIL import Image
        img = Image.open(path).convert("RGB")
        with torch.no_grad():
            if _model is not None:
                from torchvision import transforms as T
                tf = T.Compose([T.Resize((224, 224)), T.ToTensor(),
                                T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
                p = torch.softmax(_model(tf(img).unsqueeze(0)), 1)[0]
            elif _clip is not None:
                model, proc = _clip
                inp = proc(text=PROMPTS, images=img, return_tensors="pt", padding=True)
                p = model(**inp).logits_per_image.softmax(1)[0]
                acc = float(p[len(NORMAL):].sum())
                if acc >= ACCIDENT_THRESHOLD:
                    return CLASSES[1], acc
                if acc >= UNCERTAIN_THRESHOLD:
                    return "uncertain - needs human review", acc
                return CLASSES[0], 1 - acc
            else:
                return None
        i = int(p.argmax())
        return CLASSES[i], float(p[i])
    except Exception as e:
        print("AI inference failed:", e)
        return None
