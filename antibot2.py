# -*- coding: utf-8 -*-
"""
sahibinden_click_pagination_no_paging_testurl.py

Değişiklikler (istenenler):
- Sayfa geçişi (pagination) DEVRE DIŞI bırakıldı: yalnızca ilk sayfa işlenir.
- Test için varsayılan bir başlangıç URL'si eklendi ve AKTİF:
  https://www.sahibinden.com/volkswagen-passat/otomatik/sahibinden?date=1day&pagingSize=50&a116445=1263354&a4_max=200000&a5_min=2015

UYARI: Yukarıdaki test URL’si robots.txt'deki bazı Disallow kurallarıyla çakışabilir (ör. date=, pagingSize=, &a...).
Kullanım koşullarını/robots'u ihlal etmemek için bu ayarı kapatıp (USE_TEST_URL=False) parametresiz/temiz URL ile çalıştırın.
"""

import os, sys, re, time, random
from datetime import datetime, date, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from bs4 import BeautifulSoup

try:
    import openpyxl
except Exception:
    openpyxl = None

# ================== AYARLAR ==================
BASE = "https://www.sahibinden.com"
PROFILE_DIR = Path(os.environ.get("SBND_PROFILE_DIR", r"C:/Users/EXCALIBUR/ChromeDebug/Profile 16")).resolve()

WAIT_RAND_MIN = 2.0
WAIT_RAND_MAX = 4.0
DETAIL_WAIT_MIN = 35.0
DETAIL_WAIT_MAX = 80.0
BLOCK_WAIT_MIN = 120.0
BLOCK_WAIT_MAX = 240.0

# --- HEDEF TARİH AYARI (yalnızca 21 Eylül 2025 ilanları) ---
TARGET_DATE = date(2025, 9, 21)
RELATIVE_TODAY = TARGET_DATE + timedelta(days=1)

TR_MONTHS_TR = ["", "Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]

def fmt_tr_date(d):
    return f"{d.day} {TR_MONTHS_TR[d.month]} {d.year}"


# --- Yeni bayraklar ---
DISABLE_PAGING = True  # 1) Sayfa geçişi tamamen kapalı
USE_TEST_URL = True     # 2) Varsayılan başlangıç URL'si olarak TEST_URL'i kullan
APPLY_SORT_MENU = False # Sıralama menüsünü tıklama (date/sorting paramı eklenebilir)
APPLY_SELLER_FILTER_MENU = False  # Kimden: Sahibinden menü tıklaması. Test URL'sinde zaten path ile veriliyor

TEST_URL = (
    "https://www.sahibinden.com/volkswagen-passat/otomatik/sahibinden"
    "?date=1day&pagingSize=50&a116445=1263354&a4_max=200000&a5_min=2015"
)

DEBUG = True
def dbg(*a, **kw):
    if DEBUG: print(*a, flush=True, **kw)

try:
    OUT_DIR = Path(__file__).resolve().parent
except NameError:
    OUT_DIR = Path.cwd()

# ============== TR tarih yardımcıları ==============
TR_MONTHS = {
    "ocak":1,"şubat":2,"subat":2,"mart":3,"nisan":4,"mayıs":5,"mayis":5,"haziran":6,"temmuz":7,
    "ağustos":8,"agustos":8,"eylül":9,"eylul":9,"ekim":10,"kasım":11,"kasim":11,"aralık":12,"aralik":12
}

def parse_tr_date_text(txt: str):
    if not txt: return None
    low = txt.lower().strip()
    today = RELATIVE_TODAY
    if "bugün" in low: return today
    if "dün" in low or "dun" in low: return today - timedelta(days=1)
    line = " ".join(re.split(r"\s+", low))
    m = re.search(r"(\d{1,2})\s+([A-Za-zÇĞİÖŞÜçğıöşü]+)\s+(\d{4})", line)
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

# ================== Selenium (Chrome) ==================
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


def _pick_chrome_binary():
    for p in [r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
              r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe"]:
        if Path(p).exists(): return p
    return None


def _new_driver(headless=False):
    opts = ChromeOptions()
    # var olan profil (cookie/login) kullan
    opts.add_argument(f'--user-data-dir={PROFILE_DIR.parent}')
    opts.add_argument(f'--profile-directory={PROFILE_DIR.name}')
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-translate")
    opts.add_argument("--window-size=1400,900")
    if headless:
        opts.add_argument("--headless=new")
    binary = _pick_chrome_binary()
    if binary:
        opts.binary_location = binary
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(75)
    return driver


def accept_cookies(driver):
    try:
        time.sleep(random.uniform(0.9, 3.7))
        if random.random() < 0.12:
            return False
        if random.random() < 0.6:
            driver.execute_script("window.scrollBy(0, arguments[0]);", random.randint(60, 600))
            time.sleep(random.uniform(0.2, 1.0))
        css_candidates = [
            "#onetrust-accept-btn-handler",
            "button#onetrust-accept-btn-handler",
            "button[aria-label*='Kabul']",
            "button[aria-label*='Accept']",
            "button:contains('Tümünü Kabul Et')",
        ]
        xpaths = [
            "//button[contains(., 'Tümünü Kabul Et')]",
            "//button[contains(., 'Kabul Et')]",
            "//button[contains(., 'Anladım')]",
            "//button[contains(., 'Tamam')]",
            "//a[contains(., 'Kabul Et')]",
        ]
        random.shuffle(css_candidates); random.shuffle(xpaths)
        def _hover_and_click(el):
            try:
                ActionChains(driver).move_to_element(el).pause(random.uniform(0.25, 0.9)).click().perform()
                time.sleep(random.uniform(0.6, 1.8))
                return True
            except Exception:
                return False
        for sel in css_candidates:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els and _hover_and_click(random.choice(els)):
                return True
        for xp in xpaths:
            els = driver.find_elements(By.XPATH, xp)
            if els and _hover_and_click(random.choice(els)):
                return True
    except Exception:
        pass
    return False


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
    selectors = list(wait_css) if isinstance(wait_css, (list,tuple)) else [wait_css]
    selectors += ["table#searchResultsTable","div#searchResultsTable","div.searchResults"]
    try:
        wait_for_any_css(driver, selectors, timeout=timeout)
    except TimeoutException:
        dump_debug(driver, prefix="timeout_page"); raise
    time.sleep(0.6)
    return driver.page_source, (driver.title or "")


def _is_home(url: str) -> bool:
    try:
        u = urlparse(url)
        return u.netloc.endswith("sahibinden.com") and (u.path in ("", "/"))
    except: return False

# ================== Sıralamayı MENÜDEN TIKLA (bayrakla kontrol) ==================

def enforce_sort_by_latest_click(driver):
    if not APPLY_SORT_MENU:
        dbg("↷ Sıralama menüsü atlanıyor (APPLY_SORT_MENU=False)")
        return
    menu_btn = None
    for sel in ["#advancedSorting", "a#advancedSorting", "div.sortedTypes a#advancedSorting"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            menu_btn = els[0]; break
    if menu_btn:
        try:
            driver.execute_script("arguments[0].click();", menu_btn)
            time.sleep(0.4)
        except Exception:
            pass
    sort_link = None
    for css in [
        "ul.sort-order-menu ul a[title*='Tarihe göre']",
        "ul.sort-order-menu ul a[href*='sorting=date_desc']",
        "a[data-sort='date_desc']",
    ]:
        els = driver.find_elements(By.CSS_SELECTOR, css)
        if els:
            sort_link = els[0]; break
    if sort_link:
        _scroll_into_view_and_click(driver, sort_link, timeout=60)
        time.sleep(random.uniform(0.5, 1.2))
    else:
        dbg("⚠ Sıralama linki bulunamadı; mevcut sıralama ile devam.")


def _scroll_into_view_and_click(driver, el, timeout=30):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        time.sleep(0.2)
        try:
            el.click()
        except Exception:
            ActionChains(driver).move_to_element(el).pause(0.1).click().perform()
    except Exception as e:
        raise e
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "table#searchResultsTable tbody tr.searchResultsItem"))
    )

# ================== "Kimden: Sahibinden" (bayrakla kontrol) ==================

def enforce_seller_filter_sahibinden(driver):
    if not APPLY_SELLER_FILTER_MENU:
        dbg("↷ 'Kimden: Sahibinden' menü tıklaması atlanıyor (APPLY_SELLER_FILTER_MENU=False)")
        return
    dbg("🎯 Kimden filtresi: 'Sahibinden' uygulanıyor...")
    try:
        driver.execute_script("window.scrollTo(0, 0);")
    except: pass
    container = None
    for css in [
        "div.search-filter-section[data-name='a706']",
        "#searchResultLeft-a706",
        "div[data-name='a706']",
    ]:
        els = driver.find_elements(By.CSS_SELECTOR, css)
        if els:
            container = els[0]; break
    try:
        if container:
            title_dt = None
            for sel in ["dt.collapseTitle", "dt#_cllpsID_a706", "dt[class*='collapseTitle']"]:
                es = container.find_elements(By.CSS_SELECTOR, sel)
                if es:
                    title_dt = es[0]; break
            if title_dt:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", title_dt)
                is_open = False
                try:
                    dd = container.find_element(By.CSS_SELECTOR, "dd.collapseContent")
                    is_open = bool(dd.find_elements(By.CSS_SELECTOR, "a"))
                except: pass
                if not is_open:
                    try:
                        title_dt.click(); time.sleep(0.3)
                    except:
                        ActionChains(driver).move_to_element(title_dt).pause(0.1).click().perform()
    except: pass
    link = None
    for css in [
        "div.search-filter-section[data-name='a706'] a[title*='Sahibinden']",
        "#searchResultLeft-a706 a[title*='Sahibinden']",
        "ul.facetedSearchList.a706 a[title*='Sahibinden']",
        "a.js-attribute.facetedRadiobox.single-selection[title*='Sahibinden']",
        "a[data-section='a706'][data-value='32474']",
    ]:
        els = driver.find_elements(By.CSS_SELECTOR, css)
        if els:
            link = els[0]; break
    if not link:
        dbg("⚠ 'Sahibinden' linki bulunamadı; mevcut durum ile devam."); return
    try:
        cls = link.get_attribute("class") or ""
        already = ("checked" in cls) or ("selected" in cls)
    except:
        already = False
    old_first_id = _first_row_id(driver)
    if not already:
        try:
            _scroll_into_view_and_click(driver, link, timeout=60)
        except Exception as e:
            dbg(f"⚠ Kimden tıklamada hata: {e}")
        time.sleep(random.uniform(0.5, 1.2))
    try:
        WebDriverWait(driver, 30).until(lambda d: _first_row_id(d) != old_first_id)
    except Exception:
        pass

# ================== İnsan gibi scroll ==================

def human_like_scroll(driver, step_px=(350,900), pause_s=(0.25,0.9), jitter_up_prob=0.15, max_steps=10):
    try:
        y = 0
        doc_h = driver.execute_script("return document.body.scrollHeight || document.documentElement.scrollHeight;")
        steps = random.randint(5, max_steps)
        for _ in range(steps):
            inc = random.randint(*step_px); y = min(y+inc, doc_h)
            driver.execute_script("window.scrollTo(0, arguments[0]);", y)
            time.sleep(random.uniform(*pause_s))
            if random.random() < jitter_up_prob:
                up = random.randint(40,160)
                driver.execute_script("window.scrollBy(0, arguments[0]);", -up)
                time.sleep(random.uniform(0.15,0.45))
            new_h = driver.execute_script("return document.body.scrollHeight || document.documentElement.scrollHeight;")
            if new_h > doc_h: doc_h = new_h
            if y + 100 >= doc_h: break
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(0.4,1.0))
        driver.execute_script("window.scrollBy(0, -window.innerHeight/3);")
        time.sleep(random.uniform(0.3,0.8))
    except: pass

# ================== Pagination Yardımcıları (KULLANILMIYOR) ==================

def _find_pagination_container(driver):
    for css in [
        "ul.pageNaviButtons",
        "div#searchResultsTable ul.pageNaviButtons",
        "div.searchResults ul.pageNaviButtons",
    ]:
        els = driver.find_elements(By.CSS_SELECTOR, css)
        if els:
            return els[0]
    return None


def click_next_page(driver) -> bool:
    """Devre dışı: DISABLE_PAGING=True iken kullanılmıyor."""
    if DISABLE_PAGING:
        return False
    cont = _find_pagination_container(driver)
    if not cont: return False
    nxt = None
    for sel in [
        "a.prevNextBut[title*='Sonraki']",
        "a[title='Sonraki']",
        "a[rel='next']",
    ]:
        els = cont.find_elements(By.CSS_SELECTOR, sel)
        if els:
            nxt = els[0]; break
    if nxt is None:
        cur = None
        try:
            cur = cont.find_element(By.CSS_SELECTOR, "span.currentPage")
        except Exception:
            pass
        if cur:
            cur_val = (cur.text or "").strip()
            if cur_val.isdigit():
                target_num = str(int(cur_val) + 1)
                for a in cont.find_elements(By.CSS_SELECTOR, "a[href]"):
                    if (a.text or "").strip() == target_num:
                        nxt = a; break
    if nxt is None:
        return False
    old_first_id = None
    try:
        old_first_id = driver.find_elements(By.CSS_SELECTOR, "table#searchResultsTable tbody tr.searchResultsItem[data-id]")[0].get_attribute("data-id")
    except:
        pass
    _scroll_into_view_and_click(driver, nxt, timeout=60)
    time.sleep(random.uniform(0.5, 1.2))
    try:
        WebDriverWait(driver, 30).until(
            lambda d: _first_row_id(d) != old_first_id
        )
    except Exception:
        pass
    return True


def _first_row_id(driver):
    try:
        el = driver.find_elements(By.CSS_SELECTOR, "table#searchResultsTable tbody tr.searchResultsItem[data-id]")[0]
        return el.get_attribute("data-id")
    except:
        return None

# ================== Liste satırı parse ==================

def extract_listing_href(tr) -> str | None:
    for a in tr.select("a[href]"):
        href = a.get("href", "")
        if "/ilan/" in href and href not in ("#", "/#", "javascript:void(0)"):
            return href if href.startswith("http") else (BASE + href if href.startswith("/") else BASE + "/" + href)
    for a in tr.select("a"):
        for attr in ("data-href","data-url","data-detail-url"):
            v = a.get(attr)
            if v and "/ilan/" in v:
                return v if v.startswith("http") else (BASE + v if v.startswith("/") else BASE + "/" + v)
    adid = tr.get("data-id") or tr.get("data-ad-id") or tr.get("data-classified-id")
    if adid:
        return f"{BASE}/ilan/detay?adId={adid}"
    return None


def parse_row_fields(tr) -> dict:
    href = extract_listing_href(tr)
    year = None
    for td in tr.select("td.searchResultsAttributeValue"):
        txt = td.get_text(" ", strip=True)
        m = re.search(r"\b(19\d{2}|20\d{2})\b", txt)
        if m:
            cand = int(m.group(1))
            if 1980 <= cand <= 2035:
                year = cand; break
    km = None
    km_pattern = re.compile(r"\b(\d{1,3}(?:\.\d{3})+|\d{5,})\b")
    for td in tr.select("td.searchResultsAttributeValue"):
        txt = td.get_text(" ", strip=True)
        m = km_pattern.search(txt)
        if m:
            cand = normalize_km(m.group(1))
            if cand and cand >= 1000 and cand != year:
                km = cand; break
    if km is None:
        for td in tr.select("td.searchResultsAttributeValue"):
            k = normalize_km(td.get_text(" ", strip=True))
            if k and k >= 1000 and (year is None or k != year):
                km = k; break
    dt = None
    td_date = tr.select_one("td.searchResultsDateValue")
    if td_date:
        dt = parse_tr_date_text(td_date.get_text(" ", strip=True))
    return {"href": href, "year": year, "km": km, "date": dt}

# ================== Liste toplayıcı (PAGINATION YOK) ==================

def collect_links_with_filters(driver, query: str, min_year: int, max_km: int, start_url: str | None = None):
    url = start_url or f"{BASE}/otomobil?query_text={query.replace(' ', '+')}"
    dbg(f"🧭 URL: {url}")
    time.sleep(random.uniform(WAIT_RAND_MIN, WAIT_RAND_MAX))
    html, _ = get_page(driver, url, ["table#searchResultsTable","div#searchResultsTable","div.searchResults"], 75)

    # İsteğe bağlı menü tıklamaları (bayraklarla kapalı)
    enforce_sort_by_latest_click(driver)
    time.sleep(random.uniform(0.4,1.1))
    enforce_seller_filter_sahibinden(driver)
    time.sleep(random.uniform(0.4,1.1))

    seen_ids = set()
    target_date = TARGET_DATE
    out_links = []

    # >>> Yalnızca İLK SAYFA <<<
    html = driver.page_source
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table#searchResultsTable tbody tr.searchResultsItem[data-id]")
    dbg(f"🧩 Sayfadaki ilan satırı: {len(rows)}")

    for tr in rows:
        if "nativeAd" in " ".join(tr.get("class", [])):
            continue
        ad_id = tr.get("data-id") or tr.get("data-ad-id")
        if ad_id and ad_id in seen_ids:
            continue
        fields = parse_row_fields(tr)
        y, k, dt_, href = fields["year"], fields["km"], fields["date"], fields["href"]
        if dt_ is None:
            continue
        if dt_ != target_date:
            continue  # sadece bugün
        if y is not None and y < min_year: continue
        if k is not None and k > max_km:   continue
        if not href:                        continue
        out_links.append(href)
        if ad_id: seen_ids.add(ad_id)

    dbg(f"✅ Aday link sayısı (bugün + filtreler, ilk sayfa): {len(out_links)}")
    return out_links

# ================== Detayı yeni sekmede aç + parse ==================
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

# ================== Excel'e anında yaz ==================

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
            ws = book[sheet_name]; startrow = ws.max_row
            df_row.to_excel(writer, index=False, sheet_name=sheet_name, startrow=startrow, header=False)

# ================== Orkestra ==================

def scrape_to_excel(query: str, min_year: int, max_k_km: int):
    max_km = int(max_k_km) * 1000
    today = date.today()
    print(f"📅 Hedef tarih: {fmt_tr_date(TARGET_DATE)}", flush=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    xlsx_path = OUT_DIR / f"{query.lower().replace(' ','')}_bugun_filtreli_{ts}.xlsx"
    print(f"💾 Çıkış: {xlsx_path}", flush=True)

    driver = _new_driver(headless=False)
    details_done = 0
    try:
        start_url = TEST_URL if USE_TEST_URL else None
        if start_url:
            print(f"🚀 Başlangıç URL (TEST): {start_url}", flush=True)
        links = collect_links_with_filters(driver, query, min_year=min_year, max_km=max_km, start_url=start_url)
        if not links:
            print("⚠ Filtrelere uyan bugünkü ilan bulunamadı.", flush=True)
            return
        print(f"\n🔎 Detaya girilecek ilan sayısı (ilk sayfa): {len(links)}", flush=True)
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

# ================== ÇALIŞTIR ==================
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

    # Not: USE_TEST_URL=True ise query, yalnızca çıktı dosya adı için kullanılır.
    scrape_to_excel(query, min_year=min_year, max_k_km=max_k_km)
