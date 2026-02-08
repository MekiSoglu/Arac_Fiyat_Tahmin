# catboster.py
# -*- coding: utf-8 -*-

import re
import unicodedata
import numpy as np
import pandas as pd
from pathlib import Path
from catboost import CatBoostRegressor, Pool
from sklearn.model_selection import KFold

# ==============================
# Parametreler
# ==============================
FILE_NAME = r"C:\Users\EXCALIBUR\Desktop\sahibinden\megane\gundelik_megane_ilanları\megane_12eylül.xlsx"
OUT_DIR   = Path(r"C:\Users\EXCALIBUR\Desktop\sahibinden\megane\megane_model_tahminleri\catboster")
OUT_DIR.mkdir(parents=True, exist_ok=True)

HIT_LIMIT   = 100_000
N_SPLITS    = 5
RANDOM_SEED = 42

# Benzerlikte birebir eşitlenecek parça başlıkları (11 adet)

EQUAL_PART_PATTERNS = [
    "Arka Tampon", "Bagaj Kapağı", "Motor Kaputu",
    "Sağ Arka Kapı", "Sağ Ön Kapı", "Sağ Ön Çamurluk",
    "Sol Arka Kapı", "Sol Ön Kapı", "Sol Ön Çamurluk",
    "Tavan", "Ön Tampon"
]

# Modele girmeyecek sütunlar
EXCLUDE_FROM_MODEL = {
    "Adres", "Ağır Hasar Kayıtlı", "Garanti", "Plaka / Uyruk",
    "İl", "İlçe", "İlan No", "İlan Tarihi", "Link", "Boya-değişen"
}

# ==============================
# Yol çözümleyici (Unicode güvenli)
# ==============================
def norm_paths(p: Path):
    s = str(p)
    return [Path(s),
            Path(unicodedata.normalize("NFC", s)),
            Path(unicodedata.normalize("NFD", s))]

def strip_accents(s: str) -> str:
    return ''.join(ch for ch in unicodedata.normalize('NFD', s)
                   if unicodedata.category(ch) != 'Mn')

def resolve_input_file(explicit: str) -> Path:
    p = Path(explicit)

    # 1) Aynen ve NFC/NFD varyantlarıyla dene
    for cand in norm_paths(p):
        if cand.exists():
            return cand

    # 2) Ebeveyn klasörü; yoksa muhtemel klasöre düş
    parent = p.parent
    if not parent.exists():
        parent = Path(r"/superb/gündelik_superb_ilanlari")

    if parent.exists():
        # 2.a) Dosya adını aksansız/LOWER karşılaştır
        target = strip_accents(p.name.lower())
        same_name = [f for f in parent.glob("*.xlsx")
                     if strip_accents(f.name.lower()) == target]
        if same_name:
            return same_name[0]

        # 2.b) Uymadıysa klasördeki superb_*.xlsx dosyalarından en yenisini al
        cands = sorted(parent.glob("superb_*.xlsx"),
                       key=lambda x: x.stat().st_mtime,
                       reverse=True)
        if cands:
            return cands[0]

    raise FileNotFoundError(f"Girdi dosyası bulunamadı.\nAranan: {explicit}\nDenetlenen klasör: {parent}")

# ==============================
# Yardımcılar
# ==============================
def to_number(x):
    """ '1.690.000 TL' -> 1690000.0 (fiyat vb.) """
    if pd.isna(x):
        return np.nan
    if isinstance(x, (int, float, np.integer, np.floating)):
        return float(x)
    s = str(x).replace(".", "").replace(",", ".")
    s = re.sub(r"[^0-9.]", "", s)
    try:
        return float(s) if s else np.nan
    except:
        return np.nan

def parse_km(x):
    """
    KM için güvenli dönüştürme:
    - '61.902 km' -> 61902
    - '1.400.000' -> 1400000
    - parse edilemezse NaN (satır maskeden düşer)
    """
    if pd.isna(x):
        return np.nan
    s = str(x).lower().strip()
    s = s.replace("km", "")
    s = s.replace(" ", "")
    # KM'de kesir beklenmediğinden hem nokta hem virgülü tamamen temizle
    s = s.replace(".", "").replace(",", "")
    s = re.sub(r"[^0-9]", "", s)
    if not s:
        return np.nan
    try:
        return float(s)
    except:
        return np.nan

def parse_year_strict(x):
    """ Metinden güvenli 4 haneli yıl çek. """
    if pd.isna(x):
        return np.nan
    m = re.search(r"\b(19|20)\d{2}\b", str(x))
    if m:
        return float(m.group(0))
    # fallback
    v = to_number(x)
    return float(v) if not pd.isna(v) else np.nan

def pick_col(df, candidates_substr_lc):
    """ alt-string eşleşmesi (lower) """
    for c in df.columns:
        cl = c.lower()
        for key in candidates_substr_lc:
            if key in cl:
                return c
    return None

def find_first(df, names_exact):
    """ tam başlık sıralı arama """
    for n in names_exact:
        if n in df.columns:
            return n
    return None

def km_bin20k(km):
    try:
        return int(float(km) // 20_000)
    except:
        return np.nan

def norm_damage(val: object) -> str:
    """ Hücre metnini { '', 'boyali', 'degisen' }'e indirger. """
    s = str(val).strip().lower()
    if s in ("", "nan", "-", "—", "yok", "none"):
        return ""
    if ("değiş" in s) or ("degis" in s):
        return "degisen"
    if "boya" in s:  # lokal/komple dahil
        return "boyali"
    if "orij" in s or "orj" in s or "temiz" in s or "hasarsiz" in s:
        return ""
    return s  # başka metinler aynen kalsın

def normalize_tr(s: str) -> str:
    return (s
            .replace("ğ","g").replace("Ğ","G")
            .replace("ş","s").replace("Ş","S")
            .replace("ı","i").replace("İ","I")
            .replace("ç","c").replace("Ç","C")
            .replace("ö","o").replace("Ö","O")
            .replace("ü","u").replace("Ü","U"))

def match_part_columns(df, patterns):
    """
    Belirtilen desenler için DF'deki en uygun sütunu bulur.
    1) Doğrudan alt-string
    2) TR-normalize edilmiş alt-string
    """
    out = {}
    cols = list(df.columns)
    cols_norm = [normalize_tr(c.lower()) for c in cols]

    for pat in patterns:
        pat_norm = normalize_tr(pat.lower())
        best = None
        # 1) raw
        for c in cols:
            if pat.lower() in c.lower():
                best = c
                break
        if best is None:
            # 2) normalize
            for i, cn in enumerate(cols_norm):
                if pat_norm in cn:
                    best = cols[i]
                    break
        if best:
            out[pat] = best
    return out

# ==============================
# Benzer ilan ortalamaları
# ==============================
def avg_same_clean(df_ref, row, col_model, col_year, col_km, part_cols_map, km_window=30_000, min_n=3):
    """Aynı temizlik: model=aynı, yıl=aynı, km ±30k, listedeki 11 parça statüleri birebir aynı."""
    if col_model is None or col_year is None or col_km is None:
        return np.nan
    model = row.get(col_model, np.nan)
    year  = row.get(col_year,  np.nan)
    km    = row.get(col_km,    np.nan)
    if pd.isna(model) or pd.isna(year) or pd.isna(km):
        return np.nan

    m = (
        (df_ref[col_model] == model) &
        (df_ref[col_year]  == year)  &
        (df_ref[col_km].between(km - km_window, km + km_window))
    )
    # Parça statüsü birebir aynı
    for _, col in part_cols_map.items():
        ref_val = norm_damage(row.get(col, ""))
        m = m & (df_ref[col].map(norm_damage) == ref_val)

    cand = df_ref[m & (df_ref["_price"] > 0)]
    if len(cand) >= min_n:
        return float(cand["_price"].mean())

    # Fallback: parça eşitlemesini kaldır (yine de aynı yıl & km ±30k)
    cand = df_ref[
        (df_ref[col_model] == model) &
        (df_ref[col_year]  == year)  &
        (df_ref[col_km].between(km - km_window, km + km_window)) &
        (df_ref["_price"] > 0)
    ]
    return float(cand["_price"].mean()) if len(cand) >= min_n else np.nan

def avg_lenient(df_ref, row, col_model, col_year, col_km, km_window=50_000, year_window=1, min_n=3):
    """Geniş benzer: model=aynı, yıl ±1, km ±50k, parça serbest."""
    if col_model is None or col_year is None or col_km is None:
        return np.nan
    model = row.get(col_model, np.nan)
    year  = row.get(col_year,  np.nan)
    km    = row.get(col_km,    np.nan)
    if pd.isna(model) or pd.isna(year) or pd.isna(km):
        return np.nan

    m = (
        (df_ref[col_model] == model) &
        (df_ref[col_year].between(year - year_window, year + year_window)) &
        (df_ref[col_km].between(km - km_window, km + km_window)) &
        (df_ref["_price"] > 0)
    )
    cand = df_ref[m]
    return float(cand["_price"].mean()) if len(cand) >= min_n else np.nan

def build_lenient_base(df_ref, rows, col_model, col_year, col_km):
    """Validation/train ayrımına uygun geniş benzer baz vektörü (leakage yok)."""
    out = []
    gmed = float(df_ref["_price"].median()) if len(df_ref) else np.nan
    for _, r in rows.iterrows():
        v = avg_lenient(df_ref, r, col_model, col_year, col_km, km_window=50_000, year_window=1, min_n=3)
        if pd.isna(v):
            v = gmed
        out.append(max(1.0, float(v)))
    return np.array(out, dtype=float)

def build_same_clean_avg(df_ref, rows, col_model, col_year, col_km, parts_map):
    out = []
    for _, r in rows.iterrows():
        v = avg_same_clean(df_ref, r, col_model, col_year, col_km, parts_map, km_window=30_000, min_n=3)
        out.append(np.nan if pd.isna(v) else float(v))
    return np.array(out, dtype=float)

# ==============================
# Ana akış
# ==============================
def main():
    file_path = resolve_input_file(FILE_NAME)
    print(f"✅ Girdi: {file_path}")
    df_raw = pd.read_excel(file_path)
    # Aynı isimli sütunlar -> ilkini al
    df_raw = df_raw.loc[:, ~pd.Index(df_raw.columns).duplicated()]
    df_raw.columns = [str(c).strip() for c in df_raw.columns]

    df = df_raw.copy()

    # Temel kolonlar (dosyandan beklenen adlar + esnek arama)
    col_price = find_first(df, ["Fiyat"]) or pick_col(df, ["fiyat","price"])
    col_year  = find_first(df, ["Yıl","Yil"]) or pick_col(df, ["yıl","yil","year","model yılı","model yili"])
    col_km    = find_first(df, ["KM","Kilometre"]) or pick_col(df, ["km","kilometre","kilometer"])
    col_model = find_first(df, ["Model"])
    col_seri  = find_first(df, ["Seri"])  # opsiyonal

    assert col_price and col_year and col_km, "Fiyat/Yıl/KM sütunları bulunamadı."

    # Sayısal dönüşümler (KM güvenli parse)
    df["_price"] = df[col_price].apply(to_number)
    df["_year"]  = df[col_year].apply(parse_year_strict)
    df["_km"]    = df[col_km].apply(parse_km)

    # Filtre: yıl >= 2015, mantıklı sınırlar + KM görülemiyorsa (NaN) hariç
    mask = (
        (df["_year"] >= 2015) &
        (df["_price"] > 0) & (df["_price"] < 1e9) &
        (df["_km"].notna()) & (df["_km"] >= 0) & (df["_km"] < 2e6)
    )
    df = df[mask].copy()
    df_raw = df_raw.loc[df.index].copy()  # tüm giriş sütunlarıyla hizala

    # Türevler
    df["_age"]          = 2025 - df["_year"]
    df["_km_per_year"]  = df["_km"] / np.maximum(df["_age"], 1)
    df["_km_bin_20k"]   = df["_km"].apply(km_bin20k)

    # Parça sütunları (özellik amaçlı toplamlar)
    part_cols_any = [
        c for c in df.columns if any(k in c.lower() for k in
        ["kaput","tavan","tampon","çamurluk","çamurlug","camurluk",
         "kapı","kapi","bagaj","şasi","sasi","direk","podye","marşpiyel","marspiyel"])
    ]
    df["_boyali_count"]  = 0
    df["_degisen_count"] = 0
    df["_crit_boyali"]   = 0
    df["_crit_degisen"]  = 0
    df["_cos_boyali"]    = 0
    df["_cos_degisen"]   = 0

    critical_keywords = ["kaput","tavan","şasi","sasi","direk","podye"]
    cosmetic_keywords = ["tampon"]
    def is_critical(col): return any(k in col.lower() for k in critical_keywords)
    def is_cosmetic(col): return any(k in col.lower() for k in cosmetic_keywords)

    for c in part_cols_any:
        b = df[c].map(lambda v: 1 if "boya" in str(v).lower() else 0).astype(int)
        d = df[c].map(lambda v: 1 if (("değiş" in str(v).lower()) or ("degis" in str(v).lower())) else 0).astype(int)
        df["_boyali_count"]  += b
        df["_degisen_count"] += d
        if is_critical(c):
            df["_crit_boyali"]   += b
            df["_crit_degisen"]  += d
        if is_cosmetic(c):
            df["_cos_boyali"]    += b
            df["_cos_degisen"]   += d

    # Benzerlik için 11 parçayı dosyadaki kolonlara eşle
    parts_map = match_part_columns(df, EQUAL_PART_PATTERNS)

    # Kategorik sütun adayları (EXCLUDE_FROM_MODEL hariç)
    name_candidates = [
        "Seri", "Model",
        "Yakıt Tipi", "Yakıt",
        "Vites", "Şanzıman",
        "Kasa Tipi", "Çekiş",
        "Renk"
    ]
    cat_cols = [n for n in name_candidates if (n in df.columns and n not in EXCLUDE_FROM_MODEL)]

    # Sayısal özellik seti
    num_cols = [
        "_year","_km","_age","_km_per_year","_km_bin_20k",
        "_boyali_count","_degisen_count",
        "_crit_boyali","_crit_degisen",
        "_cos_boyali","_cos_degisen"
    ]

    # ============ CV ============
    kf = KFold(n_splits=N_SPLITS, shuffle=True, random_state=RANDOM_SEED)
    oof_pred    = np.zeros(len(df))   # model tahmini
    oof_base50  = np.zeros(len(df))   # geniş benzer (±50k, yıl ±1) ort.
    oof_same30  = np.zeros(len(df))   # aynı temizlik (±30k, yıl=aynı, parçalar aynı) ort.

    fold_mae, fold_mape, fold_hit = [], [], []

    for i, (tr_idx, va_idx) in enumerate(kf.split(df), 1):
        df_tr = df.iloc[tr_idx].copy()
        df_va = df.iloc[va_idx].copy()

        # Baz (geniş benzer) – sadece train referansıyla hesapla (leakage yok)
        base_va = build_lenient_base(df_tr, df_va, col_model, "_year", "_km")
        base_tr = build_lenient_base(df_tr, df_tr, col_model, "_year", "_km")

        # Aynı temizlik ortalaması – rapor amaçlı (validation satırları için)
        same30_va = build_same_clean_avg(df_tr, df_va, col_model, "_year", "_km", parts_map)

        # Residual hedef
        y_tr = np.log(df_tr["_price"].values)
        y_tr_resid = y_tr - np.log(base_tr)

        # Feature frame
        X_tr = pd.DataFrame({c: df_tr[c] for c in num_cols})
        X_va = pd.DataFrame({c: df_va[c] for c in num_cols})

        # Kategorikler: string + 'NA' (CatBoost NaN kabul etmiyor)
        cat_cols_in_tr = []
        for c in cat_cols:
            if c in df_tr.columns:
                X_tr[c] = df_tr[c].astype('string').fillna('NA')
                cat_cols_in_tr.append(c)
        cat_cols_in_va = []
        for c in cat_cols:
            if c in df_va.columns:
                X_va[c] = df_va[c].astype('string').fillna('NA')
                cat_cols_in_va.append(c)
        # Her iki tarafta da bulunan kategoriklerin kesişimini kullan
        cat_cols_in = [c for c in cat_cols if (c in X_tr.columns and c in X_va.columns)]

        # Bazı sayısalları yardımcı feature olarak ekle
        X_tr["_baseline50"] = base_tr
        X_va["_baseline50"] = base_va

        # CatBoost Pool
        train_pool = Pool(X_tr, y_tr_resid, cat_features=cat_cols_in)
        val_pool   = Pool(X_va, cat_features=cat_cols_in)

        model = CatBoostRegressor(
            loss_function="MAE",
            iterations=2000,
            depth=8,
            learning_rate=0.03,
            l2_leaf_reg=6,
            random_seed=RANDOM_SEED,
            verbose=False
        )
        model.fit(train_pool)

        resid_pred = model.predict(val_pool)
        price_pred = np.exp(np.log(base_va) + resid_pred)

        oof_pred[va_idx]   = price_pred
        oof_base50[va_idx] = base_va
        oof_same30[va_idx] = same30_va

        y_true = df_va["_price"].values
        mae  = np.mean(np.abs(price_pred - y_true))
        mape = np.mean(np.abs(price_pred - y_true) / y_true)
        hit  = np.mean(np.abs(price_pred - y_true) <= HIT_LIMIT)
        print(f"Fold {i}: MAE={mae:,.0f} TL | MAPE={mape:.2%} | Hit@{HIT_LIMIT//1000}k={hit:.2%}")
        fold_mae.append(mae); fold_mape.append(mape); fold_hit.append(hit)

    print("\n--- Ortalama Sonuçlar (5-fold CV) ---")
    print(f"MAE: {np.mean(fold_mae):,.0f} TL")
    print(f"MAPE: {np.mean(fold_mape):.2%}")
    print(f"Hit@{HIT_LIMIT//1000}k: {np.mean(fold_hit):.2%}")

    # ============ OOF çıktı ============
    oof_df = df_raw.copy()
    oof_df["Ayni_Temizlik_Ort_30k"] = oof_same30
    oof_df["Benzer_Ort_50k_Yil±1"]  = oof_base50
    oof_df["Model_Tahmin"]          = oof_pred
    oof_df["Fark"]                  = oof_df["Model_Tahmin"] - df["_price"].values
    oof_df[f"±{HIT_LIMIT//1000}k_icinde"] = (oof_df["Fark"].abs() <= HIT_LIMIT)
    oof_path = OUT_DIR / "oofr_tahminler.xlsx"
    oof_df.to_excel(oof_path, index=False)
    print(f"Kaydedildi: {oof_path}")

    # ============ Final model ============
    base_full  = build_lenient_base(df, df, col_model, "_year", "_km")
    same30_all = build_same_clean_avg(df, df, col_model, "_year", "_km", parts_map)
    y_full_resid = np.log(df["_price"].values) - np.log(base_full)

    X_full = pd.DataFrame({c: df[c] for c in num_cols})
    for c in cat_cols:
        if c in df.columns:
            X_full[c] = df[c].astype('string').fillna('NA')
    X_full["_baseline50"] = base_full
    cat_cols_in_full = [c for c in cat_cols if c in X_full.columns]

    final_pool = Pool(X_full, y_full_resid, cat_features=cat_cols_in_full)
    final_model = CatBoostRegressor(
        loss_function="MAE",
        iterations=3000,
        depth=8,
        learning_rate=0.03,
        l2_leaf_reg=6,
        random_seed=RANDOM_SEED,
        verbose=False
    )
    final_model.fit(final_pool)

    model_path = OUT_DIR / "golf_residual_catboost.cbm"
    final_model.save_model(model_path)
    print(f"Kaydedildi: {model_path}")

    # Bilgi amaçlı: tüm veri için 3 sonuç
    full_out = df_raw.copy()
    full_out["Ayni_Temizlik_Ort_30k"] = same30_all
    full_out["Benzer_Ort_50k_Yil±1"]  = base_full
    full_out["Model_Tahmin"]          = np.exp(np.log(base_full) + final_model.predict(Pool(X_full, cat_features=cat_cols_in_full)))
    full_out["Fark"]                  = full_out["Model_Tahmin"] - df["_price"].values
    full_all_path = OUT_DIR / "tum_veri_tahmin.xlsx"
    full_out.to_excel(full_all_path, index=False)
    print(f"Kaydedildi: {full_all_path}")

if __name__ == "__main__":
    main()
