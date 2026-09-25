"""CER of OCR output against ground truth, with Vietnamese diacritic errors split out."""
import json, sys, unicodedata as ud

def norm(s):
    return " ".join(ud.normalize("NFC", s).split())

def base(c):
    c = c.replace("đ", "d").replace("Đ", "D")
    return "".join(x for x in ud.normalize("NFD", c) if not ud.combining(x))

def align(r, h):
    n, m = len(r), len(h)
    D = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): D[i][0] = i
    for j in range(m + 1): D[0][j] = j
    for i in range(1, n + 1):
        ri = r[i - 1]; row, prev = D[i], D[i - 1]
        for j in range(1, m + 1):
            row[j] = min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (ri != h[j - 1]))
    i, j, ops = n, m, []
    while i or j:
        if i and j and D[i][j] == D[i-1][j-1] + (r[i-1] != h[j-1]):
            if r[i-1] != h[j-1]: ops.append(("sub", r[i-1], h[j-1]))
            i, j = i - 1, j - 1
        elif i and D[i][j] == D[i-1][j] + 1: ops.append(("del", r[i-1], "")); i -= 1
        else: ops.append(("ins", "", h[j-1])); j -= 1
    return D[n][m], ops[::-1]

def is_marked(c):  # carries a Vietnamese diacritic (tone or vowel mark, or đ)
    return base(c) != c

gt = json.load(open(sys.argv[1])); ocr = json.load(open(sys.argv[2]))
tot = {"chars": 0, "err": 0, "dia": 0, "marked": 0, "marked_wrong": 0}
rows = []
for name, lines in gt.items():
    ref, hyp = norm(" ".join(lines)), norm(ocr[name]["text"])
    dist, ops = align(ref, hyp)
    dia = sum(1 for k, a, b in ops if k == "sub" and base(a) == base(b))
    marked = sum(1 for c in ref if is_marked(c))
    # marked chars in ref that were not reproduced exactly
    marked_wrong = sum(1 for k, a, b in ops if k in ("sub", "del") and is_marked(a))
    rows.append((name, len(ref), dist, dist / len(ref), dia, marked, marked_wrong, ocr[name]["mean_confidence"], ocr[name]["secs"], ops))
    for k, v in zip(tot, (len(ref), dist, dia, marked, marked_wrong)): tot[k] += v
print(f"{'trang':24} {'ký tự':>6} {'lỗi':>4} {'CER':>7} {'lỗi dấu':>7} {'ký tự có dấu sai':>17} {'conf':>6} {'giây':>5}")
for name, n, dist, cer, dia, marked, mw, conf, secs, _ in rows:
    print(f"{name:24} {n:6} {dist:4} {cer:7.2%} {dia:7} {f'{mw}/{marked}':>17} {conf:6.3f} {secs:5}")
print(f"{'TỔNG':24} {tot['chars']:6} {tot['err']:4} {tot['err']/tot['chars']:7.2%} {tot['dia']:7} {str(tot['marked_wrong'])+'/'+str(tot['marked']):>17}")
print("\nChi tiết lỗi (ref → ocr):")
for name, *_, ops in rows:
    print(" ", name + ":", "; ".join(f"{k} {a!r}→{b!r}" for k, a, b in ops) or "không lỗi")

def kind(a):
    if a in "ΦΔαβγ→−+=·/×": return "ký hiệu/công thức"
    if a in ".,;:!?()\"' ": return "dấu câu/khoảng trắng"
    return "chữ cái/chữ số"
from collections import Counter
c = Counter(); letters = sum(1 for n, lines in gt.items() for ch in norm(" ".join(lines)) if kind(ch) == "chữ cái/chữ số")
for *_, ops in rows:
    for k, a, b in ops: c[kind(a) if a else "chèn thừa"] += 1
print("\nPhân loại lỗi:", dict(c))
print(f"CER chỉ tính chữ cái/chữ số: {c['chữ cái/chữ số']}/{letters} = {c['chữ cái/chữ số']/letters:.2%}")
