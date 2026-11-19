"""
Gesichter erkennen und unkenntlich machen (OpenCV YuNet, läuft auf der CPU).

Die Erkennung läuft auf dem Originalbild in mehreren Größen:
  * stark verkleinert  -> große Gesichter direkt vor der Kamera
  * mittel/groß        -> kleinere Gesichter weiter weg
Gefundene Bereiche werden vergrößert, verpixelt und weichgezeichnet.
"""
from __future__ import annotations

import threading
from pathlib import Path

MODEL_NAME = "face_detection_yunet_2023mar.onnx"
DEFAULT_MODEL = Path(__file__).resolve().with_name("models") / MODEL_NAME

_lock = threading.Lock()
_detectors: dict[tuple[str, float], object] = {}


def check_available(model: str | Path | None = None) -> str | None:
    """None, wenn alles da ist – sonst eine Fehlermeldung."""
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return "OpenCV fehlt (pip install opencv-python-headless) – install.sh erneut ausführen"
    m = Path(model) if model else DEFAULT_MODEL
    if not m.is_file():
        return f"Gesichtsmodell fehlt: {m}"
    return None


def _detector(model: Path, threshold: float):
    import cv2
    key = (str(model), threshold)
    det = _detectors.get(key)
    if det is None:
        # score_threshold, nms_threshold, top_k
        det = cv2.FaceDetectorYN.create(str(model), "", (320, 320), threshold, 0.3, 5000)
        _detectors[key] = det
    return det


def _scales(width: int) -> list[int]:
    """Breiten, in denen gesucht wird (nie größer als das Original)."""
    widths = [w for w in (640, 1280, 1920) if w < width]
    if width <= 1920 or not widths:
        widths.append(width)
    return sorted(set(widths))


def _iou(a, b) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax2, bx2) - max(a[0], b[0]))
    ih = max(0.0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def detect(im, threshold: float = 0.6, model: str | Path | None = None) -> list[tuple[float, float, float, float, float]]:
    """Liefert [(x, y, w, h, score), …] in Koordinaten des übergebenen PIL-Bildes."""
    import cv2
    import numpy as np
    model = Path(model) if model else DEFAULT_MODEL
    arr = np.ascontiguousarray(np.asarray(im.convert("RGB"))[:, :, ::-1])   # RGB -> BGR
    H, W = arr.shape[:2]
    found: list[tuple[float, float, float, float, float]] = []
    with _lock:
        det = _detector(model, threshold)
        for tw in _scales(W):
            s = tw / W
            th = max(1, round(H * s))
            small = arr if tw == W else cv2.resize(arr, (tw, th), interpolation=cv2.INTER_AREA)
            det.setInputSize((tw, th))
            _, faces = det.detect(small)
            if faces is None:
                continue
            for f in faces:
                x, y, w, h, score = float(f[0]) / s, float(f[1]) / s, float(f[2]) / s, float(f[3]) / s, float(f[-1])
                found.append((x, y, w, h, score))
    # doppelte Treffer aus verschiedenen Größen zusammenfassen (größeren behalten)
    found.sort(key=lambda b: b[2] * b[3], reverse=True)
    merged: list[tuple[float, float, float, float, float]] = []
    for b in found:
        if all(_iou(b, m) < 0.3 for m in merged):
            merged.append(b)
    return merged


def anonymize(im, boxes, margin: float = 0.4):
    """Verpixelt + weichzeichnet die Bereiche (in place auf einer Kopie). Gibt das neue Bild zurück."""
    from PIL import Image, ImageFilter
    im = im.copy()
    W, H = im.size
    for x, y, w, h, *_ in boxes:
        mx, my = w * margin, h * margin
        x1, y1 = max(0, int(x - mx)), max(0, int(y - my * 1.3))   # oben etwas mehr (Haare/Stirn)
        x2, y2 = min(W, int(x + w + mx)), min(H, int(y + h + my))
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        region = im.crop((x1, y1, x2, y2))
        rw, rh = region.size
        blocks = 6                                                 # ~6 Blöcke über die Gesichtsbreite
        small = region.resize((max(1, rw * blocks // max(1, int(w))), max(1, rh * blocks // max(1, int(w)))),
                              Image.BILINEAR)
        region = small.resize((rw, rh), Image.NEAREST)
        region = region.filter(ImageFilter.GaussianBlur(max(2, rw / 12)))
        im.paste(region, (x1, y1))
    return im
