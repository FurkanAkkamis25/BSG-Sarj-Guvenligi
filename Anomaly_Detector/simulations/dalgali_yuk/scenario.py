import asyncio
import math
import random
from dataclasses import dataclass
from typing import Dict, Any, List

from simulations.base.scenario_base import ScenarioBase, ScenarioConfig
from core.charge_point import SimulatedChargePoint


@dataclass
class DalgaliYukScenarioConfig(ScenarioConfig):
    """
    Dalgalı Yük Saldırısı senaryosu için konfigürasyon.
    Şimdilik ekstra alan yok, ScenarioConfig'i aynen kullanıyoruz.
    """
    pass


class DalgaliYukScenario(ScenarioBase):
    """
    Dalgalı Yük Saldırısı (Oscillatory Load Attack) senaryosu.

    - normal modda: güç değeri base_power etrafında küçük jitter ile dolaşır
    - attack modda: sinüs tabanlı salınım + jitter ile şebekeye dalgalı yük bindirilir
    """

    async def run_for_all_charge_points(
        self,
        cps: List[SimulatedChargePoint],
        mode: str,
        duration: int,
    ) -> None:
        """
        Tüm charge point'ler için senaryo akışını çalıştırır.
        """

        connector_id = 1
        tx_ids: Dict[str, int] = {}

        # -----------------------------
        # 1) CP → CS: StatusNotification(Available)
        # -----------------------------
        for cp in cps:
            await cp.send_status_notification(
                connector_id=connector_id,
                status="Available",
            )
            await asyncio.sleep(0.1)

        # -----------------------------
        # 2) Authorize → Accepted mi?
        #    FAILED ise şarj BAŞLAMAYACAK
        # -----------------------------
        for idx, cp in enumerate(cps, start=1):

            # Şimdilik sabit IDTag, CSMS tarafındaki VALID_TAGS ile uyumlu olmalı
            # csms_server.py içinde VALID_TAGS'te YUNUS_TAG tanımlı:
            # VALID_TAGS = {"YUNUS_TAG": "...", ...}
            id_tag = "YUNUS_TAG"

            status = await cp.send_authorize(id_tag)

            if status != "Accepted":
                print(f"[!] {cp.id}: IDTag reddedildi → Şarj oturumu başlamayacak.")
                continue

            # “Preparing” durumuna geç
            await cp.send_status_notification(
                connector_id=connector_id,
                status="Preparing",
            )
            await asyncio.sleep(0.1)

            # -----------------------------
            # 3) StartTransaction
            # -----------------------------
            start_res = await cp.send_start_transaction(
                connector_id=connector_id,
                id_tag=id_tag,
                meter_start=0,
            )
            tx_ids[cp.id] = start_res.transaction_id

            # “Charging” durumuna geç
            await cp.send_status_notification(
                connector_id=connector_id,
                status="Charging",
            )

        # -----------------------------
        # 4) MeterValues DÖNGÜSÜ
        # -----------------------------
                # -----------------------------
        # 4) MeterValues DÖNGÜSÜ
        # -----------------------------
        voltage_v = 400.0
        base_power_kw = 7.0       # Normalde istasyonun vermesini beklediğimiz güç
        attack_delta = 3.0        # Saldırıda pozitif/negatif salınım genliği

        # Basit bir model: her CP için başlangıç SoC = %20, batarya kapasitesi = 60 kWh varsayalım
        battery_capacity_kwh = 60.0
        soc_state: Dict[str, float] = {cp.id: 20.0 for cp in cps}

        for step in range(1, duration + 1):
            for cp in cps:

                # CP'de aktif transaction var mı?
                tx_id = tx_ids.get(cp.id)
                if not tx_id:
                    # Authorize / StartTransaction başarısız olduysa bu CP veri göndermez
                    continue

                if mode == "normal":
                    # Normal mod: küçük jitter ile neredeyse sabit güç
                    power_kw = base_power_kw + random.uniform(-0.3, 0.3)

                else:
                    # attack (dalgalı yük)
                    # Sinüs tabanlı salınım: base_power etrafında yukarı aşağı oynuyor
                    freq = 0.25  # adım başına frekans
                    angle = 2 * math.pi * freq * step
                    osc = math.sin(angle)
                    power_kw = base_power_kw + attack_delta * osc

                    # Biraz da rastgelelik ekleyelim ki tam düzgün sinüs olmasın
                    power_kw += random.uniform(-0.5, 0.3)

                    # Güç asla negatif veya 0'a çok yakın olmasın
                    power_kw = max(power_kw, 0.1)

                # P = V * I → I = P / V
                current_a = (power_kw * 1000) / voltage_v

                # -----------------------------
                # SoC HESABI
                # -----------------------------
                # Adım süresi ~1 saniye (aşağıda sleep(1) var)
                dt_hours = 1.0 / 3600.0
                energy_kwh = max(power_kw, 0.0) * dt_hours  # negatif olmasın diye güvence
                delta_soc = (energy_kwh / battery_capacity_kwh) * 100.0

                soc = soc_state.get(cp.id, 20.0)
                soc = min(100.0, soc + delta_soc)
                soc_state[cp.id] = soc

                await cp.send_meter_values(
                    connector_id=connector_id,
                    power_kw=power_kw,
                    current_a=current_a,
                    voltage_v=voltage_v,
                    transaction_id=tx_id,
                    soc_percent=soc,
                )

            

            # Her adım arasında 1 saniye bekleyelim
            await asyncio.sleep(1)

        # -----------------------------
        # 5) StopTransaction + Finishing + Available
        # -----------------------------
        for cp in cps:
            tx_id = tx_ids.get(cp.id)
            if not tx_id:
                continue

            # Finishing
            await cp.send_status_notification(
                connector_id=connector_id,
                status="Finishing",
            )

            await cp.send_stop_transaction(
                transaction_id=tx_id,
                meter_stop=0,
            )

            # tekrar Available
            await cp.send_status_notification(
                connector_id=connector_id,
                status="Available",
            )

    # -----------------------------
    # LABEL DÖNÜŞÜ
    # -----------------------------
    def get_label_for_event(self, event: Dict[str, Any], mode: str) -> str:
        """
        Her event için anomaly label'ı belirler.

        Not: ScenarioBase tarafında event içine message_type eklenmiyorsa
        sadece mode'a göre etiketleriz. İleride istersen message_type'a göre
        daha ince ayrım yapabiliriz.
        """
        if mode == "normal":
            return "normal"

        # Attack modunda isek:
        # Eğer event dict'inde message_type varsa ve MeterValues ise
        # bunu asıl saldırı olarak işaretleyebiliriz.
        message_type = event.get("message_type") or event.get("ocpp_action")

        if message_type == "MeterValues":
            return "oscillatory_load_attack"

        # Diğer her şey saldırı bağlamında meta / yardımcı kayıt
        return "attack_meta"


# ----------------------------------------------------------------------
# run_simulation.py tarafından çağrılan giriş noktası
# ----------------------------------------------------------------------
async def run_scenario(
    mode: str,
    duration: int,
    stations: int,
    output_path: str,
) -> None:
    """
    run_simulation.py'nin beklediği senaryo giriş noktası.
    """
    config = DalgaliYukScenarioConfig(name="dalgali_yuk")
    scenario = DalgaliYukScenario(config=config)

    await scenario.run(
        mode=mode,
        duration=duration,
        stations=stations,
        output_path=output_path,
    )
