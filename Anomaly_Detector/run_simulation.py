# run_simulation.py
import argparse
import asyncio
import importlib.util
import sys
from datetime import datetime
from pathlib import Path


def load_scenario_module(scenario: str):
    """
    Paket ismine güvenmek yerine, senaryoyu dosya yolundan direkt yüklüyoruz.
    simulations/<scenario>/scenario.py dosyasını arar.

    Örn:
        --scenario dalgali_yuk
        -> simulations/dalgali_yuk/scenario.py
        içinde run_scenario(...) fonksiyonunu bekler.
    """
    base_dir = Path(__file__).parent
    scenario_path = base_dir / "simulations" / scenario / "scenario.py"

    if not scenario_path.exists():
        raise SystemExit(f"Senaryo dosyası bulunamadı: {scenario_path}")

    module_name = f"{scenario}_scenario_module"

    spec = importlib.util.spec_from_file_location(module_name, scenario_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Senaryo modülü yüklenemedi: {scenario_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


async def run_simulation(
    scenario: str,
    mode: str,
    duration: int,
    stations: int,
    output: str | None = None,
) -> None:
    """
    Tüm simülasyonların ortak giriş noktası.

    Parametreler:
        scenario : simulations/<scenario>/scenario.py
        mode     : "normal" veya "attack"
        duration : step sayısı (senaryo bunu saniye, adım vs. gibi yorumlar)
        stations : sanal şarj istasyonu sayısı
        output   : opsiyonel CSV adı (logs klasörüne yazılır)

    Senaryo modülünden beklenen fonksiyon imzası:
        async def run_scenario(mode: str, duration: int, stations: int, output_path: str): ...
    """
    scenario = scenario.lower()

    # logs klasörü ve output yolu
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    if not output:
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = logs_dir / f"{scenario}_{mode}_{timestamp_str}.csv"
    else:
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = logs_dir / output_path

    # Senaryo modülünü dosyadan yükle
    scenario_module = load_scenario_module(scenario)

    if not hasattr(scenario_module, "run_scenario"):
        raise SystemExit(
            f"{scenario_module.__file__} içinde 'run_scenario' fonksiyonu yok."
        )

    run_scenario_func = scenario_module.run_scenario

    print(
        f"[INFO] Senaryo: {scenario} | Mod: {mode} | Süre: {duration} adım | "
        f"İstasyon: {stations} | Log: {output_path}"
    )

    await run_scenario_func(
        mode=mode,
        duration=duration,
        stations=stations,
        output_path=str(output_path),
    )

    print(f"[OK] Simülasyon tamamlandı. Log dosyası: {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EVSE Anomali Simülasyon Arayüzü"
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="Çalıştırılacak senaryo adı (ör: dalgali_yuk)",
    )
    parser.add_argument(
        "--mode",
        choices=["normal", "attack"],
        default="normal",
        help="Simülasyon modu: normal veya attack",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Simülasyon adım sayısı (ör: 60 = 60 step)",
    )
    parser.add_argument(
        "--stations",
        type=int,
        default=1,
        help="Sanal istasyon (charge point) sayısı",
    )
    parser.add_argument(
        "--output",
        help="Log dosyası adı (opsiyonel, logs klasörüne kaydedilir)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        run_simulation(
            scenario=args.scenario,
            mode=args.mode,
            duration=args.duration,
            stations=args.stations,
            output=args.output,
        )
    )
