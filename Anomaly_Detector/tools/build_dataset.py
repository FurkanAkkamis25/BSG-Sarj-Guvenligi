from __future__ import annotations

import csv
from pathlib import Path
from typing import List

import pandas as pd

# ------------------------------------------------------------------------------
# AYARLAR
# ------------------------------------------------------------------------------

# logs klasörünün kökü (projenin kökünden çalıştırdığını varsayıyorum)
LOGS_ROOT = Path(__file__).parent.parent / "logs"

# OCPP loglarının olduğu klasör
OCPP_DIR = LOGS_ROOT / "ocpp"

# AI ekibine verilecek dataset'lerin yazılacağı klasör
AI_DIR = LOGS_ROOT / "ai"

# Hangi kolonlar AI ekibine gitsin?
# (Sen istersen burayı sadeleştirirsin; şimdilik hepsi dursun, raw_payload opsiyonel)
BASE_COLUMNS: List[str] = [
    "timestamp",
    "charge_point_id",
    "scenario",
    "mode",
    "step",
    "message_type",
    "transaction_id",
    "connector_id",
    "id_tag",
    "power_kw",
    "current_a",
    "voltage_v",
    "soc_percent",
    "label",
    # "raw_payload",  # aşağıdaki flag'e göre eklenecek
]

# AI dataset içinde raw_payload dursun mu?
KEEP_RAW_PAYLOAD = False  # True yaparsan raw_payload da gelir


# ------------------------------------------------------------------------------
# Yardımcı fonksiyonlar
# ------------------------------------------------------------------------------

def _is_unified_dataset_file(path: Path) -> bool:
    """
    logs/ocpp içindeki CSV'lerden:
      - *_meter_values.csv
      - *_status_notifications.csv
      - *_transactions.csv
      - *_heartbeats.csv
      - *_events_raw.csv
    gibi ham tablolara DEĞİL,
    sadece birleşik (eski pipeline için üretilen) CSV'lere bakmak istiyoruz.
    """
    if path.suffix.lower() != ".csv":
        return False

    stem = path.stem  # uzantısız dosya adı

    # Ham tablolara ait suffix'ler
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
    """
    logs/ocpp altındaki tüm birleşik dataset CSV'lerini okuyup
    tek bir DataFrame'de birleştirir.
    """
    if not ocpp_dir.exists():
        raise FileNotFoundError(f"OCPP klasörü bulunamadı: {ocpp_dir}")

    csv_files = [p for p in ocpp_dir.glob("*.csv") if _is_unified_dataset_file(p)]

    if not csv_files:
        raise FileNotFoundError(
            f"{ocpp_dir} içinde birleşik dataset CSV dosyası bulunamadı. "
            f"Önce run_simulation ile bir senaryo çalıştırdığından emin ol."
        )

    print(f"[INFO] {len(csv_files)} birleşik CSV bulundu:")
    for p in csv_files:
        print(f"       - {p}")

    frames = []
    for path in csv_files:
        try:
            df = pd.read_csv(path)
            df["__source_file"] = str(path)  # istersek debug için
            frames.append(df)
        except Exception as e:
            print(f"[WARN] {path} okunurken hata: {e}")

    if not frames:
        raise RuntimeError("Hiçbir birleşik CSV başarıyla okunamadı.")

    all_df = pd.concat(frames, ignore_index=True)
    print(f"[INFO] Toplam kayıt sayısı: {len(all_df)}")

    return all_df


def _prepare_ai_dataset(all_df: pd.DataFrame) -> pd.DataFrame:
    """
    AI ekibi için kolonları düzenler:
      - Gereksiz kolonları at
      - Eksik kolonları varsa uyarı ver
      - (İstersen burada feature engineering de yapılabilir)
    """

    columns = list(BASE_COLUMNS)
    if KEEP_RAW_PAYLOAD:
        if "raw_payload" not in columns:
            columns.append("raw_payload")

    # Veri setinde kolon eksikse kırılma olmasın diye kontrol
    missing = [c for c in columns if c not in all_df.columns]
    if missing:
        print(f"[WARN] Dataset içinde eksik kolonlar var: {missing}")
        print("       Bu kolonlar çıktıda yer almayacak.")

    final_cols = [c for c in columns if c in all_df.columns]

    ai_df = all_df[final_cols].copy()

    # Binary label (AI ekibi 'normal' / 'attack' istiyorsa)
    # label kolonu:
    #   - normal → "normal"
    #   - diğer her şey → "attack"
    if "label" in ai_df.columns:
        ai_df["binary_label"] = ai_df["label"].apply(
            lambda x: "normal" if str(x).lower() == "normal" else "attack"
        )

    return ai_df


def main() -> None:
    print("==============================================")
    print("  OCPP LOG'LARINDAN AI DATASET OLUŞTURULUYOR  ")
    print("==============================================")

    # logs/ai klasörünü oluştur
    AI_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] AI dataset klasörü: {AI_DIR}")

    # 1) logs/ocpp altındaki birleşik CSV'leri yükle
    all_df = _load_unified_csvs(OCPP_DIR)

    # 2) AI dataset için kolonları düzenle
    ai_df = _prepare_ai_dataset(all_df)

    print(f"[INFO] AI dataset satır sayısı: {len(ai_df)}")

    # 3) Çıktıları kaydet
    all_path = AI_DIR / "ocpp_dataset_all.csv"
    normal_path = AI_DIR / "ocpp_dataset_normal.csv"
    attack_path = AI_DIR / "ocpp_dataset_attack.csv"

    ai_df.to_csv(all_path, index=False, quoting=csv.QUOTE_NONNUMERIC)
    print(f"[OK] Tüm kayıtlar: {all_path}")

    if "binary_label" in ai_df.columns:
        normal_df = ai_df[ai_df["binary_label"] == "normal"]
        attack_df = ai_df[ai_df["binary_label"] == "attack"]

        normal_df.to_csv(normal_path, index=False, quoting=csv.QUOTE_NONNUMERIC)
        attack_df.to_csv(attack_path, index=False, quoting=csv.QUOTE_NONNUMERIC)

        print(f"[OK] Normal kayıtlar: {normal_path} (satır: {len(normal_df)})")
        print(f"[OK] Attack kayıtlar:  {attack_path} (satır: {len(attack_df)})")
    else:
        print("[WARN] 'label' kolonu bulunamadı, normal/attack ayrımı yapılamadı.")

    print("[DONE] Dataset üretimi tamam.")


if __name__ == "__main__":
    main()
