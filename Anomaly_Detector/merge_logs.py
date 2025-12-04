# merge_logs.py
"""
logs/ klasöründeki tüm senaryo CSV'lerini okuyup
tek bir master dataset olarak birleştirir.

Çıktı: logs/all_data.csv

Her satır:
- Zaten CSV'deki kolonları (timestamp, scenario, mode, label, power_kw vs.) içerir
- Ek olarak 'source_file' kolonu eklenir (hangi log dosyasından geldiği)
"""

from pathlib import Path

import pandas as pd  # pip install pandas


def merge_logs(
    logs_dir: Path = Path("logs"),
    output_path: Path = Path("logs/all_data.csv"),
) -> None:
    if not logs_dir.exists():
        raise FileNotFoundError(f"Logs klasörü bulunamadı: {logs_dir.resolve()}")

    csv_files = sorted(logs_dir.glob("*.csv"))

    if not csv_files:
        raise FileNotFoundError(f"{logs_dir} içinde hiç .csv dosyası yok.")

    print(f"[+] {len(csv_files)} adet CSV bulundu:")
    for p in csv_files:
        print(f"    - {p.name}")

    dfs = []
    for csv_path in csv_files:
        print(f"[+] Okunuyor: {csv_path.name}")
        df = pd.read_csv(csv_path)

        # Hangi dosyadan geldiğini not edelim (debug için çok faydalı)
        df["source_file"] = csv_path.name

        dfs.append(df)

    # Hepsini alt alta birleştir
    all_df = pd.concat(dfs, ignore_index=True)

    # İstersen sadece MeterValues satırlarını filtreleyebilirsin:
    # all_df = all_df[all_df["message_type"] == "MeterValues"].reset_index(drop=True)

    # Çıktıyı yaz
    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_df.to_csv(output_path, index=False)

    print()
    print(f"[✓] Birleştirme tamamlandı.")
    print(f"[✓] Çıktı dosyası: {output_path.resolve()}")
    print(f"[✓] Toplam satır sayısı: {len(all_df)}")


if __name__ == "__main__":
    merge_logs()
