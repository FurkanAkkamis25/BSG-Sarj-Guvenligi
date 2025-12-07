# merge_logs.py
"""
Amaç:
- logs/ocpp/ altındaki tüm senaryo CSV'lerini okuyup tek bir master dataset
  (all_data.csv) üretmek
- Aynı veriden, Yapay Zeka ekibine verilecek temiz datasetleri üretmek.

Çıktılar:
1) logs/all_data.csv
   -> Tüm ham log satırları (debug / analiz için)
2) logs/ai/CLEAN_DATA.csv
   -> Sadece NORMAL koşullardaki MeterValues satırları
3) logs/ai/ATTACK_DATA.csv
   -> Sadece ANOMALİ (saldırı) koşullardaki MeterValues satırları
"""

from pathlib import Path

import pandas as pd


def merge_logs(
    ocpp_dir: Path = Path("logs") / "ocpp",
    all_output: Path = Path("logs") / "all_data.csv",
    ai_output_dir: Path | None = None,
) -> None:
    """
    logs/ocpp/*.csv dosyalarını birleştirir ve:
      - logs/all_data.csv
      - logs/ai/CLEAN_DATA.csv
      - logs/ai/ATTACK_DATA.csv
    dosyalarını üretir.
    """
    if not ocpp_dir.exists():
        raise FileNotFoundError(
            f"logs/ocpp klasörü bulunamadı: {ocpp_dir.resolve()}"
        )

    if ai_output_dir is None:
        ai_output_dir = Path("logs") / "ai"
    ai_output_dir.mkdir(parents=True, exist_ok=True)

    # all_data / eski AI dosyalarını karıştırmamak için isimleri filtrele
    exclude_names = {"all_data.csv", "CLEAN_DATA.csv", "ATTACK_DATA.csv"}

    csv_files = sorted(
        p for p in ocpp_dir.glob("*.csv") if p.name not in exclude_names
    )

    if not csv_files:
        raise FileNotFoundError(
            f"{ocpp_dir} içinde işlenecek .csv dosyası yok."
        )

    print(f"[+] {len(csv_files)} adet OCPP CSV bulundu:")
    for p in csv_files:
        print(f"    - {p.name}")

    # ------------------------------------------------------
    # 1) Ham dataları üst üste birleştir (all_data.csv)
    # ------------------------------------------------------
    dfs = []
    for csv_path in csv_files:
        print(f"[+] Okunuyor: {csv_path.name}")
        df = pd.read_csv(csv_path)

        # Hangi dosyadan geldiğini not etmek debug için faydalı
        df["source_file"] = csv_path.name
        dfs.append(df)

    all_df = pd.concat(dfs, ignore_index=True)

    # Varsayılan kolonlar ScenarioBase.FIELDNAMES ile uyumlu:
    # ["timestamp", "charge_point_id", "scenario", "mode", "step",
    #  "message_type", "transaction_id", "connector_id", "id_tag",
    #  "power_kw", "current_a", "voltage_v", "label", "raw_payload"]
    all_output.parent.mkdir(parents=True, exist_ok=True)
    all_df.to_csv(all_output, index=False)
    print(f"[✓] Tüm loglar birleştirildi: {all_output.resolve()}")

    # ------------------------------------------------------
    # 2) AI için sadece MeterValues satırlarını çıkar
    # ------------------------------------------------------
    if "message_type" not in all_df.columns:
        raise ValueError("Beklenen kolon yok: 'message_type'")

    mv_df = all_df[all_df["message_type"] == "MeterValues"].copy()

    if mv_df.empty:
        raise ValueError(
            "MeterValues satırı bulunamadı. Senaryo düzgün çalıştı mı?"
        )

    # Etiket kolonunu kontrol edelim (normal / attack ayrımı için)
    if "label" not in mv_df.columns:
        # Yoksa hepsini normal varsayıp ekleyebiliriz ama proje için
        # aslında senaryoların label üretmesi daha doğru.
        mv_df["label"] = "normal"

    # ------------------------------------------------------
    # 3) AI ekibi için kolon isimlerini yeniden adlandır
    #    (Scrum notundaki örneğe uygun)
    # ------------------------------------------------------
    rename_map = {
        "timestamp": "Timestamp",
        "transaction_id": "Transaction_ID",
        "charge_point_id": "ChargePoint_ID",
        "power_kw": "Power_Active_Import",  # kW
        "current_a": "Current_Import",      # A
        "voltage_v": "Voltage",             # V
        "scenario": "Scenario",
        "mode": "Mode",
        "label": "Label",
    }

    # Sadece işimize yarayan kolonları alalım
    keep_cols = [
        "timestamp",
        "transaction_id",
        "charge_point_id",
        "power_kw",
        "current_a",
        "voltage_v",
        "scenario",
        "mode",
        "label",
    ]

    # Eksik kolon varsa uyarı verelim ama tamamen patlamasın
    missing = [c for c in keep_cols if c not in mv_df.columns]
    if missing:
        print(f"[!] Eksik kolonlar var (NaN geçilecek): {missing}")
        for c in missing:
            mv_df[c] = pd.NA

    ai_df = mv_df[keep_cols].rename(columns=rename_map)

    # ------------------------------------------------------
    # 4) CLEAN_DATA vs ATTACK_DATA ayır
    #     - Label == 'normal' (case insensitive) → CLEAN
    #     - Diğer label'lar → ATTACK
    # ------------------------------------------------------
    label_series = ai_df["Label"].astype(str).str.lower()
    clean_df = ai_df[label_series == "normal"].copy()
    attack_df = ai_df[label_series != "normal"].copy()

    clean_path = ai_output_dir / "CLEAN_DATA.csv"
    attack_path = ai_output_dir / "ATTACK_DATA.csv"

    clean_df.to_csv(clean_path, index=False)
    attack_df.to_csv(attack_path, index=False)

    print()
    print("[✓] AI datasetleri üretildi:")
    print(f"    - CLEAN_DATA.csv : {clean_path.resolve()} (satır: {len(clean_df)})")
    print(f"    - ATTACK_DATA.csv: {attack_path.resolve()} (satır: {len(attack_df)})")


if __name__ == "__main__":
    merge_logs()
