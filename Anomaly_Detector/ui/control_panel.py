# ui/control_panel.py
"""
BSG Şarj Güvenliği - Simülasyon Kontrol Paneli

Kullanım:

1) INTERAKTİF MENÜ (senin kullandığın):

    python -m ui.control_panel --interactive

2) PARAMETRE İLE DOĞRUDAN ÇALIŞTIRMA:

    python -m ui.control_panel --scenario dalgali_yuk --mode both --duration 30 --stations 1 --output logs

    Burada:
    - --scenario : senaryo adı (SCENARIO_RUNNERS sözlüğündeki key)
    - --mode     : normal | attack | both
    - --duration : saniye cinsinden süre
    - --stations : sanal şarj istasyonu sayısı
    - --output   : 
          * sadece klasör verirsen (örn: logs) 
              -> logs/dalgali_yuk_both.csv oluşur
          * dosya verirsen (örn: logs/dalgali_yuk_both.csv)
              -> direkt o dosya kullanılır

EKİP ARKADAŞLARIN İÇİN FORMAT NOTU
----------------------------------
Yeni senaryo eklemek isteyenler:

1) Klasör açacaklar:
      simulations/<senaryo_adi>/scenario.py

2) İçine şu imzaya sahip bir fonksiyon yazacaklar:
      async def run_scenario(mode: str, duration: int, stations: int, output_path: str | None) -> None:
          ...
   - mode: "normal" veya "attack" (ikisini de desteklesinler)
   - duration: saniye
   - stations: sanal istasyon sayısı
   - output_path: yazılacak CSV yolu (control_panel verecek)

3) Bu dosyada aşağıdaki sözlüğe import edip ekleyecekler:
      from simulations.yeni_senaryo.scenario import run_scenario as run_yeni_senaryo
      SCENARIO_RUNNERS["yeni_senaryo"] = run_yeni_senaryo
"""

import argparse
import asyncio
import os
import csv
from typing import Callable, Awaitable, Dict, List

# Mevcut dalgalı yük senaryosu
from simulations.dalgali_yuk.scenario import run_scenario as run_dalgali_yuk_scenario

# Yeni senaryolar eklendikçe:
# from simulations.mitm_saldirisi.scenario import run_scenario as run_mitm_saldirisi_scenario
# from simulations.rfid_klonlama.scenario import run_scenario as run_rfid_klonlama_scenario

# Senaryo adı -> runner fonksiyonu
SCENARIO_RUNNERS: Dict[str, Callable[..., Awaitable[None]]] = {
    "dalgali_yuk": run_dalgali_yuk_scenario,
    # "mitm_saldirisi": run_mitm_saldirisi_scenario,
    # "rfid_klonlama": run_rfid_klonlama_scenario,
}


def _ensure_dir_exists(path: str) -> None:
    """Verilen dosya yolunun klasörünü oluşturur."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def _resolve_output_paths_for_both(scenario: str, output: str | None) -> tuple[str, str, str]:
    """
    both modu için:
    - normal_tmp: geçici normal CSV
    - attack_tmp: geçici attack CSV
    - combined : nihai birleşik CSV

    output:
      * None veya sadece klasör ismi (örn: 'logs'):
            logs/dalgali_yuk_both.csv
      * Dosya ismi verilmişse (örn: 'logs/dalgali_yuk_both.csv'):
            combined = bu dosya
    """
    if output is None or not output.strip():
        # Varsayılan klasör: logs
        out_dir = "logs"
        os.makedirs(out_dir, exist_ok=True)
        combined = os.path.join(out_dir, f"{scenario}_both.csv")
    else:
        # Eğer uzantı yoksa klasör kabul et
        root, ext = os.path.splitext(output)
        if ext == "":
            out_dir = output
            os.makedirs(out_dir, exist_ok=True)
            combined = os.path.join(out_dir, f"{scenario}_both.csv")
        else:
            # Dosya yolu verilmiş
            combined = output
            out_dir = os.path.dirname(combined) or "."
            os.makedirs(out_dir, exist_ok=True)

    normal_tmp = os.path.join(out_dir, f"{scenario}_normal_tmp.csv")
    attack_tmp = os.path.join(out_dir, f"{scenario}_attack_tmp.csv")
    return normal_tmp, attack_tmp, combined


def _merge_csv_files(sources: List[str], target: str) -> None:
    """
    Verilen CSV dosyalarını tek bir CSV'de birleştirir.
    - İlk dosyanın header'ı yazılır.
    - Sonraki dosyalarda header satırı atlanır.
    """
    _ensure_dir_exists(target)

    header_written = False
    writer = None

    with open(target, "w", newline="", encoding="utf-8") as out_f:
        for src in sources:
            if not os.path.exists(src):
                continue
            with open(src, "r", newline="", encoding="utf-8") as in_f:
                reader = csv.reader(in_f)
                try:
                    header = next(reader)
                except StopIteration:
                    # Dosya boş ise
                    continue

                if not header_written:
                    writer = csv.writer(out_f)
                    writer.writerow(header)
                    header_written = True

                for row in reader:
                    writer.writerow(row)


async def _run_single_scenario(
    scenario: str,
    mode: str,
    duration: int,
    stations: int,
    output_path: str | None,
) -> None:
    runner = SCENARIO_RUNNERS[scenario]

    # Eğer sadece klasör ismi geldiyse (örn: "logs") bunu doğrudan dosya olarak
    # kullanmak istemiyoruz. Tek modda çalışırken:
    #   logs/<senaryo>_<mode>.csv
    if output_path is None or not output_path.strip():
        out_dir = "logs"
        os.makedirs(out_dir, exist_ok=True)
        filename = f"{scenario}_{mode}.csv"
        final_path = os.path.join(out_dir, filename)
    else:
        root, ext = os.path.splitext(output_path)
        if ext == "":
            # Klasör verilmiş -> klasör/ senaryo_mode.csv
            out_dir = output_path
            os.makedirs(out_dir, exist_ok=True)
            filename = f"{scenario}_{mode}.csv"
            final_path = os.path.join(out_dir, filename)
        else:
            # Direkt dosya yolu verilmiş
            final_path = output_path
            _ensure_dir_exists(final_path)

    print(f"[DEBUG] {scenario} ({mode}) çıktısı: {final_path}")
    await runner(
        mode=mode,
        duration=duration,
        stations=stations,
        output_path=final_path,
    )


async def run_from_args(args: argparse.Namespace) -> None:
    # Senaryo adı
    scenario = args.scenario
    if scenario not in SCENARIO_RUNNERS:
        raise SystemExit(f"Desteklenmeyen senaryo: {scenario}")

    # mode: normal, attack veya both
    requested_mode = args.mode.lower()
    if requested_mode not in ("normal", "attack", "both"):
        raise SystemExit("mode sadece 'normal', 'attack' veya 'both' olabilir.")

    duration = args.duration
    stations = args.stations
    output_path = args.output

    if requested_mode == "both":
        # 1) normal ve attack için geçici yollar + birleşik dosya
        normal_tmp, attack_tmp, combined = _resolve_output_paths_for_both(
            scenario=scenario, output=output_path
        )

        print(f"[INFO] {scenario} senaryosu BOTH modunda (normal + attack) tek CSV'ye yazılacak.")
        print(f"[INFO] Geçici NORMAL dosyası : {normal_tmp}")
        print(f"[INFO] Geçici ATTACK dosyası : {attack_tmp}")
        print(f"[INFO] Birleşik CSV          : {combined}")

        # 2) önce normal sonra attack çalıştır
        await _run_single_scenario(scenario, "normal", duration, stations, normal_tmp)
        await _run_single_scenario(scenario, "attack", duration, stations, attack_tmp)

        # 3) CSV'leri birleştir
        _merge_csv_files([normal_tmp, attack_tmp], combined)

        # 4) geçici dosyaları sil
        for tmp in (normal_tmp, attack_tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

        print("[INFO] Simülasyon(lar) tamamlandı. Tek dosya:")
        print(f"       {combined}")

    else:
        print(f"[INFO] {scenario} senaryosu {requested_mode.upper()} modunda çalıştırılıyor...")
        await _run_single_scenario(scenario, requested_mode, duration, stations, output_path)
        print("[INFO] Simülasyon tamamlandı.")


def _ask_with_default(prompt: str, default: str) -> str:
    txt = input(f"{prompt} [{default}]: ").strip()
    return txt or default


async def interactive_menu() -> None:
    print("=" * 60)
    print("      BSG Şarj Güvenliği - Simülasyon Kontrol Paneli")
    print("=" * 60)

    # Senaryo seçimi
    scenario_names = list(SCENARIO_RUNNERS.keys())
    print("\nKullanılabilir senaryolar:")
    for idx, name in enumerate(scenario_names, start=1):
        print(f"  {idx}) {name}")

    while True:
        choice = _ask_with_default("Senaryo numarası", "1")
        try:
            idx = int(choice)
            if 1 <= idx <= len(scenario_names):
                scenario = scenario_names[idx - 1]
                break
        except ValueError:
            pass
        print("Geçersiz seçim, tekrar dene.")

    # Mod seçimi
    print("\nModlar:")
    print("  1) normal")
    print("  2) attack")
    print("  3) both (normal + attack ardışık, TEK CSV)")

    mode_map = {"1": "normal", "2": "attack", "3": "both"}

    while True:
        choice = _ask_with_default("Mod seçimi", "1")
        mode = mode_map.get(choice)
        if mode:
            break
        print("Geçersiz seçim, tekrar dene.")

    # Diğer parametreler
    duration = int(_ask_with_default("Süre (saniye)", "60"))
    stations = int(_ask_with_default("İstasyon sayısı", "1"))
    output_path = _ask_with_default(
        "Çıktı (klasör ya da dosya yolu - sadece 'logs' yazabilirsin)", "logs"
    )

    print("\nÖzet:")
    print(f"  Senaryo : {scenario}")
    print(f"  Mod     : {mode}")
    print(f"  Süre    : {duration} sn")
    print(f"  İstasyon: {stations}")
    print(f"  Çıktı   : {output_path}")
    input("\nBaşlatmak için Enter'a bas...")

    await run_from_args(
        argparse.Namespace(
            scenario=scenario,
            mode=mode,
            duration=duration,
            stations=stations,
            output=output_path,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="BSG Şarj Güvenliği - Simülasyon Kontrol Paneli"
    )
    parser.add_argument(
        "--scenario",
        choices=SCENARIO_RUNNERS.keys(),
        help="Çalıştırılacak senaryo adı (örn: dalgali_yuk).",
    )
    parser.add_argument(
        "--mode",
        choices=["normal", "attack", "both"],
        default="normal",
        help="Simülasyon modu: normal, attack veya both.",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Simülasyon süresi (saniye).",
    )
    parser.add_argument(
        "--stations",
        type=int,
        default=1,
        help="Simüle edilecek sanal şarj istasyonu sayısı.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="logs",
        help="Çıktı klasörü veya dosya yolu (varsayılan: logs klasörü).",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Parametreler yerine interaktif menü kullan.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        # Eğer interaktif flag'i varsa veya senaryo parametresi hiç girilmemişse
        # kullanıcıyı menü ile yönlendir.
        if args.interactive or args.scenario is None:
            asyncio.run(interactive_menu())
        else:
            asyncio.run(run_from_args(args))
    except KeyboardInterrupt:
        # Ctrl+C'ye basınca çirkin traceback yerine sadece sakin bir mesaj ver.
        print("\n[INFO] İşlem kullanıcı tarafından iptal edildi (Ctrl+C).")


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
