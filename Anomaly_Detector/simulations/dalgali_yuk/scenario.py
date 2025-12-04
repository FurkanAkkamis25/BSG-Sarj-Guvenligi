# simulations/dalgalı_yuk/scenario.py

from __future__ import annotations

import asyncio
import math
from typing import List, Dict, Any

from simulations.base.scenario_base import ScenarioBase, ScenarioConfig
from core.charge_point import SimulatedChargePoint


class DalgaliYukScenario(ScenarioBase):
    """
    Dalgalı Yük Saldırısı Senaryosu

    Normal mod:
        - Güç sabit ~7 kW civarı (ufak noise olabilir)

    Attack mod:
        - Güç 7 kW etrafında sinüs dalgalı osilasyon yapar
        - Ani yük dalgalanmaları ile şebekeyi gereksiz zorlar
    """

    async def run_for_all_charge_points(
        self,
        cps: List[SimulatedChargePoint],
        mode: str,
        duration: int,
    ) -> None:
        """
        Tüm charge point'ler için:
          1) Authorize
          2) StartTransaction
          3) duration boyunca periyodik MeterValues
          4) StopTransaction
        """
        # 1) Authorize + StartTransaction
        tx_ids: Dict[str, int] = {}

        for cp in cps:
            # Kimlik doğrulama
            await cp.send_authorize("TAG123")

            # Şarj oturumu başlat
            res = await cp.send_start_transaction(
                connector_id=1,
                id_tag="TAG123",
                meter_start=0,
            )
            tx_ids[cp.id] = res.transaction_id

        # 2) duration boyunca MeterValues gönder
        # duration "adım" gibi düşünülebilir, her adımda tüm CP'ler data yollar
        base_power_kw = 7.0          # normal şarj gücü
        attack_amp_kw = 3.0          # salınım genliği (attack modunda)
        sleep_seconds = 0.5          # adımlar arası bekleme

        for step in range(duration):
            for idx, cp in enumerate(cps):
                # Her CP için küçük faz farkı verelim ki dalgalar tam aynı olmasın
                phase_shift = idx * math.pi / 4

                if mode == "normal":
                    # Normal modda sabite yakın değer (istersen noise ekleyebilirsin)
                    power_kw = base_power_kw
                else:
                    # Attack mod: sinüs dalgalı yük
                    # step / 2: dalgalanmanın hızını ayarlıyor
                    power_kw = base_power_kw + attack_amp_kw * math.sin(
                        (step / 2.0) + phase_shift
                    )

                # Basitçe: P = V * I  →  I ≈ P * k sabit alıyoruz
                # Burası tamamen senaryonun soyut modeli, gerçek fizik değil
                current_a = power_kw * 4.5
                voltage_v = 380.0

                await cp.send_meter_values(
                    connector_id=1,
                    power_kw=power_kw,
                    current_a=current_a,
                    voltage_v=voltage_v,
                    transaction_id=tx_ids[cp.id],
                )

            # Bir sonraki adıma geçmeden önce biraz bekle
            await asyncio.sleep(sleep_seconds)

        # 3) StopTransaction
        for cp in cps:
            await cp.send_stop_transaction(
                transaction_id=tx_ids[cp.id],
                meter_stop=100,
            )

    # ------------------------------------------------------------------
    # Label mantığı
    # ------------------------------------------------------------------
    def get_label_for_event(self, event: Dict[str, Any], mode: str) -> str:
        """
        Normal mod:
            - tüm event'ler: "normal"

        Attack mod:
            - MeterValues: "oscillatory_load_attack"
            - diğer OCPP mesajları (Boot, Authorize, Start/Stop, vs.): "attack_meta"
        """
        message_type = event.get("message_type")

        if mode == "normal":
            return "normal"

        # mode == "attack"
        if message_type == "MeterValues":
            return "oscillatory_load_attack"
        else:
            return "attack_meta"


# ----------------------------------------------------------------------
# run_simulation.py için GİRİŞ NOKTASI
# ----------------------------------------------------------------------
async def run_scenario(
    mode: str,
    duration: int,
    stations: int,
    output_path: str,
) -> None:
    """
    run_simulation.py tarafından çağrılan ortak giriş noktası.

    Burada:
        - Senaryo nesnesi oluşturulur
        - ScenarioBase.run(...) çalıştırılır
    """
    scenario = DalgaliYukScenario(
        ScenarioConfig(name="dalgali_yuk")
    )

    await scenario.run(
        mode=mode,
        duration=duration,
        stations=stations,
        output_path=output_path,
    )
