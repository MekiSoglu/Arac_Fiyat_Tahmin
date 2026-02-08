# run_light.py
# -*- coding: utf-8 -*-

r"""
Inference scripti:
- Model / meta: C:\Users\EXCALIBUR\Desktop\sahibinden\passat_model_tahmin\light\
- Girdi:        C:\Users\EXCALIBUR\Desktop\sahibinden\passat_bugun_filtreli_20250911_2100.xlsx
- Referans:     C:\Users\EXCALIBUR\Desktop\sahibinden\full_passat.xlsx
- Çıktı:        aynı klasörde *_pred.xlsx
"""

import json
import re
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from xlsxwriter.utility import xl_col_to_name  # satır renklendirme için

class PassatPricePredictor:
    # ======= kullanıcı yolları =======
    MODEL_DIR  = Path(r"/passat/passat_model_tahmin")  # KÖK; içinde light da taranır
    INPUT_PATH = Path(r"C:\Users\EXCALIBUR\Desktop\sahibinden\passat_bugun_filtreli_20250911_2100.xlsx")
    REF_PATH   = Path(r"/tüm_passat_ilanları/full_passat.xlsx")  # comps için
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
    BASE_FEATURE_DEFAULT = "_baseline50"

    # ========================= yardımcılar =========================
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
        return PassatPricePredictor.to_number(x)

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
        try:
            return int(float(km) // 20_000)
        except:
            return np.nan

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
        # Her parça için 'PART::<pattern>' adlı standart kolon oluştur.
        for pat, col in parts_map.items():
            std_col = f"PART::{pat}"
            if col in df.columns:
                df[std_col] = df[col]
            else:
                df[std_col] = np.nan
        return df

    # ===================== benzerlik (baz) fonksiyonları =====================
    @classmethod
    def avg_same_clean(cls, df_ref, row, km_window=30_000, min_n=3):
        # Model aynı, Yıl aynı, KM ±30k, 11 parça birebir aynı.
        model = row.get("Model", np.nan)
        year  = row.get("_year", np.nan)
        km    = row.get("_km", np.nan)
        if pd.isna(model) or pd.isna(year) or pd.isna(km):
            return np.nan

        m =  (df_ref["Model"] == model) & (df_ref["_year"] == year) \
           & (df_ref["_km"].between(km - km_window, km + km_window))

        for pat in cls.EQUAL_PART_PATTERNS:
            col_std = f"PART::{pat}"
            ref_val = cls.norm_damage(row.get(col_std, ""))
            if col_std in df_ref.columns:
                m = m & (df_ref[col_std].map(cls.norm_damage) == ref_val)

        cand = df_ref[m & (df_ref["_price"] > 0)]
        if len(cand) >= min_n:
            return float(cand["_price"].mean())

        # fallback: parçasız
        cand = df_ref[(df_ref["Model"] == model) & (df_ref["_year"] == year) &
                      (df_ref["_km"].between(km - km_window, km + km_window)) & (df_ref["_price"] > 0)]
        return float(cand["_price"].mean()) if len(cand) >= min_n else np.nan

    @staticmethod
    def avg_lenient(df_ref, row, km_window=50_000, year_window=1, min_n=3):
        # Model aynı, Yıl ±1, KM ±50k, parçalar farklı olabilir.
        model = row.get("Model", np.nan)
        year  = row.get("_year", np.nan)
        km    = row.get("_km", np.nan)
        if pd.isna(model) or pd.isna(year) or pd.isna(km):
            return np.nan
        m = ((df_ref["Model"] == model) &
             (df_ref["_year"].between(year - year_window, year + year_window)) &
             (df_ref["_km"].between(km - km_window, km + km_window)) &
             (df_ref["_price"] > 0))
        cand = df_ref[m]
        return float(cand["_price"].mean()) if len(cand) >= min_n else np.nan

    # ========================= veri hazırlama =========================
    @classmethod
    def prepare_df(cls, df, require_price=False):
        # eğitimdeki ile aynı türevler + parça standardizasyonu
        df = df.loc[:, ~pd.Index(df.columns).duplicated()].copy()
        df.columns = [str(c).strip() for c in df.columns]

        col_price = cls.find_first(df, ["Fiyat"]) or cls.pick_col(df, ["fiyat","price"])
        col_year  = cls.find_first(df, ["Yıl","Yil"]) or cls.pick_col(df, ["yıl","yil","year","model yılı","model yili"])
        col_km    = cls.find_first(df, ["KM","Kilometre"]) or cls.pick_col(df, ["km","kilometre","kilometer"])
        col_model = cls.find_first(df, ["Model"])
        if not (col_year and col_km and col_model):
            raise ValueError("Model/Yıl/KM sütunları bulunamadı (en az: Model, Yıl, KM).")

        # numerikler
        if col_price:
            df["_price"] = df[col_price].map(cls.to_number)
        else:
            df["_price"] = np.nan
        df["_year"] = df[col_year].map(cls.parse_year_strict)
        df["_km"]   = df[col_km].map(cls.to_number)

        # filtre
        mask = ((df["_year"] >= 2015) &
                (df["_km"] >= 0) & (df["_km"] < 2e6))
        if require_price:
            mask = mask & (df["_price"] > 0) & (df["_price"] < 1e9)
        df = df[mask].copy()

        # türevler
        df["_age"]         = 2025 - df["_year"]
        df["_km_per_year"] = df["_km"] / np.maximum(df["_age"], 1)
        df["_km_bin_20k"]  = df["_km"].map(cls.km_bin20k)

        # parça sayaçları
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
        cosmetic_keywords  = ["tampon"]
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

        # parça standart kolonları
        parts_map = cls.match_part_columns(df, cls.EQUAL_PART_PATTERNS)
        df = cls.standardize_parts(df, parts_map)

        # kategorikler (eğitimle uyumlu isimler)
        name_candidates = [
            "Seri", "Model",
            "Yakıt Tipi", "Yakıt",
            "Vites", "Şanzıman",
            "Kasa Tipi", "Çekiş",
            "Renk"
        ]
        cat_cols = [n for n in name_candidates if n in df.columns and n not in cls.EXCLUDE_FROM_MODEL]
        for c in cat_cols:
            df[c] = df[c].astype("string").fillna("NA")

        meta_local = {
            "parts_map": parts_map,
            "cat_cols": cat_cols,
            "feature_num_cols": [
                "_year","_km","_age","_km_per_year","_km_bin_20k",
                "_boyali_count","_degisen_count","_crit_boyali","_crit_degisen","_cos_boyali","_cos_degisen"
            ]
        }
        return df, meta_local

    # ========================= ana akış =========================
    def run(self):
        # ---- META'yı bul ----
        meta_path = None
        cand = (self.MODEL_DIR / "light" / "lgbm_feature_meta.json")
        if cand.exists(): meta_path = cand
        cand2 = (self.MODEL_DIR / "lgbm_feature_meta.json")
        if (meta_path is None) and cand2.exists(): meta_path = cand2
        if meta_path is None:
            js = sorted(self.MODEL_DIR.rglob("lgbm_feature_meta.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if js: meta_path = js[0]
        if meta_path is None:
            raise FileNotFoundError("lgbm_feature_meta.json bulunamadı (MODEL_DIR ve altını kontrol edin).")
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        feature_names = meta.get("feature_names", [])
        categorical_features_meta = set(meta.get("categorical_features", []))
        base_feature_name = meta.get("base_feature", self.BASE_FEATURE_DEFAULT)

        # ---- MODEL (.txt) bul ----
        model_path = None
        for p in [
            self.MODEL_DIR / "corolla_residual_lgbm.txt",
            self.MODEL_DIR / "light" / "corolla_residual_lgbm.txt"
        ]:
            if p.exists():
                model_path = p; break
        if model_path is None:
            txts = sorted(self.MODEL_DIR.rglob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
            if txts: model_path = txts[0]
        if model_path is None or not model_path.exists():
            raise FileNotFoundError("Model .txt bulunamadı. Eğitimden çıkan 'corolla_residual_lgbm.txt' dosyasını MODEL_DIR içine koyun.")
        print(f"✅ Meta:   {meta_path}")
        print(f"✅ Model:  {model_path}")

        booster = lgb.Booster(model_file=str(model_path))

        # ---- Referans (comps) ----
        if not self.REF_PATH.exists():
            cands = sorted(self.MODEL_DIR.parent.rglob("full_passat*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not cands:
                raise FileNotFoundError("Referans full_passat*.xlsx bulunamadı.")
            self.REF_PATH = cands[0]
        df_ref_raw = pd.read_excel(self.REF_PATH)
        df_ref, _ = self.prepare_df(df_ref_raw, require_price=True)  # comps'ta fiyat zorunlu

        # ---- Girdi dosyası ----
        if not self.INPUT_PATH.exists():
            raise FileNotFoundError(f"Girdi dosyası bulunamadı: {self.INPUT_PATH}")
        df_in_raw = pd.read_excel(self.INPUT_PATH)
        df_in, in_info = self.prepare_df(df_in_raw, require_price=False)

        # ---- Benzerlikler (bazlar) ----
        same30 = []
        base50 = []
        gmed = float(df_ref["_price"].median()) if len(df_ref) else np.nan
        for _, row in df_in.iterrows():
            v1 = self.avg_same_clean(df_ref, row, km_window=30_000, min_n=3)
            v2 = self.avg_lenient(df_ref, row, km_window=50_000, year_window=1, min_n=3)
            same30.append(np.nan if pd.isna(v1) else float(v1))
            base50.append(gmed if pd.isna(v2) else max(1.0, float(v2)))
        same30 = np.array(same30, dtype=float)
        base50 = np.array(base50, dtype=float)

        # ---- Özellik matrisi ----
        num_cols_local = in_info["feature_num_cols"]
        X = pd.DataFrame(index=df_in.index)
        for c in num_cols_local:
            X[c] = df_in[c].astype(float)
        X[base_feature_name] = base50

        # kategorikler → string
        for c in in_info["cat_cols"]:
            X[c] = df_in[c].astype("string").fillna("NA")

        # Eğitimdeki feature listesi verilmişse sırayı sabitle ve eksikleri doldur
        if feature_names:
            for c in feature_names:
                if c not in X.columns:
                    if c in categorical_features_meta:
                        X[c] = pd.Series(["NA"] * len(X), dtype="string")
                    else:
                        X[c] = 0.0
            X = X[feature_names]

        # === KRİTİK ADIM: kategorik dtype'ı eğitimle EŞLE ===
        cat_feats_list = list(categorical_features_meta)
        for c in cat_feats_list:
            if c not in X.columns:
                X[c] = pd.Series(["NA"] * len(X), dtype="string")
            vals = X[c].astype("string").fillna("NA")
            cats = pd.Index(sorted(vals.unique().tolist()))
            X[c] = pd.Categorical(vals, categories=cats, ordered=False)

        # ---- Tahmin (rezidüel → fiyat) ----
        best_iter = getattr(booster, "best_iteration", None)
        if not best_iter:
            best_iter = getattr(booster, "current_iteration", lambda: None)()
        resid_pred = booster.predict(X, num_iteration=best_iter)
        price_pred = np.exp(np.log(base50) + resid_pred)

        # ---- Çıktı: giriş + 3 kolon + Fark ----
        out_df = df_in_raw.copy()
        out_df["Ayni_Temizlik_Ort_30k"] = same30
        out_df["Benzer_Ort_50k_Yil±1"]  = base50
        out_df["Model_Tahmin"]          = price_pred

        # Fark = Model_Tahmin - Fiyat (Fiyat varsa)
        price_col = self.find_first(out_df, ["Fiyat"]) or self.pick_col(out_df, ["fiyat","price"])
        if price_col:
            out_df["Fark"] = out_df["Model_Tahmin"] - out_df[price_col].map(self.to_number)
        else:
            out_df["Fark"] = np.nan  # fiyat yoksa boş kalsın

        # ===== Excel'e yaz + RENKLENDİRME =====
        with pd.ExcelWriter(self.OUT_PATH, engine="xlsxwriter") as writer:
            out_df.to_excel(writer, index=False, sheet_name="Sheet1")
            workbook  = writer.book
            worksheet = writer.sheets["Sheet1"]

            nrows, ncols = out_df.shape
            start_row, start_col = 1, 0  # header = 0. satır; data 1..n

            # Biçimler
            fmt_dark_green  = workbook.add_format({"bg_color": "#006100", "font_color": "#FFFFFF"})
            fmt_light_green = workbook.add_format({"bg_color": "#C6EFCE", "font_color": "#006100"})
            fmt_red         = workbook.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"})

            # Negatif Fark olan TÜM satırlar -> kırmızı (koşullu format)
            if "Fark" in out_df.columns:
                diff_col_idx = out_df.columns.get_loc("Fark")
                diff_col_letter = xl_col_to_name(diff_col_idx)  # 'A','B',...
                # Örn: =$F2<0 (satıra göre ayarlanır)
                formula = f"=${diff_col_letter}{start_row+1}<0"
                worksheet.conditional_format(
                    start_row, start_col, start_row + nrows - 1, start_col + ncols - 1,
                    {"type": "formula", "criteria": f"={formula}", "format": fmt_red}
                )

                # En büyük ve ikinci en büyük Fark > 0 satırlarını yeşil yap
                pos = out_df.loc[out_df["Fark"].notna() & (out_df["Fark"] > 0), "Fark"]
                if len(pos) >= 1:
                    top1_idx = pos.idxmax()
                    # Excel'de satır numarası (0-based data -> +1 header)
                    try:
                        rowpos1 = out_df.index.get_loc(top1_idx)
                    except Exception:
                        rowpos1 = int(top1_idx)
                    worksheet.set_row(start_row + rowpos1, None, fmt_dark_green)

                if len(pos) >= 2:
                    top2 = pos.nlargest(2)
                    second_idx = top2.index[1]
                    try:
                        rowpos2 = out_df.index.get_loc(second_idx)
                    except Exception:
                        rowpos2 = int(second_idx)
                    worksheet.set_row(start_row + rowpos2, None, fmt_light_green)

        print(f"✅ Kaydedildi: {self.OUT_PATH}")

if __name__ == "__main__":
    PassatPricePredictor().run()
