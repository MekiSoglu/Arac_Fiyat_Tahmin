# batch_otoendeks.py
# -*- coding: utf-8 -*-
import time, random, re, sys, difflib
from pathlib import Path
import pandas as pd
from playwright.sync_api import sync_playwright

# ===================== KULLANICI AYARLARI =====================
EXCEL_IN  = r"C:\Users\EXCALIBUR\Desktop\sahibinden\passat_bugun_filtreli_20250911_2100.xlsx"
EXCEL_OUT = r"C:\Users\EXCALIBUR\Desktop\sahibinden\sonuc_test50_full_random__SUPER_ENSEMBLE.xlsx"

SLEEP_CLICK = (0.8, 1.8)   # Tıklamalar arası bekleme
SLEEP_ROW   = (3.0, 6.0)   # Satırlar arası bekleme
HEADLESS    = False        # Debug için tarayıcıyı görünür aç

# Excel sütun adları (dosyana göre uyarlayabilirsin)
COLS = {
    "brand":  "Marka",
    "series": "Seri",        # opsiyonel
    "model":  "Model",
    "year":   "Yıl",
    "km":     "KM",
    "engine": "Motor",       # opsiyonel: "1.6 TDI" / "1.5 TSI" vb.
    "trim":   "Donanım",     # opsiyonel: "Comfortline" / "Impression" vb.
}

# ===================== YARDIMCI FONKSİYONLAR =====================
def human_sleep(a, b): time.sleep(random.uniform(a, b))

def norm_text(s):
    if s is None or (isinstance(s, float) and pd.isna(s)): return None
    return str(s).strip()

def parse_km(x):
    if pd.isna(x): return None
    s = str(x).lower().replace(".", "").replace(",", ".")
    s = re.sub(r"[^\d\.]", "", s)
    if not s: return None
    try: v = float(s)
    except: return None
    return int(round(v))

def parse_tl_to_int(s):
    if not s: return None
    s = str(s).replace(".", "").replace(" ", "")
    s = s.replace("TL", "").replace("₺", "")
    s = s.replace(",", ".")
    m = re.search(r"(\d+)(?:\.\d+)?", s)
    return int(m.group(1)) if m else None

def wait_idle(page):
    try: page.wait_for_load_state("networkidle", timeout=12000)
    except Exception: pass

def click_any(page, texts, timeout=8000):

    if isinstance(texts, str): texts = [texts]

    for t in texts:
        trials = [
            lambda: page.get_by_role("button", name=t).first,
            lambda: page.get_by_role("link", name=t).first,
            lambda: page.locator(".col-md-6, .col-md-4, .col-6").filter(has_text=t).first,
            lambda: page.get_by_text(t, exact=True).first,
        ]
        for fn in trials:
            try: fn().click(timeout=timeout); return True
            except Exception: pass

        pat = re.compile(re.escape(str(t)), re.I)
        trials = [
            lambda: page.get_by_role("button", name=pat).first,
            lambda: page.get_by_role("link", name=pat).first,
            lambda: page.locator(".col-md-6, .col-md-4, .col-6").filter(has_text=pat).first,
            lambda: page.get_by_text(pat).first,
        ]
        for fn in trials:
            try: fn().click(timeout=timeout); return True
            except Exception: pass
    return False

def tr_fold(s: str) -> str:
    table = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return s.translate(table)

def _norm(s: str) -> str:
    s = tr_fold(s.lower())
    s = s.replace("/", " ").replace("-", " ")
    s = re.sub(r"[^\w\. ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _aliases(s: str) -> str:
    s = s.replace("bluemotion", "bluemotion bmt btm blue motion")
    s = s.replace("scr", "scr adblue")
    s = s.replace("dsg", "dsg otomatik")
    s = s.replace("comfortline", "comfortline confotline")  # yaygın yazım hatası
    return s

def _score_match(target: str, cand: str) -> float:
    t = _aliases(_norm(target))
    c = _aliases(_norm(cand))
    seq = difflib.SequenceMatcher(None, t, c).ratio()
    tt = set(t.split()); ct = set(c.split())
    tok = len(tt & ct) / max(1, len(tt))
    nums_t = re.findall(r"\d+(?:\.\d+)?", t)
    num_bonus = 0.0
    if nums_t:
        hit = sum(1 for n in nums_t if n in c)
        num_bonus = 0.2 * (hit / len(nums_t))
    crits = {"tdi","tsi","bluemotion","bmt","btm","comfortline","highline","trendline","elegance","scr","dsg"}
    crit_t = [k for k in crits if k in tt]
    crit_bonus = 0.0
    if crit_t:
        hit = sum(1 for k in crit_t if k in ct)
        crit_bonus = 0.2 * (hit / len(crit_t))
    return 0.6*seq + 0.4*tok + num_bonus + crit_bonus

def fuzzy_click(page, target_text: str, scope_selector: str | None = None, min_score: float = 0.52) -> bool:

    if not target_text: return False
    scope = page.locator(scope_selector) if scope_selector else page
    cand_sel = "button, a, .col-md-6, .col-md-4, .col-6, .list-group-item, .card, .btn"
    loc = scope.locator(cand_sel)

    best = (None, -1.0, None)  # (locator, score, text)
    try: n = loc.count()
    except Exception: n = 0

    for i in range(n):
        el = loc.nth(i)
        try:
            if not el.is_visible(): continue
            txt = el.inner_text(timeout=800).strip()
            if not txt: continue
            sc = _score_match(target_text, txt)
            if sc > best[1]:
                best = (el, sc, txt)
        except Exception:
            continue

    el, sc, txt = best
    if el and sc >= min_score:
        el.click()
        print(f"[FUZZY] '{target_text}' -> '{txt}' (skor={sc:.2f})")
        return True
    print(f"[FUZZY] Eşleşme yok veya düşük skor: hedef='{target_text}', skor={sc:.2f}")
    return False

def set_km_if_present(page, km):
    if km is None: return
    selectors = [
        "input[name*=km i]",
        "input[placeholder*=KM i]",
        "input[placeholder*=Kilometre i]",
        "input[type=number]",
        "input.input-lg",
        "input.form-control",
    ]
    for css in selectors:
        loc = page.locator(css).first
        try:
            loc.wait_for(state="visible", timeout=2000)
            loc.click(); loc.fill(str(km))
            return
        except Exception:
            continue

def read_results_loose(page):

    texts = None
    try: texts = page.inner_text("body")
    except Exception: pass

    def near(label_regex):
        try:
            lab = page.get_by_text(re.compile(label_regex, re.I)).first
            lab.wait_for(state="visible", timeout=2000)
            parent = lab.locator("xpath=..")
            txt = parent.inner_text()
            m = re.search(r"([\d\.\s]+)\s*(TL|₺)", txt)
            if m: return parse_tl_to_int(m.group(0))
        except Exception:
            return None
        return None

    val_oto = near(r"OTOENDEKS\s+DEĞERİ")
    val_ort = near(r"ORT\.?\s*İLAN\s*FİYATI")

    if val_oto is None and texts:
        m = re.search(r"OTOENDEKS\s*DEĞERİ.*?([\d\.\s]+TL)", texts, re.I | re.S)
        if m: val_oto = parse_tl_to_int(m.group(1))
    if val_ort is None and texts:
        m = re.search(r"ORT\.?\s*İLAN\s*FİYATI.*?([\d\.\s]+TL)", texts, re.I | re.S)
        if m: val_ort = parse_tl_to_int(m.group(1))

    return val_oto, val_ort

def process_row(page, row):
    brand  = norm_text(row.get(COLS["brand"]))
    series = norm_text(row.get(COLS["series"])) if COLS.get("series") in row else None
    model  = norm_text(row.get(COLS["model"]))
    year   = row.get(COLS["year"])
    km     = parse_km(row.get(COLS["km"]))
    engine = norm_text(row.get(COLS["engine"])) if COLS.get("engine") in row else None
    trim   = norm_text(row.get(COLS["trim"])) if COLS.get("trim") in row else None

    if not brand or not model or pd.isna(year):
        raise RuntimeError("Zorunlu alan eksik (Marka/Model/Yıl).")

    page.goto("https://otoendeks.com/", wait_until="domcontentloaded")
    wait_idle(page)

    ok = click_any(page, ["İkinci el araç değerleme", "Araç Değeri Sorgula", "Araç değerleme"])
    if not ok:
        ok = fuzzy_click(page, "İkinci el araç değerleme", min_score=0.4) or \
             fuzzy_click(page, "Araç Değeri Sorgula", min_score=0.4)
    if not ok:
        raise RuntimeError("Değerleme giriş butonu/bağlantısı bulunamadı.")
    wait_idle(page); human_sleep(*SLEEP_CLICK)

    if not click_any(page, [brand]):
        if not fuzzy_click(page, brand, min_score=0.55):
            raise RuntimeError(f"Marka bulunamadı: {brand}")
    wait_idle(page); human_sleep(*SLEEP_CLICK)

    target_for_model = series or model
    if not click_any(page, [target_for_model]):
        combos = []
        if engine: combos.append(f"{model} {engine}")
        if trim:   combos.append(f"{model} {trim}")
        combos += [model]
        if series: combos.append(series)

        ok = False
        for t in combos:
            if fuzzy_click(page, t, min_score=0.52):
                ok = True; break
        if not ok:
            raise RuntimeError(f"Model/Seri bulunamadı: {target_for_model}")
    wait_idle(page); human_sleep(*SLEEP_CLICK)

    try_year = str(int(year))
    if not click_any(page, [fr"^{try_year}$", try_year]):
        if not fuzzy_click(page, try_year, min_score=0.60):
            raise RuntimeError(f"Yıl bulunamadı: {year}")
    wait_idle(page); human_sleep(*SLEEP_CLICK)

    if engine or trim:
        target_variant = " ".join([x for x in [model, engine, trim] if x])
        ok = fuzzy_click(page, target_variant, min_score=0.52)
        if not ok and engine:
            ok = fuzzy_click(page, engine, min_score=0.52)
        if not ok and trim:
            ok = fuzzy_click(page, trim, min_score=0.50)
        wait_idle(page); human_sleep(*SLEEP_CLICK)

    set_km_if_present(page, km)
    wait_idle(page); human_sleep(*SLEEP_CLICK)

    click_any(page, ["Fiyatı Hesapla", "Hesapla", "Devam Et", "Sonuç"])
    wait_idle(page); human_sleep(*SLEEP_CLICK)

    val_oto, val_ort = read_results_loose(page)
    return val_oto, val_ort

def main():
    df = pd.read_excel(EXCEL_IN)

    if "OtoendeksDeğeri" not in df.columns:
        df["OtoendeksDeğeri"] = pd.NA
    if "OrtalamaİlanFiyatı" not in df.columns:
        df["OrtalamaİlanFiyatı"] = pd.NA
    if "Durum" not in df.columns:
        df["Durum"] = pd.NA

    Path(EXCEL_OUT).parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(viewport={"width":1280, "height":900})
        page = context.new_page()

        try:
            for idx, row in df.iterrows():
                if pd.notna(df.at[idx, "OtoendeksDeğeri"]) or pd.notna(df.at[idx, "OrtalamaİlanFiyatı"]):
                    continue  # daha önce işlenmiş
                try:
                    oto, ort = process_row(page, row)
                    df.at[idx, "OtoendeksDeğeri"]   = oto
                    df.at[idx, "OrtalamaİlanFiyatı"] = ort
                    df.at[idx, "Durum"] = "OK"
                except Exception as e:
                    df.at[idx, "Durum"] = f"HATA: {e}"
                finally:
                    human_sleep(*SLEEP_ROW)

            df.to_excel(EXCEL_OUT, index=False)
        finally:
            context.close()
            browser.close()

if __name__ == "__main__":
    try:
        main()
        print(f"Tamamlandı. Çıktı: {EXCEL_OUT}")
    except Exception as e:
        print("Genel hata:", e, file=sys.stderr)
        sys.exit(1)
