"""Ground-truth pages of printed English for measuring OCR CER."""
import json, random
from PIL import Image, ImageDraw, ImageFont, ImageFilter
random.seed(7)
TEXTS = {
 "bio":  ["Photosynthesis is the process by which green plants use light energy",
          "to make organic compounds from carbon dioxide and water.",
          "Overall equation: 6CO2 + 6H2O → C6H12O6 + 6O2.",
          "The light reactions take place in the thylakoid membranes, producing ATP",
          "and NADPH; oxygen is released when water molecules are split.",
          "The Calvin cycle runs in the stroma of the chloroplast.",
          "C4 and CAM plants are adapted to hot and dry conditions."],
 "phys": ["Electromagnetic induction was discovered by Michael Faraday in 1831.",
          "When the magnetic flux through a closed circuit changes, an induced",
          "current flows in it. Magnetic flux Φ = B·A·cosθ, measured in webers (Wb).",
          "Lenz's law: the induced current flows in the direction that opposes",
          "the change in magnetic flux that produced it.",
          "Induced electromotive force: ε = −ΔΦ/Δt, measured in volts (V)."],
 "hist": ["On 4 July 1776, the Second Continental Congress adopted the",
          "Declaration of Independence in Philadelphia, announcing that the",
          "thirteen American colonies no longer considered themselves part of",
          "the British Empire. The war that followed lasted until the Treaty",
          "of Paris was signed in 1783, recognising the United States."],
 "lit":  ["Shakespeare's Hamlet was probably written between 1599 and 1601.",
          "To be, or not to be, that is the question: whether 'tis nobler",
          "in the mind to suffer the slings and arrows of outrageous fortune.",
          "The play explores revenge, madness and moral corruption, and it",
          "remains one of the most performed tragedies in the English language."],
}
FONTS = {"serif": "/usr/share/fonts/noto/NotoSerif-Regular.ttf",
         "sans": "/usr/share/fonts/TTF/Roboto-Regular.ttf",
         "times": "/usr/share/fonts/liberation/LiberationSerif-Regular.ttf",
         "dejavu": "/usr/share/fonts/TTF/DejaVuSans.ttf"}

def render(lines, font, size, bg="white"):
    f = ImageFont.truetype(font, size)
    w = max(int(f.getlength(l)) for l in lines) + 160
    h = int(len(lines) * size * 1.8) + 160
    im = Image.new("RGB", (w, h), bg); d = ImageDraw.Draw(im)
    for i, l in enumerate(lines):
        d.text((80, 80 + i * size * 1.8), l, font=f, fill=(20, 20, 30))
    return im

def photo(im):
    """Phone-photo look: tinted paper, uneven light, slight tilt, blur, JPEG."""
    w, h = im.size
    tint = Image.new("RGB", im.size, (236, 228, 205))
    im = Image.blend(im, tint, 0.0)
    px = im.load()
    for y in range(h):
        for x in range(0, w):
            r, g, b = px[x, y]
            shade = 0.82 + 0.18 * (x / w)
            px[x, y] = (int(min(r, 238) * shade), int(min(g, 230) * shade), int(min(b, 208) * shade))
    im = im.rotate(1.8, expand=True, fillcolor=(120, 115, 100), resample=Image.BICUBIC)
    im = im.filter(ImageFilter.GaussianBlur(1.1))
    return im

pages = [("bio", "serif", 34, False), ("phys", "sans", 30, False), ("hist", "times", 32, False),
         ("lit", "dejavu", 26, False), ("bio", "serif", 34, True), ("phys", "sans", 30, True)]
gt = {}
for topic, font, size, is_photo in pages:
    name = f"{topic}-{font}{'-photo' if is_photo else ''}"
    im = render(TEXTS[topic], FONTS[font], size)
    if is_photo:
        im = photo(im); im.save(f"{name}.jpg", quality=70); gt[name + ".jpg"] = TEXTS[topic]
    else:
        im.save(f"{name}.png"); gt[name + ".png"] = TEXTS[topic]
json.dump(gt, open("ground_truth.json", "w"), ensure_ascii=False, indent=1)
print(list(gt))
