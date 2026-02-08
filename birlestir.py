# -*- coding: utf-8 -*-
"""
İki Excel dosyasını kolon adlarına göre hizalayarak birleştirir.
- KM sütununda 2 veya 3 haneli (örn. 90, 250, 999) değerleri 1000 ile çarpar.
- 4+ haneli (örn. 1.200, 207.000) değerleri olduğu gibi bırakır.
- "Adres, Ağır Hasar Kayıtlı, Garanti, Mahalle, Plaka / Uyruk, İl, İlçe, İl/İlçe"
  kolonları üzerinde özel bir dönüştürme yapmaz; isim nasıl ise öyle kalır.
- Ortak kolon adları otomatik hizalanır; farklı kolonlar ayrı sütunlar olarak korunur.

Gereksinimler: pandas, openpyxl
pip install pandas openpyxl
"""

import re
import sys
from pathlib import Path
import pandas as pd
import numpy as np


EXCLUDE_COLS = {
    "Adres",
    "Ağır Hasar Kayıtlı",
    "Garanti",
    "Mahalle",
    "Plaka / Uyruk",
    "İl",
    "İlçe",
    "İl/İlçe",
}

def read_first_sheet(path: str | Path) -> pd.DataFrame:
    """Excel'in ilk sayfasını okur; İlan No gibi sayısal görünebilecek alanları string korumaya çalışır."""
    path = Path(path)
    # dtype'ı zorlamayalım; sadece çok bozulmaya müsait alanları string deneyebiliriz.
    try:
        df = pd.read_excel(path, sheet_name=0, engine="openpyxl")
    except Exception:
        # Fallback
        df = pd.read_excel(path, sheet_name=0)
    return df


def normalize_km_series(s: pd.Series) -> pd.Series:
    """
    KM kolonu:
    - Metinlerde nokta/virgül ayırıcıları temizlenir.
    - Yalnızca rakamlar birleştirilir (örn. "207.000" -> "207000").
    - Eğer rakam uzunluğu 2 veya 3 ise (örn. "90", "250"), 1000 ile çarpılır.
    - 4+ haneli değerler olduğu gibi bırakılır.
    - Düzgün pars edilemeyenler NaN yapılır.
    """
    def _fix_one(val):
        if pd.isna(val):
            return np.nan
        s = str(val).strip()

        # Virgül, nokta vb. ayırıcılardan arındır
        s_clean = s.replace(".", "").replace(",", "")
        # İçindeki rakam gruplarını birleştir
        digits = re.findall(r"\d+", s_clean)
        if not digits:
            return np.nan
        raw = "".join(digits)

        # Baştaki gereksiz sıfırları kaldır, tamamen sıfır kalırsa 0 olsun
        raw = raw.lstrip("0") or "0"

        try:
            km_val = int(raw)
        except Exception:
            return np.nan

        # Orijinal HANE mantığı: raw uzunluğu 2 veya 3 ise *1000
        # (örn. "90" -> 90000 değil, 90000? Dikkat: 90 * 1000 = 90000 doğru.
        # "190" -> 190000)
        if len(raw) in (2, 3):
            return km_val * 1000
        else:
            return km_val

    return s.apply(_fix_one)


def apply_km_fix(df: pd.DataFrame) -> pd.DataFrame:
    """DF içinde 'KM' (veya varyantları) varsa normalize eder."""
    # Olası KM adları
    km_candidates = ["KM", "Km", "km"]
    for col in km_candidates:
        if col in df.columns:
            df[col] = normalize_km_series(df[col])
            break
    return df


def merge_by_column_names(df1: pd.DataFrame, df2: pd.DataFrame) -> pd.DataFrame:
    """
    Kolon adlarına göre hizalanmış şekilde dikey birleştirme (concat).
    EXCLUDE_COLS setindeki kolonlar üzerinde özel eşleştirme/rename yapmaz.
    Ortak kolonlar aynı sütunda buluşur, diğerleri ayrı sütun olarak kalır.
    """
    # Pandas concat zaten kolon adlarına göre hizalar.
    merged = pd.concat([df1, df2], axis=0, ignore_index=True, sort=False)
    return merged


def main(file1: str, file2: str, out_path: str):
    df1 = read_first_sheet(file1)
    df2 = read_first_sheet(file2)

    # KM düzeltmesi
    df1 = apply_km_fix(df1.copy())
    df2 = apply_km_fix(df2.copy())

    # Birleştir
    merged = merge_by_column_names(df1, df2)

    # Çıkış
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        merged.to_excel(writer, index=False, sheet_name="birlesik")

    print(f"✅ Birleştirme tamam: {out_path}  (Satır sayısı: {len(merged)})")


if __name__ == "__main__":
    # --- KULLANIM SEÇENEKLERİ ---
    # 1) Dosya yollarını burada sabitle:
    # file1 = r"C:\...\dosya1.xlsx"
    # file2 = r"C:\...\dosya2.xlsx"
    # out   = r"C:\...\birlesik.xlsx"
    #
    # main(file1, file2, out)
    #
    # 2) Veya komut satırından ver:
    # python birlestir_km_fix.py "C:\...\dosya1.xlsx" "C:\...\dosya2.xlsx" "C:\...\birlesik.xlsx"

    if len(sys.argv) >= 4:
        f1 = sys.argv[1]
        f2 = sys.argv[2]
        out = sys.argv[3]
        main(f1, f2, out)
    else:
        # Hızlı test için buraya kendi yollarını yaz:
        file1 = r"C:\Users\EXCALIBUR\Desktop\sahibinden\gundelik_passat_ilanlari\full-passat.xlsx"
        file2 = r"C:\Users\EXCALIBUR\Desktop\sahibinden\gundelik_passat_ilanlari\passat_10eylül.xlsx"
        out   = r"C:\Users\EXCALIBUR\Desktop\sahibinden\gundelik_passat_ilanlari\full-passat4.xlsx"
        main(file1, file2, out)
