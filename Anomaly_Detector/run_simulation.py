# run_simulation.py
import argparse
import asyncio
import importlib.util
import sys
from datetime import datetime
from pathlib import Path
import logging


def load_scenario_module(scenario: str):
    """
    Paket ismine güvenmek yerine, senaryoyu dosya yolundan direkt yüklüyoruz.
    simulations/<scenario>/scenario.py dosyasını arar.

    Örn:
        --scenario dalgali_yuk
            -> simulations/dalgali_yuk/scenario.py
    """
    scenario = scenario.lower()
    scenario_path = Path("simulations") / scenario / "scenario.py"

    if not scenario_path.exists():
        raise SystemExit(
            f"Senaryo dosyası bulunamadı: {scenario_path.resolve()}"
        )

    spec = importlib.util.spec_from_file_location(
        f"simulations.{scenario}.scenario",
        scenario_path,
    )
    if spec is None or spec.loader is None:
        raise SystemExit(f"Senaryo modülü yüklenemedi: {scenario_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


async def run_simulation(
    scenario: str,
    mode: str,
    duration: int,
    stations: int,
    output: str | None = None,
    cp_list: list[str] | None = None,
) -> None:
    """
    Tüm simülasyonların ortak giriş noktası.

    Parametreler:
        scenario : simulations/<scenario>/scenario.py
        mode     : "normal" veya "attack"
        duration : step sayısı / saniye (senaryo yorumlar)
        stations : sanal şarj istasyonu sayısı
        output   : opsiyonel CSV adı (logs/ocpp altına yazılır)
        cp_list  : opsiyonel CP ID listesi (örn: ["CP_001", "CP_003", "CP_005"])
                   Verilmezse CP_001'den başlayarak stations kadar CP oluşturulur

    Senaryo modülünden beklenen fonksiyon imzası:
        async def run_scenario(
            mode: str,
            duration: int,
            stations: int,
            output_path: str,
            cp_list: list[str] | None = None,
        ) -> None: ...
    """
    scenario = scenario.lower()

    # --------------------------------------------------------------
    # 1) logs klasör yapısı: logs/ocpp/ altında dosya
    # --------------------------------------------------------------
    logs_root = Path("logs")
    ocpp_dir = logs_root / "ocpp"
    ocpp_dir.mkdir(parents=True, exist_ok=True)

    # Kullanıcı output vermezse:
    #   logs/ocpp/<scenario>_<mode>_<timestamp>.csv
    if not output:
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = ocpp_dir / f"{scenario}_{mode}_{timestamp_str}.csv"
    else:
        user_path = Path(output)
        if user_path.is_absolute():
            output_path = user_path
        else:
            # Göreli verdiyse logs/ocpp altına koy
            output_path = ocpp_dir / user_path

    # --------------------------------------------------------------
    # 2) Senaryo modülünü yükle
    # --------------------------------------------------------------
    scenario_module = load_scenario_module(scenario)

    if not hasattr(scenario_module, "run_scenario"):
        raise SystemExit(
            f"{scenario_module.__file__} içinde 'run_scenario' fonksiyonu yok."
        )

    run_scenario = scenario_module.run_scenario  # type: ignore[attr-defined]

    # --------------------------------------------------------------
    # 3) Senaryoyu çalıştır
    #    Buradan sonra iş charge_point + csms_server + scenario'da
    #    IDTag, MeterValues, TransactionId vs. orada üretilecek.
    # --------------------------------------------------------------
    # cp_list parametresini senaryoya gönder (geriye uyumlu)
    scenario_kwargs = {
        "mode": mode,
        "duration": duration,
        "stations": stations,
        "output_path": str(output_path),
    }
    # Eğer cp_list varsa ekle (yeni özellik)
    if cp_list is not None:
        scenario_kwargs["cp_list"] = cp_list
    
    await run_scenario(**scenario_kwargs)

    print()
    print("[✓] Senaryo tamamlandı.")
    print(f"[✓] OCPP log dosyası: {output_path.resolve()}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="OCPP tabanlı şarj istasyonu simülasyonu çalıştırıcı",
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="Çalıştırılacak senaryonun klasör adı (simulations/<scenario>)",
    )
    parser.add_argument(
        "--mode",
        choices=["normal", "attack"],
        default="normal",
        help="Senaryo modu: normal veya attack",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=10,
        help="Simülasyon süresi (saniye veya adım sayısı, senaryoya bağlı)",
    )
    parser.add_argument(
        "--stations",
        type=int,
        default=1,
        help="Sanal istasyon (charge point) sayısı",
    )
    parser.add_argument(
        "--output",
        help=(
            "Opsiyonel: OCPP log dosyası adı "
            "(varsayılan: logs/ocpp/<senaryo>_<mod>_<tarih>.csv)"
        ),
    )
    parser.add_argument(
        "--cp-list",
        nargs="+",
        help=(
            "Opsiyonel: Kullanılacak CP ID listesi (örn: --cp-list CP_001 CP_003 CP_005). "
            "Verilmezse CP_001'den başlayarak stations kadar CP oluşturulur."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    # 🔥 Tüm logları aç (CP + CSMS + ocpp)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    args = parse_args()
    try:
        asyncio.run(
            run_simulation(
                scenario=args.scenario,
                mode=args.mode,
                duration=args.duration,
                stations=args.stations,
                output=args.output,
                cp_list=getattr(args, "cp_list", None),
            )
        )
    except KeyboardInterrupt:
        print("\n[INFO] Simülasyon kullanıcı tarafından durduruldu.")