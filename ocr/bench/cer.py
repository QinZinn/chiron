"""CER of OCR output against ground truth, with Vietnamese diacritic errors split out."""
import json, sys, unicodedata as ud

# Old and new Vietnamese tone placement are both correct spelling ("hoá" /
# "hóa", "luỹ" / "lũy"), so neither the writer nor the OCR is wrong for picking
# one. Both sides are rewritten to the new style before comparing.
_OLD_TO_NEW = [("oà", "òa"), ("oá", "óa"), ("oả", "ỏa"), ("oã", "õa"), ("oạ", "ọa"),
               ("oè", "òe"), ("oé", "óe"), ("oẻ", "ỏe"), ("oẽ", "õe"), ("oẹ", "ọe"),
               ("uỳ", "ùy"), ("uý", "úy"), ("uỷ", "ủy"), ("uỹ", "ũy"), ("uỵ", "ụy")]


def norm(s, lower=False):
    s = " ".join(ud.normalize("NFC", s).split())
    for old, new in _OLD_TO_NEW:
        s = s.replace(old, new).replace(old.upper(), new.upper()).replace(old.capitalize(), new.capitalize())
    return s.lower() if lower else s

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

LOWER = "--lower" in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith("--")]
gt = json.load(open(args[0])); ocr = json.load(open(args[1]))
tot = {"chars": 0, "err": 0, "dia": 0, "marked": 0, "marked_wrong": 0}
rows = []
for name, lines in gt.items():
    ref, hyp = norm(" ".join(lines), LOWER), norm(ocr[name]["text"], LOWER)
    dist, ops = align(ref, hyp)
    dia = sum(1 for k, a, b in ops if k == "sub" and base(a) == base(b))
    marked = sum(1 for c in ref if is_marked(c))
    # marked chars in ref that were not reproduced exactly
    marked_wrong = sum(1 for k, a, b in ops if k in ("sub", "del") and is_marked(a))
    rows.append((name, len(ref), dist, dist / len(ref), dia, marked, marked_wrong, ocr[name]["mean_confidence"], ocr[name].get("secs", "—"), ops))
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
c = Counter(); letters = sum(1 for n, lines in gt.items() for ch in norm(" ".join(lines), LOWER) if kind(ch) == "chữ cái/chữ số")
for *_, ops in rows:
    for k, a, b in ops: c[kind(a) if a else "chèn thừa"] += 1
print("\nPhân loại lỗi:", dict(c))
print(f"CER chỉ tính chữ cái/chữ số: {c['chữ cái/chữ số']}/{letters} = {c['chữ cái/chữ số']/letters:.2%}")


# -- order-free word accuracy ----------------------------------------------
# Levenshtein punishes reading order: on a Cornell page the cue column can come
# out before or after the notes it sits beside, and every character of it then
# counts as an error although it was read right. Matching words as a multiset
# ignores order and measures recognition alone.
import re
from collections import Counter as _C

def words(text):
    return re.findall(r"[^\W_]+", norm(text, True))

print("\nTừ đọc đúng (không tính thứ tự, không phân biệt hoa thường):")
tw = tm = td = 0
for name, lines in gt.items():
    ref, hyp = _C(words(" ".join(lines))), _C(words(ocr[name]["text"]))
    exact = sum((ref & hyp).values())
    left_ref, left_hyp = ref - hyp, hyp - ref
    by_base = _C()
    for w, n in left_hyp.items():
        by_base[base(w)] += n
    dia = 0
    for w, n in left_ref.items():
        k = min(n, by_base[base(w)])
        dia += k; by_base[base(w)] -= k
    total = sum(ref.values())
    tw += total; tm += exact; td += dia
    print(f"  {name:24} {exact}/{total} = {exact/total:.1%} đúng hẳn; thêm {dia} từ đúng chữ nhưng sai dấu")
print(f"  TỔNG {tm}/{tw} = {tm/tw:.1%} đúng hẳn; {td} từ ({td/tw:.1%}) chỉ sai dấu; {tw-tm-td} từ ({(tw-tm-td)/tw:.1%}) sai chữ hoặc mất")
