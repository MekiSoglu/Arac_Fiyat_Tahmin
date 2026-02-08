# sahibinden_scrape_excel_debug.py
import os
import time
from datetime import datetime
from pathlib import Path
import os, sqlite3


import pandas as pd
import cloudscraper
from bs4 import BeautifulSoup
import browser_cookie3
import requests

# ====== AYARLAR (platforma göre varsayılan profil yolu) ======
if os.name == "nt":  # Windows
    DEFAULT_PROFILE_DIR = Path.home() / r"AppData\Local\Google\Chrome\User Data\Default"
else:                # macOS / Linux
    DEFAULT_PROFILE_DIR = Path.home() / "ChromeDebug/Default"  # senin mac'teki klasörünle uyumlu

PROFILE_DIR = Path(os.environ["USERPROFILE"]) / "ChromeDebug" / "Default"
BASE = "https://www.sahibinden.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"

# İLAN BAŞINA / SAYFA BAŞINA BEKLEME (sn)
SLEEP_PER_ITEM = 2.0       # tekil ilan arasında
SLEEP_PER_PAGE = 3.0       # sayfa geçişlerinde

# Konsol debug seviyesi
DEBUG = True
def dbg(*a, **kw):
    if DEBUG:
        print(*a, flush=True, **kw)

# Boyalı / Değişen parça isimleri (class -> TR)
PART_NAME_MAP = {
    "front-bumper": "Ön Tampon",
    "front-hood": "Motor Kaputu",
    "roof": "Tavan",
    "front-right-mudguard": "Sağ Ön Çamurluk",
    "front-right-door": "Sağ Ön Kapı",
    "rear-right-door": "Sağ Arka Kapı",
    "rear-right-mudguard": "Sağ Arka Çamurluk",
    "front-left-mudguard": "Sol Ön Çamurluk",
    "front-left-door": "Sol Ön Kapı",
    "rear-left-door": "Sol Arka Kapı",
    "rear-left-mudguard": "Sol Arka Çamurluk",
    "rear-hood": "Bagaj Kapağı",
    "rear-bumper": "Arka Tampon",
}

# ====== YARDIMCI ======
def _clean_text(s: str) -> str:
    return s.replace("\xa0", " ").strip()

def _status_from_span_text(txt: str) -> str:
    t = (txt or "").strip().upper()
    if t == "B":
        return "Boyalı"
    if t == "D":
        return "Değişen"
    return "Orijinal"

# ====== ÇEREZ / SESSION ======
def _candidate_cookie_paths(profile_dir: Path) -> list[Path]:
    # Chrome yeni sürümlerde Cookies -> Network/Cookies altına taşındı
    return [
        profile_dir / "Network" / "Cookies",
        profile_dir / "Cookies",
    ]

PROFILE_ROOT = Path(os.environ["USERPROFILE"]) / "ChromeDebug"
PROFILE_DIR  = PROFILE_ROOT / "Default"  # gerekirse "Profile 1" vs.
os.environ["BROWSERCOOKIE3_NO_SHADOWCOPY"] = "1"

def load_chrome_cookies(profile_dir: Path) -> requests.cookies.RequestsCookieJar:
    os.environ["BROWSERCOOKIE3_NO_SHADOWCOPY"] = "1"  # <-- eklendi

    cookie_file = profile_dir / "Network" / "Cookies"
    key_file    = PROFILE_ROOT / "Local State"

    print(f"🍪 Cookies dosyası: {cookie_file}")
    print(f"🔑 Local State     : {key_file}")
    print(f"👤 Profil          : {profile_dir.name}")

    if not cookie_file.exists():
        raise SystemExit(f"Cookie DB yok: {cookie_file} ...")
    if not key_file.exists():
        raise SystemExit(f"Local State yok: {key_file} ...")

    # Kilit kontrolü
    try:
        sqlite3.connect(f'file:{cookie_file}?mode=ro', uri=True).close()
    except sqlite3.OperationalError as e:
        raise SystemExit(
            "Cookie veritabanı kilitli. Chrome’u tamamen kapat (tüm chrome.exe) ve tekrar dene.\n"
            f"Teknik detay: {e}"
        )

    try:
        jar = browser_cookie3.chrome(
            cookie_file=str(cookie_file),
            key_file=str(key_file)
        )
    except Exception as e:
        print("Çerez şifresi çözülemedi.")
        print("- PowerShell/Terminal'i **YÖNETİCİ OLARAK AÇMAYIN**.")
        print("- Chrome’u **aynı Windows kullanıcı hesabıyla** açıp doğrulamayı yapın.")
        print(f"Teknik detay: {type(e).__name__}: {e}")
        raise

    cj = requests.cookies.RequestsCookieJar()
    cnt = 0
    for c in jar:
        if c.domain and "sahibinden.com" in c.domain:
            cj.set(c.name, c.value, domain=c.domain, path=c.path)
            cnt += 1
    print(f"🍪 Sahibinden cookie sayısı: {cnt}")
    return cj

def make_session(cj: requests.cookies.RequestsCookieJar) -> cloudscraper.CloudScraper:
    s = cloudscraper.create_scraper()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    s.cookies.update(cj)
    has_clearance = any(c.name == "cf_clearance" and "sahibinden.com" in c.domain for c in s.cookies)
    dbg(f"🛡️ cf_clearance var mı? {'EVET' if has_clearance else 'HAYIR'}")
    return s

def fetch_html(session, url: str) -> BeautifulSoup:
    dbg(f"🌐 GET {url}")
    r = session.get(url, timeout=45)
    dbg(f"↪ Status: {r.status_code}")
    if r.status_code == 403:
        dbg("🚫 403 Forbidden – muhtemelen challenge / cookie sorunlu")
        raise RuntimeError("403 (Forbidden) – Çerez geçersiz ya da challenge aktif. Chrome'da doğrulamayı yenileyip tekrar çalıştır.")
    r.raise_for_status()

    html = r.text
    # Bot/JS engel ihtimallerini kokla
    bot_hints = ("cf-error-details", "Attention Required", "Please enable JavaScript", "challenge-form", "/cdn-cgi/")
    if any(h in html for h in bot_hints):
        with open("debug_bot_page.html", "w", encoding="utf-8") as f:
            f.write(html)
        raise RuntimeError("Sayfa bot/JS engeli döndürdü (200 olsa da). debug_bot_page.html kaydedildi.")

    return BeautifulSoup(html, "html.parser")

# ====== ARAMA & SAYFALAMA ======
def extract_listing_links(soup: BeautifulSoup):
    links = set()

    # Eski tablo şablonu
    for a in soup.select("td.searchResultsTitleValue a.classifiedTitle"):
        href = a.get("href")
        if href:
            links.add(href if href.startswith("http") else BASE + href)

    # Yeni kart şablonu (liste kartları)
    # - başlık linkleri genelde /ilan/... içeriyor
    for a in soup.select("a[href*='/ilan/']"):
        href = a.get("href")
        if not href:
            continue
        text = (a.get_text(strip=True) or "")
        # Başlığa benzer metin ve /detay veya /ilan/... uzantısı olsun
        if "/ilan/" in href and ("/detay" in href or text != ""):
            links.add(href if href.startswith("http") else BASE + href)

    # Başka varyasyonlar (bazı sayfalarda farklı sınıf isimleri olabiliyor)
    for sel in [
        "tr.searchResultsItem a[href*='/ilan/']",
        "div.searchResultsTitle a[href*='/ilan/']",
        "div.searchResultsLargeThumb a[href*='/ilan/']",
        "h3 a[href*='/ilan/']",
    ]:
        for a in soup.select(sel):
            href = a.get("href")
            if href:
                links.add(href if href.startswith("http") else BASE + href)

    links = list(dict.fromkeys(links))  # sıra koruyarak unique
    dbg(f"🔗 Sayfadaki ilan linkleri: {len(links)}")
    # Hiç link çıkmazsa sayfayı kaydet ki bakabilelim
    if not links:
        with open("debug_search.html", "w", encoding="utf-8") as f:
            f.write(str(soup))
        dbg("💾 İpucu için debug_search.html kaydedildi (0 link).")
    return links


def find_next_page_href(soup: BeautifulSoup):
    a = soup.select_one("ul.pageNaviButtons a[rel='next']")
    if a and a.get("href"):
        href = a["href"] if a["href"].startswith("http") else BASE + a["href"]
        dbg(f"➡ next (rel=next): {href}")
        return href
    for sel in ["ul.pageNaviButtons a", "a.prevNextBut", "a.next"]:
        for cand in soup.select(sel):
            text = _clean_text(cand.get_text())
            if text in {">", "»", "Sonraki", "İleri"} and cand.get("href"):
                href = cand["href"]
                href = href if href.startswith("http") else BASE + href
                dbg(f"➡ next (label): {href}")
                return href
    try:
        cur_txt = soup.select_one("ul.pageNaviButtons span.currentPage")
        cur = int(cur_txt.get_text(strip=True)) if cur_txt else None
    except Exception:
        cur = None
    if cur is not None:
        nums = []
        for cand in soup.select("ul.pageNaviButtons li a"):
            t = _clean_text(cand.get_text())
            if t.isdigit():
                n = int(t)
                if n > cur and cand.get("href"):
                    nums.append((n, cand["href"]))
        if nums:
            n, href = sorted(nums, key=lambda x: x[0])[0]
            href = href if href.startswith("http") else BASE + href
            dbg(f"➡ next (num>{cur}): {href}")
            return href
    dbg("⛔ next sayfa bulunamadı (son sayfa).")
    return None

def search_listings_all(session, query: str, max_pages: int | None = None, max_listings: int | None = None):
    url = f"{BASE}/otomobil?query_text={query.replace(' ', '+')}"
    seen = set()
    out = []
    page_count = 0
    while True:
        page_count += 1
        dbg(f"\n📄 Sayfa {page_count} URL: {url}")
        soup = fetch_html(session, url)
        page_links = extract_listing_links(soup)
        for link in page_links:
            if link not in seen:
                seen.add(link)
                out.append(link)
                dbg(f"  + eklendi [{len(out)}]: {link}")
                if max_listings and len(out) >= max_listings:
                    dbg("🔚 max_listings sınırına ulaşıldı.")
                    return out
        next_href = find_next_page_href(soup)
        if not next_href:
            break
        if max_pages and page_count >= max_pages:
            dbg("🔚 max_pages sınırına ulaşıldı.")
            break
        dbg(f"⏳ {SLEEP_PER_PAGE}s bekleniyor (sayfa geçişi)...")
        time.sleep(SLEEP_PER_PAGE)
        url = next_href
    dbg(f"✅ Toplam toplanan link: {len(out)}")
    return out

# ====== DETAY PARSE ======
def parse_detail(session, url: str) -> dict:
    soup = fetch_html(session, url)
    h1 = soup.find("h1", class_="classifiedTitle")
    title = _clean_text(h1.get_text(strip=True)) if h1 else None
    price_el = (
        soup.select_one("span.classified-price-wrapper")
        or soup.select_one("div.classified-price-container span")
        or soup.select_one("h3.classifiedInfo span")
        or soup.select_one("h3.classifiedInfo")
    )
    price = _clean_text(price_el.get_text(strip=True)) if price_el else None
    addr_parts = [_clean_text(a.get_text(" ", strip=True)) for a in soup.select("div.classifiedInfo > h2 a")]
    il      = addr_parts[0] if len(addr_parts) >= 1 else None
    ilce    = addr_parts[1] if len(addr_parts) >= 2 else None
    mahalle = addr_parts[2] if len(addr_parts) >= 3 else None
    adres_full = " / ".join([p for p in addr_parts if p])

    details = {}
    for li in soup.select("ul.classifiedInfoList li"):
        k = li.find("strong")
        v = li.find("span")
        if k and v:
            details[_clean_text(k.get_text(strip=True))] = _clean_text(v.get_text(strip=True))

    parts_tr = {}
    for div in soup.select("div.car-parts > div"):
        classes = div.get("class") or []
        part_key = classes[0] if classes else None
        if part_key not in PART_NAME_MAP:
            part_key = next((c for c in classes if c in PART_NAME_MAP), None)
        if not part_key:
            continue
        span = div.find("span")
        status_text = span.get_text(strip=True) if span else ""
        parts_tr[PART_NAME_MAP[part_key]] = _status_from_span_text(status_text)

    dbg(f"📝 {title or '(başlık yok)'} — {price or '(fiyat yok)'} — {il or ''}/{ilce or ''}/{mahalle or ''}")
    return {
        "Başlık": title,
        "Fiyat": price,
        "İl": il,
        "İlçe": ilce,
        "Mahalle": mahalle,
        "Adres": adres_full,
        **details,
        **parts_tr,
        "Link": url,
    }

# ====== ORKESTRA & EXCEL ======
def scrape_to_excel(query: str, max_pages: int | None = None, max_listings: int | None = None):
    cj = load_chrome_cookies(PROFILE_DIR)
    if not any(c.name == "cf_clearance" and "sahibinden.com" in c.domain for c in cj):
        raise SystemExit("cf_clearance bulunamadı. Chrome profilinde sahibinden.com doğrulamasını geç ve script'i yeniden çalıştır.")

    s = make_session(cj)
    links = search_listings_all(s, query, max_pages=max_pages, max_listings=max_listings)
    if not links:
        raise SystemExit("Arama sonucunda link bulunamadı.")

    print(f"\n🔎 Toplam {len(links)} ilan linki bulundu.", flush=True)
    rows = []
    for i, link in enumerate(links, 1):
        print(f"[{i}/{len(links)}] {link}", flush=True)
        try:
            rows.append(parse_detail(s, link))
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            print(f"⚠  İlan atlandı (HTTP {code}): {link}", flush=True)
        except RuntimeError as e:
            print(f"⚠  Uyarı: {e}. Bu ilan atlandı: {link}", flush=True)
        except Exception as e:
            print(f"⚠  Beklenmeyen hata ({type(e).__name__}): {e}", flush=True)
        print(f"⏳ {SLEEP_PER_ITEM}s bekleniyor...", flush=True)
        time.sleep(SLEEP_PER_ITEM)

    if not rows:
        print("⚠ Toplanacak veri kalmadı.", flush=True)
        return

    df = pd.DataFrame(rows)
    preferred = [
        "Başlık","Fiyat","İl","İlçe","Mahalle","Adres",
        "İlan No","İlan Tarihi","Marka","Seri","Model",
        "Yıl","KM","Yakıt Tipi","Vites","Kasa Tipi",
        "Motor Gücü","Motor Hacmi","Çekiş","Renk",
        "Garanti","Ağır Hasar Kayıtlı","Plaka / Uyruk",
        "Kimden","Takas","Link"
    ]
    part_cols_tr = [PART_NAME_MAP[k] for k in PART_NAME_MAP if PART_NAME_MAP[k] in df.columns]
    preferred_extended = preferred + part_cols_tr
    cols = [c for c in preferred_extended if c in df.columns] + [c for c in df.columns if c not in preferred_extended]
    df = df[cols]

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    # Windows'ta dosya adı güvenli olsun diye boşlukları kaldır
    fname = f"{query.lower().replace(' ','')}{ts}.xlsx"
    df.to_excel(fname, index=False)
    print(f"✅ {len(df)} ilan kaydedildi: {fname}", flush=True)

# ====== ÇALIŞTIR ======
if __name__ == "__main__":
    q = input("Aramak istediğiniz araç modeli: ")
    # Hepsini çekmek için sınırlar None; test için ör: max_pages=2, max_listings=50
    scrape_to_excel(q)
