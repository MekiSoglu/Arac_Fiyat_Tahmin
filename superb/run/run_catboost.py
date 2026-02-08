# run_catboost.py
# -*- coding: utf-8 -*-

import re
import numpy as np
import pandas as pd
from pathlib import Path
from catboost import CatBoostRegressor, Pool

class CatBoostPassatRunner:
    # ======= yollar =======
    MODEL_PATH = Path(r"/passat/passat_model_tahmin\catboster\passat_residual_catboost.cbm")
    INPUT_PATH = Path(r"C:\Users\EXCALIBUR\Desktop\sahibinden\passat_bugun_filtreli_20250911_2100.xlsx")
    REF_PATH   = Path(r"/tüm_passat_ilanları/full_passat.xlsx")  # comps
    OUT_PATH   = INPUT_PATH.with_name(INPUT_PATH.stem + "_pred.xlsx")

    # ======= sabitler =======
    EQUAL_PART_PATTERNS = [
        "Arka Tampon", "Bagaj Kapağı", "Motor Kaputu",
        "Sağ Arka Kapı", "Sağ Ön Kapı", "Sağ Ön Çamurluk",
        "Sol Arka Kapı", "Sol Ön Kapı", "Sol Ön Çamurluk",
        "Tavan", "Ön Tampon"
    ]
    EXCLUDE_FROM_MODEL = {
        "Adres", "Ağır Hasar Kayıtlı", "Garanti", "Plaka / Uyruk",
        "İl", "İlçe", "İlan No", "İlan Tarihi", "Link", "Boya-değişen"
    }

    # ======= yardımcılar =======
    @staticmethod
    def to_number(x):
        if pd.isna(x): return np.nan
        if isinstance(x, (int, float, np.integer, np.floating)): return float(x)
        s = str(x).replace(".", "").replace(",", ".")
        s = re.sub(r"[^0-9.]", "", s)
        try: return float(s)
        except: return np.nan

    @staticmethod
    def parse_year_strict(x):
        if pd.isna(x): return np.nan
        m = re.search(r"\b(19|20)\d{2}\b", str(x))
        if m: return float(m.group(0))
        return CatBoostPassatRunner.to_number(x)

    @staticmethod
    def pick_col(df, keys_lc):
        for c in df.columns:
            cl = c.lower()
            for k in keys_lc:
                if k in cl: return c
        return None

    @staticmethod
    def find_first(df, names):
        for n in names:
            if n in df.columns: return n
        return None

    @staticmethod
    def km_bin20k(km):
        try: return int(float(km) // 20_000)
        except: return np.nan

    @staticmethod
    def normalize_tr(s: str) -> str:
        return (s.replace("ğ","g").replace("Ğ","G")
                 .replace("ş","s").replace("Ş","S")
                 .replace("ı","i").replace("İ","I")
                 .replace("ç","c").replace("Ç","C")
                 .replace("ö","o").replace("Ö","O")
                 .replace("ü","u").replace("Ü","U"))

    @staticmethod
    def norm_damage(val: object) -> str:
        s = str(val).strip().lower()
        if s in ("", "nan", "-", "—", "yok", "none"): return ""
        if ("değiş" in s) or ("degis" in s): return "degisen"
        if "boya" in s: return "boyali"
        if "orij" in s or "orj" in s or "temiz" in s or "hasarsiz" in s: return ""
        return s

    @classmethod
    def match_part_columns(cls, df, patterns):
        out = {}
        cols = list(df.columns)
        cols_norm = [cls.normalize_tr(c.lower()) for c in cols]
        for pat in patterns:
            pat_norm = cls.normalize_tr(pat.lower())
            best = None
            for c in cols:
                if pat.lower() in c.lower(): best = c; break
            if best is None:
                for i, cn in enumerate(cols_norm):
                    if pat_norm in cn: best = cols[i]; break
            if best: out[pat] = best
        return out

    @classmethod
    def standardize_parts(cls, df, parts_map):
        for pat, col in parts_map.items():
            std_col = f"PART::{pat}"
            if col in df.columns:
                df[std_col] = df[col]
            else:
                df[std_col] = np.nan
        return df

    # ================= benzerlik (baz) fonksiyonları =================
    @classmethod
    def avg_same_clean_std(cls, df_ref, row, km_window=30_000, min_n=3):
        model = row.get("Model", np.nan)
        year  = row.get("_year", np.nan)
        km    = row.get("_km", np.nan)
        if pd.isna(model) or pd.isna(year) or pd.isna(km):
            return np.nan

        m =  (df_ref["Model"] == model) & (df_ref["_year"] == year) \
           & (df_ref["_km"].between(km - km_window, km + km_window))

        for pat in cls.EQUAL_PART_PATTERNS:
            col_std = f"PART::{pat}"
            if col_std in df_ref.columns:
                ref_val = cls.norm_damage(row.get(col_std, ""))
                m = m & (df_ref[col_std].map(cls.norm_damage) == ref_val)

        cand = df_ref[m & (df_ref["_price"] > 0)]
        if len(cand) >= min_n: return float(cand["_price"].mean())

        cand = df_ref[(df_ref["Model"] == model) & (df_ref["_year"] == year) &
                      (df_ref["_km"].between(km - km_window, km + km_window)) & (df_ref["_price"] > 0)]
        return float(cand["_price"].mean()) if len(cand) >= min_n else np.nan

    @staticmethod
    def avg_lenient(df_ref, row, km_window=50_000, year_window=1, min_n=3):
        model = row.get("Model", np.nan)
        year  = row.get("_year", np.nan)
        km    = row.get("_km", np.nan)
        if pd.isna(model) or pd.isna(year) or pd.isna(km): return np.nan
        m = ((df_ref["Model"] == model) &
             (df_ref["_year"].between(year - year_window, year + year_window)) &
             (df_ref["_km"].between(km - km_window, km + km_window)) &
             (df_ref["_price"] > 0))
        cand = df_ref[m]
        return float(cand["_price"].mean()) if len(cand) >= min_n else np.nan

    # ================= veri hazırlama =================
    @classmethod
    def prepare_df(cls, df, require_price=False):
        df = df.loc[:, ~pd.Index(df.columns).duplicated()].copy()
        df.columns = [str(c).strip() for c in df.columns]

        col_price = cls.find_first(df, ["Fiyat"]) or cls.pick_col(df, ["fiyat","price"])
        col_year  = cls.find_first(df, ["Yıl","Yil"]) or cls.pick_col(df, ["yıl","yil","year","model yılı","model yili"])
        col_km    = cls.find_first(df, ["KM","Kilometre"]) or cls.pick_col(df, ["km","kilometre","kilometer"])
        col_model = cls.find_first(df, ["Model"])
        if not (col_year and col_km and col_model):
            raise ValueError("Gerekli sütunlar yok: Model, Yıl, KM (ve tercihen Fiyat).")

        if col_price: df["_price"] = df[col_price].map(cls.to_number)
        else:         df["_price"] = np.nan
        df["_year"]  = df[col_year].map(cls.parse_year_strict)
        df["_km"]    = df[col_km].map(cls.to_number)

        mask = ((df["_year"] >= 2015) &
                (df["_km"] >= 0) & (df["_km"] < 2e6))
        if require_price:
            mask = mask & (df["_price"] > 0) & (df["_price"] < 1e9)
        df = df[mask].copy()

        df["_age"]         = 2025 - df["_year"]
        df["_km_per_year"] = df["_km"] / np.maximum(df["_age"], 1)
        df["_km_bin_20k"]  = df["_km"].map(cls.km_bin20k)

        part_cols_any = [c for c in df.columns if any(k in c.lower() for k in
            ["kaput","tavan","tampon","çamurluk","çamurlug","camurluk","kapı","kapi",
             "bagaj","şasi","sasi","direk","podye","marşpiyel","marspiyel"])]
        df["_boyali_count"]  = 0
        df["_degisen_count"] = 0
        df["_crit_boyali"]   = 0
        df["_crit_degisen"]  = 0
        df["_cos_boyali"]    = 0
        df["_cos_degisen"]   = 0
        critical = ["kaput","tavan","şasi","sasi","direk","podye"]
        cosmetic  = ["tampon"]

        def is_critical(col): return any(k in col.lower() for k in critical)
        def is_cosmetic(col): return any(k in col.lower() for k in cosmetic)

        for c in part_cols_any:
            b = df[c].map(lambda v: 1 if "boya" in str(v).lower() else 0).astype(int)
            d = df[c].map(lambda v: 1 if (("değiş" in str(v).lower()) or ("degis" in str(v).lower())) else 0).astype(int)
            df["_boyali_count"]  += b
            df["_degisen_count"] += d
            if is_critical(c): df["_crit_boyali"] += b; df["_crit_degisen"] += d
            if is_cosmetic(c): df["_cos_boyali"]  += b; df["_cos_degisen"]  += d

        parts_map = cls.match_part_columns(df, cls.EQUAL_PART_PATTERNS)
        df = cls.standardize_parts(df, parts_map)

        name_candidates = [
            "Seri", "Model",
            "Yakıt Tipi", "Yakıt",
            "Vites", "Şanzıman",
            "Kasa Tipi", "Çekiş",
            "Renk"
        ]
        cat_cols = [n for n in name_candidates if (n in df.columns and n not in cls.EXCLUDE_FROM_MODEL)]
        for c in cat_cols:
            df[c] = df[c].astype("string").fillna("NA")

        info = {
            "parts_map": parts_map,
            "cat_cols": cat_cols,
            "num_cols": [
                "_year","_km","_age","_km_per_year","_km_bin_20k",
                "_boyali_count","_degisen_count","_crit_boyali","_crit_degisen","_cos_boyali","_cos_degisen",
            ]
        }
        return df, info

    # ================= ana akış =================
    def run(self):
        # model
        model_path = self.MODEL_PATH
        if not model_path.exists():
            cands = sorted(model_path.parent.rglob("*.cbm"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not cands:
                raise FileNotFoundError(f"Model bulunamadı: {self.MODEL_PATH}")
            model_path = cands[0]
        model = CatBoostRegressor()
        model.load_model(str(model_path))
        print(f"✅ Model: {model_path}")

        # referans (comps)
        if not self.REF_PATH.exists():
            raise FileNotFoundError(f"Referans Excel yok: {self.REF_PATH}")
        df_ref_raw = pd.read_excel(self.REF_PATH)
        df_ref, _ = self.prepare_df(df_ref_raw, require_price=True)
        df_ref = self.standardize_parts(df_ref, self.match_part_columns(df_ref, self.EQUAL_PART_PATTERNS))

        # girdi
        if not self.INPUT_PATH.exists():
            raise FileNotFoundError(f"Girdi Excel yok: {self.INPUT_PATH}")
        df_in_raw = pd.read_excel(self.INPUT_PATH)
        df_in, ininfo = self.prepare_df(df_in_raw, require_price=False)
        df_in = self.standardize_parts(df_in, self.match_part_columns(df_in, self.EQUAL_PART_PATTERNS))

        # benzerlik bazları
        same30 = []
        base50 = []
        gmed = float(df_ref["_price"].median()) if len(df_ref) else np.nan
        for _, row in df_in.iterrows():
            v1 = self.avg_same_clean_std(df_ref, row, km_window=30_000, min_n=3)
            v2 = self.avg_lenient(df_ref, row, km_window=50_000, year_window=1, min_n=3)
            same30.append(np.nan if pd.isna(v1) else float(v1))
            base50.append(gmed if pd.isna(v2) else max(1.0, float(v2)))
        same30 = np.array(same30, dtype=float)
        base50 = np.array(base50, dtype=float)

        # özellik matrisi
        num_cols = ininfo["num_cols"]
        cat_cols = ininfo["cat_cols"]
        X = pd.DataFrame(index=df_in.index)
        for c in num_cols: X[c] = df_in[c].astype(float)
        for c in cat_cols: X[c] = df_in[c].astype("string").fillna("NA")
        X["_baseline50"] = base50

        cat_in = [c for c in cat_cols if c in X.columns]
        pool = Pool(X, cat_features=cat_in)

        # tahmin
        resid_pred = model.predict(pool)
        price_pred = np.exp(np.log(base50) + resid_pred)

        # çıktı: orijinal dosya + 3 kolon (renk ve 'Fark' yok)
        out_df = df_in_raw.copy()
        out_df["Ayni_Temizlik_Ort_30k"] = np.nan
        out_df["Benzer_Ort_50k_Yil±1"]  = np.nan
        out_df["Model_Tahmin"]          = np.nan

        out_df.loc[df_in.index, "Ayni_Temizlik_Ort_30k"] = same30
        out_df.loc[df_in.index, "Benzer_Ort_50k_Yil±1"]  = base50
        out_df.loc[df_in.index, "Model_Tahmin"]          = price_pred

        out_df.to_excel(self.OUT_PATH, index=False)
        print(f"✅ Kaydedildi: {self.OUT_PATH}")

if __name__ == "__main__":
    CatBoostPassatRunner().run()
