import json, time, glob, os
from chiron_ocr import engine
engine.load(); assert engine.status()["ready"], engine.status()
out = {}
for f in sorted(glob.glob("/data/*.png") + glob.glob("/data/*.jpg")):
    t = time.time(); s = engine.recognise(f)
    out[os.path.basename(f)] = {"text": s["text"], "mean_confidence": s["mean_confidence"],
                                "low": s["low_confidence_count"], "secs": round(time.time() - t, 1)}
json.dump(out, open("/data/ocr_out.json", "w"), ensure_ascii=False, indent=1)
print("done", len(out))
