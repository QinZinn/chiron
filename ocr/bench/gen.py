"""Ground-truth pages of printed Vietnamese for measuring OCR CER."""
import json, random
from PIL import Image, ImageDraw, ImageFont, ImageFilter
random.seed(7)
TEXTS = {
 "sinh": ["Quang hợp là quá trình cây xanh sử dụng năng lượng ánh sáng",
          "để tổng hợp chất hữu cơ từ khí cacbonic và nước.",
          "Phương trình tổng quát: 6CO2 + 6H2O → C6H12O6 + 6O2.",
          "Pha sáng diễn ra ở màng thylakoid, tạo ra ATP và NADPH;",
          "ôxi được giải phóng từ quá trình quang phân li nước.",
          "Pha tối (chu trình Calvin) diễn ra ở chất nền của lục lạp.",
          "Thực vật C4 và CAM thích nghi với điều kiện khô hạn, nắng nóng."],
 "ly":   ["Hiện tượng cảm ứng điện từ được Faraday phát hiện năm 1831.",
          "Khi từ thông qua mạch kín biến thiên, trong mạch xuất hiện",
          "dòng điện cảm ứng. Từ thông Φ = B·S·cosα, đơn vị là vêbe (Wb).",
          "Định luật Len-xơ: dòng điện cảm ứng có chiều sao cho từ trường",
          "do nó sinh ra chống lại sự biến thiên của từ thông ban đầu.",
          "Suất điện động cảm ứng: e = −ΔΦ/Δt, đơn vị là vôn (V)."],
 "su":   ["Ngày 2 tháng 9 năm 1945, tại Quảng trường Ba Đình, Hà Nội,",
          "Chủ tịch Hồ Chí Minh đọc bản Tuyên ngôn Độc lập, khai sinh",
          "nước Việt Nam Dân chủ Cộng hoà. Cách mạng tháng Tám thành công",
          "đã phá tan xiềng xích nô lệ của thực dân Pháp hơn tám mươi năm",
          "và lật đổ chế độ quân chủ tồn tại hàng chục thế kỉ ở nước ta."],
 "van":  ["Truyện Kiều của Nguyễn Du gồm 3254 câu thơ lục bát.",
          "Trăm năm trong cõi người ta, chữ tài chữ mệnh khéo là ghét nhau.",
          "Trải qua một cuộc bể dâu, những điều trông thấy mà đau đớn lòng.",
          "Tác phẩm khắc hoạ số phận bi kịch của người phụ nữ tài sắc",
          "trong xã hội phong kiến, đồng thời thể hiện giá trị nhân đạo sâu sắc."],
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

pages = [("sinh", "serif", 34, False), ("ly", "sans", 30, False), ("su", "times", 32, False),
         ("van", "dejavu", 26, False), ("sinh", "serif", 34, True), ("ly", "sans", 30, True)]
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
