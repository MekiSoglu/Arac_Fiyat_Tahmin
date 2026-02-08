# run_ensemble.py
# -*- coding: utf-8 -*-

import json
import re
import numpy as np
import pandas as pd
from pathlib import Path
import lightgbm as lgb
from catboost import CatBoostRegressor, Pool
import joblib

# Math Log-Linear (tek yöntem)
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.linear_model import LinearRegression

# ---------------- Güvenli tablo okuma (xlsx/csv/xlsb vs.) ----------------
def read_spreadsheet(pathlike):
    p = Path(pathlike)
    ext = p.suffix.lower()
    try:
        if ext in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
            return pd.read_excel(p, engine="openpyxl")
        elif ext == ".xls":
            return pd.read_excel(p, engine="xlrd")
        elif ext == ".xlsb":
            return pd.read_excel(p, engine="pyxlsb")
        elif ext == ".ods":
            return pd.read_excel(p, engine="odf")
        elif ext == ".csv":
            return pd.read_csv(p)
        # Uzantı belirsiz ise sırayla dene
        for reader in (
            lambda: pd.read_excel(p, engine="openpyxl"),
            lambda: pd.read_excel(p, engine="xlrd"),
            lambda: pd.read_excel(p, engine="pyxlsb"),
            lambda: pd.read_excel(p, engine="odf"),
            lambda: pd.read_csv(p),
        ):
            try:
                return reader()
            except Exception:
                pass
        raise ValueError(f"Dosya uzantısı/formatı desteklenmiyor: {p}")
    except Exception as e:
        raise ValueError(f"Tablo okunamadı: {p} -> {e}")

class MeganeEnsembleRunner:
    # ====== yollar ======
    MODEL_DIR_LIGHT  = Path(r"C:\Users\EXCALIBUR\Desktop\sahibinden\megane\megane_model_tahminleri\light")
    MODEL_PATH_CAT   = Path(r"C:\Users\EXCALIBUR\Desktop\sahibinden\megane\megane_model_tahminleri\catboster\megane_residual_catboost.cbm")
    MODEL_PATH_RF    = Path(r"C:\Users\EXCALIBUR\Desktop\sahibinden\megane\megane_model_tahminleri\randomF\megane_residual_rf.joblib")
    MODEL_PATH_HGBR  = Path(r"C:\Users\EXCALIBUR\Desktop\sahibinden\megane\megane_model_tahminleri\stic\megane_residual_hgbr.joblib")
    REF_PATH         = Path(r"C:\Users\EXCALIBUR\Desktop\sahibinden\megane\gundelik_megane_ilanları\megane_12eylül.xlsx")
    INPUT_PATH       = Path(r"C:\Users\EXCALIBUR\Desktop\sahibinden\megane\gundelik_megane_ilanları\megane_17eylül.xlsx")
    OUT_PATH         = INPUT_PATH.with_name(INPUT_PATH.stem + "_pred_ensemble_sahibinden.xlsx")

    # ====== sabitler ======
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

    # ====== yardımcılar ======
    @staticmethod
    def to_number(x):
        if pd.isna(x): return np.nan
        if isinstance(x, (int, float, np.integer, np.floating)): return float(x)
        s = str(x).replace(".", "").replace(",", ".")
        s = re.sub(r"[^0-9.]", "", s)
        try: return float(s) if s else np.nan
        except: return np.nan

    @staticmethod
    def parse_km(x):
        if pd.isna(x): return np.nan
        s = str(x).lower().strip().replace("km", "").replace(" ", "")
        s = s.replace(".", "").replace(",", "")  # KM için kesir beklenmez
        s = re.sub(r"[^0-9]", "", s)
        try: return float(s) if s else np.nan
        except: return np.nan

    @staticmethod
    def parse_year_strict(x):
        if pd.isna(x): return np.nan
        m = re.search(r"\b(19|20)\d{2}\b", str(x))
        if m: return float(m.group(0))
        v = MeganeEnsembleRunner.to_number(x)
        return float(v) if not pd.isna(v) else np.nan

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
    def find_link_col(df):
        return (
            MeganeEnsembleRunner.find_first(df, ["Link", "URL", "Url", "Bağlantı", "Baglanti"])
            or MeganeEnsembleRunner.pick_col(df, ["link", "url", "bağlantı", "baglanti"])
        )

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
            df[std_col] = df[col] if col in df.columns else np.nan
        return df

    # ====== benzerlik (baz) ======
    @classmethod
    def avg_same_clean_std(cls, df_ref, row, km_window=30_000, min_n=3,
                           ref_link_col=None, exclude_link=None):
        model = row.get("Model", np.nan)
        year  = row.get("_year", np.nan)
        km    = row.get("_km", np.nan)
        if pd.isna(model) or pd.isna(year) or pd.isna(km): return np.nan

        m = ((df_ref["Model"] == model) &
             (df_ref["_year"] == year) &
             (df_ref["_km"].between(km - km_window, km + km_window)))

        if ref_link_col and exclude_link is not None and ref_link_col in df_ref.columns:
            m = m & (df_ref[ref_link_col].astype(str) != str(exclude_link))

        for pat in cls.EQUAL_PART_PATTERNS:
            col_std = f"PART::{pat}"
            if col_std in df_ref.columns:
                ref_val = cls.norm_damage(row.get(col_std, ""))
                m = m & (df_ref[col_std].map(cls.norm_damage) == ref_val)

        cand = df_ref[m & (df_ref["_price"] > 0)]
        if len(cand) >= min_n: return float(cand["_price"].mean())

        cand = df_ref[(df_ref["Model"] == model) &
                      (df_ref["_year"] == year) &
                      (df_ref["_km"].between(km - km_window, km + km_window)) &
                      (df_ref["_price"] > 0)]
        if ref_link_col and exclude_link is not None and ref_link_col in cand.columns:
            cand = cand[cand[ref_link_col].astype(str) != str(exclude_link)]
        return float(cand["_price"].mean()) if len(cand) >= min_n else np.nan

    @staticmethod
    def avg_lenient(df_ref, row, km_window=50_000, year_window=1, min_n=3,
                    ref_link_col=None, exclude_link=None):
        model = row.get("Model", np.nan)
        year  = row.get("_year", np.nan)
        km    = row.get("_km", np.nan)
        if pd.isna(model) or pd.isna(year) or pd.isna(km): return np.nan

        m = ((df_ref["Model"] == model) &
             (df_ref["_year"].between(year - year_window, year + year_window)) &
             (df_ref["_km"].between(km - km_window, km + km_window)) &
             (df_ref["_price"] > 0))

        if ref_link_col and exclude_link is not None and ref_link_col in df_ref.columns:
            m = m & (df_ref[ref_link_col].astype(str) != str(exclude_link))

        cand = df_ref[m]
        return float(cand["_price"].mean()) if len(cand) >= min_n else np.nan

    # ====== veri hazırlama ======
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
        df["_km"]    = df[col_km].map(cls.parse_km)

        mask = ((df["_year"] >= 2015) & (df["_km"] >= 0) & (df["_km"] < 2e6))
        if require_price: mask = mask & (df["_price"] > 0) & (df["_price"] < 1e9)
        df = df[mask].copy()

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

        name_candidates = ["Seri","Model","Yakıt Tipi","Yakıt","Vites","Şanzıman","Kasa Tipi","Çekiş","Renk"]
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

    # ====== çalıştır ======
    def run(self):
        # --- CatBoost ---
        cb_ok = False
        try:
            model_cat_path = self.MODEL_PATH_CAT
            if not model_cat_path.exists():
                cands = sorted(self.MODEL_PATH_CAT.parent.rglob("*.cbm"), key=lambda p: p.stat().st_mtime, reverse=True)
                if cands: model_cat_path = cands[0]
            cat_model = CatBoostRegressor()
            cat_model.load_model(str(model_cat_path))
            cb_ok = True
            print(f"✅ CatBoost: {model_cat_path}")
        except Exception as e:
            print(f"⚠️ CatBoost yüklenemedi: {e}")

        # --- LightGBM (txt + meta) ---
        lgb_ok = False
        try:
            meta_path = None
            for p in [self.MODEL_DIR_LIGHT / "light" / "lgbm_feature_meta.json",
                      self.MODEL_DIR_LIGHT / "lgbm_feature_meta.json"]:
                if p.exists(): meta_path = p; break
            if meta_path is None:
                js = sorted(self.MODEL_DIR_LIGHT.rglob("lgbm_feature_meta.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                if js: meta_path = js[0]
            if meta_path is None:
                raise FileNotFoundError("LightGBM meta (lgbm_feature_meta.json) bulunamadı.")
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            feature_names = meta.get("feature_names", [])
            categorical_features_meta = set(meta.get("categorical_features", []))
            base_feature_name = meta.get("base_feature", "_baseline50")

            model_lgb_path = None
            for p in [self.MODEL_DIR_LIGHT / "light" / "megane_residual_lgbm.txt",
                      self.MODEL_DIR_LIGHT / "megane_residual_lgbm.txt"]:
                if p.exists(): model_lgb_path = p; break
            if model_lgb_path is None:
                txts = sorted(self.MODEL_DIR_LIGHT.rglob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
                if txts: model_lgb_path = txts[0]
            if model_lgb_path is None or not model_lgb_path.exists():
                raise FileNotFoundError("LightGBM model (megane_residual_lgbm.txt) bulunamadı.")
            booster = lgb.Booster(model_file=str(model_lgb_path))
            lgb_ok = True
            print(f"✅ LightGBM Meta: {meta_path}")
            print(f"✅ LightGBM Txt : {model_lgb_path}")
        except Exception as e:
            print(f"⚠️ LightGBM yüklenemedi: {e}")
            feature_names = []
            categorical_features_meta = set()
            base_feature_name = "_baseline50"
            booster = None

        # --- RandomForest (joblib) ---
        rf_ok = False
        try:
            model_rf_path = self.MODEL_PATH_RF
            if not model_rf_path.exists():
                cands = sorted(self.MODEL_PATH_RF.parent.rglob("*.joblib"), key=lambda p: p.stat().st_mtime, reverse=True)
                if cands: model_rf_path = cands[0]
            bundle_rf = joblib.load(model_rf_path)
            pipe_rf = bundle_rf["pipeline"]
            rf_num_cols = bundle_rf["num_cols"]
            rf_cat_cols = bundle_rf["cat_cols"]
            rf_base_feat = bundle_rf.get("base_feature", "_baseline50")
            rf_ok = True
            print(f"✅ RandomForest: {model_rf_path}")
        except Exception as e:
            print(f"⚠️ RandomForest yüklenemedi: {e}")
            pipe_rf = None
            rf_num_cols, rf_cat_cols, rf_base_feat = [], [], "_baseline50"

        # --- scikit-learn HGBR (joblib) ---
        hgbr_ok = False
        try:
            model_hgbr_path = self.MODEL_PATH_HGBR
            if not model_hgbr_path.exists():
                cands = sorted(self.MODEL_PATH_HGBR.parent.rglob("*.joblib"), key=lambda p: p.stat().st_mtime, reverse=True)
                if cands: model_hgbr_path = cands[0]
            bundle_hgbr = joblib.load(model_hgbr_path)
            pipe_hgbr = bundle_hgbr["pipeline"]
            hgbr_num_cols = bundle_hgbr["num_cols"]
            hgbr_cat_cols = bundle_hgbr["cat_cols"]
            hgbr_base_feat = bundle_hgbr.get("base_feature", "_baseline50")
            hgbr_ok = True
            print(f"✅ HGBR (scikit-learn): {model_hgbr_path}")
        except Exception as e:
            print(f"⚠️ HGBR yüklenemedi: {e}")
            pipe_hgbr = None
            hgbr_num_cols, hgbr_cat_cols, hgbr_base_feat = [], [], "_baseline50"

        # --- Referans (comps) — SADECE sabit verdiğin yolu kullan ---
        ref_path = self.REF_PATH
        if not ref_path.exists():
            raise FileNotFoundError(f"Referans dosyası bulunamadı: {ref_path}")

        df_ref_raw = read_spreadsheet(ref_path)
        df_ref, _ = self.prepare_df(df_ref_raw, require_price=True)
        if df_ref.empty:
            raise RuntimeError("Referans veri (df_ref) boş. Lütfen referans dosyasını kontrol edin.")
        df_ref = self.standardize_parts(df_ref, self.match_part_columns(df_ref, self.EQUAL_PART_PATTERNS))
        link_col_ref = self.find_link_col(df_ref)

        # --- Girdi — SADECE sabit verdiğin yolu kullan ---
        if not self.INPUT_PATH.exists():
            raise FileNotFoundError(f"Girdi bulunamadı: {self.INPUT_PATH}")
        df_in_raw = read_spreadsheet(self.INPUT_PATH)
        df_in, ininfo = self.prepare_df(df_in_raw, require_price=False)
        if df_in.empty:
            raise RuntimeError("Girdi veri (df_in) boş. INPUT_PATH içeriğini kontrol edin.")
        df_in = self.standardize_parts(df_in, self.match_part_columns(df_in, self.EQUAL_PART_PATTERNS))
        link_col_in = self.find_link_col(df_in)

        # --- Bazlar (tek sefer) ---
        same30, base50 = [], []
        gmed = float(df_ref["_price"].median()) if len(df_ref) else np.nan
        for _, row in df_in.iterrows():
            row_link = (row.get(link_col_in) if link_col_in in df_in.columns else None) if link_col_in else None

            v1 = self.avg_same_clean_std(
                df_ref, row,
                km_window=30_000, min_n=3,
                ref_link_col=link_col_ref, exclude_link=row_link
            )
            v2 = self.avg_lenient(
                df_ref, row,
                km_window=50_000, year_window=1, min_n=3,
                ref_link_col=link_col_ref, exclude_link=row_link
            )
            same30.append(np.nan if pd.isna(v1) else float(v1))
            base50.append(gmed if pd.isna(v2) else max(1.0, float(v2)))
        same30 = np.array(same30, dtype=float)
        base50 = np.array(base50, dtype=float)

        num_cols = ininfo["num_cols"]
        cat_cols = ininfo["cat_cols"]

        # --- CatBoost tahmini ---
        if cb_ok:
            X_cat = pd.DataFrame(index=df_in.index)
            for c in num_cols: X_cat[c] = df_in[c].astype(float)
            for c in cat_cols: X_cat[c] = df_in[c].astype("string").fillna("NA")
            X_cat[base_feature_name] = base50  # lgb meta'dan adı (yoksa "_baseline50")
            # CatBoost bazı sürümlerde isim yerine indeks istiyor:
            cat_idx = [X_cat.columns.get_loc(c) for c in cat_cols]
            pool_cat = Pool(X_cat, cat_features=cat_idx)
            resid_pred_cat = cat_model.predict(pool_cat)
            cat_price = np.exp(np.log(base50) + resid_pred_cat)
        else:
            cat_price = None

        # --- LightGBM tahmini ---
        if lgb_ok:
            X_lgb = pd.DataFrame(index=df_in.index)
            for c in num_cols: X_lgb[c] = df_in[c].astype(float)
            X_lgb[base_feature_name] = base50
            for c in cat_cols:
                vals = df_in[c].astype("string").fillna("NA")
                X_lgb[c] = pd.Categorical(vals, categories=sorted(vals.unique().tolist()), ordered=False)

            if feature_names:
                for c in feature_names:
                    if c not in X_lgb.columns:
                        if c in categorical_features_meta:
                            X_lgb[c] = pd.Categorical(pd.Series(["NA"] * len(X_lgb)), categories=["NA"], ordered=False)
                        else:
                            X_lgb[c] = 0.0
                X_lgb = X_lgb[feature_names]

            best_iter = getattr(booster, "best_iteration", None)
            if not best_iter:
                best_iter = getattr(booster, "current_iteration", lambda: None)()
            resid_pred_lgb = booster.predict(X_lgb, num_iteration=best_iter)
            lgb_price = np.exp(np.log(base50) + resid_pred_lgb)
        else:
            lgb_price = None

        # --- RandomForest tahmini ---
        if rf_ok:
            X_rf = pd.DataFrame(index=df_in.index)
            for c in rf_num_cols:
                if c == rf_base_feat: X_rf[c] = base50
                else:                 X_rf[c] = df_in[c].astype(float)
            for c in rf_cat_cols:
                X_rf[c] = df_in[c].astype("string").fillna("NA")
            resid_pred_rf = pipe_rf.predict(X_rf)
            rf_price = np.exp(np.log(base50) + resid_pred_rf)
        else:
            rf_price = None

        # --- HGBR (scikit-learn) tahmini ---
        if hgbr_ok:
            X_hgbr = pd.DataFrame(index=df_in.index)
            for c in hgbr_num_cols:
                if c == hgbr_base_feat: X_hgbr[c] = base50
                else:                   X_hgbr[c] = df_in[c].astype(float)
            for c in hgbr_cat_cols:
                X_hgbr[c] = df_in[c].astype("string").fillna("NA")
            resid_pred_hgbr = pipe_hgbr.predict(X_hgbr)
            hgbr_price = np.exp(np.log(base50) + resid_pred_hgbr)
        else:
            hgbr_price = None

        # --- Math Log-Linear (tek yöntem)
        try:
            X_ref = pd.DataFrame({
                "Model": df_ref["Model"].astype("string"),
                "Year":  df_ref["_year"].astype(float),
                "logKM": np.log1p(df_ref["_km"].astype(float)),
                "Boya":  df_ref["_boyali_count"].astype(float),
                "Degisen": df_ref["_degisen_count"].astype(float),
                "KB":    df_ref["_crit_boyali"].astype(float),
                "KD":    df_ref["_crit_degisen"].astype(float),
            })
            y_ref = df_ref["_price"].astype(float).values

            pre = ColumnTransformer(
                [("enc", OneHotEncoder(handle_unknown="ignore"), ["Model"])],
                remainder="passthrough"
            )
            math_loglin_pipe = Pipeline([("pre", pre), ("reg", LinearRegression())])
            math_loglin_pipe.fit(X_ref, y_ref)

            X_in_math = pd.DataFrame({
                "Model": df_in["Model"].astype("string"),
                "Year":  df_in["_year"].astype(float),
                "logKM": np.log1p(df_in["_km"].astype(float)),
                "Boya":  df_in["_boyali_count"].astype(float),
                "Degisen": df_in["_degisen_count"].astype(float),
                "KB":    df_in["_crit_boyali"].astype(float),
                "KD":    df_in["_crit_degisen"].astype(float),
            })
            math_loglin_pred = math_loglin_pipe.predict(X_in_math)
        except Exception as e:
            print(f"⚠️ Math Log-Linear çalıştırılamadı: {e}")
            math_loglin_pred = np.full(len(df_in), np.nan, dtype=float)

        # --- ORTALAMA + FARK ---
        preds = []
        if cat_price is not None: preds.append(cat_price)
        if lgb_price is not None: preds.append(lgb_price)
        if rf_price  is not None: preds.append(rf_price)
        if hgbr_price is not None: preds.append(hgbr_price)
        if not preds:
            raise RuntimeError("Aktif model yok (CatBoost/LightGBM/RandomForest/HGBR yüklenemedi).")
        avg_price = np.mean(np.vstack(preds), axis=0)

        # --- ÇIKTI ---
        out_df = df_in_raw.copy()
        out_df["Ayni_Temizlik_Ort_30k"] = np.nan
        out_df["Benzer_Ort_50k_Yil±1"]  = np.nan
        if cb_ok:   out_df["CatBoost_Tahmin"]      = np.nan
        if lgb_ok:  out_df["LightGBM_Tahmin"]      = np.nan
        if rf_ok:   out_df["RandomForest_Tahmin"]  = np.nan
        if hgbr_ok: out_df["HGBR_Tahmin"]          = np.nan
        out_df["Ortalama_Tahmin"] = np.nan
        out_df["Math_Log-Linear"]  = np.nan  # isimde tire kullandım; istersen eskiye döndür

        # Yeni fark sütunları
        out_df["dinamik tahmin farkı"] = np.nan
        out_df["static tahmin farkı"]  = np.nan

        # değerleri yaz
        out_df.loc[df_in.index, "Ayni_Temizlik_Ort_30k"] = same30
        out_df.loc[df_in.index, "Benzer_Ort_50k_Yil±1"]  = base50
        if cb_ok:   out_df.loc[df_in.index, "CatBoost_Tahmin"]     = cat_price
        if lgb_ok:  out_df.loc[df_in.index, "LightGBM_Tahmin"]     = lgb_price
        if rf_ok:   out_df.loc[df_in.index, "RandomForest_Tahmin"] = rf_price
        if hgbr_ok: out_df.loc[df_in.index, "HGBR_Tahmin"]         = hgbr_price
        out_df.loc[df_in.index, "Ortalama_Tahmin"] = avg_price
        out_df.loc[df_in.index, "Math_Log-Linear"]  = math_loglin_pred

        # --- Farklar ---
        price_col = self.find_first(out_df, ["Fiyat"]) or self.pick_col(out_df, ["fiyat","price"])
        if price_col:
            fiyat_num = out_df[price_col].map(self.to_number)
            out_df.loc[df_in.index, "dinamik tahmin farkı"] = (
                out_df.loc[df_in.index, "Ortalama_Tahmin"] - fiyat_num.loc[df_in.index]
            )
            out_df.loc[df_in.index, "static tahmin farkı"] = (
                out_df.loc[df_in.index, "Ayni_Temizlik_Ort_30k"] - fiyat_num.loc[df_in.index]
            )
            out_df["Math_LogLinear_Fark"] = out_df["Math_Log-Linear"] - fiyat_num
        else:
            out_df["Math_LogLinear_Fark"]  = np.nan

        # Kaydet
        out_path = self.OUT_PATH  # sabit verdiğin çıktıyı kullanıyoruz
        out_df.to_excel(out_path, index=False)
        print(f"✅ Kaydedildi: {out_path}")

if __name__ == "__main__":
    MeganeEnsembleRunner().run()
