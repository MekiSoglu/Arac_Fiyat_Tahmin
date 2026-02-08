# LightGBM.py
# -*- coding: utf-8 -*-

import re
import json
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.model_selection import KFold
import lightgbm as lgb

# ==============================
# Parametreler
# ==============================
FILE_NAME = r"C:\Users\EXCALIBUR\Desktop\sahibinden\full_passat.xlsx"
OUT_DIR   = Path(r"/passat/passat_model_tahmin")
OUT_DIR.mkdir(parents=True, exist_ok=True)

HIT_LIMIT   = 100_000
N_SPLITS    = 5
RANDOM_SEED = 42
USE_GPU     = True  # GPU'lu LightGBM kuruluysa True yap

# Aynı temizlikte birebir eşitlenecek 11 parça
EQUAL_PART_PATTERNS = [
    "Arka Tampon", "Bagaj Kapağı", "Motor Kaputu",
    "Sağ Arka Kapı", "Sağ Ön Kapı", "Sağ Ön Çamurluk",
    "Sol Arka Kapı", "Sol Ön Kapı", "Sol Ön Çamurluk",
    "Tavan", "Ön Tampon"
]

# Modele alınmayacak sütunlar
EXCLUDE_FROM_MODEL = {
    "Adres", "Ağır Hasar Kayıtlı", "Garanti", "Plaka / Uyruk",
    "İl", "İlçe", "İlan No", "İlan Tarihi", "Link", "Boya-değişen"
}

# ==============================
# Dosya yolunu otomatik bulma (varsa)
# ==============================
FILE_PATH = Path(FILE_NAME)
if not FILE_PATH.exists():
    base_dir = Path(r"C:\Users\EXCALIBUR\Desktop\sahibinden")
    candidates = sorted(
        base_dir.rglob("full_passat*.xlsx"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    if candidates:
        FILE_PATH = candidates[0]
        print(f"✅ Bulunan dosya: {FILE_PATH}")
    else:
        raise FileNotFoundError(f"full_passat*.xlsx bulunamadı. FILE_NAME'ı doğru dosyaya işaret edecek şekilde güncelleyin.")

# ==============================
# Yardımcılar
# ==============================
def to_number(x):
    if pd.isna(x): return np.nan
    if isinstance(x, (int, float, np.integer, np.floating)): return float(x)
    s = str(x).replace(".", "").replace(",", ".")
    s = re.sub(r"[^0-9.]", "", s)
    try: return float(s)
    except: return np.nan

def parse_year_strict(x):
    if pd.isna(x): return np.nan
    m = re.search(r"\b(19|20)\d{2}\b", str(x))
    if m: return float(m.group(0))
    return float(to_number(x))

def pick_col(df, keys_lc):
    for c in df.columns:
        cl = c.lower()
        for k in keys_lc:
            if k in cl: return c
    return None

def find_first(df, names):
    for n in names:
        if n in df.columns: return n
    return None

def km_bin20k(km): return int(km // 20_000)

def normalize_tr(s: str) -> str:
    return (s.replace("ğ","g").replace("Ğ","G")
             .replace("ş","s").replace("Ş","S")
             .replace("ı","i").replace("İ","I")
             .replace("ç","c").replace("Ç","C")
             .replace("ö","o").replace("Ö","O")
             .replace("ü","u").replace("Ü","U"))

def norm_damage(val: object) -> str:
    s = str(val).strip().lower()
    if s in ("", "nan", "-", "—", "yok", "none"): return ""
    if ("değiş" in s) or ("degis" in s): return "degisen"
    if "boya" in s: return "boyali"
    if "orij" in s or "orj" in s or "temiz" in s or "hasarsiz" in s: return ""
    return s

def match_part_columns(df, patterns):
    out = {}
    cols = list(df.columns)
    cols_norm = [normalize_tr(c.lower()) for c in cols]
    for pat in patterns:
        pat_norm = normalize_tr(pat.lower())
        best = None
        for c in cols:
            if pat.lower() in c.lower(): best = c; break
        if best is None:
            for i, cn in enumerate(cols_norm):
                if pat_norm in cn: best = cols[i]; break
        if best: out[pat] = best
    return out

# ==============================
# Benzerlik fonksiyonları
# ==============================
def avg_same_clean(df_ref, row, col_model, col_year, col_km, part_cols_map, km_window=30_000, min_n=3):
    """1) Aynı temizlikte benzerler: Model aynı, Yıl aynı, KM ±30k, parça durumları birebir aynı."""
    if col_model is None or col_year is None or col_km is None: return np.nan
    model = row.get(col_model, np.nan); year = row.get(col_year, np.nan); km = row.get(col_km, np.nan)
    if pd.isna(model) or pd.isna(year) or pd.isna(km): return np.nan

    m = ((df_ref[col_model] == model) &
         (df_ref[col_year]  == year)  &
         (df_ref[col_km].between(km - km_window, km + km_window)))
    for _, col in part_cols_map.items():
        ref_val = norm_damage(row.get(col, ""))
        m = m & (df_ref[col].map(norm_damage) == ref_val)

    cand = df_ref[m & (df_ref["_price"] > 0)]
    if len(cand) >= min_n: return float(cand["_price"].mean())

    # Fallback: parça eşlemesi olmadan
    cand = df_ref[(df_ref[col_model]==model) & (df_ref[col_year]==year) &
                  (df_ref[col_km].between(km - km_window, km + km_window)) & (df_ref["_price"]>0)]
    return float(cand["_price"].mean()) if len(cand) >= min_n else np.nan

def avg_lenient(df_ref, row, col_model, col_year, col_km, km_window=50_000, year_window=1, min_n=3):
    """2) Esnek benzerler: Model aynı, Yıl ±1, KM ±50k, parça durumları farklı olabilir."""
    if col_model is None or col_year is None or col_km is None: return np.nan
    model = row.get(col_model, np.nan); year = row.get(col_year, np.nan); km = row.get(col_km, np.nan)
    if pd.isna(model) or pd.isna(year) or pd.isna(km): return np.nan
    m = ((df_ref[col_model] == model) &
         (df_ref[col_year].between(year - year_window, year + year_window)) &
         (df_ref[col_km].between(km - km_window, km + km_window)) &
         (df_ref["_price"] > 0))
    cand = df_ref[m]
    return float(cand["_price"].mean()) if len(cand) >= min_n else np.nan

def build_lenient_base(df_ref, rows, col_model, col_year, col_km):
    out = []
    gmed = float(df_ref["_price"].median()) if len(df_ref) else np.nan
    for _, r in rows.iterrows():
        v = avg_lenient(df_ref, r, col_model, col_year, col_km, km_window=50_000, year_window=1, min_n=3)
        if pd.isna(v): v = gmed
        out.append(max(1.0, float(v)))
    return np.array(out, dtype=float)

def build_same_clean_avg(df_ref, rows, col_model, col_year, col_km, parts_map):
    out = []
    for _, r in rows.iterrows():
        v = avg_same_clean(df_ref, r, col_model, col_year, col_km, parts_map, km_window=30_000, min_n=3)
        out.append(np.nan if pd.isna(v) else float(v))
    return np.array(out, dtype=float)

# ==============================
# Kategorik hizalama (LightGBM)
# ==============================
def prepare_categoricals(df, cat_cols):
    """Tüm dataframe üzerinde global kategori uzayı oluştur ve ata."""
    for c in cat_cols:
        vals = df[c].astype("string").fillna("NA")
        cats = pd.Index(sorted(vals.unique().tolist()))
        df[c] = pd.Categorical(vals, categories=cats, ordered=False)
    return df

# ==============================
# Ana akış
# ==============================
def main():
    # --- Excel oku ---
    df_raw = pd.read_excel(FILE_PATH)
    # yinelenen kolonları at
    df_raw = df_raw.loc[:, ~pd.Index(df_raw.columns).duplicated()]
    df_raw.columns = [str(c).strip() for c in df_raw.columns]
    df = df_raw.copy()

    # Temel kolonlar
    col_price = find_first(df, ["Fiyat"]) or pick_col(df, ["fiyat","price"])
    col_year  = find_first(df, ["Yıl","Yil"]) or pick_col(df, ["yıl","yil","year","model yılı","model yili"])
    col_km    = find_first(df, ["KM","Kilometre"]) or pick_col(df, ["km","kilometre","kilometer"])
    col_model = find_first(df, ["Model"])
    assert col_price and col_year and col_km, "Fiyat/Yıl/KM sütunları bulunamadı."

    # Numeric + filtre
    df["_price"] = df[col_price].apply(to_number)
    df["_year"]  = df[col_year].apply(parse_year_strict)
    df["_km"]    = df[col_km].apply(to_number)

    mask = ((df["_year"] >= 2015) &
            (df["_price"] > 0) & (df["_price"] < 1e9) &
            (df["_km"] >= 0) & (df["_km"] < 2e6))
    df = df[mask].copy()
    df_raw = df_raw.loc[df.index].copy()

    # Türevler
    df["_age"]         = 2025 - df["_year"]
    df["_km_per_year"] = df["_km"] / np.maximum(df["_age"], 1)
    df["_km_bin_20k"]  = df["_km"].apply(km_bin20k)

    # Parça sayacı özellikleri (bilgi amaçlı)
    part_cols_any = [c for c in df.columns if any(k in c.lower() for k in
        ["kaput","tavan","tampon","çamurluk","çamurlug","camurluk","kapı","kapi",
         "bagaj","şasi","sasi","direk","podye","marşpiyel","marspiyel"])]
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
            df["_crit_boyali"]  += b; df["_crit_degisen"] += d
        if is_cosmetic(c):
            df["_cos_boyali"]   += b; df["_cos_degisen"] += d

    # Aynı temizlik eşlemesi için 11 parça başlığını bul
    parts_map = match_part_columns(df, EQUAL_PART_PATTERNS)

    # Kategorikler (modele alınacaklar; EXCLUDE dışında)
    name_candidates = [
        "Seri", "Model",
        "Yakıt Tipi", "Yakıt",
        "Vites", "Şanzıman",
        "Kasa Tipi", "Çekiş",
        "Renk"
    ]
    cat_cols = [n for n in name_candidates if (n in df.columns and n not in EXCLUDE_FROM_MODEL)]

    # Sayısallar (baz + numerikler)
    base_feature_name = "_baseline50"
    num_cols = [
        "_year","_km","_age","_km_per_year","_km_bin_20k",
        "_boyali_count","_degisen_count","_crit_boyali","_crit_degisen","_cos_boyali","_cos_degisen",
        base_feature_name
    ]

    # Global kategorik hizalama
    df = prepare_categoricals(df, cat_cols)

    # ============ 5-fold CV ============
    kf = KFold(n_splits=N_SPLITS, shuffle=True, random_state=RANDOM_SEED)
    oof_pred   = np.zeros(len(df))
    oof_base50 = np.zeros(len(df))
    oof_same30 = np.zeros(len(df))
    fold_mae, fold_mape, fold_hit = [], [], []

    # LGBM parametreleri
    params = {
        "objective": "regression_l1",
        "metric": "l1",
        "learning_rate": 0.03,
        "num_leaves": 63,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "lambda_l1": 1.0,
        "lambda_l2": 2.0,
        "max_depth": -1,
        "verbosity": -1,
        "seed": RANDOM_SEED,
    }
    if USE_GPU:
        params.update({"device": "gpu", "gpu_platform_id": 0, "gpu_device_id": 0, "gpu_use_dp": False})

    for i, (tr_idx, va_idx) in enumerate(kf.split(df), 1):
        df_tr, df_va = df.iloc[tr_idx].copy(), df.iloc[va_idx].copy()

        # Bazlar (train referansına göre)
        base_tr = build_lenient_base(df_tr, df_tr, col_model, "_year", "_km")
        base_va = build_lenient_base(df_tr, df_va, col_model, "_year", "_km")
        same30_va = build_same_clean_avg(df_tr, df_va, col_model, "_year", "_km", parts_map)

        # Hedef: log(price) - log(baz)
        y_tr_resid = np.log(df_tr["_price"].values) - np.log(base_tr)
        y_va_resid = np.log(df_va["_price"].values) - np.log(base_va)

        # Özellik tabloları
        X_tr = pd.DataFrame({c: df_tr[c] for c in num_cols if c != base_feature_name})
        X_va = pd.DataFrame({c: df_va[c] for c in num_cols if c != base_feature_name})
        X_tr[base_feature_name] = base_tr
        X_va[base_feature_name] = base_va

        # Kategorikler ekle (zaten category dtype)
        for c in cat_cols:
            X_tr[c] = df_tr[c]
            X_va[c] = df_va[c]

        # LGBM Dataset
        cat_features = [c for c in cat_cols if c in X_tr.columns]
        lgb_tr = lgb.Dataset(X_tr, label=y_tr_resid, categorical_feature=cat_features, free_raw_data=False)
        lgb_va = lgb.Dataset(X_va, label=y_va_resid, categorical_feature=cat_features, reference=lgb_tr, free_raw_data=False)

        callbacks = [
            lgb.early_stopping(stopping_rounds=200, verbose=False),
            # lgb.log_evaluation(period=100),  # isterseniz açın
        ]

        model = lgb.train(
            params,
            lgb_tr,
            valid_sets=[lgb_tr, lgb_va],
            valid_names=["train","valid"],
            num_boost_round=5000,
            callbacks=callbacks
        )

        best_iter = getattr(model, "best_iteration", None)
        if not best_iter:
            best_iter = getattr(model, "current_iteration", lambda: None)()

        resid_pred = model.predict(X_va, num_iteration=best_iter)
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

    # ============ Final model (tüm veri) ============
    base_full = build_lenient_base(df, df, col_model, "_year", "_km")
    same30_all = build_same_clean_avg(df, df, col_model, "_year", "_km", parts_map)
    y_full_resid = np.log(df["_price"].values) - np.log(base_full)

    X_full = pd.DataFrame({c: df[c] for c in num_cols if c != base_feature_name})
    X_full[base_feature_name] = base_full
    for c in cat_cols:
        X_full[c] = df[c]  # category dtype

    cat_features_full = [c for c in cat_cols if c in X_full.columns]
    lgb_full = lgb.Dataset(X_full, label=y_full_resid, categorical_feature=cat_features_full, free_raw_data=False)

    params_full = params.copy()
    # Final modelde early stopping yok (tüm veri ile)
    model_full = lgb.train(
        params_full,
        lgb_full,
        num_boost_round=8000,
        valid_sets=[lgb_full],
        valid_names=["train"]
    )

    model_path = OUT_DIR / "corolla_residual_lgbm.txt"
    model_full.save_model(str(model_path))
    print(f"Kaydedildi: {model_path}")

    # Bilgi amaçlı tüm veri tahmini
    best_iter_full = getattr(model_full, "best_iteration", None)
    if not best_iter_full:
        best_iter_full = getattr(model_full, "current_iteration", lambda: None)()
    full_out = df_raw.copy()
    full_out["Ayni_Temizlik_Ort_30k"] = same30_all
    full_out["Benzer_Ort_50k_Yil±1"]  = base_full
    full_out["Model_Tahmin"] = np.exp(np.log(base_full) + model_full.predict(X_full, num_iteration=best_iter_full))
    full_out["Fark"] = full_out["Model_Tahmin"] - df["_price"].values
    full_all_path = OUT_DIR / "tum_veri_tahmin.xlsx"
    full_out.to_excel(full_all_path, index=False)
    print(f"Kaydedildi: {full_all_path}")

    # İnference için meta bilgileri (özellik isimleri vb.) kaydet
    meta = {
        "feature_names": list(X_full.columns),
        "categorical_features": cat_features_full,
        "base_feature": base_feature_name,
        "col_model": col_model,
        "parts_map": parts_map
    }
    with open(OUT_DIR / "lgbm_feature_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"Kaydedildi: {OUT_DIR / 'lgbm_feature_meta.json'}")

if __name__ == "__main__":
    main()
