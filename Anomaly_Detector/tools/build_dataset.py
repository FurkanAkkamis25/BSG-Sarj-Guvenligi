from __future__ import annotations

import csv
from pathlib import Path
from typing import List

import pandas as pd

# ------------------------------------------------------------------------------
# AYARLAR
# ------------------------------------------------------------------------------

LOGS_ROOT = Path("logs")
OCPP_DIR = LOGS_ROOT / "ocpp"
AI_DIR = LOGS_ROOT / "ai"

# AI ekibinin istediği kolon sırası ve isimleri (BİREBİR!)
AI_COLUMNS: List[str] = [
    "Timestamp",
    "Transaction_ID",
    "Voltage",
    "Current_Import",
    "Power_Import",
    "SoC",
    "Label",
]

# ------------------------------------------------------------------------------
# Yardımcı fonksiyonlar
# ------------------------------------------------------------------------------

def _is_unified_dataset_file(path: Path) -> bool:
    """
    logs/ocpp içindeki birleşik dataset dosyalarını seçer.
    Ham tabloları (meter_values, heartbeats vs.) hariç tutar.
    """
    if path.suffix.lower() != ".csv":
        return False

    stem = path.stem
    exclude_suffixes = [
        "_meter_values",
        "_status_notifications",
        "_transactions",
        "_heartbeats",
        "_events_raw",
    ]
    if any(stem.endswith(suf) for suf in exclude_suffixes):
        return False

    return True


def _load_unified_csvs(ocpp_dir: Path) -> pd.DataFrame:
    if not ocpp_dir.exists():
        raise FileNotFoundError(f"OCPP klasörü bulunamadı: {ocpp_dir}")

    csv_files = [p for p in ocpp_dir.glob("*.csv") if _is_unified_dataset_file(p)]
    if not csv_files:
        raise FileNotFoundError(
            f"{ocpp_dir} içinde birleşik dataset CSV dosyası bulunamadı. "
            f"Önce run_simulation ile senaryo çalıştır."
        )

    print(f"[INFO] {len(csv_files)} birleşik CSV bulundu:")
    for p in csv_files:
        print(f"       - {p}")

    frames = []
    for path in csv_files:
        try:
            df = pd.read_csv(path)
            df["__source_file"] = str(path)
            frames.append(df)
        except Exception as e:
            print(f"[WARN] {path} okunurken hata: {e}")

    if not frames:
        raise RuntimeError("Hiçbir birleşik CSV başarıyla okunamadı.")

    all_df = pd.concat(frames, ignore_index=True)
    print(f"[INFO] Toplam kayıt sayısı: {len(all_df)}")
    return all_df


def _to_ai_format(all_df: pd.DataFrame) -> pd.DataFrame:
    """
    AI ekibine GÖNDERİLECEK TEMİZ DATASET

    ŞARTLAR:
    - Transaction_ID dolu olacak
    - Voltage / Current / Power / SoC dolu olacak
    - Sadece gerçek şarj ölçümleri (MeterValues mantığı)
    """

    required_src = [
        "timestamp",
        "transaction_id",
        "voltage_v",
        "current_a",
        "power_kw",
        "soc_percent",
        "label",
    ]

    missing = [c for c in required_src if c not in all_df.columns]
    if missing:
        raise RuntimeError(f"Gerekli kolon(lar) eksik: {missing}")

    # -----------------------------
    # 🔥 TEMİZLEME (KRİTİK KISIM)
    # -----------------------------
    df = all_df.copy()

    df = df.dropna(subset=[
        "transaction_id",
        "voltage_v",
        "current_a",
        "power_kw",
        "soc_percent"
    ])

    # transaction_id string boşluk kontrolü
    df = df[df["transaction_id"].astype(str).str.strip() != ""]

    # Güvenlik: negatif / anlamsız değerleri de at
    df = df[
        (df["voltage_v"] > 0) &
        (df["current_a"] >= 0) &
        (df["power_kw"] >= 0) &
        (df["soc_percent"] >= 0)
    ]

    print(f"[INFO] AI için temizlenen kayıt sayısı: {len(df)}")

    # -----------------------------
    # 🎯 AI FORMATINA MAP
    # -----------------------------
    ai_df = pd.DataFrame()
    ai_df["Timestamp"] = df["timestamp"]
    ai_df["Transaction_ID"] = df["transaction_id"]
    ai_df["Voltage"] = df["voltage_v"]
    ai_df["Current_Import"] = df["current_a"]
    ai_df["Power_Import"] = df["power_kw"]
    ai_df["SoC"] = df["soc_percent"]

    # Label normalize
    ai_df["Label"] = df["label"].apply(
        lambda x: "normal" if str(x).lower() == "normal" else "attack"
    )

    ai_df = ai_df[AI_COLUMNS]
    return ai_df


def main() -> None:
    print("==============================================")
    print("      AI FORMATINDA DATASET ÜRETİLİYOR        ")
    print("==============================================")

    AI_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] AI klasörü: {AI_DIR}")

    all_df = _load_unified_csvs(OCPP_DIR)
    ai_df = _to_ai_format(all_df)

    out_all = AI_DIR / "ai_dataset_all.csv"
    out_normal = AI_DIR / "ai_dataset_normal.csv"
    out_attack = AI_DIR / "ai_dataset_attack.csv"

    ai_df.to_csv(out_all, index=False, quoting=csv.QUOTE_NONNUMERIC)
    print(f"[OK] Tüm kayıtlar: {out_all} (satır: {len(ai_df)})")

    normal_df = ai_df[ai_df["Label"] == "normal"]
    attack_df = ai_df[ai_df["Label"] == "attack"]

    normal_df.to_csv(out_normal, index=False, quoting=csv.QUOTE_NONNUMERIC)
    attack_df.to_csv(out_attack, index=False, quoting=csv.QUOTE_NONNUMERIC)

    print(f"[OK] Normal: {out_normal} (satır: {len(normal_df)})")
    print(f"[OK] Attack: {out_attack} (satır: {len(attack_df)})")
    print("[DONE] AI dataset üretimi tamam.")


if __name__ == "__main__":
    main()