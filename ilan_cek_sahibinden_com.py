# -*- coding: utf-8 -*-
# Windows + undetected-chromedriver
# - Liste: "Tarihe göre (Önce en yeni ilan)" (yalnız sort-order-menu; görünüm menülerine DOKUNMAZ)
# - Filtreler: yıl >= min_year, km <= max_km, sadece "bugün"; "dün" görülürse durdur
# - Detaya yeni sekmede gir; home'a düşerse 1 kez retry (?from=search)
# - Her detayı anında Excel'e ekle

import os, sys, re, time, random
from datetime import datetime, date, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

import pandas as pd
from bs4 import BeautifulSoup

try:
    import openpyxl
except Exception:
    openpyxl = None

# ============== AYARLAR ==============
PROFILE_DIR = Path(os.environ.get("SBND_PROFILE_DIR", r"C:/Users/EXCALIBUR/ChromeDebug/Profile 16")).resolve()
BASE = "https://www.sahibinden.com"

WAIT_RAND_MIN = 3.0
WAIT_RAND_MAX = 8.0
DETAIL_WAIT_MIN = 60.0
DETAIL_WAIT_MAX = 120.0
BLOCK_WAIT_MIN = 180.0
BLOCK_WAIT_MAX = 300.0

DEBUG = True
def dbg(*a, **kw):
    if DEBUG: print(*a, flush=True, **kw)

try:
    OUT_DIR = Path(__file__).resolve().parent
except NameError:
    OUT_DIR = Path.cwd()

# ============== TR Tarih yardımcıları ==============
TR_MONTHS = {
    "ocak":1,"şubat":2,"subat":2,"mart":3,"nisan":4,"mayıs":5,"mayis":5,"haziran":6,"temmuz":7,
    "ağustos":8,"agustos":8,"eylül":9,"eylul":9,"ekim":10,"kasım":11,"kasim":11,"aralık":12,"aralik":12
}

def parse_tr_date_text(txt: str):
    if not txt: return None
    low = txt.lower().strip()
    today = date.today()
    if "bugün" in low: return today
    if "dün" in low or "dun" in low: return today - timedelta(days=1)
    line = " ".join(re.split(r"\s+", low))
    m = re.search(r"(\d{1,2})\s+([A-Za-zÇĞİÖŞÜçğıöşü]+)\s+(\d{4})", line)
    if not m:
        parts = re.findall(r"(\d{1,2}\s+[A-Za-zÇĞİÖŞÜçğıöşü]+)|(\d{4})", line)
        if parts:
            day_mon, year = None, None
            for a,b in parts:
                if a and not day_mon: day_mon = a
                if b and not year: year = b
            if day_mon and year:
                m = re.match(r"^\s*(\d{1,2})\s+([A-Za-zÇĞİÖŞÜçğıöşü]+)\s+(\d{4})\s*$", f"{day_mon} {year}")
    if not m: return None
    d = int(m.group(1)); mon_name = m.group(2).lower(); y = int(m.group(3))
    mon = TR_MONTHS.get(mon_name)
    if not mon: return None
    try: return date(y, mon, d)
    except: return None

def normalize_km(text: str) -> int | None:
    if not text: return None
    digits = re.sub(r"[^\d]", "", text)
    if not digits: return None
    try: return int(digits)
    except: return None

def _clean_text(s: str) -> str:
    return (s or "").replace("\xa0", " ").strip()

# ============== Selenium ==============
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

SELENIUM_USER_DATA_DIR = str(PROFILE_DIR.parent)
SELENIUM_PROFILE_NAME  = PROFILE_DIR.name

def _pick_chrome_binary():
    for p in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]:
        if Path(p).exists(): return p
    return None

def _new_driver(headless=False):
    opts = uc.ChromeOptions()
    opts.add_argument(f'--user-data-dir={SELENIUM_USER_DATA_DIR}')
    opts.add_argument(f'--profile-directory={SELENIUM_PROFILE_NAME}')
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-features=TranslateUI")
    opts.add_argument("--window-size=1400,900")
    if headless: opts.add_argument("--headless=new")
    binary = _pick_chrome_binary()
    try:
        return uc.Chrome(options=opts, use_subprocess=True, version_main=140, browser_executable_path=binary)
    except Exception:
        return uc.Chrome(options=opts, use_subprocess=True, browser_executable_path=binary)

def accept_cookies(driver):
    try:
        for sel in ["#onetrust-accept-btn-handler","button#onetrust-accept-btn-handler",
                    "button[aria-label='Accept All']","button[aria-label='Tümünü Kabul Et']"]:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els: els[0].click(); time.sleep(0.3); return True
        for xp in ["//button[contains(., 'Tümünü Kabul Et')]","//button[contains(., 'Kabul Et')]",
                   "//button[contains(., 'Anladım')]","//button[contains(., 'Tamam')]",
                   "//a[contains(., 'Kabul Et')]"]:
            els = driver.find_elements(By.XPATH, xp)
            if els: els[0].click(); time.sleep(0.3); return True
    except: pass
    return False

def ensure_logged_in(driver, total_wait=90):
    def is_login_page():
        t = (driver.title or "").lower(); u = (driver.current_url or "").lower()
        return ("giriş" in t) or ("giris" in t) or ("login" in t) or ("/giris" in u)
    if not is_login_page(): return
    print("🔐 Giriş sayfası görünüyor. 90 sn bekleniyor, lütfen giriş yapın...", flush=True)
    start = time.time()
    while time.time()-start < total_wait:
        time.sleep(2)
        if not is_login_page():
            print("✅ Giriş tamam, devam ediyorum.", flush=True); return
    raise RuntimeError("Giriş tamamlanmadı (90 sn).")

def wait_for_any_css(driver, selectors, timeout=60):
    if isinstance(selectors, str): selectors = [selectors]
    def _any(drv):
        for s in selectors:
            try:
                if drv.find_elements(By.CSS_SELECTOR, s): return True
            except: pass
        return False
    WebDriverWait(driver, timeout).until(_any)

def dump_debug(driver, prefix="timeout_page"):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        Path(f"{prefix}_{ts}.html").write_text(driver.page_source, encoding="utf-8")
        driver.save_screenshot(f"{prefix}_{ts}.png")
    except: pass

def get_page(driver, url: str, wait_css, timeout: int = 75):
    driver.get(url)
    time.sleep(0.7)
    accept_cookies(driver)
    ensure_logged_in(driver, total_wait=90)
    selectors = list(wait_css) if isinstance(wait_css, (list,tuple)) else [wait_css]
    selectors += ["table#searchResultsTable","div#searchResultsTable","div.searchResults",
                  "h1.classifiedTitle","div.classifiedDetailTitle h1","div.classifiedInfo"]
    try:
        wait_for_any_css(driver, selectors, timeout=timeout)
    except TimeoutException:
        dump_debug(driver, prefix="timeout_page"); raise
    time.sleep(0.8)
    return driver.page_source, (driver.title or "")

# ---------- home kontrol ----------
def _is_home(url: str) -> bool:
    try:
        u = urlparse(url)
        return u.netloc.endswith("sahibinden.com") and (u.path in ("", "/"))
    except: return False

# ============== SIRALAMA ==============
def enforce_sort_by_latest(driver, query: str | None = None):
    """Sadece sort-order-menu'den 'Tarihe göre' uygular; görünüm butonlarına dokunmaz."""
    menu = None
    try:
        menu = driver.find_element(By.CSS_SELECTOR, "div.sortedTypes ul.sort-order-menu")
    except Exception:
        pass

    if menu:
        try:
            btn = menu.find_element(By.CSS_SELECTOR, "a#advancedSorting")
            driver.execute_script("arguments[0].click();", btn); time.sleep(0.35)
        except Exception:
            pass

        link_el = None
        for css in ["ul.sort-order-menu ul a[href*='sorting=date_desc']",
                    "ul.sort-order-menu ul a[title*='Tarihe göre']"]:
            els = menu.find_elements(By.CSS_SELECTOR, css)
            if els: link_el = els[0]; break

        if link_el:
            href = (link_el.get_attribute("href")
                    or link_el.get_attribute("data-href")
                    or link_el.get_attribute("data-url"))
            if href:
                u = urlparse(href)
                qs = dict(parse_qsl(u.query, keep_blank_values=True))
                if query and "query_text" not in qs: qs["query_text"] = query
                qs.setdefault("pagingSize", "50")
                qs["sorting"] = "date_desc"
                target = urlunparse((u.scheme or "https", u.netloc or "www.sahibinden.com",
                                     u.path or "/otomobil", "", urlencode(qs, doseq=True), ""))
                driver.get(target)
                try:
                    WebDriverWait(driver, 20).until(lambda d: "sorting=date_desc" in d.current_url)
                except Exception:
                    pass
                wait_for_any_css(driver, ["table#searchResultsTable","div#searchResultsTable","div.searchResults"], 60)
                return

    # Fallback: path'i koru; sadece sorting & pagingSize ekle (viewType ekleme)
    u = urlparse(driver.current_url)
    qs = dict(parse_qsl(u.query, keep_blank_values=True))
    if query: qs["query_text"] = query
    qs["sorting"] = "date_desc"
    qs.setdefault("pagingSize", "50")
    fixed = urlunparse((u.scheme or "https", u.netloc or "www.sahibinden.com",
                        u.path or "/otomobil", "", urlencode(qs, doseq=True), ""))
    driver.get(fixed)
    try:
        WebDriverWait(driver, 20).until(lambda d: "sorting=date_desc" in d.current_url)
    except Exception:
        pass
    wait_for_any_css(driver, ["table#searchResultsTable","div#searchResultsTable","div.searchResults"], 60)

# ============== İnsan gibi scroll ==============
def human_like_scroll(driver, step_px=(350,900), pause_s=(0.35,1.1), jitter_up_prob=0.15, max_steps=12):
    try:
        y = 0
        doc_h = driver.execute_script("return document.body.scrollHeight || document.documentElement.scrollHeight;")
        steps = random.randint(6, max_steps)
        for _ in range(steps):
            inc = random.randint(*step_px); y = min(y+inc, doc_h)
            driver.execute_script("window.scrollTo(0, arguments[0]);", y)
            time.sleep(random.uniform(*pause_s))
            if random.random() < jitter_up_prob:
                up = random.randint(40,160)
                driver.execute_script("window.scrollBy(0, arguments[0]);", -up)
                time.sleep(random.uniform(0.2,0.6))
            new_h = driver.execute_script("return document.body.scrollHeight || document.documentElement.scrollHeight;")
            if new_h > doc_h: doc_h = new_h
            if y + 100 >= doc_h: break
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(0.6,1.4))
        driver.execute_script("window.scrollBy(0, -window.innerHeight/3);")
        time.sleep(random.uniform(0.4,0.9))
    except: pass

# ============== Link çıkarımı: '#' asla kabul etme ==============
def _is_bad_href(h: str | None) -> bool:
    if not h: return True
    h = h.strip().lower()
    if h in ("#", "/#", "javascript:void(0)", "javascript:;", "void(0)"): return True
    return False

def _abs_url(h: str) -> str:
    return h if h.startswith("http") else (BASE + h if h.startswith("/") else BASE + "/" + h)

def extract_listing_href(tr) -> str | None:
    # 1) açık href ile gelen ilan linki
    for a in tr.select("a[href]"):
        href = a.get("href", "")
        if "/ilan/" in href and not _is_bad_href(href):
            return _abs_url(href)

    # 2) data-* içinde saklanan link
    for a in tr.select("a"):
        for attr in ("data-href","data-url","data-detail-url"):
            v = a.get(attr)
            if v and "/ilan/" in v:
                return _abs_url(v)

    # 3) tr seviyesinde data-href / data-url
    for attr in ("data-href","data-url"):
        v = tr.get(attr)
        if v and "/ilan/" in v:
            return _abs_url(v)

    # 4) tr data-id -> /ilan/detay?adId=...
    adid = tr.get("data-id") or tr.get("data-ad-id") or tr.get("data-classified-id")
    if adid:
        return f"{BASE}/ilan/detay?adId={adid}"

    return None

# ============== Listeyi oku & filtrele ==============
def parse_row_fields(tr) -> dict:
    href = extract_listing_href(tr)

    # Yıl + KM
    year = None; km = None
    td_list = tr.select("td.searchResultsAttributeValue")

    for td in td_list:
        txt = td.get_text(" ", strip=True)
        m = re.search(r"\b(19\d{2}|20\d{2})\b", txt)
        if m:
            cand = int(m.group(1))
            if 1980 <= cand <= 2035:
                year = cand; break

    km_pattern = re.compile(r"\b(\d{1,3}(?:\.\d{3})+|\d{5,})\b")
    for td in td_list:
        txt = td.get_text(" ", strip=True)
        m = km_pattern.search(txt)
        if m:
            cand = normalize_km(m.group(1))
            if cand and cand >= 1000 and cand != year:
                km = cand; break
    if km is None:
        for td in td_list:
            k = normalize_km(td.get_text(" ", strip=True))
            if k and k >= 1000 and (year is None or k != year):
                km = k; break

    # Tarih
    dt = None
    td_date = tr.select_one("td.searchResultsDateValue")
    if td_date:
        dt = parse_tr_date_text(td_date.get_text(" ", strip=True))

    return {"href": href, "year": year, "km": km, "date": dt}

def collect_links_with_filters(driver, query: str, min_year: int, max_km: int):
    url = f"{BASE}/otomobil?query_text={query.replace(' ', '+')}"
    dbg(f"🧭 URL: {url}")
    time.sleep(random.uniform(WAIT_RAND_MIN, WAIT_RAND_MAX))
    html, _ = get_page(driver, url, ["table#searchResultsTable","div#searchResultsTable","div.searchResults"], 75)

    enforce_sort_by_latest(driver, query=query)
    html, title = get_page(driver, driver.current_url,
                           ["table#searchResultsTable","div#searchResultsTable","div.searchResults"], 75)
    dbg(f"📄 Title: {title}")

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table#searchResultsTable tbody tr.searchResultsItem[data-id]")
    dbg(f"🧩 Liste satırları: {len(rows)}")

    today = date.today()
    out_links = []
    stop_due_to_yesterday = False

    for idx, tr in enumerate(rows, 1):
        if "nativeAd" in " ".join(tr.get("class", [])): continue
        fields = parse_row_fields(tr)
        y, k, dt_, href = fields["year"], fields["km"], fields["date"], fields["href"]

        dbg(f"   ▹ Satır {idx}: yıl={y}, km={k}, tarih={dt_}, link={'OK' if href else '-'}")

        if dt_ is None: continue
        if dt_ < today:
            stop_due_to_yesterday = True
            dbg("   ⛔ 'Dün' veya daha eski ilana gelindi; tarama durduruluyor.")
            break
        if dt_ != today: continue

        if y is not None and y < min_year: dbg("     ↳ yıl eşiğin altında, atlandı."); continue
        if k is not None and k > max_km:   dbg("     ↳ km eşiğin üstünde, atlandı."); continue
        if not href or _is_bad_href(href): dbg("     ↳ link boş/#, atlandı."); continue

        out_links.append(href)

    dbg(f"✅ Aday link sayısı (bugün + filtreler): {len(out_links)}")
    return out_links, stop_due_to_yesterday

# ============== Detayı yeni sekmede aç + parse ==============
PART_NAME_MAP = {
    "front-bumper":"Ön Tampon","front-hood":"Motor Kaputu","roof":"Tavan",
    "front-right-mudguard":"Sağ Ön Çamurluk","front-right-door":"Sağ Ön Kapı",
    "rear-right-door":"Sağ Arka Kapı","rear-right-mudguard":"Sağ Arka Çamurluk",
    "front-left-mudguard":"Sol Ön Çamurluk","front-left-door":"Sol Ön Kapı",
    "rear-left-door":"Sol Arka Kapı","rear-left-mudguard":"Sol Arka Çamurluk",
    "rear-hood":"Bagaj Kapağı","rear-bumper":"Arka Tampon",
}

def _status_from_span_text(txt: str) -> str:
    t = (txt or "").strip().upper()
    if t == "B": return "Boyalı"
    if t == "D": return "Değişen"
    return "Orijinal"

def parse_detail(driver, url: str) -> dict:
    list_handle = driver.current_window_handle
    handles_before = set(driver.window_handles)
    driver.execute_script("window.open(arguments[0], '_blank');", url)
    WebDriverWait(driver, 10).until(lambda d: len(d.window_handles) > len(handles_before))
    new_handle = [h for h in driver.window_handles if h not in handles_before][0]
    driver.switch_to.window(new_handle)

    try:
        accept_cookies(driver)
        try:
            wait_for_any_css(driver, ["h1.classifiedTitle","div.classifiedDetailTitle h1","div.classifiedInfo"], 75)
        except TimeoutException:
            if _is_home(driver.current_url):
                retry = url + ("&" if "?" in url else "?") + "from=search"
                driver.get(retry)
                wait_for_any_css(driver, ["h1.classifiedTitle","div.classifiedDetailTitle h1","div.classifiedInfo"], 75)
            else:
                raise

        human_like_scroll(driver)
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        h1 = soup.find("h1", class_="classifiedTitle")
        title = _clean_text(h1.get_text(strip=True)) if h1 else None

        price_el = (soup.select_one("span.classified-price-wrapper")
                    or soup.select_one("div.classified-price-container span")
                    or soup.select_one("h3.classifiedInfo span")
                    or soup.select_one("h3.classifiedInfo"))
        price = _clean_text(price_el.get_text(strip=True)) if price_el else None

        addr_parts = [_clean_text(a.get_text(" ", strip=True)) for a in soup.select("div.classifiedInfo > h2 a")]
        il   = addr_parts[0] if len(addr_parts) >= 1 else None
        ilce = addr_parts[1] if len(addr_parts) >= 2 else None
        mah  = addr_parts[2] if len(addr_parts) >= 3 else None
        adres_full = " / ".join([p for p in addr_parts if p])

        details = {}
        for li in soup.select("ul.classifiedInfoList li"):
            k = li.find("strong"); v = li.find("span")
            if k and v:
                details[_clean_text(k.get_text(strip=True))] = _clean_text(v.get_text(strip=True))

        parts_tr = {}
        for div in soup.select("div.car-parts > div"):
            classes = div.get("class") or []
            part_key = classes[0] if classes else None
            if part_key not in PART_NAME_MAP:
                part_key = next((c for c in classes if c in PART_NAME_MAP), None)
            if not part_key: continue
            span = div.find("span")
            status_text = span.get_text(strip=True) if span else ""
            parts_tr[PART_NAME_MAP[part_key]] = _status_from_span_text(status_text)

        dbg(f"📝 {title or '(başlık yok)'} — {price or '(fiyat yok)'} — {il or ''}/{ilce or ''}/{mah or ''}")
        return {"Başlık": title, "Fiyat": price, "İl": il, "İlçe": ilce, "Mahalle": mah,
                "Adres": adres_full, **details, **parts_tr, "Link": url}

    finally:
        try: driver.close()
        except: pass
        try: driver.switch_to.window(list_handle)
        except: pass

# ============== Excel'e anında yaz ==============
def append_row_to_excel(xlsx_path: Path, row: dict, sheet_name: str = "Sheet1"):
    if openpyxl is None:
        print("⛔ 'openpyxl' yok. Kurulum: pip install openpyxl", flush=True); raise SystemExit(1)
    df_row = pd.DataFrame([row])
    if not xlsx_path.exists():
        df_row.to_excel(xlsx_path, index=False, sheet_name=sheet_name); return
    with pd.ExcelWriter(xlsx_path, engine="openpyxl", mode="a", if_sheet_exists="overlay") as writer:
        book = writer.book
        if sheet_name not in book.sheetnames:
            df_row.to_excel(writer, index=False, sheet_name=sheet_name, startrow=0, header=True)
        else:
            ws = book[sheet_name]; startrow = ws.max_row; df_row.to_excel(writer, index=False, sheet_name=sheet_name, startrow=startrow, header=False)

# ============== Orkestra ==============
def scrape_to_excel(query: str, min_year: int, max_k_km: int):
    max_km = int(max_k_km) * 1000
    today = date.today()
    print(f"📅 Bugün: {today.strftime('%d %B %Y')}", flush=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    xlsx_path = OUT_DIR / f"{query.lower().replace(' ','')}_bugun_filtreli_{ts}.xlsx"
    print(f"💾 Çıkış: {xlsx_path}", flush=True)

    driver = _new_driver(headless=False)
    details_done = 0
    try:
        links, hit_yesterday = collect_links_with_filters(driver, query, min_year=min_year, max_km=max_km)
        if not links:
            print("⛔ Uygun bugünkü ilan bulunamadı." if hit_yesterday else "⚠ Filtrelere uyan bugünkü ilan bulunamadı.", flush=True)
            return

        print(f"\n🔎 Detaya girilecek ilan sayısı: {len(links)}", flush=True)
        for i, link in enumerate(links, 1):
            print(f"[{i}/{len(links)}] {link}", flush=True)
            try:
                rec = parse_detail(driver, link)
                append_row_to_excel(xlsx_path, rec)
                details_done += 1
                print(f"✅ Excel'e eklendi ({details_done}): {xlsx_path.name}", flush=True)
            except Exception as e:
                print(f"⚠ {type(e).__name__}: {e}", flush=True)
            wait_s = random.uniform(DETAIL_WAIT_MIN, DETAIL_WAIT_MAX)
            print(f"⏳ {wait_s:.0f}s bekleme (detay arası)...", flush=True)
            time.sleep(wait_s)
            if details_done > 0 and details_done % 10 == 0 and i < len(links):
                blk = random.uniform(BLOCK_WAIT_MIN, BLOCK_WAIT_MAX)
                print(f"⏳ {blk/60:.1f} dk bekleme (10 ilanlık blok arası)...", flush=True)
                time.sleep(blk)

        print(f"\n🎉 Bitti. Toplam {details_done} ilan detayları yazıldı: {xlsx_path}", flush=True)
    finally:
        try: driver.quit()
        except: pass

# ============== ÇALIŞTIR ==============
if __name__ == "__main__":
    try:
        query = input("Aramak istediğiniz araç modeli (örn: passat): ").strip()
        year_s = input("En az model yılı (örn: 2015): ").strip()
        km_s   = input("Maks km (bin olarak, örn: 200 => 200.000 km): ").strip()
    except KeyboardInterrupt:
        sys.exit(0)

    if not query or not year_s or not km_s:
        sys.exit("Gerekli girişler verilmedi.")

    try:
        min_year = int(re.sub(r"[^\d]", "", year_s))
        max_k_km = int(re.sub(r"[^\d]", "", km_s))
    except Exception:
        sys.exit("Yıl/KM sayısal olmalı.")

    if openpyxl is None:
        print("⚠ 'openpyxl' yüklü değilse Excel yazılamaz. Kurulum: pip install openpyxl", flush=True)

    scrape_to_excel(query, min_year=min_year, max_k_km=max_k_km)
