"""
Binary Mangrove Detector Module

Detects whether an image contains mangrove vegetation or not.
Uses a pre-trained EfficientNet-B0 binary classifier.
"""

import numpy as np
import torch
import torch.nn as nn
from pathlib import Path
from PIL import Image
from torchvision import transforms, models

BASE_DIR = Path(__file__).parent
BINARY_MODEL_PATH = BASE_DIR / 'bestBinary.pth'

GPU = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

BINARY_LABELS = ['Non-Mangrove', 'Mangrove']

INFERENCE_TRANSFORM = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406],
                         [0.229, 0.224, 0.225])
])


def build_binary_model(freeze_backbone: bool = True) -> nn.Module:
    """Build the binary mangrove detection model using EfficientNet-B0."""
    model = models.efficientnet_b0(weights=None)
    if freeze_backbone:
        for param in model.parameters():
            param.requires_grad = False
    in_feats = model.classifier[1].in_features
    model.classifier = nn.Sequential(
        nn.Dropout(0.3),
        nn.Linear(in_feats, 128),
        nn.ReLU(),
        nn.Dropout(0.3),
        nn.Linear(128, 2)
    )
    return model


def load_binary_model():
    """Load the pre-trained binary mangrove detection model."""
    if not BINARY_MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Binary model file not found: {BINARY_MODEL_PATH}. "
            "Please train and save the binary model first."
        )

    model = build_binary_model(freeze_backbone=True).to(GPU)
    model.load_state_dict(
        torch.load(str(BINARY_MODEL_PATH), map_location=GPU, weights_only=True)
    )
    model.eval()
    return model


def extract_tiles(img: Image.Image, tile_size: int = 512, overlap: int = 64):
    """
    Slice a large image into overlapping tiles for prediction.
    Returns a tensor of tiles and their positions.
    """
    W, H = img.size
    step = tile_size - overlap
    tiles, positions = [], []

    for top in range(0, max(H - tile_size + 1, 1), step):
        for left in range(0, max(W - tile_size + 1, 1), step):
            box = (left, top,
                   min(left + tile_size, W),
                   min(top + tile_size, H))
            tile = img.crop(box)
            if tile.size != (tile_size, tile_size):
                padded = Image.new('RGB', (tile_size, tile_size), (0, 0, 0))
                padded.paste(tile, (0, 0))
                tile = padded
            tiles.append(INFERENCE_TRANSFORM(tile))
            positions.append(box)

    return torch.stack(tiles), positions


def tile_batches(img, transform, tile_size=512, overlap=64, batch_size=8, pad=False):
    """Preserve the trained tiling convention without retaining every tensor."""
    width, height = img.size
    if not pad and (width < tile_size or height < tile_size):
        yield torch.stack([transform(img)])
        return
    batch = []
    for top in range(0, max(height - tile_size + 1, 1), tile_size - overlap):
        for left in range(0, max(width - tile_size + 1, 1), tile_size - overlap):
            tile = img.crop((left, top, min(left + tile_size, width), min(top + tile_size, height)))
            if pad and tile.size != (tile_size, tile_size):
                padded = Image.new('RGB', (tile_size, tile_size))
                padded.paste(tile, (0, 0))
                tile = padded
            batch.append(transform(tile))
            if len(batch) == batch_size:
                yield torch.stack(batch)
                batch = []
    if batch:
        yield torch.stack(batch)


def predict_binary(image_path: str, model, tile_size: int = 512,
                   overlap: int = 64, batch_size: int = 8,
                   confidence_threshold: float = 0.0) -> dict:
    """
    Run binary mangrove detection on an image.

    Returns a dict with:
        - prediction: 'Mangrove' or 'Non-Mangrove'
        - confidence: confidence score (0-1)
        - probs: [P(Non-Mangrove), P(Mangrove)]
        - n_tiles: number of tiles used
    """
    with Image.open(image_path) as original:
        img = original.convert('RGB')
    probability = []
    n_tiles = 0

    with img, torch.inference_mode():
        for batch in tile_batches(img, INFERENCE_TRANSFORM, tile_size, overlap, batch_size, pad=True):
            n_tiles += len(batch)
            batch = batch.to(GPU)
            probs = torch.softmax(model(batch), dim=1).cpu().numpy()
            if confidence_threshold > 0:
                probs = probs[probs.max(axis=1) >= confidence_threshold]
            if len(probs):
                probability.append(probs)

    if not probability:
        return {
            'prediction': 'Uncertain',
            'confidence': 0.0,
            'probs': [0.5, 0.5],
            'n_tiles': n_tiles
        }

    average_prob = np.concatenate(probability, axis=0).mean(axis=0)
    prediction_index = int(average_prob.argmax())
    return {
        'prediction': BINARY_LABELS[prediction_index],
        'confidence': float(average_prob[prediction_index]),
        'probs': average_prob.tolist(),
        'n_tiles': n_tiles
    }
