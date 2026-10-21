"""Trains the 64-16-10 MLP of this demo on the scikit-learn digits (8 x 8, 1,797 images), quantizes it after
training (int8 weights, 4-bit inputs, 4-bit hidden activations, 20-bit accumulators) and writes

    model/weights.json       the integer model the circuit computes (and how it was obtained)
    model/test_digits.json   the test split: 4-bit pixels, labels, and the integer model's prediction per digit

The rest of the demo (mlp_model.py, the Verilog generator, the tests, the dispute demo) needs only these two files
and the standard library. This script needs numpy and scikit-learn (the dataset ships with scikit-learn; nothing is
downloaded). It is deterministic (fixed seeds, full-batch gradient descent in float64).

    python3 train.py            # about 20 s

Quantization (the integer pipeline, mirrored bit for bit by mlp_model.py):
    x_q   = min(pixel, 15)                                     4-bit input (pixels are 0..16; 16 saturates)
    acc1  = sum_k x_q[k] * W1q[j][k] + b1q[j]                  int8 weights, integer bias
    h_q   = min(15, max(0, acc1) >> SHIFT1)                     ReLU, floor shift, 4-bit saturation
    acc2  = sum_i h_q[i] * W2q[c][i] + b2q[c]
    class = argmax_c acc2 (the lowest index on a tie)
W1q = round(W1 * a1), b1q = round(b1 * a1 * 15); W2q = round(W2 * a2), b2q = round(b2 * a2 * 15 * a1 / 2^SHIFT1),
with a1, a2 the largest scales that keep the weights in [-127, 127]. The float network is trained on x_q / 15.
"""
from __future__ import annotations

import json
import os

import numpy as np
from sklearn.datasets import load_digits
from sklearn.model_selection import train_test_split

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = 7
TEST_SIZE = 0.25
EPOCHS = 1500
LR = 0.05
N_HIDDEN, N_OUT, LANES = 16, 10, 16
INPUT_MAX, HIDDEN_MAX = 15, 15
ACC_BITS = 20


def quantize_pixels(images: np.ndarray) -> np.ndarray:
    return np.minimum(images.astype(np.int64), INPUT_MAX)


def train_float(x: np.ndarray, y: np.ndarray, rng: np.random.Generator):
    """Full-batch Adam on softmax cross-entropy; returns (W1, b1, W2, b2)."""
    n_in = x.shape[1]
    W1 = rng.normal(0, np.sqrt(2.0 / n_in), (N_HIDDEN, n_in))
    b1 = np.zeros(N_HIDDEN)
    W2 = rng.normal(0, np.sqrt(2.0 / N_HIDDEN), (N_OUT, N_HIDDEN))
    b2 = np.zeros(N_OUT)
    params = [W1, b1, W2, b2]
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    onehot = np.eye(N_OUT)[y]
    for ep in range(1, EPOCHS + 1):
        z1 = x @ W1.T + b1
        h = np.maximum(z1, 0)
        z2 = h @ W2.T + b2
        z2 -= z2.max(axis=1, keepdims=True)
        p = np.exp(z2)
        p /= p.sum(axis=1, keepdims=True)
        g2 = (p - onehot) / len(x)
        gW2 = g2.T @ h
        gb2 = g2.sum(axis=0)
        gh = (g2 @ W2) * (z1 > 0)
        gW1 = gh.T @ x
        gb1 = gh.sum(axis=0)
        grads = [gW1, gb1, gW2, gb2]
        for i, (prm, g) in enumerate(zip(params, grads)):
            g = g + 1e-4 * prm if prm.ndim == 2 else g       # small L2 on the weights
            m[i] = 0.9 * m[i] + 0.1 * g
            v[i] = 0.999 * v[i] + 0.001 * g * g
            mh = m[i] / (1 - 0.9 ** ep)
            vh = v[i] / (1 - 0.999 ** ep)
            prm -= LR * 0.1 * mh / (np.sqrt(vh) + 1e-8)
    return params


def float_predict(params, x):
    W1, b1, W2, b2 = params
    return np.argmax(np.maximum(x @ W1.T + b1, 0) @ W2.T + b2, axis=1)


def quantize(params, xq_train: np.ndarray, y_train: np.ndarray):
    W1, b1, W2, b2 = params
    a1 = 127.0 / np.abs(W1).max()
    W1q = np.round(W1 * a1).astype(np.int64)
    b1q = np.round(b1 * a1 * INPUT_MAX).astype(np.int64)
    acc1 = xq_train @ W1q.T + b1q
    pos = np.maximum(acc1, 0)
    # the shift that maps the 99th percentile of the (nonzero) hidden pre-activations to the 4-bit range, then the
    # neighbouring shifts: the one with the best integer accuracy on the training set is kept (ties: the larger)
    p99 = np.percentile(pos[pos > 0], 99) if (pos > 0).any() else 1
    s0 = max(0, int(np.ceil(np.log2(max(p99, 1) / HIDDEN_MAX))))
    a2 = 127.0 / np.abs(W2).max()
    W2q = np.round(W2 * a2).astype(np.int64)
    best = None
    for shift1 in range(max(0, s0 - 2), s0 + 2):
        b2q = np.round(b2 * a2 * INPUT_MAX * a1 / (1 << shift1)).astype(np.int64)
        acc = float(np.mean(int_forward(xq_train, W1q, b1q, W2q, b2q, shift1)[3] == y_train))
        if best is None or acc >= best[0]:
            best = (acc, shift1, b2q)
    _, shift1, b2q = best
    return W1q, b1q, W2q, b2q, shift1, a1, a2


def int_forward(xq: np.ndarray, W1q, b1q, W2q, b2q, shift1):
    acc1 = xq @ W1q.T + b1q
    h = np.minimum(np.maximum(acc1, 0) >> shift1, HIDDEN_MAX)
    acc2 = h @ W2q.T + b2q
    return acc1, h, acc2, np.argmax(acc2, axis=1)


def dump_rows(model: dict) -> str:
    """JSON with one weight row per line (readable diffs)."""
    out = {k: v for k, v in model.items() if k not in ("w1", "b1", "w2", "b2")}
    text = json.dumps(out, indent=1)
    rows = []
    for key in ("w1", "b1", "w2", "b2"):
        val = model[key]
        if isinstance(val[0], list):
            body = ",\n  ".join(json.dumps(r) for r in val)
            rows.append(f' "{key}": [\n  {body}\n ]')
        else:
            rows.append(f' "{key}": {json.dumps(val)}')
    return text[:-2] + ",\n" + ",\n".join(rows) + "\n}\n"


def main() -> None:
    digits = load_digits()
    xq_all = quantize_pixels(digits.images.reshape(len(digits.images), -1))
    x_tr, x_te, y_tr, y_te = train_test_split(xq_all, digits.target, test_size=TEST_SIZE, random_state=SEED,
                                              stratify=digits.target)
    rng = np.random.default_rng(SEED)
    params = train_float(x_tr / INPUT_MAX, y_tr, rng)
    f_tr = float(np.mean(float_predict(params, x_tr / INPUT_MAX) == y_tr))
    f_te = float(np.mean(float_predict(params, x_te / INPUT_MAX) == y_te))
    W1q, b1q, W2q, b2q, shift1, a1, a2 = quantize(params, x_tr, y_tr)
    acc1, h, acc2, pred_te = int_forward(x_te, W1q, b1q, W2q, b2q, shift1)
    _, _, _, pred_tr = int_forward(x_tr, W1q, b1q, W2q, b2q, shift1)
    i_te = float(np.mean(pred_te == y_te))
    i_tr = float(np.mean(pred_tr == y_tr))
    bound = 1 << (ACC_BITS - 1)
    assert np.abs(acc1).max() < bound and np.abs(acc2).max() < bound, "accumulators must fit 20 bits"
    assert 64 * INPUT_MAX * 127 + np.abs(b1q).max() < bound and 16 * HIDDEN_MAX * 127 + np.abs(b2q).max() < bound
    model = {
        "format": "digits-mlp-int8/1",
        "nIn": 64, "nHidden": N_HIDDEN, "nOut": N_OUT, "lanes": LANES,
        "inputBits": 4, "hiddenBits": 4, "weightBits": 8, "accBits": ACC_BITS,
        "shift1": shift1,
        "w1": W1q.tolist(), "b1": b1q.tolist(), "w2": W2q.tolist(), "b2": b2q.tolist(),
        "training": {
            "dataset": "sklearn.datasets.load_digits (1,797 images of 8 x 8, pixel values 0..16)",
            "split": {"testSize": TEST_SIZE, "randomState": SEED, "stratified": True,
                      "nTrain": int(len(x_tr)), "nTest": int(len(x_te))},
            "optimizer": f"full-batch Adam, {EPOCHS} epochs, lr {LR * 0.1}, L2 1e-4 on weights, seed {SEED}",
            "floatTrainAccuracy": f_tr, "floatTestAccuracy": f_te,
            "intTrainAccuracy": i_tr, "intTestAccuracy": i_te,
            "scales": {"a1": a1, "a2": a2},
            "hiddenSaturated": float(np.mean(h == HIDDEN_MAX)), "hiddenZero": float(np.mean(h == 0)),
        },
    }
    with open(os.path.join(HERE, "model", "weights.json"), "w") as f:
        f.write(dump_rows(model))
    test = {
        "note": "test split of the sklearn digits; pixels as 64 hex nibbles (row-major, min(pixel, 15)); "
                "intPrediction is the integer model's class (mlp_model.py reproduces it)",
        "pixels": ["".join("%x" % v for v in row) for row in x_te],
        "labels": y_te.tolist(),
        "intPrediction": pred_te.tolist(),
        "floatPrediction": float_predict(params, x_te / INPUT_MAX).tolist(),
    }
    with open(os.path.join(HERE, "model", "test_digits.json"), "w") as f:
        json.dump(test, f, separators=(",", ":"))
    print(f"train {len(x_tr)}  test {len(x_te)}  float acc train {f_tr:.4f} test {f_te:.4f}  "
          f"int acc train {i_tr:.4f} test {i_te:.4f}  shift1 {shift1}  a1 {a1:.2f} a2 {a2:.2f}  "
          f"hidden saturated {model['training']['hiddenSaturated']:.3f} zero {model['training']['hiddenZero']:.3f}")


if __name__ == "__main__":
    main()
