"""Fine-tune ResNet18, then evaluate on a SEPARATE test set.
Layout: data/train/{accident,no_accident}/*.jpg  and  data/test/{accident,no_accident}/*.jpg
Run:  python train.py --data data --epochs 5
"""
import argparse, os
import torch
from torch import nn
from torchvision import datasets, models, transforms as T

ap = argparse.ArgumentParser()
ap.add_argument("--data", default="data")
ap.add_argument("--epochs", type=int, default=5)
a = ap.parse_args()

norm = T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
tr = T.Compose([T.Resize((224, 224)), T.RandomHorizontalFlip(), T.ToTensor(), norm])
te = T.Compose([T.Resize((224, 224)), T.ToTensor(), norm])
# ImageFolder sorts alphabetically: accident=0, no_accident=1. Remap so 1 = accident-related (ai.CLASSES).
tgt = lambda c: 1 if c == 0 else 0
train = datasets.ImageFolder(f"{a.data}/train", tr, target_transform=tgt)
test = datasets.ImageFolder(f"{a.data}/test", te, target_transform=tgt)
dl = torch.utils.data.DataLoader(train, batch_size=32, shuffle=True)
tl = torch.utils.data.DataLoader(test, batch_size=32)

m = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
m.fc = nn.Linear(m.fc.in_features, 2)
opt, lossf = torch.optim.Adam(m.parameters(), 1e-4), nn.CrossEntropyLoss()
for ep in range(a.epochs):
    m.train()
    for x, y in dl:
        opt.zero_grad(); lossf(m(x), y).backward(); opt.step()
    print("epoch", ep + 1, "done")

m.eval(); tp = fp = fn = tn = 0
with torch.no_grad():
    for x, y in tl:
        p = m(x).argmax(1)
        tp += int(((p == 1) & (y == 1)).sum()); fp += int(((p == 1) & (y == 0)).sum())
        fn += int(((p == 0) & (y == 1)).sum()); tn += int(((p == 0) & (y == 0)).sum())
print(f"precision={tp/max(tp+fp,1):.3f} recall={tp/max(tp+fn,1):.3f} false_positive_rate={fp/max(fp+tn,1):.3f}")
os.makedirs("models", exist_ok=True)
torch.save(m.state_dict(), "models/accident_classifier.pt")
print("saved models/accident_classifier.pt")
