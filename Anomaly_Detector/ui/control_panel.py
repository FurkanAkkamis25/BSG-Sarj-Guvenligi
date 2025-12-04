# ui/control_panel.py
import asyncio

from run_simulation import run_simulation


def ask_scenario() -> str:
    print("--------- Senaryo Seçimi ---------")
    print("1) Dalgalı Yük (dalgali_yuk)")
    # İleride buraya yeni senaryolar eklenebilir
    choice = input("Senaryo numarası: ").strip()

    if choice == "1":
        return "dalgali_yuk"

    print("Geçersiz seçim, varsayılan: dalgali_yuk")
    return "dalgali_yuk"


def ask_mode() -> str:
    print("--------- Mod Seçimi ---------")
    print("1) Normal")
    print("2) Attack")
    choice = input("Mod numarası: ").strip()

    if choice == "2":
        return "attack"
    return "normal"


def ask_int(prompt: str, default: int) -> int:
    value = input(f"{prompt} (varsayılan {default}): ").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        print("Geçersiz sayı, varsayılan kullanılıyor.")
        return default


def main() -> None:
    print("===============================")
    print(" EVSE ANOMALY SIMULATOR PANEL ")
    print("===============================")

    scenario = ask_scenario()
    mode = ask_mode()
    duration = ask_int("Süre (adım sayısı)", 60)
    stations = ask_int("İstasyon sayısı", 1)

    print("\n[INFO] Simülasyon başlatılıyor...")
    asyncio.run(
        run_simulation(
            scenario=scenario,
            mode=mode,
            duration=duration,
            stations=stations,
            output=None,  # otomatik isimlendirme
        )
    )
    print("[OK] Panel: Simülasyon bitti.")


if __name__ == "__main__":
    main()
