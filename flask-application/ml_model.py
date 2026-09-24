import os
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, WeightedRandomSampler
from torchvision import datasets, transforms, models
from tqdm import tqdm

BASE_DIR = Path(__file__).parent
PROJECT_ROOT = BASE_DIR.parent

MODEL_PATH = BASE_DIR / 'best_mangrove_model.pth'
DATASET_PATH = PROJECT_ROOT / 'TrainingData'
TESTING_DIR = PROJECT_ROOT / 'test'


def bmodel(num_classes=3, freeze_base=True, pretrained=True):
    model = models.efficientnet_b0(weights='IMAGENET1K_V1' if pretrained else None)
    if freeze_base:
        for param in model.parameters():
            param.requires_grad = False

    feat = model.classifier[1].in_features
    model.classifier = nn.Sequential(
        nn.Dropout(0.3),
        nn.Linear(feat, 128),
        nn.ReLU(),
        nn.Dropout(0.3),
        nn.Linear(128, num_classes)
    )
    return model


def train_epoch(model, loader, optimizer, criterion, gpu):
    model.train()
    total_loss, correct, total = 0, 0, 0

    for images, labels in tqdm(loader, leave=False):
        images, labels = images.to(gpu), labels.to(gpu)
        optimizer.zero_grad()
        outputs = model(images)
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()

        total_loss += loss.item() * images.size(0)
        correct += (outputs.argmax(1) == labels).sum().item()
        total += images.size(0)

    return total_loss / total, correct / total


def val_epoch(model, loader, criterion, gpu):
    model.eval()
    total_loss, correct, total = 0, 0, 0

    with torch.no_grad():
        for images, labels in loader:
            images, labels = images.to(gpu), labels.to(gpu)
            outputs = model(images)
            loss = criterion(outputs, labels)

            total_loss += loss.item() * images.size(0)
            correct += (outputs.argmax(1) == labels).sum().item()
            total += images.size(0)

    return total_loss / total, correct / total


def extract_test(img, transform, tile_size=512, overlap=64):
    w, h = img.size
    step = tile_size - overlap
    test_images, positions = [], []

    if w < tile_size or h < tile_size:
        test_images.append(transform(img))
        positions.append((0, 0, w, h))
        return torch.stack(test_images), positions

    for top in range(0, h - tile_size + 1, step):
        for left in range(0, w - tile_size + 1, step):
            box = (left, top, left + tile_size, top + tile_size)
            tile = img.crop(box)
            test_images.append(transform(tile))
            positions.append(box)

    return torch.stack(test_images), positions

# Robust image loading that can handle various formats and potential issues
def _load_image_as_pil(image_path):
    try:
        return Image.open(image_path).convert('RGB')
    except Exception:
        pass
    try:
        import tifffile # We used to support tiff images but decided to stop, keeping this here just in case we want to add support back in the future
        arr = tifffile.imread(image_path)
        if arr.ndim == 2:
            # Grayscale -> RGB
            img = Image.fromarray(arr, mode='L')
            return img.convert('RGB')
        elif arr.ndim == 3:
            if arr.shape[2] == 4:
                img = Image.fromarray(arr, mode='RGBA')
                return img.convert('RGB')
            elif arr.shape[2] == 3:
                # RGB -> convert to correct mode (tifffile may load as BGR-like)
                img = Image.fromarray(arr, mode='RGB')
                return img.convert('RGB')
            else:
                # Take first 3 channels
                img = Image.fromarray(arr[:, :, :3], mode='RGB')
                return img.convert('RGB')
        else:
            raise ValueError(f"Unexpected array dimensions: {arr.shape}")
    except Exception as e:
        raise IOError(f"Cannot open image '{image_path}': {e}")


def predicting_test(image_path, model, transform, mangrove_type, gpu, batch_size=8):
    from binary_detector import tile_batches
    img = _load_image_as_pil(image_path)
    all_probs = []

    with img, torch.inference_mode():
        for batch in tile_batches(img, transform, batch_size=batch_size):
            batch = batch.to(gpu)
            outputs = model(batch)
            probs = torch.softmax(outputs, dim=1)
            all_probs.append(probs.cpu().numpy())

    all_probs = np.concatenate(all_probs, axis=0)
    avg_probs = np.mean(all_probs, axis=0)
    pred_idx = np.argmax(avg_probs)
    confidence = avg_probs[pred_idx]

    return mangrove_type[pred_idx], float(confidence), avg_probs


INFERENCE_TRANSFORM = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406],
                         [0.229, 0.224, 0.225])
])


def get_class_names():
    if not DATASET_PATH.exists():
        print(f"⚠️ Dataset path not found: {DATASET_PATH}")
        return ['orange', 'red', 'yellow']

    valid_ext = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.webp'}
    valid_classes = []

    for folder in sorted(DATASET_PATH.iterdir()):
        if folder.is_dir():
            has_images = any(
                f.is_file() and f.suffix.lower() in valid_ext
                for f in folder.iterdir()
            )
            if has_images:
                valid_classes.append(folder.name)
                print(f"Verified class: {folder.name}")
            else:
                print(f"Skipped empty/corrupt folder: {folder.name}")

    if not valid_classes:
        print("No valid classes found. Falling back to defaults.")
        return ['orange', 'red', 'yellow']

    try:
        dataset = datasets.ImageFolder(root=str(DATASET_PATH))
        return dataset.classes
    except RuntimeError as e:
        print(f"⚠️ ImageFolder fallback: {e}")
        return valid_classes


def load_model():
    gpu = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    # The bundled checkpoint was trained with ImageFolder's alphabetical order.
    mangrove_type = ['orange', 'red', 'yellow']

    model = bmodel(num_classes=len(mangrove_type), freeze_base=True, pretrained=False).to(gpu)

    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model file not found: {MODEL_PATH}")

    model.load_state_dict(torch.load(str(MODEL_PATH), map_location=gpu, weights_only=True))
    model.eval()
    return model, mangrove_type, gpu


def train_model(num_epochs_stage1=50, num_epochs_stage2=50, save_model=True):
    if not DATASET_PATH.exists():
        raise FileNotFoundError(f"Dataset path not found: {DATASET_PATH}")

    gpu = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    image_dataset = datasets.ImageFolder(root=str(DATASET_PATH))
    mangrove_type = image_dataset.classes

    total = len(image_dataset)
    if total == 0:
        raise ValueError("Training dataset is empty.")

    training = int(0.8 * total)
    validating = total - training

    train_set, val_set = torch.utils.data.random_split(
        image_dataset,
        [training, validating],
        generator=torch.Generator().manual_seed(42)
    )

    train_transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.RandomHorizontalFlip(),
        transforms.RandomVerticalFlip(),
        transforms.RandomRotation(20),
        transforms.ColorJitter(brightness=0.2, contrast=0.2),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406],
                             [0.229, 0.224, 0.225])
    ])

    val_transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406],
                             [0.229, 0.224, 0.225])
    ])

    train_set.dataset.transform = train_transform
    val_set.dataset.transform = val_transform

    print(f"Total train set size: {len(train_set)} | Total validation set size: {len(val_set)}")

    training_label = [image_dataset.targets[i] for i in train_set.indices]
    counting_class = np.bincount(training_label)
    class_weights = 1.0 / counting_class
    weights = [class_weights[l] for l in training_label]

    sampler = WeightedRandomSampler(
        weights=weights,
        num_samples=len(weights),
        replacement=True
    )

    train_loader = DataLoader(
        train_set,
        batch_size=32,
        sampler=sampler,
        num_workers=0,
        pin_memory=True
    )

    val_loader = DataLoader(
        val_set,
        batch_size=32,
        shuffle=False,
        num_workers=0,
        pin_memory=True
    )

    model = bmodel(num_classes=len(mangrove_type), freeze_base=True).to(gpu)

    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(filter(lambda p: p.requires_grad, model.parameters()), lr=1e-3)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=3)

    best_val_acc = 0
    patience_counter = 0

    print("Starting stage 1 training...")
    for epoch in range(num_epochs_stage1):
        train_loss, train_acc = train_epoch(model, train_loader, optimizer, criterion, gpu)
        val_loss, val_acc = val_epoch(model, val_loader, criterion, gpu)
        scheduler.step(val_loss)

        print(
            f"Stage 1 Epoch {epoch+1:02d}/{num_epochs_stage1} | "
            f"train loss: {train_loss:.4f} acc: {train_acc:.4f} | "
            f"val loss: {val_loss:.4f} acc: {val_acc:.4f}"
        )

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            if save_model:
                torch.save(model.state_dict(), str(MODEL_PATH))
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= 5:
                print("Early stopping stage 1")
                break

    for param in model.parameters():
        param.requires_grad = False
    for param in list(model.features.parameters())[-20:]:
        param.requires_grad = True
    for param in model.classifier.parameters():
        param.requires_grad = True

    optimizer = torch.optim.Adam(filter(lambda p: p.requires_grad, model.parameters()), lr=1e-5)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=3)
    patience_counter = 0

    print("Starting stage 2 fine-tuning...")
    for epoch in range(num_epochs_stage2):
        train_loss, train_acc = train_epoch(model, train_loader, optimizer, criterion, gpu)
        val_loss, val_acc = val_epoch(model, val_loader, criterion, gpu)
        scheduler.step(val_loss)

        print(
            f"Stage 2 Epoch {epoch+1:02d}/{num_epochs_stage2} | "
            f"train loss: {train_loss:.4f} acc: {train_acc:.4f} | "
            f"val loss: {val_loss:.4f} acc: {val_acc:.4f}"
        )

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            if save_model:
                torch.save(model.state_dict(), str(MODEL_PATH))
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= 5:
                print("Early stopping stage 2")
                break

    if save_model and MODEL_PATH.exists():
        model.load_state_dict(torch.load(str(MODEL_PATH), map_location=gpu, weights_only=True))

    model.eval()
    return model, mangrove_type, gpu


def load_or_train_model():
    try:
        return load_model()
    except FileNotFoundError:
        print("No saved model found. Training a new model...")
        return train_model()
    except Exception as e:
        print(f"Error loading model: {e}")
        print("Attempting to train a new model...")
        return train_model()


def predict_mangrove(image_path, model, mangrove_type, gpu):
    try:
        pred_class, confidence, avg_probs = predicting_test(
            image_path=image_path,
            model=model,
            transform=INFERENCE_TRANSFORM,
            mangrove_type=mangrove_type,
            gpu=gpu
        )

        result = {
            'predicted_class': pred_class,
            'confidence': confidence,
            'probabilities': {
                mangrove_type[i]: float(avg_probs[i]) for i in range(len(mangrove_type))
            }
        }

        return result, pred_class, None

    except Exception as e:
        return None, None, str(e)


def predict_combined(image_path, binary_model, model, mangrove_type, gpu):
    """
    Combined pipeline: binary mangrove detection first, then multi-class
    classification only if mangrove is detected.

    Returns a dict with:
        - binary: binary detection result
        - multi_class: multi-class result (or None if no mangrove detected)
    """
    from binary_detector import predict_binary

    # Step 1: Binary detection
    binary_result = predict_binary(image_path, binary_model)

    # Step 2: If mangrove detected, run multi-class classification
    multi_class_result = None
    if binary_result['prediction'] == 'Mangrove':
        pred_class, confidence, avg_probs = predicting_test(
            image_path=image_path,
            model=model,
            transform=INFERENCE_TRANSFORM,
            mangrove_type=mangrove_type,
            gpu=gpu
        )
        multi_class_result = {
            'predicted_class': pred_class,
            'confidence': confidence,
            'probabilities': {
                mangrove_type[i]: float(avg_probs[i]) for i in range(len(mangrove_type))
            }
        }

    return {
        'binary': binary_result,
        'multi_class': multi_class_result
    }


if __name__ == '__main__':
    model, mangrove_type, gpu = load_or_train_model()
    print("Model ready.")
